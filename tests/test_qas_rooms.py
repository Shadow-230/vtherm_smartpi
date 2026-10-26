"""The QAS test rooms (tests/qas/custom_components/smartpi_test_rooms): their physics and their
Home Assistant entities."""

from __future__ import annotations

import math
import pathlib
import sys

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

QAS = pathlib.Path(__file__).resolve().parent / "qas"
if str(QAS) not in sys.path:
    sys.path.insert(0, str(QAS))

from custom_components.smartpi_test_rooms.model import (  # noqa: E402
    OUTDOOR_PERIOD_S,
    STEP_S,
    RoomModel,
    Simulation,
    reading,
)


def _sim(water: bool = False, delay_min: float = 0.0, lag_min: float = 0.0) -> Simulation:
    model = RoomModel(temperature=18.0, loss=0.001, gain=0.03, delay_s=delay_min * 60.0, lag_min=lag_min, water=water)
    return Simulation({"room": model}, outdoor_mean=5.0)


def _run(sim: Simulation, until_s: float) -> None:
    t = sim.t_s
    while t < until_s:
        t = min(until_s, t + STEP_S)
        sim.advance(t)


def test_a_heated_room_follows_the_model_smartpi_learns() -> None:
    """With its heater on, a room approaches T_out + gain/loss as exp(-loss * t), within the outdoor's +/- 0.2 degC."""
    sim = _sim()
    sim.set_command("room", 1.0)
    _run(sim, 7200.0)
    t_inf = 5.0 + 0.03 / 0.001
    expected = t_inf + (18.0 - t_inf) * math.exp(-0.001 * 120.0)
    assert sim.rooms["room"].temperature == pytest.approx(expected, abs=0.05)


def test_the_delay_holds_the_heat_back() -> None:
    off, on = _sim(delay_min=3.0), _sim(delay_min=3.0)
    on.set_command("room", 1.0)
    for sim in (off, on):
        _run(sim, 170.0)
    assert on.rooms["room"].temperature == pytest.approx(off.rooms["room"].temperature, abs=1e-12)
    for sim in (off, on):
        _run(sim, 900.0)
    assert on.rooms["room"].temperature > off.rooms["room"].temperature + 0.1


def test_the_lag_lets_the_heat_in_gradually() -> None:
    unheated, plain, lagged = _sim(), _sim(), _sim(lag_min=10.0)
    for sim in (plain, lagged):
        sim.set_command("room", 1.0)
    for sim in (unheated, plain, lagged):
        _run(sim, 600.0)
    temps = [sim.rooms["room"].temperature for sim in (unheated, lagged, plain)]
    assert temps == sorted(temps) and temps[0] < temps[1] < temps[2]


def test_a_radiator_heats_only_while_the_boiler_is_on() -> None:
    """The valve open with the boiler off gives no heat; the boiler on gives it, and off again stops it."""
    with_boiler, without = _sim(water=True), _sim(water=True)
    for sim in (with_boiler, without):
        sim.set_command("room", 1.0)
    with_boiler.set_boiler(True)
    for sim in (with_boiler, without):
        _run(sim, 1800.0)
    unheated = _sim(water=True)
    _run(unheated, 1800.0)
    assert without.rooms["room"].temperature == pytest.approx(unheated.rooms["room"].temperature, abs=1e-12)
    assert with_boiler.rooms["room"].temperature > without.rooms["room"].temperature + 0.3
    with_boiler.set_boiler(False)
    _run(with_boiler, 1800.0 + 600.0)
    assert with_boiler.rooms["room"].output == 0.0


def test_the_outdoor_moves_and_no_period_is_a_multiple_of_smartpis() -> None:
    sim = _sim()
    readings = {reading(sim.outdoor_c(t)) for t in range(0, int(OUTDOOR_PERIOD_S), 60)}
    assert min(readings) == pytest.approx(4.8) and max(readings) == pytest.approx(5.2)
    assert STEP_S % 60.0 != 0.0 and 60.0 % STEP_S != 0.0 and OUTDOOR_PERIOD_S % 60.0 != 0.0


@pytest.fixture
async def rooms_hass(hass: HomeAssistant):
    config = {
        "smartpi_test_rooms": {
            "outdoor": 3.0,
            "rooms": {
                "west": {"heater": "radiator", "temperature": 19.0, "delay_min": 0},
                "north": {"heater": "electric", "temperature": 19.0},
            },
        }
    }
    assert await async_setup_component(hass, "smartpi_test_rooms", config)
    await hass.async_block_till_done()
    yield hass


def _later(hass: HomeAssistant, seconds: float) -> None:
    hub = hass.data["smartpi_test_rooms"]
    hub._start -= seconds  # noqa: SLF001 — the test moves the rooms' clock on
    hub.update()


async def test_the_entities(rooms_hass: HomeAssistant) -> None:
    """Every entity is the test rooms', with what VTherm needs."""
    assert set(rooms_hass.states.async_entity_ids()) == {
        "sensor.smartpi_test_rooms_west_temperature",
        "sensor.smartpi_test_rooms_north_temperature",
        "sensor.smartpi_test_rooms_outdoor_temperature",
        "number.smartpi_test_rooms_west_valve",
        "switch.smartpi_test_rooms_north_heater",
        "switch.smartpi_test_rooms_boiler",
    }
    sensor = rooms_hass.states.get("sensor.smartpi_test_rooms_west_temperature")
    assert sensor.attributes["device_class"] == "temperature" and sensor.state == "19.0"


async def test_a_radiator_warms_its_room_with_the_boiler(rooms_hass: HomeAssistant) -> None:
    hass = rooms_hass
    await hass.services.async_call("number", "set_value", {"entity_id": "number.smartpi_test_rooms_west_valve", "value": 100}, blocking=True)
    _later(hass, 1800.0)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.smartpi_test_rooms_west_temperature").state) < 19.0
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.smartpi_test_rooms_boiler"}, blocking=True)
    before = float(hass.states.get("sensor.smartpi_test_rooms_west_temperature").state)
    _later(hass, 1800.0)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.smartpi_test_rooms_west_temperature").state) > before + 0.3
    assert hass.states.get("number.smartpi_test_rooms_west_valve").state == "100.0"


async def test_an_electric_heater_warms_its_room(rooms_hass: HomeAssistant) -> None:
    hass = rooms_hass
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.smartpi_test_rooms_north_heater"}, blocking=True)
    assert hass.states.get("switch.smartpi_test_rooms_north_heater").state == "on"
    _later(hass, 1800.0)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.smartpi_test_rooms_north_temperature").state) > 19.5
