"""SmartPI pauses learning while it calls for heat below the central boiler threshold (documentation/en/technical_doc.md, 3.7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

import custom_components.vtherm_smartpi.algo as algo_module
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.boiler_call import (
    VT_ACTIVE_COUNT_UNIQUE_ID,
    VT_ACTIVE_POWER_UNIQUE_ID,
    VT_COUNT_THRESHOLD_UNIQUE_ID,
    VT_DOMAIN,
    VT_POWER_THRESHOLD_UNIQUE_ID,
    central_boiler_threshold_reached,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from fakes.fake_thermostat_runtime import FakeThermostatRuntime
from helpers import force_smartpi_stable_mode


# --- reading Versatile Thermostat's central boiler threshold -------------------


def _vt_entity(hass, domain: str, unique_id: str, state: str, unit: str | None = None) -> None:
    entry = er.async_get(hass).async_get_or_create(domain, VT_DOMAIN, unique_id, suggested_object_id=unique_id)
    hass.states.async_set(entry.entity_id, state, {"unit_of_measurement": unit} if unit else {})


async def test_threshold_unknown_without_values(hass) -> None:
    assert central_boiler_threshold_reached(hass) is None


async def test_threshold_unknown_when_unavailable(hass) -> None:
    _vt_entity(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID, "unavailable")
    _vt_entity(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID, "3")

    assert central_boiler_threshold_reached(hass) is None


@pytest.mark.parametrize(
    ("count", "count_threshold", "reached"),
    [("3", "3", True), ("2", "3", False), ("5", "0", False)],
)
async def test_count_threshold(hass, count, count_threshold, reached) -> None:
    """Versatile Thermostat's rule: a threshold counts when it is above 0 and the count reaches it."""
    _vt_entity(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID, count)
    _vt_entity(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID, count_threshold)

    assert central_boiler_threshold_reached(hass) is reached


@pytest.mark.parametrize(
    ("power", "power_unit", "threshold", "threshold_unit", "reached"),
    [
        ("2000", "W", "3000", "W", False),
        ("3000", "W", "3000", "W", True),
        ("3.5", "kW", "3000", "W", True),
        ("2.5", "kW", "3", "kW", False),
        ("9000", "W", "0", "W", False),
    ],
)
async def test_power_threshold(hass, power, power_unit, threshold, threshold_unit, reached) -> None:
    _vt_entity(hass, "sensor", VT_ACTIVE_POWER_UNIQUE_ID, power, power_unit)
    _vt_entity(hass, "number", VT_POWER_THRESHOLD_UNIQUE_ID, threshold, threshold_unit)

    assert central_boiler_threshold_reached(hass) is reached


async def test_either_threshold_is_enough(hass) -> None:
    _vt_entity(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID, "1")
    _vt_entity(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID, "3")
    _vt_entity(hass, "sensor", VT_ACTIVE_POWER_UNIQUE_ID, "4000", "W")
    _vt_entity(hass, "number", VT_POWER_THRESHOLD_UNIQUE_ID, "3000", "W")

    assert central_boiler_threshold_reached(hass) is True


# --- SmartPI's learning ---------------------------------------------------------


def _smartpi() -> SmartPI:
    smartpi = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Boiler"
    )
    force_smartpi_stable_mode(smartpi)
    smartpi._committed_on_percent = 0.6
    return smartpi


def _calculate_twice(smartpi: SmartPI, monkeypatch) -> None:
    """Two calculations ten minutes apart, so that learning is fed."""
    now = [5000.0]
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    for _ in range(2):
        smartpi.calculate(
            target_temp=20.0, current_temp=19.0, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT
        )
        now[0] += 600.0


def test_pause_drops_the_observations_in_flight() -> None:
    smartpi = _smartpi()
    smartpi.learn_win._active = True
    smartpi._t_heat_episode_start = 100.0

    smartpi.set_boiler_learning_paused(True)

    assert smartpi.boiler_learning_paused is True
    assert smartpi.learn_win_active is False
    assert smartpi._t_heat_episode_start is None
    assert smartpi.dt_est.last_power == pytest.approx(0.6)


def test_no_learning_during_the_pause(monkeypatch) -> None:
    """Neither the room model nor the dead time is learned."""
    smartpi = _smartpi()
    smartpi.set_boiler_learning_paused(True)
    smartpi.learn_win.update = MagicMock(return_value=(0, 0))
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    smartpi.learn_win.update.assert_not_called()
    smartpi.dt_est.update.assert_not_called()


