"""The handler reads configured dependencies from Home Assistant states."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_DEPENDENCIES
from custom_components.vtherm_smartpi.handler import SmartPIHandler

from fakes.fake_thermostat_runtime import FakeThermostatRuntime

VT_DOMAIN = "versatile_thermostat"


def _hall_attributes(temperature=21.5, window="off"):
    return {
        "current_temperature": temperature,
        "safety_manager": {"safety_state": "off"},
        "window_manager": {"window_state": window, "window_auto_state": "off"},
    }


def _handler(hass, dependencies) -> tuple[SmartPIHandler, FakeThermostatRuntime]:
    thermostat = FakeThermostatRuntime()
    thermostat.hass = hass
    thermostat.entry_infos = {CONF_SMART_PI_DEPENDENCIES: dependencies}
    handler = SmartPIHandler(thermostat)
    handler.init_algorithm()
    return handler, thermostat


def _readings(thermostat):
    return thermostat.prop_algorithm._known_inputs_provider()


def _neighbour(thermostat):
    (observation,) = _readings(thermostat).neighbours
    return observation


async def test_without_dependencies_no_provider_is_registered(hass) -> None:
    _, thermostat = _handler(hass, [])

    assert thermostat.prop_algorithm._known_inputs_provider is None


async def test_neighbour_is_read_through_its_registry_id(hass) -> None:
    """The link survives an entity id change and follows the door sensor."""
    registry = er.async_get(hass)
    hall = registry.async_get_or_create("climate", VT_DOMAIN, "vt-hall", suggested_object_id="hall")
    door = registry.async_get_or_create("binary_sensor", "test", "door-1", suggested_object_id="hall_door")
    hass.states.async_set("climate.hall", "heat", _hall_attributes(window="on"))
    hass.states.async_set("binary_sensor.hall_door", "off")
    _, thermostat = _handler(
        hass,
        [
            {
                "id": "n1",
                "kind": "neighbour",
                "entity": hall.id,
                "mode": "door",
                "door_sensors": [door.id],
                "neighbour_window_action": "nothing",
            }
        ],
    )

    observation = _neighbour(thermostat)
    assert observation.item.item_id == "n1"
    assert observation.reading.entity_id == "climate.hall"
    assert observation.reading.temperature == 21.5
    assert observation.reading.window_open
    assert observation.reading.usable
    assert not observation.door_open
    assert not observation.connected

    hass.states.async_set("binary_sensor.hall_door", "on")
    assert _neighbour(thermostat).connected

    registry.async_update_entity("climate.hall", new_entity_id="climate.stair_hall")
    hass.states.async_set("climate.stair_hall", "heat", _hall_attributes(temperature=19.0))
    observation = _neighbour(thermostat)
    assert observation.reading.entity_id == "climate.stair_hall"
    assert observation.reading.temperature == 19.0


async def test_neighbour_appearing_later_becomes_available(hass) -> None:
    """A neighbour that starts after the zone is unavailable until its state exists."""
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create("sensor", "test", "hall-temp", suggested_object_id="hall_temperature")
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": sensor.id, "mode": "always"}],
    )
    assert not _neighbour(thermostat).reading.usable

    hass.states.async_set("sensor.hall_temperature", "18.2")
    observation = _neighbour(thermostat)
    assert observation.reading.usable
    assert observation.connected
    assert observation.reading.temperature == 18.2


async def test_unknown_registry_id_has_no_influence(hass) -> None:
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "always"}],
    )
    observation = _neighbour(thermostat)

    assert observation.reading.entity_id is None
    assert not observation.reading.usable
    assert not observation.connected


async def test_state_of_removed_items_is_dropped_at_startup(hass) -> None:
    handler, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "none"}],
    )
    state = thermostat.prop_algorithm.dependency_state
    state.for_item("n1")["k"] = 0.001
    state.for_item("gone")["k"] = 0.002

    await handler.async_added_to_hass()

    assert set(state.items) == {"n1"}


async def test_zone_presets_are_read_from_the_thermostat_state(hass) -> None:
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "none"}],
    )
    hass.states.async_set(
        thermostat.entity_id,
        "heat",
        {"preset_temperatures": {"frost_temp": 7.0, "eco_temp": 17.5, "boost_temp": "n/a"}},
    )

    assert _readings(thermostat).presets == {"frost_temp": 7.0, "eco_temp": 17.5}
