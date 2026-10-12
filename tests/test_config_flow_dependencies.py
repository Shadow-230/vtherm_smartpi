"""Tests for the options menu of a thermostat entry and its dependency list."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
)

VT_DOMAIN = "versatile_thermostat"


def _neighbour(item_id: str, entity: str) -> dict:
    return {
        "id": item_id,
        "kind": "neighbour",
        "entity": entity,
        "mode": "always",
        "door_sensors": [],
        "neighbour_window_action": "nothing",
    }


def _thermostat_entry(hass, dependencies=None) -> MockConfigEntry:
    options = {CONF_TARGET_VTHERM: "vt-living", **DEFAULT_OPTIONS}
    if dependencies is not None:
        options[CONF_SMART_PI_DEPENDENCIES] = dependencies
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living SmartPI",
        unique_id=f"{DOMAIN}-vt-living",
        data={CONF_TARGET_VTHERM: "vt-living", **DEFAULT_OPTIONS},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


async def _open(hass, entry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


async def test_thermostat_entry_opens_a_menu(hass) -> None:
    entry = _thermostat_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert result["menu_options"] == ["settings", "dependencies"]


async def test_global_entry_keeps_the_settings_form(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="SmartPI defaults", unique_id=DOMAIN, data=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_settings_keep_the_dependency_list(hass) -> None:
    dependencies = [_neighbour("n1", "0123456789abcdef")]
    entry = _thermostat_entry(hass, dependencies)

    result = await _open(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SMART_PI_DEADBAND: 0.1}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_DEADBAND] == 0.1
    assert entry.options[CONF_SMART_PI_DEPENDENCIES] == dependencies


async def test_dependency_list_shows_items_and_removes_the_selected_ones(hass) -> None:
    registry = er.async_get(hass)
    hall = registry.async_get_or_create("climate", VT_DOMAIN, "vt-hall", suggested_object_id="hall")
    kitchen = registry.async_get_or_create("climate", VT_DOMAIN, "vt-kitchen", suggested_object_id="kitchen")
    entry = _thermostat_entry(hass, [_neighbour("n1", hall.id), _neighbour("n2", kitchen.id)])

    result = await _open(hass, entry, "dependencies")

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "dependencies"
    assert result["description_placeholders"] == {"count": "2"}
    remove_selector = result["data_schema"].schema[next(iter(result["data_schema"].schema))]
    labels = {option["value"]: option["label"] for option in remove_selector.config["options"]}
    assert labels == {"n1": "neighbour: climate.hall", "n2": "neighbour: climate.kitchen"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"remove": ["n1"]})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_DEPENDENCIES] == [_neighbour("n2", kitchen.id)]


async def test_dependency_list_without_selection_changes_nothing(hass) -> None:
    dependencies = [_neighbour("n1", "0123456789abcdef")]
    entry = _thermostat_entry(hass, dependencies)

    result = await _open(hass, entry, "dependencies")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"remove": []})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "dependencies_unchanged"
    assert entry.options[CONF_SMART_PI_DEPENDENCIES] == dependencies


async def test_empty_dependency_list_has_nothing_to_remove(hass) -> None:
    entry = _thermostat_entry(hass)

    result = await _open(hass, entry, "dependencies")

    assert result["type"] == FlowResultType.FORM
    assert result["description_placeholders"] == {"count": "0"}
    assert not result["data_schema"].schema


async def test_valve_thermostat_menu_opens_the_valve_curve(hass, monkeypatch) -> None:
    vt_entry = MockConfigEntry(
        domain=VT_DOMAIN,
        unique_id="vt-living",
        data={"thermostat_type": "thermostat_over_climate", "auto_regulation_mode": "auto_regulation_valve"},
    )
    original_async_get_entry = hass.config_entries.async_get_entry
    monkeypatch.setattr(
        hass.config_entries,
        "async_get_entry",
        lambda entry_id: vt_entry if entry_id == vt_entry.entry_id else original_async_get_entry(entry_id),
    )
    er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, "vt-living", suggested_object_id="living", config_entry=vt_entry
    )
    entry = _thermostat_entry(hass)

    result = await _open(hass, entry, "valve_curve")

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "valve_curve"
