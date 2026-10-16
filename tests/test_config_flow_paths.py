"""The options forms in the cases only some setups reach.

These tests check what a user sees when an entity has no unique id, when a neighbour already
lists this zone or its door is also the thermostat's window sensor, which split is recommended
for two devices of similar speed, what an entry whose thermostat was deleted still offers, and
what happens when another window (or Versatile Thermostat's own options) changes something
while a window is open: the save ends the window and writes nothing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flow_helpers import choose, configure, default, fields, options, save
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
)

VT_DOMAIN = "versatile_thermostat"
SENSOR = "sensor.living_temperature"


@pytest.fixture(autouse=True)
def _entries_are_not_set_up():
    """Saving reloads or creates entries; setting them up would start Versatile Thermostat."""
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        yield


def _vtherm(hass, unique_id: str, object_id: str, **config):
    """A Versatile Thermostat climate entity; its configuration in an entry of another domain."""
    data = {CONF_PROP_FUNCTION: PROP_FUNCTION_SMART_PI, "temperature_sensor_entity_id": SENSOR, **config}
    vt_entry = MockConfigEntry(domain="test_vtherm", data=data, unique_id=f"vt-{unique_id}")
    vt_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, unique_id, suggested_object_id=object_id, config_entry=vt_entry
    )


def _master(hass, devices: list[str]):
    return _vtherm(
        hass,
        "vt-master",
        "master",
        thermostat_type="thermostat_over_climate",
        underlying_entity_ids=devices,
        auto_regulation_mode="auto_regulation_none",
        **{CONF_PROP_FUNCTION: "tpi"},
    )


def _entry(hass, target: str, heating_type: str | None = "radiator", dependencies=None, title=None) -> MockConfigEntry:
    stored = {CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS}
    if heating_type is not None:
        stored[CONF_SMART_PI_HEATING_TYPE] = heating_type
    if dependencies is not None:
        stored[CONF_SMART_PI_DEPENDENCIES] = dependencies
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-{target}",
        title=title or target,
        data={CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS},
        options=stored,
    )
    entry.add_to_hass(hass)
    return entry


def _zone(hass, floor_entry: bool = True):
    """A living room heated by a radiator and a water floor under one master."""
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor")
    master = _master(hass, [radiator.entity_id, floor.entity_id])
    entry = _entry(hass, "vt-radiator", title="Radiator SmartPI")
    floor_entry_ = _entry(hass, "vt-floor", "floor_water", title="Floor SmartPI") if floor_entry else None
    return radiator, floor, master, entry, floor_entry_


def _zone_item(floor, master) -> dict:
    return {
        "id": "s1", "kind": "source", "entity": floor.id, "zone_master": master.id, "split_rule": "together",
        "split_ratio": 100, "lead_emitter": "radiator", "source_emitter": "floor_water",
    }


def _neighbour(entity_id: str) -> dict:
    return {"id": "n1", "kind": "neighbour", "entity": entity_id, "mode": "always", "door_sensors": [],
            "neighbour_window_action": "nothing"}


def _targets(hass) -> list[str]:
    return sorted(e.data[CONF_TARGET_VTHERM] for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_TARGET_VTHERM))


def _stairs(hass):
    return er.async_get(hass).async_get_or_create(
        "sensor", "test", "stairs", suggested_object_id="stairs_temperature", original_device_class="temperature"
    )


async def _neighbour_add(hass, entry, entity_id: str):
    result = await options(hass, entry)
    result = await choose(hass, result, "dependencies")
    result = await choose(hass, result, "neighbour_add")
    return await configure(hass, result, {"entity": entity_id})


async def _set_zone(hass, result, master, main):
    """From the zone's master step to the menu: both members have a heating type, the rule together."""
    result = await configure(hass, result, {"master": master.entity_id})
    result = await configure(hass, result, {"main_source": main.entity_id, "split_rule": "together", "custom_response": False})
    return await configure(hass, result, {"split_ratio": 100})