@pytest.mark.parametrize("paused", [True, False])
def test_dead_time_before_the_model_is_learned(paused, monkeypatch) -> None:
    """Before the model is learned, SmartPI regulates by hysteresis and still measures the dead time."""
    smartpi = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Boiler"
    )
    smartpi.set_boiler_learning_paused(paused)
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    assert smartpi.dt_est.update.called is not paused


def test_learning_runs_without_the_pause(monkeypatch) -> None:
    smartpi = _smartpi()
    smartpi.learn_win.update = MagicMock(return_value=(0, 0))
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    smartpi.learn_win.update.assert_called()
    smartpi.dt_est.update.assert_called()


def test_the_user_setting_is_untouched() -> None:
    """The user's pause and the boiler's pause are two separate causes."""
    smartpi = _smartpi()
    smartpi.set_learning_enabled(False)

    smartpi.set_boiler_learning_paused(True)
    smartpi.set_boiler_learning_paused(False)

    assert smartpi.learning_enabled is False
    assert smartpi._learning_active is False
    assert smartpi.save_state()["learning_enabled"] is False

    smartpi.set_learning_enabled(True)

    assert smartpi._learning_active is True


def test_after_the_pause_learning_waits_for_the_room_dead_time(monkeypatch) -> None:
    """As after any start of heating, SmartPI does not learn during the room's dead time."""
    smartpi = _smartpi()
    smartpi.dt_est.deadtime_heat_s = 1200.0
    smartpi.dt_est.deadtime_heat_reliable = True
    smartpi._t_heat_episode_start = 100.0
    smartpi.set_boiler_learning_paused(True)
    smartpi._update_deadtime_episode_status(0.6, VThermHvacMode_HEAT, 200.0)

    smartpi.set_boiler_learning_paused(False)
    smartpi._update_deadtime_episode_status(0.6, VThermHvacMode_HEAT, 5000.0)

    assert smartpi._t_heat_episode_start == 5000.0
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: 5600.0)
    assert smartpi.in_deadtime_window is True
    smartpi.update_learning(dt_min=10.0, current_temp=19.0, ext_temp=5.0, u_active=0.6, hvac_mode=VThermHvacMode_HEAT)
    assert smartpi.est.learn_last_reason == "skip: deadtime window"

    monkeypatch.setattr(algo_module.time, "monotonic", lambda: 6300.0)
    assert smartpi.in_deadtime_window is False


def test_regulation_is_unchanged(monkeypatch) -> None:
    """SmartPI still sees the room cool and keeps calling for heat."""
    paused, normal = _smartpi(), _smartpi()
    paused.set_boiler_learning_paused(True)

    _calculate_twice(paused, monkeypatch)
    _calculate_twice(normal, monkeypatch)

    assert paused.on_percent == pytest.approx(normal.on_percent)
    assert paused.on_percent > 0.0
    assert paused.ctl.integral == pytest.approx(normal.ctl.integral)


# --- the handler ----------------------------------------------------------------


def _handler(counted: bool = True) -> SmartPIHandler:
    thermostat = FakeThermostatRuntime()
    thermostat.is_used_by_central_boiler = counted
    handler = SmartPIHandler(thermostat)
    with patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"):
        handler.init_algorithm()
    return handler


async def _control(handler: SmartPIHandler, reached: bool | None, power: float) -> SmartPI:
    algo = handler._thermostat.prop_algorithm
    algo._committed_on_percent = power
    with (
        patch("custom_components.vtherm_smartpi.handler.central_boiler_threshold_reached", return_value=reached),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"),
        patch.object(handler, "_async_save", new_callable=AsyncMock),
    ):
        await handler.control_heating(force=False)
    return algo


async def test_handler_pauses_learning_below_the_threshold() -> None:
    algo = await _control(_handler(), reached=False, power=0.5)

    assert algo.boiler_learning_paused is True


@pytest.mark.parametrize(
    ("counted", "reached", "power"),
    [
        (False, False, 0.5),  # Versatile Thermostat does not count it for the boiler
        (True, False, 0.0),  # SmartPI does not call for heat
        (True, True, 0.5),  # the threshold is reached
        (True, None, 0.5),  # the threshold cannot be read
    ],
)
async def test_handler_does_not_pause(counted, reached, power) -> None:
    algo = await _control(_handler(counted), reached=reached, power=power)

    assert algo.boiler_learning_paused is False


async def test_handler_resumes_learning_when_the_threshold_is_reached() -> None:
    handler = _handler()
    await _control(handler, reached=False, power=0.5)

    algo = await _control(handler, reached=True, power=0.5)

    assert algo.boiler_learning_paused is False
