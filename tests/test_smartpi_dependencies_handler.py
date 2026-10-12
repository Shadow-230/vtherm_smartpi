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


def _status(thermostat):
    return thermostat.prop_algorithm._known_inputs_provider().status


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

    assert _status(thermostat) == (
        {
            "id": "n1",
            "kind": "neighbour",
            "entity": "climate.hall",
            "mode": "door",
            "available": True,
            "active": False,
            "t_neighbour": 21.5,
            "neighbour_window": True,
            "k": None,
            "k_reliable": False,
        },
    )

    hass.states.async_set("binary_sensor.hall_door", "on")
    assert _status(thermostat)[0]["active"] is True

    registry.async_update_entity("climate.hall", new_entity_id="climate.stair_hall")
    hass.states.async_set("climate.stair_hall", "heat", _hall_attributes(temperature=19.0))
    status = _status(thermostat)[0]
    assert status["entity"] == "climate.stair_hall"
    assert status["t_neighbour"] == 19.0


async def test_neighbour_appearing_later_becomes_available(hass) -> None:
    """A neighbour that starts after the zone is unavailable until its state exists."""
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create("sensor", "test", "hall-temp", suggested_object_id="hall_temperature")
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": sensor.id, "mode": "always"}],
    )
    assert _status(thermostat)[0]["available"] is False

    hass.states.async_set("sensor.hall_temperature", "18.2")
    status = _status(thermostat)[0]
    assert status["available"] is True
    assert status["active"] is True
    assert status["t_neighbour"] == 18.2


async def test_unknown_registry_id_has_no_influence(hass) -> None:
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "always"}],
    )
    status = _status(thermostat)[0]

    assert status["entity"] is None
    assert status["available"] is False
    assert status["active"] is False


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