# --- neighbours -----------------------------------------------------------------


async def test_a_neighbour_without_a_unique_id_is_refused(hass) -> None:
    """A temperature sensor made in YAML without a unique id cannot be referenced reliably."""
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass, "vt-living")
    hass.states.async_set("sensor.yaml_temperature", "19.5", {"device_class": "temperature"})

    result = await _neighbour_add(hass, entry, "sensor.yaml_temperature")

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "neighbour_add"
    assert result["errors"] == {"entity": "entity_without_unique_id"}


async def test_a_door_sensor_without_a_unique_id_is_refused(hass) -> None:
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass, "vt-living")
    hass.states.async_set("binary_sensor.yaml_door", "off", {"device_class": "door"})

    result = await _neighbour_add(hass, entry, _stairs(hass).entity_id)
    result = await configure(hass, result, {"mode": "door", "door_sensors": ["binary_sensor.yaml_door"]})

    assert result["step_id"] == "neighbour_details"
    assert result["errors"] == {"door_sensors": "entity_without_unique_id"}
    assert CONF_SMART_PI_DEPENDENCIES not in entry.options


async def test_a_neighbour_whose_entry_lists_this_zone_back_needs_no_confirmation(hass) -> None:
    """The hall's own entry already has the living room as a neighbour: no warning, straight back to the menu."""
    living = _vtherm(hass, "vt-living", "living")
    hall = _vtherm(hass, "vt-hall", "hall")
    _entry(hass, "vt-hall", dependencies=[_neighbour(living.id)])
    entry = _entry(hass, "vt-living")

    result = await _neighbour_add(hass, entry, hall.entity_id)
    result = await configure(hass, result, {"mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"})

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "thermostat_menu"
    result = await save(hass, result, reset=True)
    (stored,) = entry.options[CONF_SMART_PI_DEPENDENCIES]
    assert stored["entity"] == hall.id


async def test_a_door_that_is_also_the_window_sensor_asks_for_confirmation(hass) -> None:
    """The door between the zones is the thermostat's window sensor in Versatile Thermostat: a warning, kept on confirmation."""
    living = _vtherm(hass, "vt-living", "living")
    door = er.async_get(hass).async_get_or_create(
        "binary_sensor", "test", "living-door", suggested_object_id="living_door", original_device_class="door"
    )
    hass.states.async_set(living.entity_id, "heat", {"window_manager": {"window_sensor_entity_id": door.entity_id}})
    entry = _entry(hass, "vt-living")

    result = await _neighbour_add(hass, entry, _stairs(hass).entity_id)
    result = await configure(hass, result, {"mode": "door", "door_sensors": [door.entity_id]})

    assert result["step_id"] == "neighbour_confirm"
    assert result["description_placeholders"]["warnings"] == (
        f"- {door.entity_id} is also the window sensor of this thermostat."
    )
    result = await configure(hass, result, {})
    result = await save(hass, result, reset=True)
    (stored,) = entry.options[CONF_SMART_PI_DEPENDENCIES]
    assert stored["door_sensors"] == [door.id]


# --- zone with two heat sources -------------------------------------------------


@pytest.mark.parametrize(
    ("heater_type", "recommendation"),
    [
        (
            "electric_fast",
            (
                "Recommended: the rule together; both respond at a similar speed. The main source corrects quickly: "
                "climate.heater responds a little faster."
            ),
        ),
        ("radiator", "Recommended: the rule together; both respond at the same speed, either may be the main source."),
    ],
    ids=["one_a_little_faster", "same_speed"],
)
async def test_devices_of_similar_speed_are_recommended_to_work_together(hass, heater_type, recommendation) -> None:
    """No slow source to carry the base load: the rule together, the faster device (or the first) as the main source."""
    heater = _vtherm(hass, "vt-heater", "heater")
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    master = _master(hass, [heater.entity_id, radiator.entity_id])
    _entry(hass, "vt-heater", heater_type)
    entry = _entry(hass, "vt-radiator")

    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await configure(hass, result, {"master": master.entity_id})

    assert result["step_id"] == "zone_sources"
    assert result["description_placeholders"]["recommendation"] == recommendation
    assert default(result, "split_rule") == "together"
    assert default(result, "main_source") == heater.entity_id


