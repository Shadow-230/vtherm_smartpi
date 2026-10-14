"""Tests for the options form of a zone with two heat sources, set up from its master thermostat."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_RESET_TOKEN,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
)

VT_DOMAIN = "versatile_thermostat"
SENSOR = "sensor.living_temperature"


@pytest.fixture(autouse=True)
def _entries_are_not_set_up():
    """A saved zone may create an entry; setting it up would start Versatile Thermostat."""
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        yield


def _vtherm(hass, unique_id: str, object_id: str, **config):
    """A Versatile Thermostat climate entity with its configuration.

    The configuration lives in a config entry of another domain: under its own
    domain it would start the real Versatile Thermostat integration.
    """
    data = {CONF_PROP_FUNCTION: PROP_FUNCTION_SMART_PI, "temperature_sensor_entity_id": SENSOR, **config}
    vt_entry = MockConfigEntry(domain="test_vtherm", data=data, unique_id=f"vt-{unique_id}")
    vt_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, unique_id, suggested_object_id=object_id, config_entry=vt_entry
    )


def _master(hass, devices: list[str], unique_id: str = "vt-master", object_id: str = "master", **config):
    values = {
        "thermostat_type": "thermostat_over_climate",
        "underlying_entity_ids": devices,
        "auto_regulation_mode": "auto_regulation_none",
        CONF_PROP_FUNCTION: "tpi",
        **config,
    }
    return _vtherm(hass, unique_id, object_id, **values)


def _smartpi_entry(hass, target: str, heating_type: str = "radiator", dependencies=None, title=None) -> MockConfigEntry:
    options = {CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: heating_type}
    if dependencies is not None:
        options[CONF_SMART_PI_DEPENDENCIES] = dependencies
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-{target}",
        title=title or target,
        data={CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


def _zone(hass, floor_entry: bool = True):
    """A living room heated by a radiator and a water floor under one master."""
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor")
    master = _master(hass, [radiator.entity_id, floor.entity_id])
    radiator_entry = _smartpi_entry(hass, "vt-radiator", title="Radiator SmartPI")
    floor_entry_ = _smartpi_entry(hass, "vt-floor", "floor_water", title="Floor SmartPI") if floor_entry else None
    return radiator, floor, master, radiator_entry, floor_entry_


async def _configure(hass, result, user_input):
    return await hass.config_entries.options.async_configure(result["flow_id"], user_input)


async def _open_zone(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await _configure(hass, result, {"next_step_id": "zone"})


def _default(result, name):
    for key in result["data_schema"].schema:
        if getattr(key, "schema", key) == name:
            return key.default()
    raise KeyError(name)


def _fields(result) -> set[str]:
    return {getattr(key, "schema", key) for key in result["data_schema"].schema}


def _source_item(entry) -> dict | None:
    return next((item for item in entry.options.get(CONF_SMART_PI_DEPENDENCIES, []) if item["kind"] == "source"), None)


async def test_a_zone_is_set_up_from_its_master_with_the_recommended_split(hass) -> None:
    """Radiator and water floor: the faster radiator is the main source, lead_band the rule, the reset offered."""
    radiator, floor, master, entry, _floor_entry = _zone(hass)

    result = await _open_zone(hass, entry)
    assert result["step_id"] == "zone_master"
    assert _default(result, "master") == master.entity_id

    result = await _configure(hass, result, {"master": master.entity_id})
    assert result["step_id"] == "zone_sources"
    placeholders = result["description_placeholders"]
    assert (placeholders["source_1"], placeholders["source_1_type"]) == (radiator.entity_id, "radiator")
    assert (placeholders["source_2"], placeholders["source_2_type"]) == (floor.entity_id, "floor_water")
    assert "lead_band" in placeholders["recommendation"]
    assert _default(result, "main_source") == radiator.entity_id
    assert _default(result, "split_rule") == "lead_band"

    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    assert result["step_id"] == "zone_parameter"
    assert _default(result, "lead_band_percent") == 20.0
    assert "margin" in result["description_placeholders"]["hint"]

    result = await _configure(hass, result, {"lead_band_percent": 25})
    assert result["step_id"] == "zone_reset"
    result = await _configure(hass, result, {"reset_model": True})
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    item = _source_item(entry)
    assert item["entity"] == floor.id
    assert item["zone_master"] == master.id
    assert (item["split_rule"], item["lead_band_percent"]) == ("lead_band", 25)
    assert (item["lead_emitter"], item["source_emitter"]) == ("radiator", "floor_water")
    assert entry.options[CONF_SMART_PI_RESET_TOKEN]


async def test_the_other_source_gets_its_entry_and_shows_its_role(hass) -> None:
    """A floor without a SmartPI entry gets one; its options then show the zone and only its own settings."""
    radiator, floor, master, entry, _ = _zone(hass, floor_entry=False)

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False}
    )
    result = await _configure(hass, result, {"split_ratio": 100})
    result = await _configure(hass, result, {"reset_model": False})
    await hass.async_block_till_done()

    floor_entry = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_TARGET_VTHERM) == "vt-floor")
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options
    result = await hass.config_entries.options.async_init(floor_entry.entry_id)
    assert result["step_id"] == "source_role"
    assert result["description_placeholders"]["main"] == "Radiator SmartPI"
    assert _fields(result) == {
        "minimal_activation_delay",
        "minimal_deactivation_delay",
        "smart_pi_heating_type",
    }


async def test_choosing_the_other_thermostat_as_main_moves_the_zone_with_its_neighbours(hass) -> None:
    radiator, floor, master, entry, floor_entry = _zone(hass)
    hall = _vtherm(hass, "vt-hall", "hall")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_SMART_PI_DEPENDENCIES: [
                {"id": "n1", "kind": "neighbour", "entity": hall.id, "mode": "always", "door_sensors": [],
                 "neighbour_window_action": "nothing"}
            ],
        },
    )

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": floor.entity_id, "split_rule": "source_first", "custom_response": False}
    )
    assert "responds more slowly" in result["description_placeholders"]["hint"]
    result = await _configure(hass, result, {"source_alone_percent": 50})
    assert result["step_id"] == "zone_reset"
    result = await _configure(hass, result, {"reset_model": True})
    await hass.async_block_till_done()

    assert entry.options[CONF_SMART_PI_DEPENDENCIES] == []
    moved = floor_entry.options[CONF_SMART_PI_DEPENDENCIES]
    assert {item["kind"] for item in moved} == {"neighbour", "source"}
    source = next(item for item in moved if item["kind"] == "source")
    assert source["entity"] == radiator.id
    assert (source["lead_emitter"], source["source_emitter"]) == ("floor_water", "radiator")
    assert floor_entry.options[CONF_SMART_PI_RESET_TOKEN]
    # The radiator now works as the floor's additional source.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "source_role"


async def test_changing_only_the_rule_keeps_the_learned_model(hass) -> None:
    radiator, floor, master, entry, _ = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    result = await _configure(hass, result, {"lead_band_percent": 20})
    result = await _configure(hass, result, {"reset_model": False})
    item_id = _source_item(entry)["id"]

    result = await _open_zone(hass, entry)
    assert result["type"] == FlowResultType.MENU
    assert result["description_placeholders"]["source"] == floor.entity_id
    result = await _configure(hass, result, {"next_step_id": "zone_master"})
    result = await _configure(hass, result, {"master": master.entity_id})
    assert _default(result, "main_source") == radiator.entity_id
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    assert _default(result, "lead_band_percent") == 20
    result = await _configure(hass, result, {"lead_band_percent": 30})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    item = _source_item(entry)
    assert (item["id"], item["lead_band_percent"]) == (item_id, 30)


async def test_a_custom_response_is_set_for_the_additional_source_only(hass) -> None:
    """The main source's delay comes from the zone's fit: the form does not ask for it."""
    radiator, floor, master, entry, _ = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": True}
    )
    assert {"source_lag_min", "source_tau_min"} <= _fields(result)
    assert not {"lead_lag_min", "lead_tau_min"} & _fields(result)
    assert (_default(result, "source_lag_min"), _default(result, "source_tau_min")) == (30, 180)
    result = await _configure(hass, result, {"lead_band_percent": 20, "source_lag_min": 45, "source_tau_min": 240})
    result = await _configure(hass, result, {"reset_model": True})

    item = _source_item(entry)
    assert (item["source_emitter"], item["source_lag_min"], item["source_tau_min"]) == ("custom", 45, 240)
    assert item["lead_emitter"] == "radiator"
    assert "lead_lag_min" not in item and "lead_tau_min" not in item


async def test_an_older_custom_main_response_does_not_offer_a_reset_when_only_the_parameter_changes(hass) -> None:
    """A zone saved with the main source's own delay and lag, which nothing used: dropping them
    changes nothing the zone learned."""
    radiator, floor, master, entry, _ = _zone(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_SMART_PI_DEPENDENCIES: [
                {"id": "s1", "kind": "source", "entity": floor.id, "split_rule": "lead_band", "lead_band_percent": 20,
                 "lead_emitter": "custom", "lead_lag_min": 5, "lead_tau_min": 20, "source_emitter": "floor_water",
                 "zone_master": master.id}
            ],
        },
    )

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zone_master"})
    result = await _configure(hass, result, {"master": master.entity_id})
    assert _default(result, "custom_response") is False
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    result = await _configure(hass, result, {"lead_band_percent": 30})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    item = _source_item(entry)
    assert (item["id"], item["lead_band_percent"], item["lead_emitter"]) == ("s1", 30, "radiator")


async def test_a_custom_response_equal_to_the_heating_type_s_does_not_offer_a_reset(hass) -> None:
    """The zone learned with the same delay and lag: its fits are kept, as at run time."""
    radiator, floor, master, entry, _ = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    result = await _configure(hass, result, {"lead_band_percent": 20})
    result = await _configure(hass, result, {"reset_model": False})

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zone_master"})
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": True}
    )
    result = await _configure(hass, result, {"lead_band_percent": 20, "source_lag_min": 30, "source_tau_min": 180})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zone_master"})
    result = await _configure(hass, result, {"master": master.entity_id})
    assert _default(result, "custom_response") is True
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": True}
    )
    result = await _configure(hass, result, {"lead_band_percent": 20, "source_lag_min": 45, "source_tau_min": 180})
    assert result["step_id"] == "zone_reset"


async def test_removing_the_zone_lets_both_regulate_alone(hass) -> None:
    radiator, floor, master, entry, floor_entry = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False}
    )
    result = await _configure(hass, result, {"split_ratio": 100})
    result = await _configure(hass, result, {"reset_model": False})

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zone_remove"})
    assert result["step_id"] == "zone_reset"
    result = await _configure(hass, result, {"reset_model": True})

    assert _source_item(entry) is None
    result = await hass.config_entries.options.async_init(floor_entry.entry_id)
    assert result["type"] == FlowResultType.MENU


async def test_without_a_master_the_form_explains_what_to_create(hass) -> None:
    _vtherm(hass, "vt-radiator", "radiator")
    entry = _smartpi_entry(hass, "vt-radiator")
    result = await _open_zone(hass, entry)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_master"


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("auto_regulation", "master_auto_regulation"),
        ("three_devices", "master_not_two_devices"),
        ("tpi_device", "member_without_smartpi"),
        ("two_sensors", "members_not_one_sensor"),
        ("other_zone", "member_in_another_zone"),
    ],
)
async def test_a_master_that_cannot_carry_the_zone_is_refused(hass, change, error) -> None:
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(
        hass,
        "vt-floor",
        "floor",
        **{
            CONF_PROP_FUNCTION: "tpi" if change == "tpi_device" else PROP_FUNCTION_SMART_PI,
            "temperature_sensor_entity_id": "sensor.other" if change == "two_sensors" else SENSOR,
        },
    )
    devices = [radiator.entity_id, floor.entity_id]
    if change == "three_devices":
        devices.append(_vtherm(hass, "vt-third", "third").entity_id)
    master = _master(
        hass,
        devices,
        auto_regulation_mode="auto_regulation_slow" if change == "auto_regulation" else "auto_regulation_none",
    )
    entry = _smartpi_entry(hass, "vt-radiator")
    if change == "other_zone":
        bedroom = _vtherm(hass, "vt-bedroom", "bedroom")
        _smartpi_entry(
            hass,
            "vt-bedroom",
            dependencies=[{"id": "s1", "kind": "source", "entity": floor.id, "split_rule": "together",
                           "split_ratio": 100, "lead_emitter": "radiator", "source_emitter": "radiator"}],
        )
        del bedroom

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"master": error}


async def test_the_zone_s_source_is_not_listed_with_the_neighbours(hass) -> None:
    radiator, floor, master, entry, _ = _zone(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_SMART_PI_DEPENDENCIES: [
                {"id": "s1", "kind": "source", "entity": floor.id, "split_rule": "together", "split_ratio": 100,
                 "lead_emitter": "radiator", "source_emitter": "floor_water", "zone_master": master.id}
            ],
        },
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _configure(hass, result, {"next_step_id": "dependencies"})
    assert result["menu_options"] == ["neighbour_add"]
    assert result["description_placeholders"]["count"] == "0"


async def test_moving_the_main_drops_a_neighbour_that_is_now_the_zone_itself(hass) -> None:
    """The radiator had the floor as a neighbour: once the floor is the main source it is not its own neighbour."""
    radiator, floor, master, entry, floor_entry = _zone(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_SMART_PI_DEPENDENCIES: [
                {"id": "n1", "kind": "neighbour", "entity": floor.id, "mode": "always", "door_sensors": [],
                 "neighbour_window_action": "nothing"}
            ],
        },
    )
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": floor.entity_id, "split_rule": "together", "custom_response": False}
    )
    result = await _configure(hass, result, {"split_ratio": 100})
    result = await _configure(hass, result, {"reset_model": False})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert [item["kind"] for item in floor_entry.options[CONF_SMART_PI_DEPENDENCIES]] == ["source"]


async def test_a_zone_changed_by_another_form_is_not_overwritten(hass) -> None:
    """Two forms open at once: the second save finds the zone changed and stops."""
    radiator, floor, master, entry, floor_entry = _zone(hass)
    first = await _open_zone(hass, entry)
    first = await _configure(hass, first, {"master": master.entity_id})
    first = await _configure(
        hass, first, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False}
    )
    first = await _configure(hass, first, {"split_ratio": 100})
    assert first["step_id"] == "zone_reset"

    second = await _open_zone(hass, floor_entry)
    second = await _configure(hass, second, {"master": master.entity_id})
    second = await _configure(
        hass, second, {"main_source": floor.entity_id, "split_rule": "together", "custom_response": False}
    )
    second = await _configure(hass, second, {"split_ratio": 100})
    second = await _configure(hass, second, {"reset_model": False})
    assert second["type"] == FlowResultType.CREATE_ENTRY

    first = await _configure(hass, first, {"reset_model": False})
    assert first["type"] == FlowResultType.ABORT
    assert first["reason"] == "zone_changed"
    assert _source_item(entry) is None
    assert _source_item(floor_entry)["entity"] == radiator.id


async def test_a_new_heating_type_alone_does_not_offer_a_reset_when_only_the_rule_changes(hass) -> None:
    radiator, floor, master, entry, floor_entry = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    result = await _configure(hass, result, {"lead_band_percent": 20})
    result = await _configure(hass, result, {"reset_model": False})
    hass.config_entries.async_update_entry(
        floor_entry, options={**floor_entry.options, CONF_SMART_PI_HEATING_TYPE: "floor_electric"}
    )

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zone_master"})
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "lead_band", "custom_response": False}
    )
    result = await _configure(hass, result, {"lead_band_percent": 30})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert _source_item(entry)["source_emitter"] == "floor_electric"


@pytest.mark.parametrize("which", ["source", "master"])
async def test_the_zone_s_source_or_master_cannot_be_its_neighbour(hass, which) -> None:
    radiator, floor, master, entry, _ = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False}
    )
    result = await _configure(hass, result, {"split_ratio": 100})
    await _configure(hass, result, {"reset_model": False})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _configure(hass, result, {"next_step_id": "dependencies"})
    result = await _configure(hass, result, {"next_step_id": "neighbour_add"})
    result = await _configure(hass, result, {"entity": (floor if which == "source" else master).entity_id})
    assert result["errors"] == {"entity": f"{which}_is_neighbour"}


async def test_a_master_listing_one_device_twice_is_refused(hass) -> None:
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    master = _master(hass, [radiator.entity_id, radiator.entity_id])
    entry = _smartpi_entry(hass, "vt-radiator")
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    assert result["errors"] == {"master": "master_not_two_devices"}


async def test_a_member_that_is_the_main_of_another_zone_is_refused(hass) -> None:
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor")
    bedroom = _vtherm(hass, "vt-bedroom", "bedroom")
    master = _master(hass, [radiator.entity_id, floor.entity_id])
    entry = _smartpi_entry(hass, "vt-radiator")
    _smartpi_entry(
        hass,
        "vt-floor",
        dependencies=[{"id": "s1", "kind": "source", "entity": bedroom.id, "split_rule": "together",
                       "split_ratio": 100, "lead_emitter": "radiator", "source_emitter": "radiator"}],
    )
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    assert result["errors"] == {"master": "member_in_another_zone"}


async def test_removing_the_zone_offers_and_stores_the_reset(hass) -> None:
    radiator, floor, master, entry, _ = _zone(hass)
    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"master": master.entity_id})
    result = await _configure(
        hass, result, {"main_source": radiator.entity_id, "split_rule": "together", "custom_response": False}
    )
    result = await _configure(hass, result, {"split_ratio": 100})
    await _configure(hass, result, {"reset_model": False})
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options

    result = await _open_zone(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zone_remove"})
    result = await _configure(hass, result, {"reset_model": True})
    assert entry.options[CONF_SMART_PI_RESET_TOKEN]


async def test_a_valve_source_keeps_its_valve_curve(hass) -> None:
    """A valve still follows its curve while it works for the zone: the form keeps it."""
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor", thermostat_type="thermostat_over_valve")
    _master(hass, [radiator.entity_id, floor.entity_id])
    _smartpi_entry(
        hass,
        "vt-radiator",
        dependencies=[{"id": "s1", "kind": "source", "entity": floor.id, "split_rule": "together",
                       "split_ratio": 100, "lead_emitter": "radiator", "source_emitter": "floor_water"}],
    )
    floor_entry = _smartpi_entry(hass, "vt-floor", "floor_water")
    result = await hass.config_entries.options.async_init(floor_entry.entry_id)
    assert result["step_id"] == "source_role"
    assert "smart_pi_enable_valve_linearization" in _fields(result)
    result = await _configure(
        hass,
        result,
        {"minimal_activation_delay": 0, "minimal_deactivation_delay": 0, "smart_pi_heating_type": "floor_water",
         "smart_pi_enable_valve_linearization": True},
    )
    assert result["step_id"] == "valve_curve"
