"""Tests for adding and editing a neighbouring zone in the thermostat options."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_DEPENDENCIES,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
)

VT_DOMAIN = "versatile_thermostat"


def _schema_keys(schema) -> set[str]:
    return {getattr(key, "schema", key) for key in schema.schema}


def _default(schema, name):
    for key in schema.schema:
        if getattr(key, "schema", key) == name:
            return key.default()
    raise KeyError(name)


def _setup(hass):
    """Living room thermostat with a door sensor in its area, a hall thermostat and a sensor."""
    registry = er.async_get(hass)
    living_area = ar.async_get(hass).async_create("Living room")
    living = registry.async_get_or_create("climate", VT_DOMAIN, "vt-living", suggested_object_id="living")
    registry.async_update_entity(living.entity_id, area_id=living_area.id)
    hall = registry.async_get_or_create("climate", VT_DOMAIN, "vt-hall", suggested_object_id="hall")
    sensor = registry.async_get_or_create(
        "sensor", "test", "stairs-temp", suggested_object_id="stairs_temperature", original_device_class="temperature"
    )
    door = registry.async_get_or_create(
        "binary_sensor", "test", "living-door", suggested_object_id="living_door", original_device_class="door"
    )
    registry.async_update_entity(door.entity_id, area_id=living_area.id)
    window = registry.async_get_or_create(
        "binary_sensor", "test", "living-window", suggested_object_id="living_window", original_device_class="window"
    )
    return registry.async_get(living.entity_id), hall, sensor, registry.async_get(door.entity_id), window


def _entry(hass, dependencies=None, target="vt-living") -> MockConfigEntry:
    options = {CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS}
    if dependencies is not None:
        options[CONF_SMART_PI_DEPENDENCIES] = dependencies
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-{target}",
        data={CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


async def _add(hass, entry, neighbour_entity_id):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "dependencies"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "neighbour_add"})
    return await hass.config_entries.options.async_configure(result["flow_id"], {"entity": neighbour_entity_id})


async def test_thermostat_neighbour_with_suggested_door(hass) -> None:
    _living, hall, _sensor, door, _window = _setup(hass)
    entry = _entry(hass)

    result = await _add(hass, entry, hall.entity_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "neighbour_details"
    assert _schema_keys(result["data_schema"]) == {"mode", "door_sensors", "neighbour_window_action"}
    assert _default(result["data_schema"], "door_sensors") == [door.entity_id]
    assert _default(result["data_schema"], "mode") == "door"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"mode": "door", "door_sensors": [door.entity_id], "neighbour_window_action": "frost"},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    (stored,) = entry.options[CONF_SMART_PI_DEPENDENCIES]
    assert stored["kind"] == "neighbour"
    assert stored["entity"] == hall.id
    assert stored["door_sensors"] == [door.id]
    assert stored["mode"] == "door"
    assert stored["neighbour_window_action"] == "frost"
    assert stored["id"]


async def test_sensor_neighbour_has_no_window_reaction(hass) -> None:
    _living, _hall, sensor, _door, _window = _setup(hass)
    entry = _entry(hass)

    result = await _add(hass, entry, sensor.entity_id)
    assert "neighbour_window_action" not in _schema_keys(result["data_schema"])

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"mode": "always", "door_sensors": []})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_DEPENDENCIES][0]["neighbour_window_action"] == "nothing"


async def test_neighbour_errors(hass) -> None:
    living, hall, _sensor, door, window = _setup(hass)
    entry = _entry(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": hall.id, "mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"}],
    )

    assert (await _add(hass, entry, living.entity_id))["errors"] == {"entity": "self_reference"}
    assert (await _add(hass, entry, hall.entity_id))["errors"] == {"entity": "duplicate_neighbour"}

    other = er.async_get(hass).async_get_or_create("climate", VT_DOMAIN, "vt-office", suggested_object_id="office")
    result = await _add(hass, entry, other.entity_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"mode": "door", "door_sensors": []})
    assert result["errors"] == {"door_sensors": "door_mode_without_sensor"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mode": "door", "door_sensors": [window.entity_id]}
    )
    assert result["errors"] == {"door_sensors": "window_sensor_rejected"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mode": "door", "door_sensors": [door.entity_id]}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_warnings_ask_for_confirmation(hass) -> None:
    living, hall, _sensor, door, _window = _setup(hass)
    hass.states.async_set(living.entity_id, "heat", {"is_window_auto_configured": True})
    hall_entry = _entry(hass, [], target="vt-hall")
    entry = _entry(hass)
    del hall_entry

    result = await _add(hass, entry, hall.entity_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mode": "door", "door_sensors": [door.entity_id], "neighbour_window_action": "nothing"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "neighbour_confirm"
    warnings = result["description_placeholders"]["warnings"]
    assert "does not list this zone" in warnings
    assert "Automatic window detection" in warnings

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert len(entry.options[CONF_SMART_PI_DEPENDENCIES]) == 1


async def test_edit_keeps_the_item_id(hass) -> None:
    _living, hall, _sensor, door, _window = _setup(hass)
    entry = _entry(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": hall.id, "mode": "door", "door_sensors": [door.id], "neighbour_window_action": "eco"}],
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "dependencies"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "dependency_edit"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"dependency": "n1"})

    assert result["step_id"] == "neighbour_details"
    assert _default(result["data_schema"], "door_sensors") == [door.entity_id]
    assert _default(result["data_schema"], "neighbour_window_action") == "eco"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mode": "always", "door_sensors": [], "neighbour_window_action": "turn_off"}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    (stored,) = entry.options[CONF_SMART_PI_DEPENDENCIES]
    assert (stored["id"], stored["mode"], stored["neighbour_window_action"]) == ("n1", "always", "turn_off")