async def test_an_additional_source_whose_zone_was_removed_elsewhere_names_no_main(hass) -> None:
    """The floor's window opened as the zone's additional source; the zone is removed in another window
    meanwhile: its forms name no main thermostat, and saving its own settings still works."""
    _radiator, floor, master, entry, floor_entry = _zone(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_SMART_PI_DEPENDENCIES: [_zone_item(floor, master)]}
    )
    result = await options(hass, floor_entry)
    assert result["step_id"] == "source_menu"
    assert result["description_placeholders"]["main"] == "Radiator SmartPI"

    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SMART_PI_DEPENDENCIES: []})
    result = await choose(hass, result, "source_role")

    assert result["step_id"] == "source_role"
    assert result["description_placeholders"]["main"] == "-"
    result = await configure(hass, result, {key: default(result, key) for key in fields(result)})
    assert result["step_id"] == "source_menu"
    assert result["description_placeholders"]["main"] == "-"
    result = await save(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_the_master_changed_in_versatile_thermostat_ends_the_window_at_save(hass) -> None:
    """Self-regulation turned on in the master's own options after the zone was set: nothing is written."""
    radiator, _floor, master, entry, floor_entry = _zone(hass)
    stored, floor_stored = dict(entry.options), dict(floor_entry.options)
    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await _set_zone(hass, result, master, radiator)
    assert result["step_id"] == "thermostat_menu"

    vt_entry = hass.config_entries.async_get_entry(master.config_entry_id)
    hass.config_entries.async_update_entry(vt_entry, data={**vt_entry.data, "auto_regulation_mode": "auto_regulation_slow"})
    result = await choose(hass, result, "save")

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "zone_changed"
    assert (dict(entry.options), dict(floor_entry.options)) == (stored, floor_stored)


async def test_a_member_entry_deleted_while_the_zone_is_edited_is_not_created_again(hass) -> None:
    """Another window deletes the floor's entry while the zone form is open. Opened again, the zone asks
    for the floor's heating type (its entry is gone); the save still sees the change made before the
    second edit, ends the window and creates no entry for the floor."""
    radiator, _floor, master, entry, floor_entry = _zone(hass)
    stored = dict(entry.options)
    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await configure(hass, result, {"master": master.entity_id})
    assert result["step_id"] == "zone_sources"

    assert await hass.config_entries.async_remove(floor_entry.entry_id)
    result = await configure(hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False})
    result = await configure(hass, result, {"split_ratio": 100})
    assert result["step_id"] == "thermostat_menu"

    result = await choose(hass, result, "zone")
    result = await choose(hass, result, "zone_master")
    result = await configure(hass, result, {"master": master.entity_id})
    assert result["step_id"] == "zone_heating_types"
    assert fields(result) == ["source_2_heating_type"]
    result = await configure(hass, result, {"source_2_heating_type": "floor_water"})
    result = await configure(hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False})
    result = await configure(hass, result, {"split_ratio": 100})
    result = await choose(hass, result, "save")

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "zone_changed"
    assert _targets(hass) == ["vt-radiator"]
    assert dict(entry.options) == stored


