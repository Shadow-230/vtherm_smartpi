"""The QAS room simulator (tests/qas/custom_components/smartpi_room_sim): its physics, the same
file as the in-process simulator's, its real-time engine and its Home Assistant entities."""

from __future__ import annotations

import math
import pathlib
import sys

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

QAS = pathlib.Path(__file__).resolve().parent / "qas"
if str(QAS) not in sys.path:
    sys.path.insert(0, str(QAS))

from custom_components.smartpi_room_sim import engine as sim_engine  # noqa: E402
from custom_components.smartpi_room_sim.engine import (  # noqa: E402
    WIGGLE_PERIOD_S,
    Simulation,
    quantize,
    spec_from_config,
)

from sim import plant  # noqa: E402

PHYSICS = QAS / "custom_components" / "smartpi_room_sim" / "physics.py"


def _sim(config: dict, start_hour: float = 12.0) -> Simulation:
    return Simulation(spec_from_config(config), start_hour=start_hour)


def _room(**kw) -> dict:
    heater = {"room": "room", "a": 0.03, "delay_min": 0.0} | kw.pop("heater", {})
    return {
        "rooms": {"room": {"temperature": 18.0, "b": 0.001} | kw.pop("room", {})},
        "heaters": {"heater": heater},
        "outdoor": {"mean": 5.0, "swing": 0.0, "wiggle": 0.0},
        **kw,
    }


# --- one source of physics --------------------------------------------------------------------


def test_the_in_process_simulator_uses_the_same_physics_file() -> None:
    """tests/sim/plant.py re-exports the simulator integration's physics.py, not a copy."""
    assert pathlib.Path(plant._physics.__file__).resolve() == PHYSICS.resolve()  # noqa: SLF001
    engine_physics = sys.modules[sim_engine.ThermalNetwork.__module__]
    assert pathlib.Path(engine_physics.__file__).resolve() == PHYSICS.resolve()


def test_a_room_follows_the_first_order_model() -> None:
    """A room with its heater on follows T_inf + (T0 - T_inf)·exp(-b·t), T_inf = T_ext + a/b."""
    sim = _sim(_room())
    sim.set_command("heater", 1.0)
    sim.advance(7200.0)
    t_inf = 5.0 + 0.03 / 0.001
    expected = t_inf + (18.0 - t_inf) * math.exp(-0.001 * 120.0)
    assert sim.temperature("room") == pytest.approx(expected, abs=1e-6)


def test_the_engine_and_the_in_process_network_agree() -> None:
    """The engine's network and one built by the in-process simulator give the same temperature."""
    sim = _sim(_room(heater={"delay_min": 3.0, "lag_min": 20.0}))
    sim.set_command("heater", 1.0)
    for t in range(7, 3600, 7):
        sim.advance(float(t))
    net = plant.ThermalNetwork()
    net.add_node("room", 18.0)
    net.link_outdoor("room", 0.001)
    history = plant.PowerHistory(0.0)
    history.set(0.0, 1.0)
    net.add_lagged_heater("room", 0.03, plant.DelayedPower(history, 180.0), 20.0)
    for t in range(7, 3600, 7):
        net.advance(float(t - 7), float(t), lambda _t: 5.0)
    assert sim.temperature("room") == pytest.approx(net.temperature("room"), abs=1e-9)


# --- engine -----------------------------------------------------------------------------------


def test_the_delay_holds_the_heat_back() -> None:
    """Within its delay a heater changes nothing; afterwards its heat arrives."""
    off = _sim(_room(heater={"delay_min": 3.0}))
    on = _sim(_room(heater={"delay_min": 3.0}))
    on.set_command("heater", 1.0)
    for sim in (off, on):
        sim.advance(170.0)
    assert on.temperature("room") == pytest.approx(off.temperature("room"), abs=1e-12)
    for sim in (off, on):
        sim.advance(600.0)
    assert on.temperature("room") > off.temperature("room") + 0.1


