"""Config flow for vtherm_smartpi."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigFlow, OptionsFlow
from homeassistant.helpers import selector
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN

from .const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEADBAND_ALLOW_P,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEBUG,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HYSTERESIS_OFF,
    CONF_SMART_PI_HYSTERESIS_ON,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
    CONF_SMART_PI_MIN_VALVE,
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_RELEASE_TAU_FACTOR,
    CONF_SMART_PI_RESET_TOKEN,
    CONF_SMART_PI_USE_FF3,
    CONF_SMART_PI_USE_SETPOINT_FILTER,
    CONF_TARGET_VTHERM,
    BOILER_SIGNAL_KEYS,
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_REASON_ENTITY,
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_FAULT_ENTITY,
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_MODEL_KEPT_FOR_WATER,
    CONF_SMART_PI_SUPPLY_ENTITY,
    CONF_SMART_PI_WATER_SOURCE,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
)
from .heating_inputs import (
    SIGNALS_DEFAULTS,
    SIGNALS_OWN,
    SOURCE_ENTITIES,
    SOURCE_NONE,
    chosen_call_source,
    chosen_water_source,
    config_from_options,
    provider_name,
    provider_names,
    provider_source,
    resolve_entity,
    stored_entity,
)
from .smartpi.boiler_call import SOURCE_VTHERM
from .smartpi.const import HEATING_TYPES, WATER_EMITTERS
from .smartpi.dependencies import (
    KEY_LEAD_BAND,
    KEY_LEAD_EMITTER,
    KEY_SOURCE_ALONE,
    KEY_SOURCE_EMITTER,
    KEY_SOURCE_LAG_MIN,
    KEY_SOURCE_TAU_MIN,
    KEY_SPLIT_RATIO,
    KEY_SPLIT_RULE,
    KEY_ZONE_MASTER,
    KIND_NEIGHBOUR,
    KIND_SOURCE,
    MODE_ALWAYS,
    MODE_DOOR,
    NEIGHBOUR_MODES,
    WINDOW_ACTION_NOTHING,
    WINDOW_ACTIONS,
    DependencyItem,
    dump_dependencies,
    parse_dependencies,
    validate_dependencies,
)
from .smartpi.source_model import EMITTER_CUSTOM, SourceResponse
from .smartpi.split import (
    REASON_SLOW_SOURCE,
    RULE_LEAD_BAND,
    RULE_SOURCE_FIRST,
    RULE_TOGETHER,
    RULES,
    recommend,
)

ERROR_INVALID_VALVE_CURVE = "invalid_valve_curve"
ABORT_DEPENDENCIES_UNCHANGED = "dependencies_unchanged"
FIELD_REMOVE_DEPENDENCIES = "remove"
FIELD_EDIT_DEPENDENCY = "dependency"
FIELD_NEIGHBOUR = "entity"
FIELD_MODE = "mode"
FIELD_DOOR_SENSORS = "door_sensors"
FIELD_WINDOW_ACTION = "neighbour_window_action"
VT_DOMAIN = "versatile_thermostat"
DOOR_DEVICE_CLASSES = ["door", "opening", "garage_door"]
WINDOW_DEVICE_CLASS = "window"
THERMOSTAT_TYPE_VALVE = "thermostat_over_valve"
THERMOSTAT_TYPE_CLIMATE = "thermostat_over_climate"
AUTO_REGULATION_VALVE = "auto_regulation_valve"
CONF_THERMOSTAT_TYPE_KEY = "thermostat_type"
CONF_AUTO_REGULATION_MODE_KEY = "auto_regulation_mode"
# Versatile Thermostat's keys read by the zone form.
VT_UNDERLYING_LIST = "underlying_entity_ids"
VT_TEMP_SENSOR = "temperature_sensor_entity_id"
VT_AUTO_REGULATION_NONE = "auto_regulation_none"

FIELD_MASTER = "master"
FIELD_MAIN_SOURCE = "main_source"
FIELD_CUSTOM_RESPONSE = "custom_response"
FIELD_RESET_MODEL = "reset_model"
# Versatile Thermostat's key: the thermostat counts for VT's central boiler.
VT_USED_BY_CENTRAL_BOILER = "used_by_controls_central_boiler"
# The signals of the boiler, by the inputs that use them.
_WATER_SIGNAL_KEYS = (CONF_SMART_PI_HEAT_AVAILABLE_ENTITY, CONF_SMART_PI_DHW_ENTITY, CONF_SMART_PI_FAULT_ENTITY)
_CALL_SIGNAL_KEYS = (CONF_SMART_PI_BOILER_CALLED_ENTITY, CONF_SMART_PI_BOILER_REASON_ENTITY, CONF_SMART_PI_FAULT_ENTITY)
# The entity domain of each signal.
_SIGNAL_DOMAINS = {
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY: "binary_sensor",
    CONF_SMART_PI_DHW_ENTITY: "binary_sensor",
    CONF_SMART_PI_FAULT_ENTITY: "binary_sensor",
    CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor",
    CONF_SMART_PI_BOILER_REASON_ENTITY: "sensor",
}
# The rule's parameter, in percent, by rule: key, default, minimum, maximum.
_RULE_PARAMETER = {
    RULE_TOGETHER: (KEY_SPLIT_RATIO, 100.0, 10.0, 1000.0),
    RULE_SOURCE_FIRST: (KEY_SOURCE_ALONE, 50.0, 0.0, 100.0),
    RULE_LEAD_BAND: (KEY_LEAD_BAND, 20.0, 0.0, 100.0),
}
# What the parameter means, by rule, for the zone form.
_RULE_HINT = {
    RULE_TOGETHER: "{other} works at this percentage of {main}'s command (100 %: both alike).",
    RULE_SOURCE_FIRST: "{other} works alone up to this command; above it {main} joins and both reach 100 % together.",
    RULE_LEAD_BAND: "{other} settles so that {main}'s average command stays at this percentage, a margin for quick corrections.",
}


def build_main_options_schema(
    defaults: dict[str, Any],
    *,
    include_valve_linearization: bool,
) -> vol.Schema:
    """Build the SmartPI main options schema."""
    schema = {
        vol.Optional(
            CONF_MINIMAL_ACTIVATION_DELAY,
            default=defaults[CONF_MINIMAL_ACTIVATION_DELAY],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_MINIMAL_DEACTIVATION_DELAY,
            default=defaults[CONF_MINIMAL_DEACTIVATION_DELAY],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_SMART_PI_DEADBAND,
            default=defaults[CONF_SMART_PI_DEADBAND],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=2.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_HYSTERESIS_ON,
            default=defaults[CONF_SMART_PI_HYSTERESIS_ON],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=2.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_HYSTERESIS_OFF,
            default=defaults[CONF_SMART_PI_HYSTERESIS_OFF],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=2.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_RELEASE_TAU_FACTOR,
            default=defaults[CONF_SMART_PI_RELEASE_TAU_FACTOR],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=5.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_USE_SETPOINT_FILTER,
            default=defaults[CONF_SMART_PI_USE_SETPOINT_FILTER],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_USE_FF3,
            default=defaults[CONF_SMART_PI_USE_FF3],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_DEADBAND_ALLOW_P,
            default=defaults[CONF_SMART_PI_DEADBAND_ALLOW_P],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
            default=defaults[CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_DEBUG,
            default=defaults[CONF_SMART_PI_DEBUG],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_HEATING_TYPE,
            default=defaults.get(CONF_SMART_PI_HEATING_TYPE, DEFAULT_OPTIONS[CONF_SMART_PI_HEATING_TYPE]),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(HEATING_TYPES),
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="heating_type",
            )
        ),
    }
    if include_valve_linearization:
        schema[
            vol.Optional(
                CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
                default=defaults[CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION],
            )
        ] = bool
    return vol.Schema(schema)


def build_valve_curve_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the SmartPI valve curve schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_SMART_PI_MIN_VALVE,
                default=defaults[CONF_SMART_PI_MIN_VALVE],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=20,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SMART_PI_KNEE_DEMAND,
                default=defaults[CONF_SMART_PI_KNEE_DEMAND],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50,
                    max=95,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SMART_PI_KNEE_VALVE,
                default=defaults[CONF_SMART_PI_KNEE_VALVE],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=50,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SMART_PI_MAX_VALVE,
                default=defaults[CONF_SMART_PI_MAX_VALVE],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50,
                    max=100,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def build_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the SmartPI defaults schema."""
    return build_main_options_schema(defaults, include_valve_linearization=False)


