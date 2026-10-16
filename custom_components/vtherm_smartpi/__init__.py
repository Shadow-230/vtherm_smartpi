"""The vtherm_smartpi integration."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import service as service_helper
from homeassistant.helpers.start import async_at_started
from vtherm_api.log_collector import get_vtherm_logger
from vtherm_api.vtherm_api import VThermAPI

from .const import (
    ATTR_LEARNING_ENABLED,
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_ZONE_RECOVERY,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_SETTINGS_TO_MOVE,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DATA_FACTORY_REGISTERED,
    DATA_SAVE_GROUP,
    DATA_SERVICES_REGISTERED,
    DATA_SOURCE_LINK,
    DEFAULT_OPTIONS,
    DOMAIN,
    ISSUE_THERMOSTAT_ENTRIES_CREATED,
    PROP_FUNCTION_SMART_PI,
    SHARED_SETTINGS,
    THERMOSTAT_SETTINGS,
    VALVE_CURVE_POINTS,
    SERVICE_FORCE_SMARTPI_CALIBRATION,
    SERVICE_RESET_SMARTPI_INTEGRAL,
    SERVICE_RESET_SMARTPI_LEARNING,
    SERVICE_SET_SMARTPI_LEARNING,
)
from .factory import SmartPIHandlerFactory
from .settings import entry_options, heating_type_of, is_defaults_entry, own_entry, uses_defaults
from .smartpi.dependencies import parse_dependencies

VT_DOMAIN = "versatile_thermostat"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = get_vtherm_logger(__name__)
DATA_SKIP_FULL_RELOAD = "skip_full_reload"
# An entry SmartPI updates itself (not the user): its update listener does nothing once.
DATA_SKIP_UPDATE = "skip_update"
# The listeners of one save run at once; an older mark is not used.
SAVE_GROUP_MAX_AGE_S = 60.0


def _ensure_domain_data(hass: HomeAssistant) -> dict[str, Any]:
    """Return the plugin data storage in hass."""
    return hass.data.setdefault(DOMAIN, {})


def _register_factory(hass: HomeAssistant) -> bool:
    """Register the SmartPI factory in the shared VT API."""
    data = _ensure_domain_data(hass)
    if data.get(DATA_FACTORY_REGISTERED) is True:
        return True

    api = VThermAPI.get_vtherm_api(hass)
    if api is None:
        _LOGGER.warning("Unable to register SmartPI factory because VThermAPI is unavailable")
        return False

    factory = SmartPIHandlerFactory()
    existing_factory = api.get_prop_algorithm(factory.name)
    if existing_factory is None:
        api.register_prop_algorithm(factory)

    data[DATA_FACTORY_REGISTERED] = True
    return True


def _unregister_factory(hass: HomeAssistant) -> None:
    """Unregister the SmartPI factory from the shared VT API."""
    api = VThermAPI.get_vtherm_api(hass)
    if api is not None:
        api.unregister_prop_algorithm(PROP_FUNCTION_SMART_PI)
    _ensure_domain_data(hass)[DATA_FACTORY_REGISTERED] = False


def _register_services(hass: HomeAssistant) -> None:
    """Register SmartPI services on the plugin domain."""
    data = _ensure_domain_data(hass)
    if data.get(DATA_SERVICES_REGISTERED) is True:
        return

    async def _call_on_vtherms(call, method_name: str, *args) -> None:
        entity_ids = set(await service_helper.async_extract_entity_ids(call))

        call_target = getattr(call, "target", None)
        if isinstance(call_target, dict):
            target_entity_ids = call_target.get("entity_id")
            if isinstance(target_entity_ids, str):
                entity_ids.add(target_entity_ids)
            elif isinstance(target_entity_ids, list):
                entity_ids.update(
                    entity_id for entity_id in target_entity_ids if isinstance(entity_id, str)
                )

        explicit_entity_ids = call.data.get("entity_id")
        if isinstance(explicit_entity_ids, str):
            entity_ids.add(explicit_entity_ids)
        elif isinstance(explicit_entity_ids, list):
            entity_ids.update(
                entity_id for entity_id in explicit_entity_ids if isinstance(entity_id, str)
            )

        component = hass.data.get(CLIMATE_DOMAIN)
        if not component or not entity_ids:
            return

        for entity_id in sorted(entity_ids):
            entity = component.get_entity(entity_id) if hasattr(component, "get_entity") else None
            if entity is None:
                entity = next(
                    (candidate for candidate in list(component.entities) if candidate.entity_id == entity_id),
                    None,
                )
            if entity is None:
                continue

            if getattr(entity, "proportional_function", None) != PROP_FUNCTION_SMART_PI:
                continue
            handler = getattr(entity, method_name, None)
            if handler is None:
                algo_handler = getattr(entity, "_algo_handler", None)
                handler = getattr(algo_handler, method_name, None) if algo_handler is not None else None
            if handler is None:
                _LOGGER.warning(
                    "Service %s not available on %s", method_name, entity.entity_id
                )
                continue
            await handler(*args)

    async def _handle_reset_learning(call) -> None:
        """Handle SmartPI learning reset with a real async callable."""
        await _call_on_vtherms(call, "service_reset_smart_pi_learning")

    async def _handle_set_learning(call) -> None:
        """Handle SmartPI learning enablement changes."""
        await _call_on_vtherms(
            call,
            "service_set_smartpi_learning",
            call.data[ATTR_LEARNING_ENABLED],
        )

    async def _handle_force_calibration(call) -> None:
        """Handle SmartPI calibration forcing with a real async callable."""
        await _call_on_vtherms(call, "service_force_smartpi_calibration")

    async def _handle_reset_integral(call) -> None:
        """Handle SmartPI integral reset with a real async callable."""
        await _call_on_vtherms(call, "service_reset_smartpi_integral")

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SMARTPI_LEARNING,
        _handle_reset_learning,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SMARTPI_LEARNING,
        _handle_set_learning,
        schema=vol.Schema(
            {vol.Required(ATTR_LEARNING_ENABLED): cv.boolean},
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_SMARTPI_CALIBRATION,
        _handle_force_calibration,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SMARTPI_INTEGRAL,
        _handle_reset_integral,
    )

    data[DATA_SERVICES_REGISTERED] = True


def _unregister_services(hass: HomeAssistant) -> None:
    """Unregister SmartPI services from the plugin domain."""
    hass.services.async_remove(DOMAIN, SERVICE_RESET_SMARTPI_LEARNING)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SMARTPI_LEARNING)
    hass.services.async_remove(DOMAIN, SERVICE_FORCE_SMARTPI_CALIBRATION)
    hass.services.async_remove(DOMAIN, SERVICE_RESET_SMARTPI_INTEGRAL)
    _ensure_domain_data(hass)[DATA_SERVICES_REGISTERED] = False


async def _reload_smartpi_vtherms(hass: HomeAssistant) -> None:
    """Reload VT entries that currently target the SmartPI proportional function."""
    reload_tasks = [
        hass.config_entries.async_reload(entry.entry_id)
        for entry in hass.config_entries.async_entries(VT_DOMAIN)
        if entry.data.get(CONF_PROP_FUNCTION) == PROP_FUNCTION_SMART_PI
    ]
    if reload_tasks:
        await asyncio.gather(*reload_tasks)


def _get_dedicated_target_unique_ids(hass: HomeAssistant) -> set[str]:
    """Return VT unique ids that have a dedicated SmartPI config entry."""
    return {
        plugin_entry.data[CONF_TARGET_VTHERM]
        for plugin_entry in hass.config_entries.async_entries(DOMAIN)
        if plugin_entry.data.get(CONF_TARGET_VTHERM)
    }


async def _reload_smartpi_vtherms_for_target(
    hass: HomeAssistant,
    target_unique_id: str,
) -> None:
    """Reload the VT entry bound to the given thermostat unique id."""
    registry = er.async_get(hass)
    climate_entity_id = registry.async_get_entity_id(
        CLIMATE_DOMAIN,
        "versatile_thermostat",
        target_unique_id,
    )
    if not climate_entity_id:
        return

    climate_entry = registry.async_get(climate_entity_id)
    if climate_entry is None or climate_entry.config_entry_id is None:
        return

    await hass.config_entries.async_reload(climate_entry.config_entry_id)


def _get_own_values_target_unique_ids(hass: HomeAssistant) -> set[str]:
    """VT unique ids whose SmartPI entry holds its own shared settings (does not use the defaults)."""
    return {
        plugin_entry.data[CONF_TARGET_VTHERM]
        for plugin_entry in hass.config_entries.async_entries(DOMAIN)
        if plugin_entry.data.get(CONF_TARGET_VTHERM) and not uses_defaults(entry_options(plugin_entry))
    }


async def _reload_smartpi_vtherms_using_defaults(hass: HomeAssistant) -> None:
    """Reload SmartPI VT entries that use the defaults: without an entry of their own, or
    with one that takes the shared settings from the defaults."""
    dedicated_target_unique_ids = _get_own_values_target_unique_ids(hass)
    component = hass.data.get(CLIMATE_DOMAIN)
    if not component:
        return

    registry = er.async_get(hass)
    vt_entry_ids: set[str] = set()
    for entity in component.entities:
        if getattr(entity, "proportional_function", None) != PROP_FUNCTION_SMART_PI:
            continue
        if getattr(entity, "unique_id", None) in dedicated_target_unique_ids:
            continue

        climate_entry = registry.async_get(entity.entity_id)
        if climate_entry is None or climate_entry.config_entry_id is None:
            continue
        vt_entry_ids.add(climate_entry.config_entry_id)

    if vt_entry_ids:
        await asyncio.gather(
            *(hass.config_entries.async_reload(entry_id) for entry_id in vt_entry_ids)
        )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up vtherm_smartpi from YAML."""
    del config
    _register_factory(hass)
    _register_services(hass)
    return True


