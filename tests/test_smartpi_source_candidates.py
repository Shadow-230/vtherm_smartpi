"""Tests for the candidate responses of a second source (the time scales the zone fit chooses from)."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.const import SOURCE_FAST_TAU_MIN, SOURCE_SCALE_MULTIPLIERS
from custom_components.vtherm_smartpi.smartpi.source_candidates import SourceCandidates
from custom_components.vtherm_smartpi.smartpi.source_model import SourceHeat, SourceResponse

FLOOR = SourceResponse(1800.0, 10800.0)


def test_a_slow_source_is_stretched_around_its_emitter_s_response() -> None:
    candidates = SourceCandidates(FLOOR)

    assert [c.lag_s / FLOOR.lag_s for c in candidates.candidates] == pytest.approx(list(SOURCE_SCALE_MULTIPLIERS))
    assert [c.tau_s / FLOOR.tau_s for c in candidates.candidates] == pytest.approx(list(SOURCE_SCALE_MULTIPLIERS))
    assert candidates.current == FLOOR
    assert candidates.current_index == candidates.start_index


def test_a_source_without_delay_and_lag_gets_lags_to_choose_from() -> None:
    candidates = SourceCandidates(SourceResponse(0.0, 0.0))

    assert [c.lag_s for c in candidates.candidates] == [0.0] * len(SOURCE_FAST_TAU_MIN)
    assert [c.tau_s / 60.0 for c in candidates.candidates] == pytest.approx(list(SOURCE_FAST_TAU_MIN))
    assert candidates.current == SourceResponse(0.0, 0.0)


def test_each_candidate_rebuilds_the_heat_from_the_same_commands() -> None:
    candidates = SourceCandidates(FLOOR)
    candidates.record(0.0, 1.0)
    values = candidates.values(4 * 3600.0)

    for candidate, value in zip(candidates.candidates, values):
        alone = SourceHeat(candidate)
        alone.record(0.0, 1.0)
        assert value == pytest.approx(alone.value(4 * 3600.0))
    # A slower response has given less heat so far.
    assert values == tuple(sorted(values, reverse=True))


def test_the_selected_candidate_is_resumed_after_a_restart() -> None:
    candidates = SourceCandidates(FLOOR)
    candidates.select(7)
    candidates.record(0.0, 1.0)
    state = candidates.save_state()

    resumed = SourceCandidates(FLOOR)
    resumed.load_state(state, None, 0.0)
    assert resumed.current_index == 7

    unknown = SourceCandidates(FLOOR)
    unknown.load_state({"lag_s": 1.0, "tau_s": 2.0}, None, 0.0)
    unknown.load_state("broken", None, 0.0)
    unknown.select(99)
    assert unknown.current_index == unknown.start_index