async def test_a_pending_change_saved_meanwhile_in_another_window_is_no_unsaved_change(hass) -> None:
    """The zone gives the floor (heating type not set) a heating type; another window saves the same one
    on the floor's entry: the menu lists no unsaved change, and saving asks nothing."""
    radiator, floor, master, entry, _ = _zone(hass, floor_entry=False)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_SMART_PI_DEPENDENCIES: [_zone_item(floor, master)]}
    )
    floor_entry = _entry(hass, "vt-floor", heating_type=None, title="Floor SmartPI")
    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await choose(hass, result, "zone_master")
    result = await configure(hass, result, {"master": master.entity_id})
    assert result["step_id"] == "zone_heating_types"
    result = await configure(hass, result, {"source_2_heating_type": "floor_water"})
    result = await configure(hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False})
    result = await configure(hass, result, {"split_ratio": 100})
    assert result["description_placeholders"]["changes"] == "Zone with two heat sources"

    hass.config_entries.async_update_entry(
        floor_entry, options={**floor_entry.options, CONF_SMART_PI_HEATING_TYPE: "floor_water"}
    )
    result = await choose(hass, result, "dependencies")
    result = await choose(hass, result, "thermostat_menu")

    assert result["description_placeholders"]["changes"] == "—"
    result = await save(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert floor_entry.options[CONF_SMART_PI_HEATING_TYPE] == "floor_water"


# --- an entry whose thermostat was deleted ------------------------------------------


async def test_an_entry_whose_thermostat_was_deleted_still_opens_its_sections(hass) -> None:
    """The Versatile Thermostat is gone, its SmartPI entry is left: the zone section finds no master,
    "Water and boiler" offers no call from VT's central boiler."""
    entry = _entry(hass, "vt-gone")
    result = await options(hass, entry)
    assert result["step_id"] == "thermostat_menu"

    result = await choose(hass, result, "zone")
    assert result["step_id"] == "thermostat_menu"
    assert result["description_placeholders"]["note"].startswith("No Versatile Thermostat over climate")

    result = await choose(hass, result, "heating_inputs")
    assert result["step_id"] == "heating_inputs"
    call = next(key for key in result["data_schema"].schema if key == CONF_SMART_PI_BOILER_CALL_SOURCE)
    choices = [option["value"] for option in result["data_schema"].schema[call].config["options"]]
    assert choices == ["none", "entities"]
    assert default(result, CONF_SMART_PI_BOILER_CALL_SOURCE) == "none"


# --- changes made elsewhere at save -------------------------------------------------


async def test_a_change_made_elsewhere_while_the_reset_question_is_open_writes_nothing(hass) -> None:
    """The reset question is shown; another window saves this entry's neighbours before it is answered."""
    _vtherm(hass, "vt-living", "living")
    entry = _entry(hass, "vt-living", heating_type=None)
    result = await options(hass, entry)
    result = await choose(hass, result, "settings")
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_USE_DEFAULTS: True})
    result = await choose(hass, result, "save")
    assert result["step_id"] == "model_reset"

    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SMART_PI_DEPENDENCIES: []})
    result = await configure(hass, result, {"reset_model": True})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "zone_changed"
    assert CONF_SMART_PI_HEATING_TYPE not in entry.options


async def test_a_main_source_entry_that_cannot_be_created_ends_the_save(hass) -> None:
    """The zone moves to the floor, which has no entry yet, while a window creating the floor's entry is
    open: SmartPI cannot create it, so the radiator's entry is not written either."""
    _radiator, floor, master, entry, _ = _zone(hass, floor_entry=False)
    # With a defaults entry, adding the integration opens a thermostat's entry.
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="SmartPI defaults", data={}).add_to_hass(hass)
    stored = dict(entry.options)
    creating = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    creating = await configure(hass, creating, {CONF_TARGET_VTHERM: floor.entity_id})
    assert creating["step_id"] == "thermostat_menu"

    result = await options(hass, entry)
    result = await choose(hass, result, "zone")
    result = await configure(hass, result, {"master": master.entity_id})
    result = await configure(hass, result, {"source_2_heating_type": "floor_water"})
    result = await configure(hass, result, {"main_source": floor.entity_id, "split_rule": "together", "custom_response": False})
    result = await configure(hass, result, {"split_ratio": 100})
    result = await save(hass, result, reset=False)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "main_entry_missing"
    assert _targets(hass) == ["vt-radiator"]
    assert dict(entry.options) == stored