def test_heat_is_counted_per_heater() -> None:
    """The heat a heater sends is a·power·time, from the end of its delay."""
    sim = _sim(_room(heater={"delay_min": 3.0}))
    sim.set_command("heater", 1.0)
    for t in range(7, 3600 + 7, 7):
        sim.advance(float(t))
    assert sim.heat["heater"] == pytest.approx(0.03 * (sim.t_s / 60.0 - 3.0), abs=0.005)


def _two_rooms() -> dict:
    return {
        "rooms": {"a": {"temperature": 22.0, "b": 0.0}, "b": {"temperature": 16.0, "b": 0.0}},
        "links": [{"rooms": ["a", "b"], "g": 0.01, "door": "door"}],
        "openings": {"door": {}, "window": {"device_class": "window", "room": "b", "g": 0.02}},
        "outdoor": {"mean": 0.0, "swing": 0.0, "wiggle": 0.0},
    }


def test_a_door_links_two_rooms_only_while_open() -> None:
    """A closed door exchanges nothing; an open one brings the rooms together."""
    sim = _sim(_two_rooms())
    sim.advance(600.0)
    assert (sim.temperature("a"), sim.temperature("b")) == pytest.approx((22.0, 16.0))
    sim.set_opening("door", True)
    sim.advance(1200.0)
    assert sim.temperature("a") < 22.0 - 0.3
    assert sim.temperature("b") > 16.0 + 0.3
    assert sim.temperature("a") + sim.temperature("b") == pytest.approx(38.0, abs=1e-9)


def test_a_window_opens_a_room_to_the_outdoors() -> None:
    """An open window cools its room towards the outdoor temperature."""
    sim = _sim(_two_rooms())
    sim.set_opening("window", True)
    sim.advance(1200.0)
    assert sim.temperature("b") < 16.0 - 1.0
    assert sim.temperature("a") == pytest.approx(22.0)


def test_an_opening_follows_its_schedule_unless_set() -> None:
    """A door opens on its daily schedule, a scenario overrides it, and it goes back to it."""
    config = _two_rooms()
    config["openings"]["door"] = {"schedule": [[7.0, 9.0]]}
    sim = _sim(config, start_hour=6.95)
    assert not sim.is_open("door")
    sim.advance(400.0)
    assert sim.is_open("door")
    sim.set_opening("door", False)
    assert not sim.is_open("door")
    sim.set_opening("door", None)
    assert sim.is_open("door")


def _boiler_room() -> dict:
    return _room(
        room={"temperature": 20.0},
        heater={"emitter": "radiator", "a": 0.03},
        boiler={"fire_c": 60.0, "idle_c": 12.0, "tau_up_min": 4.0, "tau_down_min": 20.0},
    )


def test_a_water_emitter_heats_only_while_the_boiler_does() -> None:
    """With the boiler off the water is below the room and the radiator gives nothing; once the
    boiler fires, its water reaches the reference and the radiator its full power."""
    sim = _sim(_boiler_room())
    sim.set_command("heater", 1.0)
    sim.advance(1800.0)
    assert sim.heat["heater"] == 0.0
    sim.set_boiler(True)
    sim.advance(1800.0 + 3600.0)
    assert sim.water_at(sim.t_s) == pytest.approx(60.0, abs=0.01)
    assert sim.heat["heater"] > 0.03 * 45.0


def test_a_device_that_stops_responding_keeps_what_it_did() -> None:
    """``ignore`` keeps the device's last state whatever is asked; ``ok`` applies the last ask."""
    sim = _sim(_room())
    sim.set_mode("heater", "ignore")
    sim.set_command("heater", 1.0)
    assert sim.applied["heater"] == 0.0
    sim.set_mode("heater", "unavailable")
    assert sim.modes["heater"] == "unavailable"
    sim.set_mode("heater", "ok")
    assert sim.applied["heater"] == 1.0