from homeassistant.const import Platform

PLATFORMS = [Platform.SENSOR]


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload affected VT entries when plugin options change."""
    data = _ensure_domain_data(hass)
    if data.pop(f"{DATA_SKIP_UPDATE}_{entry.entry_id}", False):
        return
    reloading = _saved_together(data, entry.entry_id)
    data[f"{DATA_SKIP_FULL_RELOAD}_{entry.entry_id}"] = True
    await hass.config_entries.async_reload(entry.entry_id)

    target_unique_id = entry.data.get(CONF_TARGET_VTHERM)
    if target_unique_id:
        await _reload_smartpi_vtherms_for_target(hass, target_unique_id)
        # A zone's main source reads its additional source's entry (heating type) at start.
        for main_unique_id in _zone_mains_of(hass, target_unique_id):
            if main_unique_id in reloading:
                # The same save wrote the main source's entry: it reloads by itself.
                continue
            await _reload_smartpi_vtherms_for_target(hass, main_unique_id)
        return

    # The thermostats' own entries that use the defaults show "Record full diagnostics" as the
    # defaults set it: their diagnostic sensor is made again.
    for plugin_entry in hass.config_entries.async_entries(DOMAIN):
        if plugin_entry.data.get(CONF_TARGET_VTHERM) and uses_defaults(entry_options(plugin_entry)):
            data[f"{DATA_SKIP_FULL_RELOAD}_{plugin_entry.entry_id}"] = True
            await hass.config_entries.async_reload(plugin_entry.entry_id)
    await _reload_smartpi_vtherms_using_defaults(hass)


def _saved_together(data: dict[str, Any], entry_id: str) -> set[str]:
    """The thermostats that reload by themselves after the save that wrote this entry (none if
    the entry was not written by a save of the options, or that save is long past)."""
    group = data.get(DATA_SAVE_GROUP)
    if not group or entry_id not in group["pending"]:
        return set()
    group["pending"].discard(entry_id)
    if not group["pending"]:
        data.pop(DATA_SAVE_GROUP, None)
    if time.monotonic() - group["at"] > SAVE_GROUP_MAX_AGE_S:
        return set()
    return group["reloading"]


def _zone_mains_of(hass: HomeAssistant, source_unique_id: str) -> list[str]:
    """Unique ids of the thermostats whose zone has ``source_unique_id`` as its additional source."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(CLIMATE_DOMAIN, "versatile_thermostat", source_unique_id)
    reg_entry = registry.async_get(entity_id) if entity_id else None
    if reg_entry is None:
        return []
    mains = []
    for plugin_entry in hass.config_entries.async_entries(DOMAIN):
        main_unique_id = plugin_entry.data.get(CONF_TARGET_VTHERM)
        if not main_unique_id or main_unique_id == source_unique_id:
            continue
        options = plugin_entry.options or plugin_entry.data
        items = parse_dependencies(options.get(CONF_SMART_PI_DEPENDENCIES))
        if any(item.is_source and item.entity == reg_entry.id for item in items):
            mains.append(main_unique_id)
    return mains


