# pylint: disable=line-too-long, abstract-method
"""SmartPI algorithm handler for the plugin runtime."""

import logging
import math
import time
from dataclasses import replace
from typing import TYPE_CHECKING
from homeassistant.util import slugify
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from datetime import timedelta

from .algo import SmartPI
from .smartpi.command_ownership import project_cycle_command
from .smartpi.const import (
    SMARTPI_RECALC_INTERVAL_SEC,
    SOURCE_COMMAND_MAX_AGE_S,
    SmartPIPhase,
    SmartPICalibrationPhase,
    SmartPICalibrationResult,
    NEAR_BAND_HYSTERESIS_C,
)
from .const import (
    CONF_TARGET_VTHERM,
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_USE_SETPOINT_FILTER,
    CONF_SMART_PI_USE_FF3,
    CONF_SMART_PI_HYSTERESIS_ON,
    CONF_SMART_PI_HYSTERESIS_OFF,
    CONF_SMART_PI_RELEASE_TAU_FACTOR,
    CONF_SMART_PI_DEADBAND_ALLOW_P,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEBUG,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_MIN_VALVE,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_RESET_TOKEN,
    DATA_SOURCE_LINK,
    DEFAULT_OPTIONS,
    DIAGNOSTIC_SENSOR_UNIQUE_ID_PREFIX,
    DOMAIN,
    EventType,
    SIGNAL_SMARTPI_TARGET_UPDATED,
)
from .hvac_mode import VThermHvacMode_OFF, VThermHvacMode_HEAT, VThermHvacMode_COOL
from .commons import write_event_log
from .smartpi.valve_curve import ValveCurveParams
from .smartpi.water_input import SOURCE_LASTING, WaterInput, WaterReading
from .smartpi.boiler_call import REASON_NOT_CALLED, SOURCE_SMART_BOILER, SOURCE_VTHERM, BoilerReading
from .smartpi.dependencies import (
    KEY_LEAD_EMITTER,
    KEY_SOURCE_EMITTER,
    KEY_ZONE_MASTER,
    DependencyItem,
    parse_dependencies,
)
from .smartpi.source_model import EMITTER_CUSTOM
from .smartpi.source_link import SourceCycle, SourceLink
from .smartpi.source_settings import SourceSettings
from .smartpi.dependency_inputs import (
    DependencyReadings,
    EntitySnapshot,
    NeighbourObservation,
    SourceObservation,
    doors_open,
    read_neighbour,
)

if TYPE_CHECKING:
    from vtherm_api.interfaces import InterfaceThermostatRuntime
    from .smartpi.autocalib import AutoCalibEvent

_LOGGER = logging.getLogger(__name__)

# Smart Boiler's water interface (its documentation/en/water-interface.md).
SMART_BOILER_MANAGER = "vtherm_smart_boiler"
SMART_BOILER_API_KEY = "vtherm_smart_boiler_api"
WATER_FIELDS = ("supply_temperature", "supply_reason", "heat_available", "heat_reason")
# Whether Smart Boiler calls the boiler (its water interface, agreed on 2026-10-14), and the
# demand against its start criteria, for the diagnostics.
CALL_REASONS = ("below_threshold", "no_demand", "activation_delay", "boiler_fault")
DEMAND_FIELDS = {
    "demand_count": int,
    "count_threshold": int,
    "count_by": str,
    "demand_power_kw": float,
    "power_threshold_kw": float,
    "demand_opening": float,
    "opening_threshold": float,
}
BOILER_FIELDS = ("boiler_called", "call_reason", *DEMAND_FIELDS)
# Versatile Thermostat's central boiler: its binary sensor, on while VT calls the boiler.
VT_DOMAIN = "versatile_thermostat"
VT_CENTRAL_BOILER_UNIQUE_ID = "central_boiler_state"
# Key SmartPI writes into the thermostat's specific_states for Smart Boiler.
WATER_FACTOR_KEY = "water_factor"
WATER_ISSUE_PREFIX = "water_data_changed_"


def _water_reading(fields: dict) -> WaterReading:
    """Keep only well-typed values: anything else means no data for that field."""
    supply = fields.get("supply_temperature")
    if isinstance(supply, bool) or not isinstance(supply, (int, float)):
        supply = None
    heat_available = fields.get("heat_available")

    def text(value):
        return value if isinstance(value, str) and value else None

    return WaterReading(
        supply_c=float(supply) if supply is not None else None,
        supply_reason=text(fields.get("supply_reason")),
        heat_available=heat_available if isinstance(heat_available, bool) else None,
        heat_reason=text(fields.get("heat_reason")),
    )

def _boiler_reading(fields: dict) -> BoilerReading | None:
    """Smart Boiler's call of the boiler, or None when it does not say (it does not control the
    boiler, an older version without the fields, a wrong type, a reason SmartPI does not know)."""
    called = fields.get("boiler_called")
    reason = fields.get("call_reason")
    if called is True:
        return BoilerReading(True, None, SOURCE_SMART_BOILER, _demand(fields))
    if called is False and reason in CALL_REASONS:
        return BoilerReading(False, reason, SOURCE_SMART_BOILER, _demand(fields))
    return None


def _demand(fields: dict) -> dict | None:
    """The demand fields Smart Boiler gave with a well-formed value (diagnostics only)."""
    demand = {}
    for name, kind in DEMAND_FIELDS.items():
        value = fields.get(name)
        if isinstance(value, bool):
            continue
        if kind is str and isinstance(value, str) and value:
            demand[name] = value
        elif kind is int and isinstance(value, int):
            demand[name] = value
        elif kind is int and isinstance(value, float) and value.is_integer():
            demand[name] = int(value)
        elif kind is float and isinstance(value, (int, float)) and math.isfinite(value):
            demand[name] = round(float(value), 3)
    return demand or None


def vtherm_central_boiler_on(hass) -> bool | None:
    """Whether Versatile Thermostat's central boiler is on (None: no such boiler, or not known)."""
    try:
        entity_id = er.async_get(hass).async_get_entity_id("binary_sensor", VT_DOMAIN, VT_CENTRAL_BOILER_UNIQUE_ID)
        state = hass.states.get(entity_id) if entity_id is not None else None
    except Exception:  # pylint: disable=broad-except
        return None
    if state is None or state.state not in ("on", "off"):
        return None
    return state.state == "on"


STORAGE_VERSION = 1
STORAGE_KEY = "vtherm_smartpi.{}"
LEGACY_STORAGE_KEY = "versatile_thermostat.smartpi.{}"