def build_user_target_schema() -> vol.Schema:
    """Build the SmartPI target thermostat schema."""
    return vol.Schema(
        {
            vol.Required(CONF_TARGET_VTHERM): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN)
            )
        }
    )


def build_user_settings_schema(defaults: dict[str, Any], is_valve: bool) -> vol.Schema:
    """Build the SmartPI per-thermostat settings schema."""
    schema = dict(
        build_main_options_schema(
            defaults,
            include_valve_linearization=is_valve,
        ).schema
    )
    return vol.Schema(schema)


def _schema_defaults(user_input: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return form defaults merged with the latest submitted values."""
    defaults = dict(DEFAULT_OPTIONS)
    if user_input is not None:
        defaults.update(user_input)
    return defaults


def _is_valve_state(state: Any) -> bool:
    """Return whether a VTherm state exposes a valve command space."""
    attributes = getattr(state, "attributes", {}) or {}
    configuration = attributes.get("configuration") or {}
    if _is_valve_config(configuration):
        return True
    return (
        attributes.get("vtherm_over_valve") is not None
        or attributes.get("vtherm_over_climate_valve", {}).get("have_valve_regulation")
        is True
    )


def _is_valve_config(config: dict[str, Any]) -> bool:
    """Return whether a VTherm config entry targets valve command space."""
    thermostat_type = config.get(CONF_THERMOSTAT_TYPE_KEY) or config.get("type")
    return (
        thermostat_type == THERMOSTAT_TYPE_VALVE
        or (
            thermostat_type == THERMOSTAT_TYPE_CLIMATE
            and config.get(CONF_AUTO_REGULATION_MODE_KEY) == AUTO_REGULATION_VALVE
        )
        or config.get("have_valve_regulation") is True
    )


def _is_valve_entity(hass: Any, entity_id: str, target_unique_id: str) -> bool:
    """Return whether a registered VTherm entity targets valve command space."""
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id)
    if reg_entry is not None and reg_entry.config_entry_id is not None:
        config_entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
        if config_entry is not None and _is_valve_config(config_entry.data):
            return True

    state = hass.states.get(entity_id)
    if state is not None:
        return _is_valve_state(state)

    for entry in registry.entities.values():
        if entry.domain != CLIMATE_DOMAIN or entry.unique_id != target_unique_id:
            continue
        fallback_state = hass.states.get(entry.entity_id)
        return fallback_state is not None and _is_valve_state(fallback_state)
    return False


def _validate_valve_curve_config(config: dict[str, Any]) -> dict[str, str]:
    """Validate cross-field valve curve constraints."""
    try:
        min_valve = float(config[CONF_SMART_PI_MIN_VALVE])
        knee_demand = float(config[CONF_SMART_PI_KNEE_DEMAND])
        knee_valve = float(config[CONF_SMART_PI_KNEE_VALVE])
        max_valve = float(config[CONF_SMART_PI_MAX_VALVE])
    except (KeyError, TypeError, ValueError):
        return {"base": ERROR_INVALID_VALVE_CURVE}

    if (
        0.0 <= min_valve < knee_valve < max_valve <= 100.0
        and 0.0 < knee_demand < 100.0
    ):
        return {}
    return {"base": ERROR_INVALID_VALVE_CURVE}


class SmartPIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Manage SmartPI plugin config entries."""

    VERSION = 1
    _pending_thermostat_data: dict[str, Any] | None = None
    _pending_thermostat_entity_id: str | None = None
    _pending_thermostat_is_valve: bool = False
    _pending_thermostat_title: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Create default plugin settings on first install."""
        if not self._async_current_entries():
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="SmartPI defaults",
                data=dict(DEFAULT_OPTIONS),
            )

        return await self.async_step_thermostat(user_input)

    async def async_step_global(self, user_input: dict[str, Any] | None = None):
        """Handle the global defaults entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="SmartPI defaults", data=user_input)

        return self.async_show_form(
            step_id="global",
            data_schema=build_options_schema(DEFAULT_OPTIONS),
        )

    async def async_step_thermostat(self, user_input: dict[str, Any] | None = None):
        """Select the target thermostat."""
        if user_input is not None:
            entity_id = user_input.get(CONF_TARGET_VTHERM)
            registry = er.async_get(self.hass)
            reg_entry = registry.async_get(entity_id)
            if reg_entry is None or reg_entry.unique_id is None:
                return self.async_show_form(
                    step_id="thermostat",
                    data_schema=build_user_target_schema(),
                    errors={CONF_TARGET_VTHERM: "invalid_entity"},
                )

            target_unique_id = reg_entry.unique_id
            await self.async_set_unique_id(f"{DOMAIN}-{target_unique_id}")
            self._abort_if_unique_id_configured()

            self._pending_thermostat_data = {CONF_TARGET_VTHERM: target_unique_id}
            self._pending_thermostat_entity_id = entity_id
            self._pending_thermostat_is_valve = _is_valve_entity(
                self.hass,
                entity_id,
                target_unique_id,
            )
            state = self.hass.states.get(entity_id)
            self._pending_thermostat_title = state.name if state is not None else entity_id
            return await self.async_step_thermostat_settings()

        return self.async_show_form(
            step_id="thermostat",
            data_schema=build_user_target_schema(),
        )

    async def async_step_thermostat_settings(
        self, user_input: dict[str, Any] | None = None
    ):
        """Handle the per-thermostat main settings entry."""
        data = dict(self._pending_thermostat_data or {})

        if user_input is not None:
            data.update(user_input)
            self._pending_thermostat_data = data
            if (
                self._pending_thermostat_is_valve
                and user_input.get(CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION)
            ):
                return self.async_show_form(
                    step_id="thermostat_valve_curve",
                    data_schema=build_valve_curve_schema(_schema_defaults(user_input)),
                )

            return self.async_create_entry(
                title=(
                    self._pending_thermostat_title
                    or self._pending_thermostat_entity_id
                ),
                data=data,
            )

        return self.async_show_form(
            step_id="thermostat_settings",
            data_schema=build_user_settings_schema(
                _schema_defaults(data),
                self._pending_thermostat_is_valve,
            ),
        )

    async def async_step_thermostat_valve_curve(
        self, user_input: dict[str, Any] | None = None
    ):
        """Handle the per-thermostat valve curve entry."""
        data = dict(self._pending_thermostat_data or {})

        if user_input is not None:
            data.update(user_input)
            errors = _validate_valve_curve_config(_schema_defaults(data))
            if errors:
                return self.async_show_form(
                    step_id="thermostat_valve_curve",
                    data_schema=build_valve_curve_schema(_schema_defaults(data)),
                    errors=errors,
                )
            return self.async_create_entry(
                title=(
                    self._pending_thermostat_title
                    or self._pending_thermostat_entity_id
                ),
                data=data,
            )

        return self.async_show_form(
            step_id="thermostat_valve_curve",
            data_schema=build_valve_curve_schema(_schema_defaults(data)),
        )

    async def async_step_import(self, import_data: dict[str, Any]):
        """Create the entry of a thermostat that joins a zone with two heat sources.

        It starts from the values the thermostat ran with: the global defaults.
        """
        target_unique_id = import_data[CONF_TARGET_VTHERM]
        await self.async_set_unique_id(f"{DOMAIN}-{target_unique_id}")
        self._abort_if_unique_id_configured()
        data = dict(DEFAULT_OPTIONS)
        global_entry = next(
            (entry for entry in self._async_current_entries() if entry.unique_id == DOMAIN), None
        )
        if global_entry is not None:
            data.update(global_entry.data)
            data.update(global_entry.options)
        data.pop(CONF_SMART_PI_DEPENDENCIES, None)
        # The boiler's signals stay in the defaults: the new entry takes them from there.
        for key in (*BOILER_SIGNAL_KEYS, CONF_SMART_PI_BOILER_SIGNALS):
            data.pop(key, None)
        data[CONF_TARGET_VTHERM] = target_unique_id
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(CLIMATE_DOMAIN, VT_DOMAIN, target_unique_id)
        state = self.hass.states.get(entity_id) if entity_id else None
        return self.async_create_entry(
            title=state.name if state is not None else (entity_id or target_unique_id), data=data
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SmartPIOptionsFlow(config_entry)


def _source_response(extra: dict[str, Any]) -> tuple[float, float] | None:
    """The additional source's delay and lag a zone item gives, in seconds (None if invalid)."""
    try:
        response = SourceResponse.for_emitter(
            extra.get(KEY_SOURCE_EMITTER), extra.get(KEY_SOURCE_LAG_MIN), extra.get(KEY_SOURCE_TAU_MIN)
        )
    except (KeyError, TypeError, ValueError):
        return None
    return round(response.lag_s), round(response.tau_s)


class SmartPIOptionsFlow(OptionsFlow):
    """Edit SmartPI plugin defaults."""

    def __init__(self, config_entry) -> None:
        """Store the config entry being edited."""
        self._config_entry = config_entry
        self._pending_options_data: dict[str, Any] | None = None
        self._editing: DependencyItem | None = None
        self._neighbour_entity: er.RegistryEntry | None = None
        self._pending_item: DependencyItem | None = None
        self._pending_warnings: list[str] = []
        # The zone being configured: its master and two members (registry entries), the choices so far.
        self._zone_master: er.RegistryEntry | None = None
        self._zone_members: list[er.RegistryEntry] = []
        self._zone_pending: dict[str, Any] = {}
        self._zone_item: DependencyItem | None = None
        self._zone_main: er.RegistryEntry | None = None
        self._zone_snapshot: tuple = ()
        self._pending_items: list[DependencyItem] = []
        self._pending_base: str = ""
        # The water and boiler sources being chosen, and whether the call is part of the form.
        self._heating_pending: dict[str, Any] = {}
        self._heating_call_shown: bool = True

    def _is_valve_target_entry(self) -> bool:
        """Return whether the edited entry targets a valve thermostat."""
        target_unique_id = self._config_entry.data.get(CONF_TARGET_VTHERM)
        if target_unique_id is None:
            return False

        registry = er.async_get(self.hass)
        for entry in registry.entities.values():
            if entry.domain != CLIMATE_DOMAIN or entry.unique_id != target_unique_id:
                continue
            return _is_valve_entity(
                self.hass,
                entry.entity_id,
                target_unique_id,
            )
        return False

    def _current_options(self) -> dict[str, Any]:
        """Return the stored options merged over the defaults."""
        options = dict(DEFAULT_OPTIONS)
        options.update(self._config_entry.options or self._config_entry.data)
        return options

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Open the options: a menu for a thermostat entry, the settings form for the defaults.

        A thermostat that works as the additional source of a zone shows where
        the zone is configured and only the settings it still uses.
        """
        if self._config_entry.data.get(CONF_TARGET_VTHERM) is None:
            del user_input
            return self.async_show_menu(step_id="init", menu_options=["settings", "boiler_signals"])
        own = self._target_registry_entry()
        if self._main_naming(own.id if own is not None else None) is not None:
            if self._uses_water():
                return await self.async_step_source_menu()
            return await self.async_step_source_role()
        return await self.async_step_thermostat_menu()

    async def async_step_source_menu(self, user_input: dict[str, Any] | None = None):
        """The additional source of a zone heated by water: its settings, or its water data."""
        del user_input
        return self.async_show_menu(step_id="source_menu", menu_options=["source_role", "heating_inputs"])

    async def async_step_thermostat_menu(self, user_input: dict[str, Any] | None = None):
        """Offer the option groups of a thermostat entry."""
        del user_input
        menu_options = ["settings", "zone", "dependencies"]
        if self._uses_water():
            menu_options.append("heating_inputs")
        if self._is_valve_target_entry() and self._current_options().get(
            CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION
        ):
            menu_options.append("valve_curve")
        return self.async_show_menu(step_id="thermostat_menu", menu_options=menu_options)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        """Edit the regulator settings of a thermostat entry or of the defaults."""
        return await self._async_settings_form("settings", user_input)

    async def _async_settings_form(self, step_id: str, user_input: dict[str, Any] | None):
        """Show or save the regulator settings form."""
        defaults = self._current_options()
        is_valve_target = self._is_valve_target_entry()

        if user_input is not None:
            data = dict(defaults)
            data.update(user_input)
            self._pending_options_data = data
            if (
                is_valve_target
                and user_input.get(CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION)
            ):
                return self.async_show_form(
                    step_id="valve_curve",
                    data_schema=build_valve_curve_schema(data),
                )
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id=step_id,
            data_schema=build_main_options_schema(
                defaults,
                include_valve_linearization=is_valve_target,
            ),
        )

    def _neighbour_items(self) -> list[DependencyItem]:
        """The neighbours: the zone's second source is configured in its own form."""
        return [item for item in self._dependency_items() if item.is_neighbour]

    def _dependency_label(self, item: DependencyItem) -> str:
        """Return a readable label: the kind and the current entity id of the item."""
        entity_id = er.async_resolve_entity_id(er.async_get(self.hass), item.entity)
        return f"{item.kind}: {entity_id or item.entity}"

    def _dependency_items(self) -> list[DependencyItem]:
        return parse_dependencies(self._current_options().get(CONF_SMART_PI_DEPENDENCIES))

    def _save_dependencies(self, items: list[DependencyItem]):
        data = self._current_options()
        data[CONF_SMART_PI_DEPENDENCIES] = dump_dependencies(items)
        return self.async_create_entry(title="", data=data)

    def _target_registry_entry(self) -> er.RegistryEntry | None:
        """Registry entry of the thermostat this options entry belongs to."""
        target = self._config_entry.data.get(CONF_TARGET_VTHERM)
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(CLIMATE_DOMAIN, VT_DOMAIN, target) if target else None
        return registry.async_get(entity_id) if entity_id else None

    def _entity_id(self, registry_id: str) -> str | None:
        return er.async_resolve_entity_id(er.async_get(self.hass), registry_id)

    async def async_step_dependencies(self, user_input: dict[str, Any] | None = None):
        """Offer to add a neighbouring zone, or to edit or remove a dependency."""
        del user_input
        items = self._neighbour_items()
        menu_options = ["neighbour_add"]
        if items:
            menu_options.append("dependency_edit")
            menu_options.append("dependency_remove")
        listing = "\n".join(f"- {self._dependency_label(item)}" for item in items) or "-"
        return self.async_show_menu(
            step_id="dependencies",
            menu_options=menu_options,
            description_placeholders={"count": str(len(items)), "items": listing},
        )

    async def async_step_dependency_remove(self, user_input: dict[str, Any] | None = None):
        """Remove the selected dependencies."""
        items = self._neighbour_items()
        if user_input is not None:
            removed = set(user_input.get(FIELD_REMOVE_DEPENDENCIES, []))
            if not removed:
                return self.async_abort(reason=ABORT_DEPENDENCIES_UNCHANGED)
            return self._finish(
                [item for item in self._dependency_items() if item.item_id not in removed], True
            )
        return self.async_show_form(
            step_id="dependency_remove",
            data_schema=vol.Schema(
                {
                    vol.Optional(FIELD_REMOVE_DEPENDENCIES, default=[]): self._item_selector(
                        items, multiple=True
                    )
                }
            ),
        )

    async def async_step_dependency_edit(self, user_input: dict[str, Any] | None = None):
        """Choose the neighbour to edit."""
        neighbours = [item for item in self._dependency_items() if item.is_neighbour]
        if user_input is not None:
            item = next(i for i in neighbours if i.item_id == user_input[FIELD_EDIT_DEPENDENCY])
            self._editing = item
            self._neighbour_entity = er.async_get(self.hass).async_get(item.entity)
            if self._neighbour_entity is None:
                return self.async_abort(reason="neighbour_missing")
            return await self.async_step_neighbour_details()
        return self.async_show_form(
            step_id="dependency_edit",
            data_schema=vol.Schema(
                {vol.Required(FIELD_EDIT_DEPENDENCY): self._item_selector(neighbours, multiple=False)}
            ),
        )

    def _item_selector(self, items: list[DependencyItem], multiple: bool) -> selector.SelectSelector:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=item.item_id, label=self._dependency_label(item))
                    for item in items
                ],
                multiple=multiple,
                mode=selector.SelectSelectorMode.LIST,
            )
        )

    async def async_step_neighbour_add(self, user_input: dict[str, Any] | None = None):
        """Choose the neighbouring zone: a Versatile Thermostat or a temperature sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            registry = er.async_get(self.hass)
            entry = registry.async_get(user_input[FIELD_NEIGHBOUR])
            own = self._target_registry_entry()
            if entry is None:
                errors[FIELD_NEIGHBOUR] = "entity_without_unique_id"
            elif own is not None and entry.id == own.id:
                errors[FIELD_NEIGHBOUR] = "self_reference"
            elif any(i.is_neighbour and i.entity == entry.id for i in self._dependency_items()):
                errors[FIELD_NEIGHBOUR] = "duplicate_neighbour"
            elif any(i.is_source and i.entity == entry.id for i in self._dependency_items()):
                errors[FIELD_NEIGHBOUR] = "source_is_neighbour"
            elif any(
                i.is_source and i.extra.get(KEY_ZONE_MASTER) == entry.id for i in self._dependency_items()
            ):
                errors[FIELD_NEIGHBOUR] = "master_is_neighbour"
            else:
                self._editing = None
                self._neighbour_entity = entry
                return await self.async_step_neighbour_details()
        return self.async_show_form(
            step_id="neighbour_add",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_NEIGHBOUR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            filter=[
                                selector.EntityFilterSelectorConfig(
                                    integration=VT_DOMAIN, domain=CLIMATE_DOMAIN
                                ),
                                selector.EntityFilterSelectorConfig(
                                    domain="sensor", device_class="temperature"
                                ),
                            ]
                        )
                    )
                }
            ),
            errors=errors,
        )

    def _suggested_door_sensors(self) -> list[str]:
        """Door sensors in the area of the zone or of the neighbour, not used by another neighbour.

        They are only listed in the form: a door sensor in one of the two areas
        may as well be an exterior door.
        """
        own = self._target_registry_entry()
        devices = dr.async_get(self.hass)

        def area_of(entry: er.RegistryEntry | None) -> str | None:
            if entry is None:
                return None
            if entry.area_id:
                return entry.area_id
            device = devices.async_get(entry.device_id) if entry.device_id else None
            return device.area_id if device else None

        areas = {area_of(own), area_of(self._neighbour_entity)} - {None}
        if not areas:
            return []
        editing_id = self._editing.item_id if self._editing is not None else None
        used = {
            sensor
            for item in self._dependency_items()
            if item.item_id != editing_id
            for sensor in item.door_sensors
        }
        return sorted(
            entry.entity_id
            for entry in er.async_get(self.hass).entities.values()
            if entry.domain == "binary_sensor"
            and (entry.device_class or entry.original_device_class) in DOOR_DEVICE_CLASSES
            and area_of(entry) in areas
            and entry.id not in used
        )

    def _details_schema(self, defaults: dict[str, Any], is_thermostat: bool) -> vol.Schema:
        schema: dict[Any, Any] = {
            vol.Required(FIELD_MODE, default=defaults[FIELD_MODE]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(NEIGHBOUR_MODES), translation_key="dependency_mode"
                )
            ),
            vol.Optional(FIELD_DOOR_SENSORS, default=defaults[FIELD_DOOR_SENSORS]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    multiple=True,
                    filter=[
                        selector.EntityFilterSelectorConfig(
                            domain="binary_sensor",
                            device_class=DOOR_DEVICE_CLASSES + [WINDOW_DEVICE_CLASS],
                        )
                    ],
                )
            ),
        }
        if is_thermostat:
            schema[
                vol.Required(FIELD_WINDOW_ACTION, default=defaults[FIELD_WINDOW_ACTION])
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WINDOW_ACTIONS), translation_key="neighbour_window_action"
                )
            )
        return vol.Schema(schema)

    async def async_step_neighbour_details(self, user_input: dict[str, Any] | None = None):
        """Connection mode, door sensors and, for a thermostat, the reaction to its open window."""
        neighbour = self._neighbour_entity
        is_thermostat = neighbour.domain == CLIMATE_DOMAIN
        editing = self._editing
        errors: dict[str, str] = {}
        if user_input is not None:
            item, errors = self._build_neighbour(user_input, is_thermostat)
            if not errors:
                self._pending_item = item
                warnings = self._neighbour_warnings(item)
                if warnings:
                    self._pending_warnings = warnings
                    return await self.async_step_neighbour_confirm()
                return self._store_neighbour(item)
            defaults = {
                FIELD_MODE: user_input.get(FIELD_MODE, MODE_ALWAYS),
                FIELD_DOOR_SENSORS: user_input.get(FIELD_DOOR_SENSORS, []),
                FIELD_WINDOW_ACTION: user_input.get(FIELD_WINDOW_ACTION, WINDOW_ACTION_NOTHING),
            }
        elif editing is not None:
            defaults = {
                FIELD_MODE: editing.mode,
                FIELD_DOOR_SENSORS: [e for e in map(self._entity_id, editing.door_sensors) if e],
                FIELD_WINDOW_ACTION: editing.neighbour_window_action,
            }
        else:
            defaults = {
                FIELD_MODE: MODE_ALWAYS,
                FIELD_DOOR_SENSORS: [],
                FIELD_WINDOW_ACTION: WINDOW_ACTION_NOTHING,
            }
        suggested = self._suggested_door_sensors()
        return self.async_show_form(
            step_id="neighbour_details",
            data_schema=self._details_schema(defaults, is_thermostat),
            errors=errors,
            description_placeholders={
                "neighbour": neighbour.entity_id,
                "suggestions": (
                    "\n\nDoor sensors in the areas of the two zones: "
                    + ", ".join(suggested)
                    + ". Choose only the doors between the zones."
                    if suggested
                    else ""
                ),
            },
        )

    def _build_neighbour(
        self, user_input: dict[str, Any], is_thermostat: bool
    ) -> tuple[DependencyItem | None, dict[str, str]]:
        registry = er.async_get(self.hass)
        sensor_ids: list[str] = []
        for entity_id in user_input.get(FIELD_DOOR_SENSORS, []):
            entry = registry.async_get(entity_id)
            if entry is None:
                return None, {FIELD_DOOR_SENSORS: "entity_without_unique_id"}
            if (entry.device_class or entry.original_device_class) == WINDOW_DEVICE_CLASS:
                return None, {FIELD_DOOR_SENSORS: "window_sensor_rejected"}
            sensor_ids.append(entry.id)
        editing = self._editing
        mode = user_input[FIELD_MODE]
        # What was learned belongs to one connection: a new mode or other doors
        # start from scratch under a new id.
        same_connection = (
            editing is not None
            and editing.mode == mode
            and set(editing.door_sensors) == set(sensor_ids)
        )
        item = DependencyItem(
            item_id=editing.item_id if same_connection else uuid.uuid4().hex,
            kind=KIND_NEIGHBOUR,
            entity=self._neighbour_entity.id,
            mode=mode,
            door_sensors=tuple(sensor_ids),
            neighbour_window_action=(
                user_input.get(FIELD_WINDOW_ACTION, WINDOW_ACTION_NOTHING)
                if is_thermostat
                else WINDOW_ACTION_NOTHING
            ),
        )
        replaced_id = editing.item_id if editing is not None else item.item_id
        others = [i for i in self._dependency_items() if i.item_id != replaced_id]
        own = self._target_registry_entry()
        for issue in validate_dependencies([*others, item], own.id if own else None):
            if issue.item_id == item.item_id:
                field = "base" if issue.code in ("self_reference", "duplicate_neighbour") else FIELD_DOOR_SENSORS
                return None, {field: issue.code}
        return item, {}

    def _neighbour_warnings(self, item: DependencyItem) -> list[str]:
        """Consistency checks that do not block saving."""
        warnings = []
        own = self._target_registry_entry()
        neighbour = self._neighbour_entity
        if neighbour.domain == CLIMATE_DOMAIN and own is not None:
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.data.get(CONF_TARGET_VTHERM) != neighbour.unique_id:
                    continue
                options = entry.options or entry.data
                back = parse_dependencies(options.get(CONF_SMART_PI_DEPENDENCIES))
                if not any(i.is_neighbour and i.entity == own.id for i in back):
                    warnings.append(
                        f"{neighbour.entity_id} has its own SmartPI entry that does not list this zone as a neighbour."
                    )
        state = self.hass.states.get(own.entity_id) if own is not None else None
        attributes = state.attributes if state is not None else {}
        window_manager = attributes.get("window_manager") or {}
        window_sensor = window_manager.get("window_sensor_entity_id")
        door_entities = [self._entity_id(sensor) for sensor in item.door_sensors]
        if window_sensor and window_sensor in door_entities:
            warnings.append(f"{window_sensor} is also the window sensor of this thermostat.")
        if item.mode == MODE_DOOR and attributes.get("is_window_auto_configured"):
            warnings.append(
                "Automatic window detection of this thermostat may react to the open door."
            )
        return warnings

    async def async_step_neighbour_confirm(self, user_input: dict[str, Any] | None = None):
        """Show the warnings and save on confirmation."""
        if user_input is not None:
            return self._store_neighbour(self._pending_item)
        return self.async_show_form(
            step_id="neighbour_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "warnings": "\n".join(f"- {warning}" for warning in self._pending_warnings)
            },
        )

    def _store_neighbour(self, item: DependencyItem):
        items = self._dependency_items()
        replaced_id = self._editing.item_id if self._editing is not None else item.item_id
        replaced = [item if existing.item_id == replaced_id else existing for existing in items]
        if not any(existing.item_id == replaced_id for existing in items):
            replaced.append(item)
        # A new connection (added, another mode or other doors) changes what was learned.
        affects_learning = self._editing is None or self._editing.item_id != item.item_id
        return self._finish(replaced, affects_learning)

    def _finish(self, items: list[DependencyItem], affects_learning: bool):
        """Save the dependency list, asking first about the learned model when the change affects it."""
        if not affects_learning:
            return self._save_dependencies(items)
        self._pending_items = items
        self._pending_base = repr(self._current_options().get(CONF_SMART_PI_DEPENDENCIES))
        return self.async_show_form(
            step_id="model_reset",
            data_schema=vol.Schema({vol.Required(FIELD_RESET_MODEL, default=True): bool}),
        )

    async def async_step_model_reset(self, user_input: dict[str, Any] | None = None):
        """Reset the zone's learned model with the change of its neighbours, as recommended."""
        if user_input is None:
            return self._finish(self._pending_items, True)
        if repr(self._current_options().get(CONF_SMART_PI_DEPENDENCIES)) != self._pending_base:
            return self.async_abort(reason="zone_changed")
        return self._save_items(self._pending_items, reset=bool(user_input.get(FIELD_RESET_MODEL)))

    # --- zone with two heat sources ---------------------------------------

    def _smartpi_entries(self) -> list:
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_TARGET_VTHERM)
        ]

    @staticmethod
    def _entry_options(entry) -> dict[str, Any]:
        options = dict(DEFAULT_OPTIONS)
        options.update(entry.options or entry.data)
        return options

    def _entry_items(self, entry) -> list[DependencyItem]:
        return parse_dependencies(self._entry_options(entry).get(CONF_SMART_PI_DEPENDENCIES))

    def _entry_of(self, unique_id: str | None):
        """The SmartPI entry of a thermostat, by its unique id."""
        return next(
            (entry for entry in self._smartpi_entries() if entry.data.get(CONF_TARGET_VTHERM) == unique_id),
            None,
        )

    def _main_naming(self, registry_id: str | None):
        """The SmartPI entry, other than this one, whose zone has ``registry_id`` as its additional source."""
        if registry_id is None:
            return None
        for entry in self._smartpi_entries():
            if entry.entry_id == self._config_entry.entry_id:
                continue
            if any(item.is_source and item.entity == registry_id for item in self._entry_items(entry)):
                return entry
        return None

    def _zone_signature(self, members: list[er.RegistryEntry]) -> tuple:
        """What the zone form relies on: the dependency lists of this entry and of the members' entries."""
        entries = {self._config_entry.entry_id: self._config_entry}
        for member in members:
            entry = self._entry_of(member.unique_id)
            if entry is not None:
                entries[entry.entry_id] = entry
        return tuple(
            (entry_id, repr(self._entry_options(entry).get(CONF_SMART_PI_DEPENDENCIES)))
            for entry_id, entry in sorted(entries.items())
        )

    def _zone_changed(self) -> bool:
        """Another form changed the zone, or the master's devices changed, since this form read them."""
        error, members = self._master_error(self._zone_master)
        if error is not None or {m.id for m in members} != {m.id for m in self._zone_members}:
            return True
        return self._zone_signature(self._zone_members) != self._zone_snapshot

    def _zone_source_item(self) -> DependencyItem | None:
        return next((item for item in self._dependency_items() if item.is_source), None)

    def _vt_config(self, entry: er.RegistryEntry | None) -> dict[str, Any]:
        """Versatile Thermostat's configuration of a climate entity."""
        if entry is None or entry.config_entry_id is None:
            return {}
        vt_entry = self.hass.config_entries.async_get_entry(entry.config_entry_id)
        if vt_entry is None:
            return {}
        return {**vt_entry.data, **vt_entry.options}

    def _heating_type(self, member: er.RegistryEntry) -> str:
        """The heating type set in a thermostat's SmartPI entry (else the defaults')."""
        entry = self._entry_of(member.unique_id)
        if entry is None:
            entry = next(
                (e for e in self.hass.config_entries.async_entries(DOMAIN) if e.unique_id == DOMAIN), None
            )
        options = self._entry_options(entry) if entry is not None else dict(DEFAULT_OPTIONS)
        heating_type = options.get(CONF_SMART_PI_HEATING_TYPE)
        return heating_type if heating_type in HEATING_TYPES else DEFAULT_OPTIONS[CONF_SMART_PI_HEATING_TYPE]

    def _masters(self) -> list[er.RegistryEntry]:
        """Versatile Thermostat over_climate thermostats with this thermostat among their devices."""
        own = self._target_registry_entry()
        if own is None:
            return []
        registry = er.async_get(self.hass)
        masters = []
        for entry in registry.entities.values():
            if entry.domain != CLIMATE_DOMAIN or entry.platform != VT_DOMAIN or entry.id == own.id:
                continue
            config = self._vt_config(entry)
            if config.get(CONF_THERMOSTAT_TYPE_KEY) != THERMOSTAT_TYPE_CLIMATE:
                continue
            if own.entity_id in (config.get(VT_UNDERLYING_LIST) or []):
                masters.append(entry)
        return sorted(masters, key=lambda entry: entry.entity_id)

    def _master_error(self, master: er.RegistryEntry) -> tuple[str | None, list[er.RegistryEntry]]:
        """Why the master cannot carry a zone with two heat sources, and its two devices."""
        config = self._vt_config(master)
        if config.get(CONF_AUTO_REGULATION_MODE_KEY, VT_AUTO_REGULATION_NONE) != VT_AUTO_REGULATION_NONE:
            return "master_auto_regulation", []
        registry = er.async_get(self.hass)
        devices = config.get(VT_UNDERLYING_LIST) or []
        members = [registry.async_get(entity_id) for entity_id in devices]
        if len(set(devices)) != 2 or len(devices) != 2 or any(member is None for member in members):
            return "master_not_two_devices", []
        for member in members:
            if member.platform != VT_DOMAIN or self._vt_config(member).get(CONF_PROP_FUNCTION) != PROP_FUNCTION_SMART_PI:
                return "member_without_smartpi", []
        sensors = {self._vt_config(member).get(VT_TEMP_SENSOR) for member in members}
        if len(sensors) != 1 or None in sensors:
            return "members_not_one_sensor", []
        for member in members:
            naming = self._main_naming(member.id)
            if naming is not None:
                return "member_in_another_zone", []
            entry = self._entry_of(member.unique_id)
            if entry is not None and entry.entry_id != self._config_entry.entry_id:
                if any(
                    item.is_source and item.entity not in {m.id for m in members}
                    for item in self._entry_items(entry)
                ):
                    return "member_in_another_zone", []
        return None, members

    async def async_step_zone(self, user_input: dict[str, Any] | None = None):
        """The zone this thermostat heats with a second one: set it up, change it or remove it."""
        del user_input
        item = self._zone_source_item()
        if item is None:
            return await self.async_step_zone_master()
        registry = er.async_get(self.hass)
        other = registry.async_get(item.entity)
        master = registry.async_get(item.extra.get(KEY_ZONE_MASTER, "")) if item.extra.get(KEY_ZONE_MASTER) else None
        return self.async_show_menu(
            step_id="zone",
            menu_options=["zone_master", "zone_remove"],
            description_placeholders={
                "master": master.entity_id if master is not None else "-",
                "source": other.entity_id if other is not None else item.entity,
                "rule": str(item.extra.get(KEY_SPLIT_RULE, "-")),
            },
        )

    async def async_step_zone_master(self, user_input: dict[str, Any] | None = None):
        """Choose the master thermostat whose two devices heat the zone."""
        masters = self._masters()
        if not masters:
            return self.async_abort(reason="no_master")
        errors: dict[str, str] = {}
        current = self._zone_source_item()
        default = None
        if current is not None:
            master = er.async_get(self.hass).async_get(current.extra.get(KEY_ZONE_MASTER, ""))
            default = master.entity_id if master is not None else None
        if user_input is not None:
            master = er.async_get(self.hass).async_get(user_input[FIELD_MASTER])
            error, members = self._master_error(master) if master is not None else ("master_not_two_devices", [])
            if error is None:
                self._zone_master, self._zone_members = master, members
                self._zone_pending = {}
                self._zone_snapshot = self._zone_signature(members)
                return await self.async_step_zone_sources()
            errors[FIELD_MASTER] = error
            default = user_input[FIELD_MASTER]
        options = [entry.entity_id for entry in masters]
        return self.async_show_form(
            step_id="zone_master",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_MASTER, default=default or options[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
                    )
                }
            ),
            errors=errors,
        )

    def _response_of(self, member: er.RegistryEntry) -> SourceResponse:
        return SourceResponse.for_emitter(self._heating_type(member))

    def _faster_first(self) -> list[er.RegistryEntry]:
        """The two members, the faster to respond first: the recommended main source."""
        return sorted(self._zone_members, key=lambda member: self._response_of(member).settle_s)

    async def async_step_zone_sources(self, user_input: dict[str, Any] | None = None):
        """Source 1 and Source 2, which of them is the main source, and the split rule."""
        members = self._zone_members
        current = self._zone_source_item()
        own = self._target_registry_entry()
        if user_input is not None:
            self._zone_pending.update(user_input)
            return await self.async_step_zone_parameter()
        faster = self._faster_first()
        main_default = faster[0].entity_id
        if current is not None and own is not None and current.entity in {m.id for m in members}:
            main_default = own.entity_id
        main = next(m for m in members if m.entity_id == main_default)
        other = next(m for m in members if m.id != main.id)
        recommendation = recommend(self._response_of(main), self._response_of(other))
        rule_default = recommendation.rule.rule
        if current is not None and current.extra.get(KEY_SPLIT_RULE) in RULES:
            rule_default = current.extra[KEY_SPLIT_RULE]
        # Only the additional source's response can be custom (an older zone may still
        # hold the main source's, which nothing uses).
        custom_default = current is not None and current.extra.get(KEY_SOURCE_EMITTER) == EMITTER_CUSTOM
        recommended = recommend(self._response_of(faster[0]), self._response_of(faster[1]))
        if recommended.reason == REASON_SLOW_SOURCE:
            text = (
                f"Recommended: {faster[0].entity_id} as the main source with the rule lead_band: the slower "
                f"{faster[1].entity_id} carries the base load and the main source keeps a margin to correct quickly."
            )
        elif self._response_of(faster[0]).settle_s < self._response_of(faster[1]).settle_s:
            text = (
                f"Recommended: the rule together; both respond at a similar speed. The main source "
                f"corrects quickly: {faster[0].entity_id} responds a little faster."
            )
        else:
            text = "Recommended: the rule together; both respond at the same speed, either may be the main source."
        return self.async_show_form(
            step_id="zone_sources",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_MAIN_SOURCE, default=main_default): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[member.entity_id for member in members],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(KEY_SPLIT_RULE, default=rule_default): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=list(RULES), translation_key="split_rule")
                    ),
                    vol.Required(FIELD_CUSTOM_RESPONSE, default=custom_default): bool,
                }
            ),
            description_placeholders={
                "master": self._zone_master.entity_id,
                "source_1": members[0].entity_id,
                "source_1_type": self._heating_type(members[0]),
                "source_2": members[1].entity_id,
                "source_2_type": self._heating_type(members[1]),
                "recommendation": text,
            },
        )

    async def async_step_zone_parameter(self, user_input: dict[str, Any] | None = None):
        """The rule's parameter and, if asked for, the additional source's delay and lag.

        The main source's delay is not asked: the zone's fit learns it from a grid.
        """
        pending = self._zone_pending
        members = self._zone_members
        main = next(m for m in members if m.entity_id == pending[FIELD_MAIN_SOURCE])
        other = next(m for m in members if m.id != main.id)
        rule = pending[KEY_SPLIT_RULE]
        key, default, low, high = _RULE_PARAMETER[rule]
        current = self._zone_source_item()
        same_pair = current is not None and current.entity == other.id
        if user_input is not None:
            pending.update(user_input)
            return await self._async_store_zone(main, other)
        percent = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=low, max=high, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%"
            )
        )
        stored = current.extra.get(key) if same_pair else None
        schema: dict[Any, Any] = {vol.Required(key, default=stored if stored is not None else default): percent}
        if pending.get(FIELD_CUSTOM_RESPONSE):
            minutes = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min"
                )
            )
            response = self._response_of(other)
            for key, start_s in ((KEY_SOURCE_LAG_MIN, response.lag_s), (KEY_SOURCE_TAU_MIN, response.tau_s)):
                value = current.extra.get(key) if same_pair and current.extra.get(key) is not None else round(start_s / 60.0)
                schema[vol.Required(key, default=value)] = minutes
        hint = _RULE_HINT[rule].format(main=main.entity_id, other=other.entity_id)
        if recommend(self._response_of(main), self._response_of(other)).roles_reversed:
            hint += (
                f" Warning: the main source {main.entity_id} responds more slowly than {other.entity_id}; "
                "quick corrections belong to the faster device."
            )
        return self.async_show_form(
            step_id="zone_parameter",
            data_schema=vol.Schema(schema),
            description_placeholders={"main": main.entity_id, "source": other.entity_id, "hint": hint},
        )

    def _build_zone_item(self, main: er.RegistryEntry, other: er.RegistryEntry) -> DependencyItem:
        pending = self._zone_pending
        rule = pending[KEY_SPLIT_RULE]
        parameter = _RULE_PARAMETER[rule][0]
        extra: dict[str, Any] = {
            KEY_ZONE_MASTER: self._zone_master.id,
            KEY_SPLIT_RULE: rule,
            parameter: pending[parameter],
        }
        # The heating types of the thermostats' entries; the handler reads them again at
        # start. Only the additional source's response may be custom: it starts the
        # candidates of its time scale, while the main source's delay comes from the fit.
        extra[KEY_LEAD_EMITTER] = self._heating_type(main)
        if pending.get(FIELD_CUSTOM_RESPONSE):
            extra[KEY_SOURCE_EMITTER] = EMITTER_CUSTOM
            extra[KEY_SOURCE_LAG_MIN] = pending[KEY_SOURCE_LAG_MIN]
            extra[KEY_SOURCE_TAU_MIN] = pending[KEY_SOURCE_TAU_MIN]
        else:
            extra[KEY_SOURCE_EMITTER] = self._heating_type(other)
        current = self._zone_source_item()
        same_pair = current is not None and current.entity == other.id
        return DependencyItem(
            item_id=current.item_id if same_pair else uuid.uuid4().hex,
            kind=KIND_SOURCE,
            entity=other.id,
            extra=extra,
        )

    async def _async_store_zone(self, main: er.RegistryEntry, other: er.RegistryEntry):
        """Ask about the learned model when the change affects it, then save."""
        item = self._build_zone_item(main, other)
        own = self._target_registry_entry()
        current = self._zone_source_item()
        # Only the additional source's response shapes what the zone learns, and the
        # learned fits are kept when its delay and lag stay the same (as at run time).
        affects_learning = (
            current is None
            or own is None
            or main.id != own.id
            or current.entity != item.entity
            or _source_response(self._effective(current, main, other)) != _source_response(item.extra)
        )
        self._zone_item, self._zone_main = item, main
        if affects_learning:
            return await self.async_step_zone_reset()
        return await self._async_save_zone(reset=False)

    def _effective(self, item: DependencyItem, main: er.RegistryEntry, other: er.RegistryEntry) -> dict[str, Any]:
        """A stored zone item as it runs now: emitters that are not custom follow the
        heating types the thermostats' entries set today, as the handler reads them."""
        extra = dict(item.extra)
        for member, key in ((main, KEY_LEAD_EMITTER), (other, KEY_SOURCE_EMITTER)):
            if extra.get(key) != EMITTER_CUSTOM:
                extra[key] = self._heating_type(member)
        return extra

    async def async_step_zone_reset(self, user_input: dict[str, Any] | None = None):
        """Reset the zone's learned model with the change, as recommended."""
        if user_input is not None:
            if self._zone_item is None:
                return await self._async_remove_zone(reset=bool(user_input.get(FIELD_RESET_MODEL)))
            return await self._async_save_zone(reset=bool(user_input.get(FIELD_RESET_MODEL)))
        return self.async_show_form(
            step_id="zone_reset",
            data_schema=vol.Schema({vol.Required(FIELD_RESET_MODEL, default=True): bool}),
        )

    async def _async_entry_for(self, member: er.RegistryEntry):
        """The SmartPI entry of a zone member, created from the defaults if it has none."""
        entry = self._entry_of(member.unique_id)
        if entry is None:
            await self.hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={CONF_TARGET_VTHERM: member.unique_id}
            )
            entry = self._entry_of(member.unique_id)
        return entry

    async def _async_save_zone(self, reset: bool):
        """Store the zone in the main source's entry; the other member gets an entry if it has none.

        Choosing the other thermostat as the main source moves the zone, with
        its neighbours, to that thermostat's entry.
        """
        if self._zone_changed():
            return self.async_abort(reason="zone_changed")
        item, main = self._zone_item, self._zone_main
        own = self._target_registry_entry()
        other_member = next(m for m in self._zone_members if m.id != main.id)
        await self._async_entry_for(other_member)
        own_items = self._dependency_items()
        if own is not None and main.id == own.id:
            items = [
                existing
                for existing in own_items
                if not existing.is_source and existing.entity not in {item.entity, self._zone_master.id}
            ]
            items.append(item)
            if validate_dependencies(items, own.id):
                return self.async_abort(reason="zone_move_conflict")
            return self._save_items(items, reset)
        # The zone moves to the other thermostat's entry.
        target = await self._async_entry_for(main)
        if target is None:
            return self.async_abort(reason="main_entry_missing")
        zone_entities = {main.id, item.entity}
        moved = [existing for existing in own_items if existing.is_neighbour and existing.entity not in zone_entities]
        moved_sensors = {sensor for existing in moved for sensor in existing.door_sensors}
        # The moved neighbours win over the new main's own ones that clash with them.
        kept = [
            existing
            for existing in self._entry_items(target)
            if existing.is_neighbour
            and existing.entity not in zone_entities
            and existing.entity not in {m.entity for m in moved}
            and not moved_sensors.intersection(existing.door_sensors)
        ]
        merged = [*kept, *moved, item]
        if validate_dependencies(merged, main.id):
            return self.async_abort(reason="zone_move_conflict")
        target_options = self._entry_options(target)
        target_options[CONF_SMART_PI_DEPENDENCIES] = dump_dependencies(merged)
        if reset:
            target_options[CONF_SMART_PI_RESET_TOKEN] = uuid.uuid4().hex
        self.hass.config_entries.async_update_entry(target, options=target_options)
        return self._save_items([], reset=False)

    async def async_step_zone_remove(self, user_input: dict[str, Any] | None = None):
        """Remove the zone: this thermostat regulates alone again, the other one too."""
        del user_input
        self._zone_item = None
        self._pending_base = repr(self._current_options().get(CONF_SMART_PI_DEPENDENCIES))
        return await self.async_step_zone_reset()

    async def _async_remove_zone(self, reset: bool):
        if repr(self._current_options().get(CONF_SMART_PI_DEPENDENCIES)) != self._pending_base:
            return self.async_abort(reason="zone_changed")
        items = [existing for existing in self._dependency_items() if not existing.is_source]
        return self._save_items(items, reset)

    def _save_items(self, items: list[DependencyItem], reset: bool):
        """Save this entry's dependency list (and a reset request)."""
        data = self._current_options()
        data[CONF_SMART_PI_DEPENDENCIES] = dump_dependencies(items)
        if reset:
            data[CONF_SMART_PI_RESET_TOKEN] = uuid.uuid4().hex
        return self.async_create_entry(title="", data=data)

    # --- water and boiler (heating_inputs.py) -----------------------------

    def _uses_water(self) -> bool:
        """The thermostat's heating type uses the water (radiator, water floor)."""
        return self._current_options().get(CONF_SMART_PI_HEATING_TYPE) in WATER_EMITTERS

    def _is_additional_source(self) -> bool:
        own = self._target_registry_entry()
        return self._main_naming(own.id if own is not None else None) is not None

    def _defaults_options(self) -> dict[str, Any]:
        """The options of the "SmartPI defaults" entry."""
        entry = next((e for e in self.hass.config_entries.async_entries(DOMAIN) if e.unique_id == DOMAIN), None)
        return {**entry.data, **entry.options} if entry is not None else {}

    def _used_by_central_boiler(self) -> bool:
        return self._vt_config(self._target_registry_entry()).get(VT_USED_BY_CENTRAL_BOILER) is True

    def _chosen_sources(self, options: dict[str, Any]) -> tuple[str, str]:
        """The water source and the call source in the options (the defaults without a choice)."""
        return chosen_water_source(options), chosen_call_source(options, self._used_by_central_boiler())

    def _source_options(self, fixed: list[str], current: str) -> list[selector.SelectOptionDict]:
        """The sources to choose from: the fixed ones and the registered providers. A chosen
        provider that is no longer registered stays, marked as not available."""
        options = [selector.SelectOptionDict(value=value, label=value) for value in fixed]
        names = provider_names(self.hass)
        options += [selector.SelectOptionDict(value=provider_source(name), label=name) for name in names]
        missing = provider_name(current)
        if missing is not None and missing not in names:
            options.append(selector.SelectOptionDict(value=current, label=f"{missing} (not available)"))
        return options

    def _source_selector(self, fixed: list[str], current: str, translation_key: str) -> selector.SelectSelector:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=self._source_options(fixed, current),
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=translation_key,
            )
        )

    async def async_step_heating_inputs(self, user_input: dict[str, Any] | None = None):
        """Choose where the water data and the boiler's call come from.

        The additional source of a zone chooses its water data only: its learning
        is paused while it follows the main source.
        """
        current = self._current_options()
        self._heating_call_shown = not self._is_additional_source()
        water, call = self._chosen_sources(current)
        if user_input is not None:
            pending = dict(current)
            pending[CONF_SMART_PI_WATER_SOURCE] = user_input[CONF_SMART_PI_WATER_SOURCE]
            if self._heating_call_shown:
                pending[CONF_SMART_PI_BOILER_CALL_SOURCE] = user_input[CONF_SMART_PI_BOILER_CALL_SOURCE]
            self._heating_pending = pending
            if self._heating_signal_keys() or pending[CONF_SMART_PI_WATER_SOURCE] == SOURCE_ENTITIES:
                return await self.async_step_heating_entities()
            return self._heating_finish()
        schema: dict[Any, Any] = {
            vol.Required(CONF_SMART_PI_WATER_SOURCE, default=water): self._source_selector(
                [SOURCE_NONE, SOURCE_ENTITIES], water, "water_source"
            )
        }
        if self._heating_call_shown:
            fixed = [SOURCE_NONE]
            if self._used_by_central_boiler() or call == SOURCE_VTHERM:
                fixed.append(SOURCE_VTHERM)
            fixed.append(SOURCE_ENTITIES)
            schema[vol.Required(CONF_SMART_PI_BOILER_CALL_SOURCE, default=call)] = self._source_selector(
                fixed, call, "boiler_call_source"
            )
        return self.async_show_form(step_id="heating_inputs", data_schema=vol.Schema(schema))

    def _heating_signal_keys(self) -> list[str]:
        """The boiler's signals the chosen sources read from entities, in the form's order."""
        pending = self._heating_pending
        keys: list[str] = []
        if pending.get(CONF_SMART_PI_WATER_SOURCE) == SOURCE_ENTITIES:
            keys += _WATER_SIGNAL_KEYS
        if self._heating_call_shown and pending.get(CONF_SMART_PI_BOILER_CALL_SOURCE) == SOURCE_ENTITIES:
            keys += [key for key in _CALL_SIGNAL_KEYS if key not in keys]
        return [key for key in BOILER_SIGNAL_KEYS if key in keys]

    def _signals_text(self, options: dict[str, Any], keys: list[str]) -> str:
        """The boiler's signals of an entry, as text for a form's description."""
        lines = []
        for key in keys:
            entity_id = resolve_entity(self.hass, options.get(key))
            lines.append(f"- {key.removeprefix('smart_pi_').removesuffix('_entity')}: {entity_id or '-'}")
        return "\n".join(lines)

    async def async_step_heating_entities(self, user_input: dict[str, Any] | None = None):
        """The supply water sensor, and whether the boiler's signals are the defaults' or the thermostat's own."""
        pending = self._heating_pending
        water_entities = pending.get(CONF_SMART_PI_WATER_SOURCE) == SOURCE_ENTITIES
        keys = self._heating_signal_keys()
        if user_input is not None:
            if water_entities:
                pending[CONF_SMART_PI_SUPPLY_ENTITY] = stored_entity(self.hass, user_input[CONF_SMART_PI_SUPPLY_ENTITY])
            choice = user_input.get(CONF_SMART_PI_BOILER_SIGNALS, SIGNALS_DEFAULTS)
            pending[CONF_SMART_PI_BOILER_SIGNALS] = choice
            if choice == SIGNALS_OWN:
                return await self.async_step_heating_signals()
            for key in BOILER_SIGNAL_KEYS:
                pending.pop(key, None)
            return self._heating_finish()
        schema: dict[Any, Any] = {}
        if water_entities:
            supply = resolve_entity(self.hass, pending.get(CONF_SMART_PI_SUPPLY_ENTITY))
            key = vol.Required(CONF_SMART_PI_SUPPLY_ENTITY, default=supply) if supply else vol.Required(CONF_SMART_PI_SUPPLY_ENTITY)
            schema[key] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            )
        choice = pending.get(CONF_SMART_PI_BOILER_SIGNALS)
        schema[vol.Required(CONF_SMART_PI_BOILER_SIGNALS, default=choice if choice == SIGNALS_OWN else SIGNALS_DEFAULTS)] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(options=[SIGNALS_DEFAULTS, SIGNALS_OWN], translation_key="boiler_signals")
            )
        )
        return self.async_show_form(
            step_id="heating_entities",
            data_schema=vol.Schema(schema),
            description_placeholders={"defaults": self._signals_text(self._defaults_options(), keys)},
        )

    async def async_step_heating_signals(self, user_input: dict[str, Any] | None = None):
        """The thermostat's own boiler signals (prefilled from the defaults)."""
        pending = self._heating_pending
        keys = self._heating_signal_keys()
        if user_input is not None:
            for key in keys:
                value = stored_entity(self.hass, user_input.get(key))
                if value:
                    pending[key] = value
                else:
                    pending.pop(key, None)
            return self._heating_finish()
        # A thermostat that already has its own signals keeps them, an empty one too; one
        # that took the defaults' starts from them.
        had_own = self._current_options().get(CONF_SMART_PI_BOILER_SIGNALS) == SIGNALS_OWN
        defaults = {} if had_own else self._defaults_options()
        schema = {}
        for key in keys:
            value = resolve_entity(self.hass, pending.get(key) or defaults.get(key))
            schema[vol.Optional(key, description={"suggested_value": value} if value else None)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=_SIGNAL_DOMAINS[key])
            )
        return self.async_show_form(step_id="heating_signals", data_schema=vol.Schema(schema))

    def _heating_finish(self):
        """Save the sources; a change of the water source or of its supply sensor first asks
        about the learned model (the learned power changes units)."""
        pending = self._heating_pending
        if pending.get(CONF_SMART_PI_WATER_SOURCE) != SOURCE_ENTITIES:
            pending.pop(CONF_SMART_PI_SUPPLY_ENTITY, None)
        before = config_from_options(self._current_options(), None, False).water_inputs
        after = config_from_options(pending, None, False).water_inputs
        if before != after:
            return self.async_show_form(
                step_id="heating_reset",
                data_schema=vol.Schema({vol.Required(FIELD_RESET_MODEL, default=True): bool}),
            )
        return self.async_create_entry(title="", data=pending)

    async def async_step_heating_reset(self, user_input: dict[str, Any] | None = None):
        """Reset the learned model with the change of the water data, as recommended.

        Kept, the data appearing from these inputs are not asked about again.
        """
        if user_input is None:
            return self._heating_finish()
        pending = self._heating_pending
        if user_input.get(FIELD_RESET_MODEL):
            pending[CONF_SMART_PI_RESET_TOKEN] = uuid.uuid4().hex
            pending.pop(CONF_SMART_PI_MODEL_KEPT_FOR_WATER, None)
        else:
            pending[CONF_SMART_PI_MODEL_KEPT_FOR_WATER] = config_from_options(pending, None, False).water_inputs
        return self.async_create_entry(title="", data=pending)

    async def async_step_boiler_signals(self, user_input: dict[str, Any] | None = None):
        """The boiler's signals, shared by the thermostats that read entities (defaults entry)."""
        current = self._current_options()
        if user_input is not None:
            data = dict(current)
            for key in BOILER_SIGNAL_KEYS:
                value = stored_entity(self.hass, user_input.get(key))
                if value:
                    data[key] = value
                else:
                    data.pop(key, None)
            return self.async_create_entry(title="", data=data)
        schema = {}
        for key in BOILER_SIGNAL_KEYS:
            value = resolve_entity(self.hass, current.get(key))
            schema[vol.Optional(key, description={"suggested_value": value} if value else None)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=_SIGNAL_DOMAINS[key])
            )
        return self.async_show_form(step_id="boiler_signals", data_schema=vol.Schema(schema))

    async def async_step_source_role(self, user_input: dict[str, Any] | None = None):
        """A thermostat working as a zone's additional source: its activation delays, heating type and valve curve."""
        defaults = self._current_options()
        is_valve_target = self._is_valve_target_entry()
        if user_input is not None:
            data = dict(defaults)
            data.update(user_input)
            self._pending_options_data = data
            if is_valve_target and user_input.get(CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION):
                return self.async_show_form(step_id="valve_curve", data_schema=build_valve_curve_schema(data))
            return self.async_create_entry(title="", data=data)
        own = self._target_registry_entry()
        main = self._main_naming(own.id if own is not None else None)
        # What still applies while it follows the main source: its cycle's delays, its
        # heating type (its water factor and the zone's response) and its valve curve.
        kept = (
            CONF_MINIMAL_ACTIVATION_DELAY,
            CONF_MINIMAL_DEACTIVATION_DELAY,
            CONF_SMART_PI_HEATING_TYPE,
            CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
        )
        return self.async_show_form(
            step_id="source_role",
            data_schema=vol.Schema(
                {
                    key: value
                    for key, value in build_main_options_schema(
                        defaults, include_valve_linearization=is_valve_target
                    ).schema.items()
                    if getattr(key, "schema", key) in kept
                }
            ),
            description_placeholders={"main": main.title if main is not None else "-"},
        )

    async def async_step_valve_curve(self, user_input: dict[str, Any] | None = None):
        """Handle the options valve curve flow."""
        defaults = dict(DEFAULT_OPTIONS)
        defaults.update(self._config_entry.options or self._config_entry.data)
        data = dict(self._pending_options_data or defaults)

        if user_input is not None:
            data.update(user_input)
            errors = _validate_valve_curve_config(_schema_defaults(data))
            if errors:
                return self.async_show_form(
                    step_id="valve_curve",
                    data_schema=build_valve_curve_schema(_schema_defaults(data)),
                    errors=errors,
                )
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="valve_curve",
            data_schema=build_valve_curve_schema(_schema_defaults(data)),
        )
