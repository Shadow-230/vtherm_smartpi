"""The pattern of the options: sections reached from a menu, the changes kept in the open window,
one "Save and finish" at the end; closing the window discards them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import CoreState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.vtherm_smartpi as smartpi_init
from custom_components.vtherm_smartpi.const import (
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_RESET_TOKEN,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_SMART_PI_WATER_SOURCE,
    CONF_TARGET_VTHERM,
    DATA_SAVE_GROUP,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
    SHARED_SETTINGS,
)

from flow_helpers import choose, configure, options, save

# The real setup of an entry (the autouse fixture stands in for it).
REAL_SETUP_ENTRY = smartpi_init.async_setup_entry

VT_DOMAIN = "versatile_thermostat"
SENSOR = "sensor.living_temperature"
SHARED = {key: DEFAULT_OPTIONS[key] for key in SHARED_SETTINGS}


@pytest.fixture(autouse=True)
def _entries_are_not_set_up():
    """Saving reloads entries; setting them up would start Versatile Thermostat."""
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        yield


def _vtherm(hass, unique_id: str, object_id: str, **config):
    data = {CONF_PROP_FUNCTION: PROP_FUNCTION_SMART_PI, "temperature_sensor_entity_id": SENSOR, **config}
    vt_entry = MockConfigEntry(domain="test_vtherm", data=data, unique_id=f"vt-{unique_id}")
    vt_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, unique_id, suggested_object_id=object_id, config_entry=vt_entry
    )


def _entry(hass, target: str = "vt-living", title: str = "Living SmartPI", **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-{target}",
        title=title,
        data={CONF_TARGET_VTHERM: target},
        options={CONF_TARGET_VTHERM: target, CONF_SMART_PI_USE_DEFAULTS: True, **options},
    )
    entry.add_to_hass(hass)
    return entry


async def _add_sensor_neighbour(hass, result, sensor_id: str):
    result = await choose(hass, result, "dependencies")
    result = await choose(hass, result, "neighbour_add")
    result = await configure(hass, result, {"entity": sensor_id})
    return await configure(hass, result, {"mode": "always", "door_sensors": []})


def _stairs(hass):
    return er.async_get(hass).async_get_or_create(
        "sensor", "test", "stairs", suggested_object_id="stairs_temperature", original_device_class="temperature"
    )


async def test_nothing_is_written_before_save(hass) -> None:
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass)
    stored = dict(entry.options)

    result = await options(hass, entry)
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: True})
    result = await _add_sensor_neighbour(hass, result, _stairs(hass).entity_id)

    assert result["step_id"] == "thermostat_menu"
    assert dict(entry.options) == stored
    result = await save(hass, result, reset=False)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_HEATING_TYPE] == "radiator"
    assert len(entry.options[CONF_SMART_PI_DEPENDENCIES]) == 1


async def test_closing_the_window_writes_nothing_and_touches_no_other_entry(hass) -> None:
    """Even a zone moved to the other thermostat, with an entry to create for it, is discarded."""
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor")
    master = _vtherm(
        hass,
        "vt-master",
        "master",
        thermostat_type="thermostat_over_climate",
        underlying_entity_ids=[radiator.entity_id, floor.entity_id],
        auto_regulation_mode="auto_regulation_none",
        **{CONF_PROP_FUNCTION: "tpi"},
    )
    entry = _entry(hass, "vt-radiator", **{CONF_SMART_PI_HEATING_TYPE: "radiator"})
    stored = dict(entry.options)

    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await configure(hass, result, {"master": master.entity_id})
    result = await configure(hass, result, {"source_2_heating_type": "floor_water"})
    result = await configure(hass, result, {"main_source": floor.entity_id, "split_rule": "together", "custom_response": False})
    result = await configure(hass, result, {"split_ratio": 100})
    assert result["step_id"] == "thermostat_menu"
    hass.config_entries.options.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert dict(entry.options) == stored
    assert [e.data.get(CONF_TARGET_VTHERM) for e in hass.config_entries.async_entries(DOMAIN)] == ["vt-radiator"]


async def test_the_menu_names_the_unsaved_sections_in_the_interface_s_language(hass) -> None:
    """The labels come from the interface's translations (here a stand-in for another language)."""
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass)
    result = await options(hass, entry)
    assert result["description_placeholders"]["changes"] == "—"

    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: True})
    assert result["description_placeholders"]["changes"] == "Heating type and regulation"

    prefix = f"component.{DOMAIN}.options.step.thermostat_menu.menu_options"
    texts = {f"{prefix}.settings": "Ogrzewanie i regulacja", f"{prefix}.dependencies": "Strefy sąsiednie"}
    hass.config.language = "pl"
    with patch("custom_components.vtherm_smartpi.config_flow.async_get_translations", AsyncMock(return_value=texts)) as get:
        result = await _add_sensor_neighbour(hass, result, _stairs(hass).entity_id)
    assert get.await_args.args[1:3] == ("pl", "options")
    assert result["description_placeholders"]["changes"] == "Ogrzewanie i regulacja, Strefy sąsiednie"