def _settings_to_move(options: dict[str, Any]) -> dict[str, Any]:
    """The "thermostat only" settings the defaults entry sets (they are no longer read there)."""
    moved: dict[str, Any] = {}
    if heating_type_of(options) is not None:
        moved[CONF_SMART_PI_HEATING_TYPE] = options[CONF_SMART_PI_HEATING_TYPE]
    for key in (CONF_MINIMAL_ACTIVATION_DELAY, CONF_MINIMAL_DEACTIVATION_DELAY):
        value = options.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            moved[key] = value
    if options.get(CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE) is True:
        moved[CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE] = True
    if options.get(CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION) is True and all(
        key in options for key in VALVE_CURVE_POINTS
    ):
        moved[CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION] = True
        moved.update({key: options[key] for key in VALVE_CURVE_POINTS})
    return moved


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Minor version 1 to 2: the settings sorted into shared and "thermostat only"; 2 to 3: the
    shared setting of a zone's recovery after a setback.

    A thermostat's entry keeps every value (a full copy) and does not use the defaults. The
    defaults entry gets every shared setting and loses the "thermostat only" ones; those it
    sets are kept under a temporary key until Home Assistant has started, when each
    thermostat on SmartPI without an entry of its own gets one with them. The major version
    stays 1, so the release 0.4.0 still loads the entries.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        options = entry_options(entry)
        if is_defaults_entry(entry):
            moved = _settings_to_move(options)
            new_options = {key: value for key, value in options.items() if key not in THERMOSTAT_SETTINGS}
            for key in SHARED_SETTINGS:
                new_options.setdefault(key, DEFAULT_OPTIONS[key])
            if moved:
                new_options[CONF_SMART_PI_SETTINGS_TO_MOVE] = moved
            new_data = {key: value for key, value in entry.data.items() if key not in THERMOSTAT_SETTINGS}
            hass.config_entries.async_update_entry(entry, data=new_data, options=new_options, minor_version=2)
        else:
            hass.config_entries.async_update_entry(
                entry, options={**options, CONF_SMART_PI_USE_DEFAULTS: False}, minor_version=2
            )
        _LOGGER.info("SmartPI entry %s migrated to version 1.2", entry.title)
    if entry.minor_version < 3:
        # The new shared setting: in the defaults entry, and in a thermostat's entry that
        # holds its own values, the value the form starts from.
        options = entry_options(entry)
        if is_defaults_entry(entry) or not uses_defaults(options):
            options = {CONF_SMART_PI_ZONE_RECOVERY: DEFAULT_OPTIONS[CONF_SMART_PI_ZONE_RECOVERY], **options}
        hass.config_entries.async_update_entry(entry, options=options, minor_version=3)
        _LOGGER.info("SmartPI entry %s migrated to version 1.3", entry.title)
    return True