def test_a_room_changes_gently_and_the_outdoors_moves() -> None:
    """A ramp moves a room in a straight line; the outdoor mean moves over its minutes."""
    sim = _sim(_room(room={"b": 0.0}))
    sim.ramp_room("room", 20.0, 10.0)
    sim.advance(300.0)
    assert sim.temperature("room") == pytest.approx(19.0, abs=1e-9)
    sim.advance(700.0)
    assert sim.temperature("room") == pytest.approx(20.0, abs=1e-9)
    sim.set_outdoor(-5.0, minutes=60.0)
    assert sim.outdoor_c(sim.t_s + 1800.0) == pytest.approx(0.0, abs=1e-9)


def test_reset_puts_everything_back() -> None:
    """After a reset the rooms, openings, devices and boiler are as configured."""
    sim = _sim(_boiler_room())
    sim.set_command("heater", 1.0)
    sim.set_boiler(True)
    sim.set_mode("heater", "ignore")
    sim.advance(3600.0)
    sim.reset()
    assert sim.temperature("room") == 20.0
    assert sim.applied["heater"] == 0.0 and sim.modes["heater"] == "ok"
    assert not sim.boiler_on and sim.water_at(sim.t_s) == 12.0


def test_no_period_is_a_multiple_of_smartpis() -> None:
    """The tick and the outdoor wiggle are not multiples of SmartPI's 60 s (the bench's lessons)."""
    assert sim_engine.DEFAULT_STEP_S % 60.0 != 0.0 and 60.0 % sim_engine.DEFAULT_STEP_S != 0.0
    assert WIGGLE_PERIOD_S % 60.0 != 0.0


def test_a_sensor_reading_is_rounded_to_its_resolution() -> None:
    assert quantize(19.04999, 0.1) == 19.0
    assert quantize(19.05001, 0.1) == 19.1
    assert quantize(19.0449, 0.01) == 19.04


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"heaters": {"h": {"room": "nowhere"}}}, "no room"),
        ({"heaters": {"h": {"room": "room", "emitter": "radiator"}}}, "needs the boiler"),
        ({"links": [{"rooms": ["room", "room"], "g": 0.1, "door": "missing"}]}, "no door"),
    ],
)
def test_a_wrong_configuration_is_refused(change: dict, message: str) -> None:
    config = {"rooms": {"room": {}}} | change
    with pytest.raises(ValueError, match=message):
        spec_from_config(config)


# --- Home Assistant entities and services -----------------------------------------------------


@pytest.fixture
async def sim_hass(hass: HomeAssistant):
    config = {
        "smartpi_room_sim": {
            "rooms": {"living": {"temperature": 19.0, "b": 0.001, "resolution": 0.1}},
            "heaters": {
                "radiator": {"room": "living", "a": 0.05, "power_w": 1500},
                "floor": {"room": "living", "kind": "valve", "a": 0.03, "lag_min": 60, "power_w": 1000},
            },
            "openings": {"door": {}},
            "boiler": {},
            "outdoor": {"mean": 3.0, "swing": 0.0, "wiggle": 0.0},
        }
    }
    assert await async_setup_component(hass, "smartpi_room_sim", config)
    await hass.async_block_till_done()
    yield hass


def _advance(hass: HomeAssistant, seconds: float) -> None:
    hub = hass.data["smartpi_room_sim"]
    hub._start -= seconds  # noqa: SLF001 — the test moves the simulation's clock on
    hub.tick()


async def test_the_entities_are_the_simulators(sim_hass: HomeAssistant) -> None:
    """Every entity id starts with smartpi_room_sim_, with the device classes VTherm expects."""
    ids = set(sim_hass.states.async_entity_ids())
    assert {
        "sensor.smartpi_room_sim_living_temperature",
        "sensor.smartpi_room_sim_outdoor_temperature",
        "sensor.smartpi_room_sim_house_power",
        "sensor.smartpi_room_sim_boiler_water",
        "sensor.smartpi_room_sim_radiator_heat",
        "switch.smartpi_room_sim_radiator",
        "switch.smartpi_room_sim_boiler",
        "number.smartpi_room_sim_floor",
        "binary_sensor.smartpi_room_sim_door",
    } <= ids
    temperature = sim_hass.states.get("sensor.smartpi_room_sim_living_temperature")
    assert temperature.attributes["device_class"] == "temperature"
    assert temperature.state == "19.0"