async def test_a_change_undone_is_no_unsaved_change(hass) -> None:
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass, **{CONF_SMART_PI_HEATING_TYPE: "radiator"})
    result = await options(hass, entry)
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "floor_water", CONF_SMART_PI_USE_DEFAULTS: True})
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: True})
    assert result["description_placeholders"]["changes"] == "—"
    # Nothing changed: no reset question, and saving writes the same options.
    result = await save(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_the_menu_follows_the_draft(hass) -> None:
    """A water heating type set in the window shows "Water and boiler" at once; the valve curve
    follows its switch."""
    _vtherm(hass, "vt-living", "living", thermostat_type="thermostat_over_valve")
    entry = _entry(hass)
    result = await options(hass, entry)
    assert result["menu_options"] == ["settings", "zone", "dependencies", "save"]

    result = await choose(hass, result, "settings")
    result = await configure(
        hass,
        result,
        {CONF_SMART_PI_HEATING_TYPE: "floor_water", CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: True, CONF_SMART_PI_USE_DEFAULTS: True},
    )
    assert result["step_id"] == "valve_curve"
    result = await configure(hass, result, {})
    assert result["menu_options"] == ["settings", "heating_inputs", "zone", "dependencies", "valve_curve", "save"]

    result = await choose(hass, result, "settings")
    result = await configure(
        hass,
        result,
        {CONF_SMART_PI_HEATING_TYPE: "electric_fast", CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: False, CONF_SMART_PI_USE_DEFAULTS: True},
    )
    assert result["menu_options"] == ["settings", "zone", "dependencies", "save"]


async def test_the_reset_question_is_asked_once_with_its_reasons(hass) -> None:
    """A new heating type, other water data and a new neighbour: one question names them all."""
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass)
    result = await options(hass, entry)
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: True})
    result = await choose(hass, result, "heating_inputs")
    hass.data.setdefault("vtherm_smartpi_heating_providers", {})["alpha"] = object()
    result = await configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "none", "smart_pi_boiler_call_source": "none"})
    result = await _add_sensor_neighbour(hass, result, _stairs(hass).entity_id)

    result = await choose(hass, result, "save")
    assert result["step_id"] == "model_reset"
    assert result["description_placeholders"]["reasons"] == "Heating type and regulation, Neighbouring zones"
    result = await configure(hass, result, {"reset_model": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_RESET_TOKEN]


async def test_a_change_that_learned_nothing_asks_nothing(hass) -> None:
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass, **{CONF_SMART_PI_HEATING_TYPE: "radiator"})
    result = await options(hass, entry)
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: False})
    result = await configure(hass, result, {**SHARED, CONF_SMART_PI_DEADBAND: 0.2})
    result = await save(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_DEADBAND] == 0.2
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options


async def test_the_dependencies_changed_elsewhere_end_the_window_at_save(hass) -> None:
    """Another window saved this entry's neighbours meanwhile: nothing is written."""
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass)
    result = await options(hass, entry)
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: True})
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SMART_PI_DEPENDENCIES: []})

    result = await choose(hass, result, "save")

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "zone_changed"
    assert CONF_SMART_PI_HEATING_TYPE not in entry.options


async def test_a_save_reloads_each_entry_once_also_when_the_zone_moves(hass) -> None:
    """The zone moves from the radiator's entry to the floor's: each entry's listener reloads its
    own thermostat; the radiator's does not reload the floor again through the zone."""
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor")
    master = _vtherm(
        hass,
        "vt-master",
        "master",
        thermostat_type="thermostat_over_climate",
        underlying_entity_ids=[radiator.entity_id, floor.entity_id],
        auto_regulation_mode="auto_regulation_none",
        **{CONF_PROP_FUNCTION: "tpi"},
    )
    entry = _entry(hass, "vt-radiator", **{CONF_SMART_PI_HEATING_TYPE: "radiator"})
    floor_entry = _entry(hass, "vt-floor", "Floor SmartPI", **{CONF_SMART_PI_HEATING_TYPE: "floor_water"})
    reloaded: list[str] = []

    async def reload_target(hass, unique_id):
        reloaded.append(unique_id)

    for each in (entry, floor_entry):
        each.add_update_listener(smartpi_init._async_update_options)
    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await configure(hass, result, {"master": master.entity_id})
    result = await configure(hass, result, {"main_source": floor.entity_id, "split_rule": "together", "custom_response": False})
    result = await configure(hass, result, {"split_ratio": 100})
    async def reload_entry(entry_id):
        # As Home Assistant: the entry's unload, then its setup (which reloads its thermostat).
        plugin_entry = hass.config_entries.async_get_entry(entry_id)
        await smartpi_init.async_unload_entry(hass, plugin_entry)
        return await REAL_SETUP_ENTRY(hass, plugin_entry)

    with (
        patch.object(smartpi_init, "_reload_smartpi_vtherms_for_target", reload_target),
        patch.object(hass.config_entries, "async_reload", reload_entry),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)),
    ):
        hass.state = CoreState.running
        result = await save(hass, result, reset=True)
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert sorted(reloaded) == ["vt-floor", "vt-radiator"]
    assert DATA_SAVE_GROUP not in hass.data[DOMAIN]
    # The zone moved with the reset to the floor's entry; the radiator keeps no item.
    assert floor_entry.options[CONF_SMART_PI_RESET_TOKEN]
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options
    assert entry.options[CONF_SMART_PI_DEPENDENCIES] == []


async def test_a_save_mark_long_past_is_not_used(hass) -> None:
    data = {DATA_SAVE_GROUP: {"reloading": {"vt-floor"}, "pending": {"e1"}, "at": -1000.0}}
    assert smartpi_init._saved_together(data, "e1") == set()
    assert DATA_SAVE_GROUP not in data
    assert smartpi_init._saved_together({}, "e1") == set()