def _smartpi_vtherm_unique_ids(hass: HomeAssistant) -> list[tuple[str, str]]:
    """(unique id, name) of each Versatile Thermostat on SmartPI."""
    found: list[tuple[str, str]] = []
    for reg_entry in er.async_get(hass).entities.values():
        if reg_entry.domain != CLIMATE_DOMAIN or reg_entry.platform != VT_DOMAIN or not reg_entry.unique_id:
            continue
        vt_entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id) if reg_entry.config_entry_id else None
        if vt_entry is None or {**vt_entry.data, **vt_entry.options}.get(CONF_PROP_FUNCTION) != PROP_FUNCTION_SMART_PI:
            continue
        found.append((reg_entry.unique_id, vt_entry.title or reg_entry.entity_id))
    return found


async def _async_move_settings(hass: HomeAssistant, entry_id: str) -> None:
    """Give the "thermostat only" settings the defaults entry held to an entry created for
    each thermostat on SmartPI that has none, then tell the user which (a repair notice)."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return
    options = entry_options(entry)
    values = options.pop(CONF_SMART_PI_SETTINGS_TO_MOVE, None)
    created: list[str] = []
    if isinstance(values, dict) and values:
        values = {key: value for key, value in values.items() if key in THERMOSTAT_SETTINGS}
        for unique_id, name in _smartpi_vtherm_unique_ids(hass):
            if own_entry(hass, unique_id) is not None:
                continue
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={CONF_TARGET_VTHERM: unique_id, **values}
            )
            if result.get("type") == "create_entry":
                created.append(result.get("title") or name)
    _ensure_domain_data(hass)[f"{DATA_SKIP_UPDATE}_{entry.entry_id}"] = True
    if not hass.config_entries.async_update_entry(entry, options=options):
        _ensure_domain_data(hass).pop(f"{DATA_SKIP_UPDATE}_{entry.entry_id}", None)
    if created:
        _LOGGER.warning(
            "SmartPI created an entry of its own for %s: they used settings that are no longer set in the defaults",
            ", ".join(created),
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_THERMOSTAT_ENTRIES_CREATED,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_THERMOSTAT_ENTRIES_CREATED,
            translation_placeholders={"entries": ", ".join(created)},
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up vtherm_smartpi from a config entry."""
    data = _ensure_domain_data(hass)
    data[entry.entry_id] = entry.entry_id
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    if CONF_SMART_PI_SETTINGS_TO_MOVE in entry_options(entry):
        # VTherm's thermostats are all known once Home Assistant has started.
        entry_id = entry.entry_id

        async def _at_started(hass: HomeAssistant) -> None:
            await _async_move_settings(hass, entry_id)

        entry.async_on_unload(async_at_started(hass, _at_started))
    _register_factory(hass)
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    skip_full_reload = data.pop(f"{DATA_SKIP_FULL_RELOAD}_{entry.entry_id}", False)
    # Only reload VT entries when HA is already running (live install/update).
    # During initial startup, VT handles deferred algorithm init via async_startup
    # which fires after EVENT_HOMEASSISTANT_STARTED — reloading here would force
    # entity re-creation before the recorder has restored previous states.
    if hass.state == CoreState.running and not skip_full_reload:
        target_unique_id = entry.data.get(CONF_TARGET_VTHERM)
        if entry.source == SOURCE_IMPORT and target_unique_id:
            # The entry of a zone member, created by the zone form: only that thermostat changes.
            await _reload_smartpi_vtherms_for_target(hass, target_unique_id)
        else:
            await _reload_smartpi_vtherms(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a vtherm_smartpi config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    data = _ensure_domain_data(hass)
    data.pop(entry.entry_id, None)

    if not [
        key
        for key in data
        if key not in (DATA_FACTORY_REGISTERED, DATA_SERVICES_REGISTERED, DATA_SOURCE_LINK, DATA_SAVE_GROUP)
    ]:
        _unregister_factory(hass)
        _unregister_services(hass)

    await _reload_smartpi_vtherms(hass)
    return True
