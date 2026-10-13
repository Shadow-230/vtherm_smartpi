"""Tests for the second source's heat x_f: delay, lag, prediction and emitter types."""

from __future__ import annotations

import math

import pytest

from custom_components.vtherm_smartpi.smartpi.source_model import (
    EMITTER_CUSTOM,
    EMITTER_FLOOR_WATER,
    EMITTER_RADIATOR,
    EMITTER_RESPONSE_MIN,
    SourceHeat,
    SourceResponse,
)

LAG_S, TAU_S = 1800.0, 10800.0


def _brute_force(commands, response: SourceResponse, end_s: float, step_s: float = 1.0) -> float:
    """x_f integrated in small steps from the command list, for comparison."""
    x, t = 0.0, 0.0
    while t < end_s - 1e-9:
        delayed = 0.0
        for change_s, command in commands:
            if change_s <= t - response.lag_s:
                delayed = command
        if response.tau_s <= 0.0:
            x = delayed
        else:
            x += (delayed - x) * (1.0 - math.exp(-step_s / response.tau_s))
        t += step_s
    return x


def test_step_reaches_the_zone_after_the_delay_through_the_lag() -> None:
    heat = SourceHeat(SourceResponse(LAG_S, TAU_S))
    heat.record(0.0, 1.0)

    assert heat.value(LAG_S - 1.0) == 0.0
    assert heat.value(LAG_S + TAU_S) == pytest.approx(1.0 - math.exp(-1.0))
    assert heat.value(LAG_S + 5 * TAU_S) == pytest.approx(1.0, abs=0.01)


def test_fast_source_follows_the_delayed_command() -> None:
    heat = SourceHeat(SourceResponse(120.0, 0.0))
    heat.record(0.0, 0.4)
    heat.record(600.0, 0.9)

    assert heat.value(100.0) == 0.0
    assert heat.value(300.0) == 0.4
    assert heat.value(800.0) == 0.9


def test_varying_commands_match_a_fine_integration() -> None:
    response = SourceResponse(LAG_S, TAU_S)
    commands = [(0.0, 0.8), (3000.0, 0.2), (3100.0, 1.0), (9000.0, 0.0), (20000.0, 0.5)]
    heat = SourceHeat(response)
    for change_s, command in commands:
        heat.record(change_s, command)
        heat.value(change_s)

    assert heat.value(30000.0) == pytest.approx(_brute_force(commands, response, 30000.0), abs=1e-3)


def test_prediction_holds_the_last_command() -> None:
    """Commands already sent keep arriving for the delay; the last one holds after it."""
    response = SourceResponse(LAG_S, TAU_S)
    heat = SourceHeat(response)
    heat.record(0.0, 1.0)
    heat.record(3600.0, 0.0)

    predicted = heat.at(3600.0, 7200.0)
    later = SourceHeat(response)
    later.record(0.0, 1.0)
    later.record(3600.0, 0.0)

    assert predicted == pytest.approx(later.value(3600.0 + 7200.0))
    assert heat.value(3600.0) < predicted


def test_old_commands_are_dropped_without_changing_the_result() -> None:
    response = SourceResponse(600.0, 1200.0)
    heat = SourceHeat(response)
    commands = [(minute * 60.0, (minute % 7) / 6.0) for minute in range(0, 600, 10)]
    for change_s, command in commands:
        heat.record(change_s, command)
        heat.value(change_s)

    assert len(heat._commands) <= 3
    assert heat.value(36000.0) == pytest.approx(_brute_force(commands, response, 36000.0), abs=1e-3)


def test_commands_are_clamped() -> None:
    heat = SourceHeat(SourceResponse(0.0, 0.0))
    heat.record(100.0, 1.7)

    assert heat.value(200.0) == 1.0


def test_a_cycle_cut_short_replaces_the_rest_of_its_plan() -> None:
    """A switched cycle heats first; a new cycle started early replaces the planned rest."""
    heat = SourceHeat(SourceResponse(0.0, 0.0))
    heat.record_cycle(0.0, on_s=400.0, duration_s=600.0, power=0.66, switched=True)
    assert heat.at(0.0, 300.0) == 1.0
    assert heat.at(0.0, 500.0) == 0.0

    heat.record_cycle(200.0, on_s=0.0, duration_s=600.0, power=0.0, switched=True)
    assert heat.value(250.0) == 0.0

    valve = SourceHeat(SourceResponse(0.0, 0.0))
    valve.record_cycle(0.0, on_s=400.0, duration_s=600.0, power=0.66, switched=False)
    assert valve.value(500.0) == 0.66


def test_a_switched_cycle_on_water_heats_at_its_water_factor() -> None:
    """With water data a switched emitter on gives its water factor, not its full power."""
    heat = SourceHeat(SourceResponse(0.0, 0.0))
    heat.record_cycle(0.0, on_s=600.0, duration_s=600.0, power=0.4, switched=True, level=0.4)

    assert heat.at(0.0, 300.0) == pytest.approx(0.4)


def test_saved_heat_resumes_and_decays() -> None:
    heat = SourceHeat(SourceResponse(0.0, 3600.0))
    heat.record(0.0, 1.0)
    heat.value(7200.0)
    saved = heat.save_state()

    restored = SourceHeat(SourceResponse(0.0, 3600.0))
    restored.load_state(saved, now_s=100.0)

    assert restored.value(100.0) == pytest.approx(1.0 - math.exp(-2.0))
    assert restored.value(3700.0) == pytest.approx((1.0 - math.exp(-2.0)) * math.exp(-1.0))


def test_emitter_types_give_starting_values() -> None:
    radiator = SourceResponse.for_emitter(EMITTER_RADIATOR)
    floor = SourceResponse.for_emitter(EMITTER_FLOOR_WATER)

    lag_min, tau_min = EMITTER_RESPONSE_MIN[EMITTER_FLOOR_WATER]
    assert (floor.lag_s, floor.tau_s) == (lag_min * 60.0, tau_min * 60.0)
    assert radiator.settle_s < floor.settle_s
    assert SourceResponse.for_emitter(EMITTER_CUSTOM, 12.0, 45.0) == SourceResponse(720.0, 2700.0)
    with pytest.raises(ValueError):
        SourceResponse.for_emitter(EMITTER_CUSTOM)