async def test_a_heater_warms_its_room_and_the_sensor_writes_only_changes(sim_hass: HomeAssistant) -> None:
    """A heater switched on warms the room; its sensor is written only when its reading changes."""
    hass = sim_hass
    writes = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: writes.append(event.data["new_state"].state)
        if event.data["entity_id"] == "sensor.smartpi_room_sim_living_temperature"
        else None,
    )
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.smartpi_room_sim_radiator"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.smartpi_room_sim_radiator").state == "on"
    assert hass.states.get("sensor.smartpi_room_sim_house_power").state == "1750"
    for _ in range(20):
        _advance(hass, 7.0)
        await hass.async_block_till_done()
    assert float(hass.states.get("sensor.smartpi_room_sim_living_temperature").state) > 19.0
    assert len(writes) == len(set(writes)) and writes == sorted(writes)


async def test_a_valve_takes_an_opening(sim_hass: HomeAssistant) -> None:
    hass = sim_hass
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.smartpi_room_sim_floor", "value": 40}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.data["smartpi_room_sim"].sim.applied["floor"] == pytest.approx(0.4)
    assert hass.states.get("number.smartpi_room_sim_floor").state == "40.0"


async def test_the_scenario_services(sim_hass: HomeAssistant) -> None:
    """Openings, devices, rooms and the outdoors are set by services, and reset puts them back."""
    hass = sim_hass
    await hass.services.async_call("smartpi_room_sim", "set_opening", {"opening": "door", "state": "open"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.smartpi_room_sim_door").state == "on"
    await hass.services.async_call("smartpi_room_sim", "set_device", {"heater": "radiator", "mode": "unavailable"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.smartpi_room_sim_radiator").state == "unavailable"
    await hass.services.async_call("smartpi_room_sim", "set_room", {"room": "living", "temperature": 21.27}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.smartpi_room_sim_living_temperature").state == "21.3"
    await hass.services.async_call("smartpi_room_sim", "set_outdoor", {"mean": -4.0}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.smartpi_room_sim_outdoor_temperature").state == "-4.0"
    await hass.services.async_call("smartpi_room_sim", "reset", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.smartpi_room_sim_door").state == "off"
    assert hass.states.get("switch.smartpi_room_sim_radiator").state == "off"
    assert hass.states.get("sensor.smartpi_room_sim_living_temperature").state == "19.0"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("smartpi_room_sim", "set_room", {"room": "attic", "temperature": 20}, blocking=True)


async def test_the_boiler_switch_warms_the_water(sim_hass: HomeAssistant) -> None:
    hass = sim_hass
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.smartpi_room_sim_boiler"}, blocking=True)
    _advance(hass, 1800.0)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.smartpi_room_sim_boiler_water").state) >= 59.5


def test_a_reset_between_scenarios_keeps_what_vtherm_drives() -> None:
    """``reset(rooms=False)`` clears the scenario's inputs but keeps temperatures, heaters and
    the boiler as they are."""
    config = _boiler_room()
    config["openings"] = {"door": {}}
    config["links"] = []
    sim = _sim(config)
    sim.set_command("heater", 1.0)
    sim.set_boiler(True)
    sim.set_opening("door", True)
    sim.set_mode("heater", "ignore")
    sim.set_command("heater", 0.0)
    sim.set_outdoor(-10.0)
    sim.advance(1800.0)
    temperature = sim.temperature("room")
    sim.reset(rooms=False)
    assert sim.temperature("room") == temperature
    assert sim.boiler_on and not sim.is_open("door")
    assert sim.modes["heater"] == "ok" and sim.applied["heater"] == 0.0
    assert sim.outdoor_c(sim.t_s) == pytest.approx(5.0)