class SmartPIHandler:
    """Handler for SmartPI-specific logic."""

    def __init__(self, thermostat: "InterfaceThermostatRuntime"):
        """Initialize handler with parent thermostat reference."""
        self._thermostat = thermostat
        self._store: Store | None = None
        self._legacy_store: Store | None = None
        self._recalc_timer_remove = None
        # While this thermostat is off and has a source: it still tells the source so.
        self._source_heartbeat_remove = None
        # The zone's master thermostat (registry id), when the zone form set it.
        self._zone_master_id: str | None = None
        # State for learning
        self._last_temp = None
        self._last_ext_temp = None
        self._last_time = None
        self._last_on_percent = 0.0
        self._should_publish_intermediate: bool = True
        self._last_publish_signature = None
        self._pending_publish_signature = None
        # Track calibration state for completion detection
        self._prev_is_calibrating: bool = False
        self._allow_pwm_cycle_force: bool = False
        self._valve_linearization_configured: bool = False
        self._valve_curve_params: ValveCurveParams | None = None
        self._dependency_items: list[DependencyItem] = []
        # Second source of this zone (lead side): its settings and unique id.
        self._source_settings: SourceSettings | None = None
        self._source_unique_id: str | None = None
        # Last command applied as a source (valve changes are reported).
        self._applied_source_command: float | None = None

    def init_algorithm(self):
        """Initialize SmartPI algorithm."""
        t = self._thermostat
        entry, _ = self._resolve_effective_config()

        # Initialize storage with slugified name to allow retrieval if re-created
        safe_name = slugify(t.name)
        self._store = Store(t.hass, STORAGE_VERSION, STORAGE_KEY.format(safe_name))
        self._legacy_store = Store(
            t.hass, STORAGE_VERSION, LEGACY_STORAGE_KEY.format(safe_name)
        )

        # Use the thermostat's cycle_min property directly for consistency
        # (base_thermostat reads CONF_CYCLE_MIN in __init__)
        cycle_min = t.cycle_min
        minimal_activation_delay = entry.get(CONF_MINIMAL_ACTIVATION_DELAY, 0)
        minimal_deactivation_delay = entry.get(CONF_MINIMAL_DEACTIVATION_DELAY, 0)
        max_on_percent = getattr(t, "max_on_percent", None)

        # Update thermostat attributes directly (like TPIHandler)
        t.minimal_activation_delay = minimal_activation_delay
        t.minimal_deactivation_delay = minimal_deactivation_delay
        self._synchronize_scheduler_timing(
            minimal_activation_delay,
            minimal_deactivation_delay,
        )

        # SmartPI specific
        deadband = entry.get(CONF_SMART_PI_DEADBAND, 0.05)
        use_setpoint_filter = entry.get(CONF_SMART_PI_USE_SETPOINT_FILTER, True)
        use_ff3 = entry.get(
            CONF_SMART_PI_USE_FF3,
            DEFAULT_OPTIONS[CONF_SMART_PI_USE_FF3],
        )
        hyst_on = entry.get(CONF_SMART_PI_HYSTERESIS_ON, 0.3)
        hyst_off = entry.get(CONF_SMART_PI_HYSTERESIS_OFF, 0.5)
        release_tau_factor = entry.get(CONF_SMART_PI_RELEASE_TAU_FACTOR, 0.5)
        deadband_allow_p = entry.get(CONF_SMART_PI_DEADBAND_ALLOW_P, False)
        self._allow_pwm_cycle_force = entry.get(
            CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
            DEFAULT_OPTIONS[CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE],
        )
        debug_mode = entry.get(CONF_SMART_PI_DEBUG, False)
        self._valve_linearization_configured = entry.get(
            CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
            False,
        )
        valve_mode_enabled = bool(getattr(t.cycle_scheduler, "is_valve_mode", False))
        valve_linearization_enabled = (
            self._valve_linearization_configured
            and valve_mode_enabled
        )
        try:
            self._valve_curve_params = ValveCurveParams(
                min_valve=float(
                    entry.get(
                        CONF_SMART_PI_MIN_VALVE,
                        DEFAULT_OPTIONS[CONF_SMART_PI_MIN_VALVE],
                    )
                ),
                knee_demand=float(
                    entry.get(
                        CONF_SMART_PI_KNEE_DEMAND,
                        DEFAULT_OPTIONS[CONF_SMART_PI_KNEE_DEMAND],
                    )
                ),
                knee_valve=float(
                    entry.get(
                        CONF_SMART_PI_KNEE_VALVE,
                        DEFAULT_OPTIONS[CONF_SMART_PI_KNEE_VALVE],
                    )
                ),
                max_valve=float(
                    entry.get(
                        CONF_SMART_PI_MAX_VALVE,
                        DEFAULT_OPTIONS[CONF_SMART_PI_MAX_VALVE],
                    )
                ),
            )
        except (TypeError, ValueError) as err:
            _LOGGER.error("%s - Invalid SmartPI valve curve parameters: %s", t, err)
            self._valve_linearization_configured = False
            valve_linearization_enabled = False
            self._valve_curve_params = None
        # Create SmartPI instance
        # Note: saved_state is loaded asynchronously later
        t.prop_algorithm = SmartPI(
            hass=t.hass,
            cycle_min=cycle_min,
            minimal_activation_delay=minimal_activation_delay,
            minimal_deactivation_delay=minimal_deactivation_delay,
            name=t.name,
            max_on_percent=max_on_percent,
            deadband_c=deadband,
            use_setpoint_filter=use_setpoint_filter,
            use_ff3=use_ff3,
            hysteresis_on=hyst_on,
            hysteresis_off=hyst_off,
            release_tau_factor=release_tau_factor,
            deadband_allow_p=deadband_allow_p,
            debug_mode=debug_mode,
            enable_valve_linearization=valve_linearization_enabled,
            valve_curve_params=self._valve_curve_params,
        )
        t.prop_algorithm.configure_valve_linearization(
            valve_linearization_enabled,
            self._valve_curve_params,
            valve_mode=valve_mode_enabled,
        )
        t.prop_algorithm.configure_heating_type(
            entry.get(CONF_SMART_PI_HEATING_TYPE, DEFAULT_OPTIONS[CONF_SMART_PI_HEATING_TYPE])
        )
        self._dependency_items = parse_dependencies(entry.get(CONF_SMART_PI_DEPENDENCIES))
        t.prop_algorithm.set_known_inputs_provider(
            self._read_dependencies if self._dependency_items else None
        )
        source_item = next((item for item in self._dependency_items if item.is_source), None)
        self._zone_master_id = source_item.extra.get(KEY_ZONE_MASTER) if source_item is not None else None
        if source_item is not None:
            source_item = self._with_heating_types(source_item, entry)
        self._source_settings = SourceSettings.from_item(source_item) if source_item else None
        t.prop_algorithm.configure_source(self._source_settings)
        self._attach_source()

        _LOGGER.info("%s - SmartPI Algorithm initialized", t)
        async_dispatcher_send(t.hass, SIGNAL_SMARTPI_TARGET_UPDATED, t.unique_id)

    def _with_heating_types(self, item: DependencyItem, own_config: dict) -> DependencyItem:
        """The zone's source item with the heating types the two thermostats' entries set now.

        A custom response set in the zone form is kept.
        """
        extra = dict(item.extra)
        if extra.get(KEY_LEAD_EMITTER) != EMITTER_CUSTOM:
            extra[KEY_LEAD_EMITTER] = own_config.get(
                CONF_SMART_PI_HEATING_TYPE, DEFAULT_OPTIONS[CONF_SMART_PI_HEATING_TYPE]
            )
        if extra.get(KEY_SOURCE_EMITTER) != EMITTER_CUSTOM:
            heating_type = self._source_heating_type(item.entity)
            if heating_type is not None:
                extra[KEY_SOURCE_EMITTER] = heating_type
        return replace(item, extra=extra)

    def _source_heating_type(self, registry_id: str) -> str | None:
        """The heating type of the source thermostat's SmartPI entry (else the defaults')."""
        hass = self._thermostat.hass
        try:
            unique_id = self._resolve_unique_id(registry_id)
            entries = hass.config_entries.async_entries(DOMAIN)
        except Exception:  # pylint: disable=broad-except
            return None
        own = next((e for e in entries if unique_id and e.data.get(CONF_TARGET_VTHERM) == unique_id), None)
        chosen = own or next((e for e in entries if e.unique_id == DOMAIN), None)
        if chosen is None:
            return None
        config = {**chosen.data, **chosen.options}
        return config.get(CONF_SMART_PI_HEATING_TYPE)

    def _resolve_effective_config(self) -> tuple[dict, object | None]:
        """Return the merged SmartPI configuration for the thermostat."""
        t = self._thermostat
        config = dict(DEFAULT_OPTIONS)
        config.update(t.entry_infos or {})

        plugin_entries = t.hass.config_entries.async_entries(DOMAIN)
        matching_entry = next(
            (
                entry
                for entry in plugin_entries
                if entry.data.get(CONF_TARGET_VTHERM) == t.unique_id
            ),
            None,
        )
        global_entry = next(
            (entry for entry in plugin_entries if entry.unique_id == DOMAIN),
            None,
        )
        entry_to_apply = matching_entry or global_entry
        if entry_to_apply is not None:
            config.update(entry_to_apply.data)
            config.update(entry_to_apply.options)

        return config, entry_to_apply

    def _get_effective_config(self) -> dict:
        """Return the merged SmartPI configuration for the thermostat."""
        config, _ = self._resolve_effective_config()
        return config

    async def async_added_to_hass(self):
        """Load persistent data."""
        t = self._thermostat
        if self._store:
            try:
                data = await self._store.async_load()
                if data is None and self._legacy_store is not None:
                    data = await self._legacy_store.async_load()
                    if data is not None:
                        t.hass.async_create_task(self._store.async_save(data))
                if data and t.prop_algorithm:
                    t.prop_algorithm.load_state(data)
                    _LOGGER.debug("%s - SmartPI state loaded", t)
                if t.prop_algorithm:
                    t.prop_algorithm.forget_dependencies_except(
                        item.item_id for item in self._dependency_items
                    )
                    self._apply_reset_request()
            except Exception as e:
                _LOGGER.error("%s - Failed to load SmartPI state: %s", t, e)

    def _apply_reset_request(self) -> None:
        """Reset the learned model once after a change of the zone asked for it."""
        t = self._thermostat
        algo = t.prop_algorithm
        token = self._get_effective_config().get(CONF_SMART_PI_RESET_TOKEN)
        if not isinstance(algo, SmartPI) or not token or algo.applied_reset_token == token:
            return
        algo.reset_learning()
        algo.applied_reset_token = token
        _LOGGER.info("%s - Learned model reset after a change of the zone", t)

    async def async_startup(self):
        """Startup actions."""
        # Initialize the cycle start state if possible to enable first-cycle learning
        t = self._thermostat
        if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
            # Check availability of sensors
            if t.current_temperature is not None and t.current_outdoor_temperature is not None:
                _LOGGER.debug("%s - SmartPI startup: ready for cycle management", t)

        # Check if we need to start the periodic recalculation timer
        await self.on_state_changed(True)

    async def _async_save(self):
        """Save SmartPI state to storage."""
        t = self._thermostat
        if self._store and t.prop_algorithm:
            try:
                data = t.prop_algorithm.save_state()
                t.hass.async_create_task(self._store.async_save(data))
                _LOGGER.debug("%s - SmartPI state saved", t)
            except Exception as e:
                _LOGGER.error("%s - Failed to save SmartPI state: %s", t, e)

    # --- second source -----------------------------------------------------

    def _source_link(self) -> SourceLink:
        data = self._thermostat.hass.data.setdefault(DOMAIN, {})
        link = data.get(DATA_SOURCE_LINK)
        if not isinstance(link, SourceLink):
            link = data[DATA_SOURCE_LINK] = SourceLink()
        return link

    def _resolve_unique_id(self, registry_id: str) -> str | None:
        """Unique id of the climate entity behind a registry id."""
        registry = er.async_get(self._thermostat.hass)
        entity_id = er.async_resolve_entity_id(registry, registry_id)
        entry = registry.async_get(entity_id) if entity_id else None
        return entry.unique_id if entry is not None else None

    def _attach_source(self) -> None:
        """Make the configured source work for this thermostat (lead side)."""
        t = self._thermostat
        link = self._source_link()
        link.detach_lead(t.unique_id)
        self._source_unique_id = None
        if self._source_settings is None:
            return
        try:
            source_unique_id = self._resolve_unique_id(self._source_settings.entity)
        except Exception:  # pylint: disable=broad-except
            source_unique_id = None
        if source_unique_id is None or source_unique_id == t.unique_id:
            _LOGGER.warning("%s - The second source cannot be found", t)
            return
        if not link.attach(t.unique_id, source_unique_id, time.monotonic()):
            _LOGGER.warning("%s - The second source already works for another thermostat", t)
            return
        self._source_unique_id = source_unique_id
        self._check_zone_master()

    def _check_zone_master(self) -> None:
        """Warn when the zone's master no longer has exactly these two thermostats as its devices."""
        t = self._thermostat
        master_id = self._zone_master_id if self._source_settings else None
        if not master_id:
            return
        try:
            registry = er.async_get(t.hass)
            master = registry.async_get(master_id)
            source_entity = er.async_resolve_entity_id(registry, self._source_settings.entity)
            vt_entry = (
                t.hass.config_entries.async_get_entry(master.config_entry_id)
                if master is not None and master.config_entry_id
                else None
            )
        except Exception:  # pylint: disable=broad-except
            return
        if vt_entry is None:
            _LOGGER.warning("%s - The zone's master thermostat cannot be found; check the zone", t)
            return
        devices = {**vt_entry.data, **vt_entry.options}.get("underlying_entity_ids") or []
        if set(devices) != {t.entity_id, source_entity}:
            _LOGGER.warning(
                "%s - The zone's master %s no longer has exactly this thermostat and %s as its devices; check the zone",
                t,
                master.entity_id,
                source_entity,
            )

    def _publish_source_command(self) -> None:
        """Give the source the command the dependency layer decided (lead side)."""
        if self._source_unique_id is None:
            return
        t = self._thermostat
        algo = t.prop_algorithm if isinstance(t.prop_algorithm, SmartPI) else None
        hvac_mode = t.vtherm_hvac_mode
        command = 0.0
        if algo is not None and hvac_mode in (VThermHvacMode_HEAT, VThermHvacMode_COOL):
            command = algo.source_command or 0.0
        self._source_link().publish_command(t.unique_id, command, time.monotonic(), str(hvac_mode))

    def _read_source(self) -> SourceObservation | None:
        """The source as the lead sees it at this calculation."""
        if self._source_settings is None:
            return None
        t = self._thermostat
        source_unique_id = self._source_unique_id
        link = self._source_link()
        algo = t.prop_algorithm if isinstance(t.prop_algorithm, SmartPI) else None
        after_s = algo.dependency_model.source_cycles_after_s if algo is not None else 0.0
        try:
            entity_id = er.async_resolve_entity_id(er.async_get(t.hass), self._source_settings.entity)
        except Exception:  # pylint: disable=broad-except
            entity_id = None
        return SourceObservation(
            item_id=self._source_settings.item_id,
            entity_id=entity_id,
            active=source_unique_id is not None
            and link.is_active(source_unique_id, time.monotonic(), SOURCE_COMMAND_MAX_AGE_S),
            cycles=tuple(link.cycles_since(source_unique_id, after_s)) if source_unique_id else (),
            power_cap=link.power_cap(source_unique_id) if source_unique_id else 1.0,
        )

    @property
    def is_executive_source(self) -> bool:
        """This thermostat currently works for a lead as its second source."""
        algo = self._thermostat.prop_algorithm
        return isinstance(algo, SmartPI) and algo.is_executive

    def _enter_source_role(self, lead: str) -> None:
        t = self._thermostat
        algo = t.prop_algorithm
        if isinstance(algo, SmartPI):
            algo.enter_source_role(lead)
        self._applied_source_command = None
        _LOGGER.info("%s - Working as the second source of another thermostat; own regulation and learning paused", t)

    def _leave_source_role(self) -> None:
        t = self._thermostat
        algo = t.prop_algorithm
        if isinstance(algo, SmartPI):
            algo.leave_source_role()
            algo.reset_cycle_state()
        self._applied_source_command = None
        _LOGGER.info("%s - No longer a second source; own regulation resumed", t)

    async def _control_as_source(self, force: bool) -> None:
        """Apply the lead's command with this thermostat's own cycle and delays."""
        t = self._thermostat
        link = self._source_link()
        now_s = time.monotonic()
        hvac_mode = t.vtherm_hvac_mode
        running = hvac_mode in (VThermHvacMode_HEAT, VThermHvacMode_COOL)
        command = (
            link.command_for(t.unique_id, now_s, SOURCE_COMMAND_MAX_AGE_S, str(hvac_mode))
            if running and not t.is_overpowering_detected
            else 0.0
        )
        link.set_active(t.unique_id, running, now_s)
        algo = t.prop_algorithm if isinstance(t.prop_algorithm, SmartPI) else None
        if algo is not None:
            # The lead's command is power at the reference water: this source's
            # own water factor turns it into the command of its emitter.
            self._update_water(algo)
            link.set_power_cap(t.unique_id, algo.source_power_cap)
            algo.set_source_command(command)
        if not running:
            setattr(t, "_on_time_sec", 0)
            setattr(t, "_off_time_sec", int(t.cycle_min * 60))
            if t.is_device_active:
                await t.async_underlying_entity_turn_off()
            return
        scheduler = t.cycle_scheduler
        if bool(getattr(scheduler, "is_valve_mode", False)) and command != self._applied_source_command:
            # A valve takes a new opening within its cycle: report it as it comes.
            duration_s = float(t.cycle_min) * 60.0
            power = algo._to_linear_power(algo.on_percent) if algo is not None else command
            link.report_cycle(t.unique_id, SourceCycle(now_s, duration_s, duration_s, power, False))
        self._applied_source_command = command
        await scheduler.start_cycle(hvac_mode, algo.on_percent if algo is not None else command, force)

    def remove(self):
        """Cleanup and save state on removal."""
        t = self._thermostat
        if self._store and t.prop_algorithm:
            # We can't await here easily, but we schedule save
            t.hass.async_create_task(self._async_save())

        self._stop_recalc_timer()
        self._stop_source_heartbeat()
        if self._source_unique_id is not None:
            self._source_link().detach_lead(t.unique_id)
        if self.is_executive_source:
            # The lead must not keep counting the heat of a source that is gone.
            self._source_link().set_active(t.unique_id, False, time.monotonic())

    def on_scheduler_ready(self, scheduler) -> None:
        """Register SmartPI learning callbacks on the cycle scheduler."""
        algo = self._thermostat.prop_algorithm
        if algo:
            self._synchronize_scheduler_timing(
                self._thermostat.minimal_activation_delay,
                self._thermostat.minimal_deactivation_delay,
                scheduler=scheduler,
            )
            algo.configure_valve_linearization(
                self._valve_linearization_configured
                and bool(getattr(scheduler, "is_valve_mode", False)),
                self._valve_curve_params,
                valve_mode=bool(getattr(scheduler, "is_valve_mode", False)),
            )
            scheduler.register_cycle_start_callback(self._on_scheduler_cycle_started)
            scheduler.register_cycle_end_callback(self._on_scheduler_cycle_completed)

    async def _on_scheduler_cycle_completed(self, **kwargs) -> None:
        """Forward the end of a cycle to SmartPI, unless this thermostat works as a source."""
        algo = self._thermostat.prop_algorithm
        if self.is_executive_source or not isinstance(algo, SmartPI):
            return
        await algo.on_cycle_completed(**kwargs)

    def _synchronize_scheduler_timing(
        self,
        minimal_activation_delay: int,
        minimal_deactivation_delay: int,
        *,
        scheduler=None,
    ) -> None:
        """Keep the scheduler timing inputs aligned with SmartPI configuration."""
        scheduler = (
            scheduler if scheduler is not None else self._thermostat.cycle_scheduler
        )
        if scheduler is None:
            return
        scheduler.min_activation_delay = int(minimal_activation_delay)
        scheduler.min_deactivation_delay = int(minimal_deactivation_delay)

    def _project_scheduler_command(self, on_percent: float):
        """Project one request from the effective scheduler timing inputs."""
        t = self._thermostat
        scheduler = t.cycle_scheduler
        cycle_duration_sec = getattr(scheduler, "_cycle_duration_sec", None)
        cycle_min = (
            float(cycle_duration_sec) / 60.0
            if isinstance(cycle_duration_sec, (int, float)) and cycle_duration_sec > 0
            else t.cycle_min
        )
        minimal_activation_delay = getattr(
            scheduler,
            "min_activation_delay",
            t.minimal_activation_delay,
        )
        minimal_deactivation_delay = getattr(
            scheduler,
            "min_deactivation_delay",
            t.minimal_deactivation_delay,
        )
        return project_cycle_command(
            on_percent=on_percent,
            cycle_min=cycle_min,
            minimal_activation_delay=int(minimal_activation_delay),
            minimal_deactivation_delay=int(minimal_deactivation_delay),
        )

    @staticmethod
    def _published_valve_power(thermostat) -> float | None:
        """Return the current valve opening published by the thermostat."""
        valve_open_percent = getattr(thermostat, "valve_open_percent", None)
        if valve_open_percent is None:
            return None
        try:
            return max(0.0, min(1.0, float(valve_open_percent) / 100.0))
        except (TypeError, ValueError):
            return None

    async def _on_scheduler_cycle_started(
        self,
        on_time_sec: float,
        off_time_sec: float,
        on_percent: float,
        hvac_mode: str,
    ) -> None:
        """Forward scheduler starts after valve publication when required."""
        algo = self._thermostat.prop_algorithm
        link = self._source_link()
        if link.lead_of(self._thermostat.unique_id) is not None:
            # Also while regulating on its own because the lead is silent: once
            # back, the lead rebuilds the heat on its way from these cycles.
            scheduler = self._thermostat.cycle_scheduler
            link.report_cycle(
                self._thermostat.unique_id,
                SourceCycle(
                    start_s=time.monotonic(),
                    on_s=float(on_time_sec),
                    duration_s=float(on_time_sec) + float(off_time_sec),
                    power=algo._to_linear_power(float(on_percent)) if isinstance(algo, SmartPI) else float(on_percent),
                    switched=not bool(getattr(scheduler, "is_valve_mode", False)),
                    level=algo.water_factor if isinstance(algo, SmartPI) else 1.0,
                ),
            )
        if self.is_executive_source:
            return
        if not isinstance(algo, SmartPI):
            return
        if not bool(
            getattr(self._thermostat.cycle_scheduler, "is_valve_mode", False)
        ):
            await algo.on_cycle_started(on_time_sec, off_time_sec, on_percent, hvac_mode)
            return
        await algo.on_cycle_started(
            on_time_sec,
            off_time_sec,
            on_percent,
            hvac_mode,
            physical_on_percent=self._published_valve_power(self._thermostat),
        )

    def should_publish_intermediate(self) -> bool:
        """Return True when VT may publish the current control iteration."""
        if self._should_publish_intermediate:
            self._last_publish_signature = self._pending_publish_signature
        return self._should_publish_intermediate

    def _build_publish_signature(self):
        """Return the thermostat inputs that should refresh the climate state."""
        t = self._thermostat
        return (
            t.current_temperature,
            t.current_outdoor_temperature,
            t.target_temperature,
            t.last_temperature_slope,
            t.vtherm_hvac_mode,
            getattr(t, "last_temperature_measure", None),
            getattr(t, "last_ext_temperature_measure", None),
        )

    def _autocalib_event_data(self, event: "AutoCalibEvent") -> dict:
        """Return the bus data of an AutoCalib event, keyed by the climate entity_id."""
        return {**event.payload, "entity_id": self._thermostat.entity_id}

    def _entity_snapshot(self, registry, registry_id: str) -> EntitySnapshot | None:
        """Return the state of an entity referenced by its entity registry id."""
        entity_id = er.async_resolve_entity_id(registry, registry_id)
        if entity_id is None:
            return EntitySnapshot(None)
        state = self._thermostat.hass.states.get(entity_id)
        if state is None:
            return EntitySnapshot(entity_id)
        return EntitySnapshot(entity_id, state.state, state.attributes)

    def _read_dependencies(self) -> DependencyReadings:
        """Read the configured dependencies and the zone presets for one calculation."""
        hass = self._thermostat.hass
        registry = er.async_get(hass)
        neighbours = []
        for item in self._dependency_items:
            if not item.is_neighbour:
                continue
            door_open = doors_open(
                self._entity_snapshot(registry, sensor) for sensor in item.door_sensors
            )
            neighbours.append(
                NeighbourObservation(
                    item=item,
                    reading=read_neighbour(item, self._entity_snapshot(registry, item.entity)),
                    door_open=door_open,
                )
            )
        presets = {}
        temp_range = None
        own_state = hass.states.get(self._thermostat.entity_id) if self._thermostat.entity_id else None
        if own_state is not None:
            raw_presets = own_state.attributes.get("preset_temperatures")
            if isinstance(raw_presets, dict):
                presets = {
                    str(name): float(value)
                    for name, value in raw_presets.items()
                    if isinstance(value, (int, float))
                }
            low = own_state.attributes.get("min_temp")
            high = own_state.attributes.get("max_temp")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low < high:
                temp_range = (float(low), float(high))
        return DependencyReadings(
            neighbours=tuple(neighbours),
            presets=presets,
            temp_range=temp_range,
            source=self._read_source(),
        )

    async def control_heating(self, timestamp=None, force=False):
        """Control heating using SmartPI."""
        t = self._thermostat
        link = self._source_link()
        lead = link.lead_of(t.unique_id)
        if lead is not None and link.lead_silent(t.unique_id, time.monotonic(), SOURCE_COMMAND_MAX_AGE_S):
            # The lead no longer works: this thermostat regulates the zone with
            # its own SmartPI until the lead's commands come back.
            if self.is_executive_source:
                _LOGGER.warning("%s - No command from the lead thermostat; regulating on its own", t)
                link.set_active(t.unique_id, False, time.monotonic())
            lead = None
        if lead is not None:
            if not self.is_executive_source:
                self._enter_source_role(lead)
            await self._control_as_source(force)
            return
        if self.is_executive_source:
            self._leave_source_role()
        from datetime import datetime
        from .smartpi.guards import GuardAction

        algo = t.prop_algorithm if isinstance(t.prop_algorithm, SmartPI) else None
        previous_committed = algo.committed_on_percent if algo is not None else None
        self._pending_publish_signature = self._build_publish_signature()
        input_changed = self._pending_publish_signature != self._last_publish_signature
        self._should_publish_intermediate = (
            algo is None or timestamp is not None or force or input_changed
        )

        # When a forced recalculation is requested by the thermostat state machine
        # (setpoint/hvac/preset changes), close the running cycle first so the cycle-end
        # callback updates learning context before the next calculate().
        if force and t.cycle_scheduler and t.cycle_scheduler.is_cycle_running:
            if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
                t.prop_algorithm._last_restart_reason = "external_force"
            await t.cycle_scheduler.cancel_cycle()
            force = False

        if t.prop_algorithm:
            # Learning update
            current_temp = t.current_temperature

            # --- Guard Cut ---
            algo = t.prop_algorithm

            guard_cut_action = algo.guards.check_guard_cut(
                current_temp=current_temp,
                target_temp=t.target_temperature,
                near_band_above=algo.deadband_mgr.near_band_above_deg,
                in_near_band=algo.in_near_band,
                is_device_active=t.is_device_active,
                hvac_mode=t.vtherm_hvac_mode,
                is_calibration=(algo.phase == SmartPIPhase.CALIBRATION)
            )

            if guard_cut_action == GuardAction.CUT_TRIGGER:
                algo._last_restart_reason = "guard_cut"
                force = True

            # --- Guard Kick ---
            guard_kick_action = algo.guards.check_guard_kick(
                current_temp=current_temp,
                target_temp=t.target_temperature,
                near_band_below=algo.deadband_mgr.near_band_below_deg,
                in_near_band=algo.in_near_band,
                on_percent=algo.calculated_on_percent,
                hvac_mode=t.vtherm_hvac_mode,
                is_calibration=(algo.phase == SmartPIPhase.CALIBRATION)
            )

            if guard_kick_action == GuardAction.KICK_TRIGGER:
                algo._last_restart_reason = "guard_kick"
                force = True

            self._update_water(algo)
            water_input = getattr(algo, "water", None)
            # A boiler heats only a water heating type; an electric one learns as before.
            uses_water = isinstance(water_input, WaterInput) and water_input.uses_water
            algo.update_boiler(self._read_boiler() if uses_water else None)

            # Calculate uses current temp, ext temp, etc.
            # If guard_cut is active, calculate() will set on_percent=0.
            cycle_boundary = (
                force
                or t.cycle_scheduler is None
                or not t.cycle_scheduler.is_cycle_running
            )
            t.prop_algorithm.calculate(
                target_temp=t.target_temperature,
                current_temp=t.current_temperature,
                ext_current_temp=t.current_outdoor_temperature,
                slope=t.last_temperature_slope,
                hvac_mode=t.vtherm_hvac_mode,
                power_shedding=t.is_overpowering_detected,
                cycle_boundary=cycle_boundary,
                off_reason=t.hvac_off_reason,
                temperature_measure_id=getattr(t, "last_temperature_measure", None),
            )

            # Force cycle restart on deadband and near-band transitions so the
            # physically engaged PWM follows the zone change quickly.
            if (
                self._allow_pwm_cycle_force
                and getattr(algo.deadband_mgr, "deadband_changed", False) is True
            ):
                _LOGGER.debug(
                    "%s - Deadband state transition detected, forcing cycle restart",
                    t,
                )
                algo._last_restart_reason = "deadband_transition"
                force = True

            if (
                self._allow_pwm_cycle_force
                and getattr(algo.deadband_mgr, "near_band_changed", False) is True
            ):
                _LOGGER.debug(
                    "%s - Near-band state transition detected, forcing cycle restart",
                    t,
                )
                algo._last_restart_reason = "near_band_transition"
                force = True

        self._publish_source_command()

        # Stop here if we are off
        if t.vtherm_hvac_mode == VThermHvacMode_OFF:
            _LOGGER.debug("%s - End of cycle (HVAC_MODE_OFF)", t)
            setattr(t, "_on_time_sec", 0)
            setattr(t, "_off_time_sec", int(t.cycle_min * 60))
            if t.is_device_active:
                await t.async_underlying_entity_turn_off()
        else:
            on_percent = t.prop_algorithm.on_percent if t.prop_algorithm else 0.0

            # Check if on_percent has changed
            on_percent_changed = abs(on_percent - self._last_on_percent) > 0.001
            self._last_on_percent = on_percent
            was_cycle_running = bool(getattr(t.cycle_scheduler, "is_cycle_running", False))
            force_cycle = (
                force
                or (t.prop_algorithm.phase == SmartPIPhase.CALIBRATION)
                or (t.prop_algorithm.phase == SmartPIPhase.HYSTERESIS and on_percent_changed)
            )

            is_valve_mode = bool(
                getattr(t.cycle_scheduler, "is_valve_mode", False)
            )
            projection = self._project_scheduler_command(on_percent)
            valve_segment_staged = False
            if algo is not None:
                if not is_valve_mode:
                    algo.stage_cycle_command(projection, t.vtherm_hvac_mode)
                else:
                    algo.stage_valve_command(projection, t.vtherm_hvac_mode)
                    valve_segment_staged = True

            await t.cycle_scheduler.start_cycle(
                t.vtherm_hvac_mode,
                on_percent,
                force_cycle,
            )
            if (
                algo is not None
                and was_cycle_running
                and not force_cycle
                and is_valve_mode
                and valve_segment_staged
            ):
                algo.on_applied_power_updated(
                    on_percent=self._published_valve_power(t),
                    hvac_mode=t.vtherm_hvac_mode,
                    projection=projection,
                )

        if (
            algo is not None
            and timestamp is None
            and not force
            and previous_committed is not None
        ):
            committed_changed = abs(algo.committed_on_percent - previous_committed) > 0.001
            self._should_publish_intermediate = self._should_publish_intermediate or committed_changed

        # Save state after cycle to persist learning data
        await self._async_save()

        # --- AutoCalibTrigger: hourly check ---
        if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
            algo = t.prop_algorithm
            now_wall = time.time()

            # Detect calibration completion (CALIBRATING -> IDLE transition)
            currently_calibrating = algo.calibration_state != SmartPICalibrationPhase.IDLE
            if self._prev_is_calibrating and not currently_calibrating:
                # Calibration just ended
                ac_event = algo.autocalib.on_calibration_complete(
                    now_wall=now_wall,
                    algo=algo,
                    result=algo.calibration_mgr.calibration_result,
                )
                if ac_event is not None:
                    t.hass.bus.async_fire(
                        ac_event.event_type, self._autocalib_event_data(ac_event)
                    )
                    _LOGGER.info(
                        "%s - AutoCalib event fired: %s", t.name, ac_event.event_type
                    )
            self._prev_is_calibrating = currently_calibrating

            # Hourly stagnation check
            ac_event = algo.autocalib.check_hourly(
                now_wall=now_wall,
                algo=algo,
                ext_temp=t.current_outdoor_temperature,
                current_temp=t.current_temperature,
            )
            if ac_event is not None:
                t.hass.bus.async_fire(
                    ac_event.event_type, self._autocalib_event_data(ac_event)
                )
                _LOGGER.info(
                    "%s - AutoCalib event fired: %s", t.name, ac_event.event_type
                )
                if ac_event.should_trigger_calibration:
                    # AutoCalibTrigger decided to start a calibration
                    algo.calibration_mgr.request_calibration(phase=algo.phase)
                    _LOGGER.warning(
                        "%s - AutoCalib: calibration requested by supervisor", t.name
                    )

        if self._should_publish_intermediate:
            async_dispatcher_send(t.hass, f"smartpi_diag_update_{t.unique_id}")

    async def on_state_changed(self, changed: bool):
        """Handle state changes."""
        del changed
        t = self._thermostat
        if t.vtherm_hvac_mode in [VThermHvacMode_HEAT, VThermHvacMode_COOL]:
            # Check if we're resuming from OFF (timer was stopped)
            timer_was_stopped = self._recalc_timer_remove is None

            self._stop_source_heartbeat()
            self._start_recalc_timer()

            # When resuming from OFF state (e.g., window close), reset the cycle start state
            # to prevent using stale learning window data.
            if timer_was_stopped and t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
                t.prop_algorithm.reset_cycle_state()
                _LOGGER.debug("%s - SmartPI resumed from OFF: cycle and learning window reset", t.name)
        else:
            self._stop_recalc_timer()
            self._start_source_heartbeat()
            # Cancel any ongoing calibration when switching to OFF/SLEEP mode
            await self._cancel_calibration_if_active()

    def _start_recalc_timer(self):
        """Start the periodic recalculation timer."""
        t = self._thermostat
        if self._recalc_timer_remove:
            return

        async def _recalc_callback(now):
            _LOGGER.debug("%s - SmartPI periodic calculation trigger", t)
            # Force control_heating but without timestamp to avoid triggering learning
            # The learning should only be triggered by the cycle manager (every cycle_min)
            # automatic recalculation is done inside control_heating
            await t.async_control_heating(timestamp=None)

        self._recalc_timer_remove = async_track_time_interval(
            t.hass,
            _recalc_callback,
            timedelta(seconds=SMARTPI_RECALC_INTERVAL_SEC)
        )
        _LOGGER.debug("%s - SmartPI calc timer started", t)

    def _start_source_heartbeat(self) -> None:
        """While off, keep telling the source so, more often than it would take this
        thermostat for silent (VTherm's own control call may come every cycle_min only)."""
        t = self._thermostat
        if self._source_heartbeat_remove or self._source_unique_id is None:
            return

        async def _heartbeat(now):
            del now
            self._publish_source_command()

        self._source_heartbeat_remove = async_track_time_interval(
            t.hass, _heartbeat, timedelta(seconds=SMARTPI_RECALC_INTERVAL_SEC)
        )
        self._publish_source_command()

    def _stop_source_heartbeat(self) -> None:
        if self._source_heartbeat_remove:
            self._source_heartbeat_remove()
            self._source_heartbeat_remove = None

    def _stop_recalc_timer(self):
        """Stop the periodic recalculation timer."""
        t = self._thermostat
        if self._recalc_timer_remove:
            self._recalc_timer_remove()
            self._recalc_timer_remove = None
            _LOGGER.debug("%s - SmartPI calc timer stopped", t)

    async def _cancel_calibration_if_active(self):
        """Cancel any ongoing calibration (manual or auto) when HVAC is turned off."""
        t = self._thermostat
        algo = t.prop_algorithm
        if not algo or not isinstance(algo, SmartPI):
            return

        if not algo.calibration_mgr.is_calibrating:
            return

        _LOGGER.info("%s - HVAC OFF: canceling ongoing calibration", t.name)

        # Reset the calibration manager
        algo.calibration_mgr.reset()

        # Notify autocalib trigger about the cancellation
        now_wall = time.time()
        event = algo.autocalib.on_calibration_complete(now_wall, algo, SmartPICalibrationResult.CANCELLED)

        # Fire event if autocalib returns one
        if event:
            t.hass.bus.fire(EventType.SMART_PI_EVENT.value, {"entity_id": t.entity_id, "type": event.event_type, "data": event.data or {}})

        # Reset tracking state
        self._prev_is_calibrating = False

        # Update attributes and save state
        self.update_attributes()
        t.async_write_ha_state()
        await self._async_save()

    def update_attributes(self):
        """Keep climate attributes generic for SmartPI."""
        t = self._thermostat
        specific_states = t._attr_extra_state_attributes["specific_states"]
        diagnostic_entity_id = self._resolve_diagnostic_entity_id()
        specific_states["regulation_diagnostics"] = diagnostic_entity_id
        algo = t.prop_algorithm if isinstance(t.prop_algorithm, SmartPI) else None
        specific_states["smartpi_learning_enabled"] = (
            algo.learning_enabled if algo is not None else True
        )
        # Smart Boiler reads this key: present means "this thermostat's algorithm
        # uses the water data" (Smart Boiler then skips its pause on water swings),
        # so it is written only while water data are behind the factor.
        water_input = getattr(algo, "water", None)
        factor = water_input.state.published_factor if isinstance(water_input, WaterInput) else None
        if factor is not None:
            specific_states[WATER_FACTOR_KEY] = round(factor, 3)
        else:
            specific_states.pop(WATER_FACTOR_KEY, None)

    def _update_water(self, algo: SmartPI) -> None:
        """Water data for this calculation (documentation/en/water_input.md)."""
        water_input = getattr(algo, "water", None)
        if isinstance(water_input, WaterInput) and water_input.uses_water:
            water = algo.update_water(self._read_water(), self._thermostat.current_temperature)
            if water.changed_for_good:
                self._offer_model_reset(algo, gone=water.source == SOURCE_LASTING)

    def _read_water(self) -> WaterReading | None:
        """Smart Boiler's water data for this thermostat (None: no data now)."""
        fields = self._smart_boiler_fields(WATER_FIELDS)
        return _water_reading(fields) if fields is not None else None

    def _read_boiler(self) -> BoilerReading | None:
        """Whether the boiler is called (documentation/en/water_input.md, section 11).

        Smart Boiler says it when it controls the boiler; otherwise Versatile
        Thermostat's central boiler, for a thermostat VT counts for it. None: not
        known, SmartPI learns as before.
        """
        fields = self._smart_boiler_fields(BOILER_FIELDS)
        reading = _boiler_reading(fields) if fields is not None else None
        if reading is not None:
            return reading
        t = self._thermostat
        if getattr(t, "is_used_by_central_boiler", None) is True:
            on = vtherm_central_boiler_on(getattr(t, "hass", None))
            if on is not None:
                return BoilerReading(on, None if on else REASON_NOT_CALLED, SOURCE_VTHERM)
        return None

    def _smart_boiler_fields(self, names: tuple[str, ...]) -> dict | None:
        """Smart Boiler's fields for this thermostat (None: no data now).

        Nothing of Smart Boiler is imported. The feature manager is read where VT
        offers ``get_feature_manager`` (10.5.0.beta1 and later) and the thermostat
        has it; otherwise the public API in ``hass.data``, which works with any VT
        version and before VT's reload. Any failure means no data.
        """
        t = self._thermostat
        try:
            getter = getattr(t, "get_feature_manager", None)
            manager = getter(SMART_BOILER_MANAGER) if callable(getter) else None
            if manager is not None:
                return {name: getattr(manager, name, None) for name in names}
            data = getattr(getattr(t, "hass", None), "data", None)
            api = data.get(SMART_BOILER_API_KEY) if isinstance(data, dict) else None
            zone_water = getattr(api, "zone_water", None)
            mapping = zone_water(t.entity_id) if callable(zone_water) else None
            if mapping is None:
                return None
            return {name: mapping.get(name) for name in names}
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("%s - Smart Boiler's data cannot be read: %s", t, err)
            return None

    def _offer_model_reset(self, algo: SmartPI, gone: bool) -> None:
        """Water data appeared or went for good: offer the model reset (repair issue).

        The learned power changes units; nothing to offer while SmartPI still learns.
        """
        t = self._thermostat
        if algo.phase == SmartPIPhase.HYSTERESIS:
            return
        try:
            ir.async_create_issue(
                t.hass,
                DOMAIN,
                f"{WATER_ISSUE_PREFIX}{t.unique_id}",
                is_fixable=True,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="water_data_gone" if gone else "water_data_appeared",
                translation_placeholders={"name": str(t.name)},
                data={"entity_id": t.entity_id, "name": str(t.name)},
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("%s - The model reset cannot be offered: %s", t, err)

    def _resolve_diagnostic_entity_id(self) -> str | None:
        """Resolve the SmartPI diagnostic sensor entity id from the registry."""
        t = self._thermostat
        registry = er.async_get(t.hass)
        diagnostic_unique_id = f"{DIAGNOSTIC_SENSOR_UNIQUE_ID_PREFIX}_{t.unique_id}"
        return registry.async_get_entity_id(SENSOR_DOMAIN, DOMAIN, diagnostic_unique_id)

    async def service_reset_smart_pi_learning(self):
        """Reset learning data."""
        t = self._thermostat
        if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
            t.prop_algorithm.reset_learning()
            t.hass.bus.fire(EventType.SMART_PI_EVENT.value, {
                 "entity_id": t.entity_id,
                 "type": "learning_reset"
             })
            write_event_log(_LOGGER, t, "SmartPI learning reset")
            await self.control_heating(force=True)
            self.update_attributes()
            t.async_write_ha_state()
            await self._async_save()

    async def service_set_smartpi_learning(self, learning_enabled: bool):
        """Enable or pause SmartPI thermal learning."""
        t = self._thermostat
        if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
            enabled = bool(learning_enabled)
            t.prop_algorithm.set_learning_enabled(enabled)
            t.hass.bus.fire(EventType.SMART_PI_EVENT.value, {
                "entity_id": t.entity_id,
                "type": "learning_enabled_changed",
                "learning_enabled": enabled,
            })
            write_event_log(
                _LOGGER,
                t,
                f"SmartPI learning {'enabled' if enabled else 'paused'}",
            )
            self.update_attributes()
            t.async_write_ha_state()
            async_dispatcher_send(t.hass, f"smartpi_diag_update_{t.unique_id}")
            await self._async_save()

    async def service_force_smartpi_calibration(self):
        """Force calibration."""
        t = self._thermostat
        if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
            event = t.prop_algorithm.force_calibration()
            t.hass.bus.fire(EventType.SMART_PI_EVENT.value, {
                 "entity_id": t.entity_id,
                 "type": "force_calibration"
             })
            if event is not None:
                t.hass.bus.fire(event.event_type, self._autocalib_event_data(event))

            write_event_log(_LOGGER, t, "SmartPI forced calibration triggered")
            # Force immediate recalculation to update state
            await self.control_heating(force=True)
            self.update_attributes()
            t.async_write_ha_state()
            await self._async_save()

            _LOGGER.debug("%s - AutoCalib: manual calibration triggered, will check exit on completion", t.name)

    async def service_reset_smartpi_integral(self):
        """Reset the integral accumulator to zero."""
        t = self._thermostat
        if t.prop_algorithm and isinstance(t.prop_algorithm, SmartPI):
            t.prop_algorithm.ctl.integral = 0.0
            t.prop_algorithm.ctl.clear_integral_hold()
            t.hass.bus.fire(EventType.SMART_PI_EVENT.value, {
                "entity_id": t.entity_id,
                "type": "integral_reset"
            })
            write_event_log(_LOGGER, t, "SmartPI integral reset to zero")
            self.update_attributes()
            t.async_write_ha_state()
            await self._async_save()
