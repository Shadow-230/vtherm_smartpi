"""Tests for the zone's joint fit: losses, both sources, the main source's delay and the additional
source's time scale, from simulated measurements."""

from __future__ import annotations

import math

import pytest

from custom_components.vtherm_smartpi.smartpi.const import ZONE_FIT_LEAD_DELAYS_MIN, ZONE_FIT_MIN_WINDOWS
from custom_components.vtherm_smartpi.smartpi.source_candidates import SourceCandidates
from custom_components.vtherm_smartpi.smartpi.source_model import SourceHeat, SourceResponse
from custom_components.vtherm_smartpi.smartpi.zone_fit import ZoneFit

A, B, A_F, K = 0.03, 0.001, 0.025, 0.002
FLOOR = SourceResponse(1800.0, 10800.0)
DELAYS_S = tuple(minutes * 60.0 for minutes in ZONE_FIT_LEAD_DELAYS_MIN)
STEP_S = 10.0


def _lead(t_s: float) -> float:
    """The main source: 10-min cycles whose duty follows a slow wave."""
    duty = 0.5 + 0.4 * math.sin(2.0 * math.pi * t_s / (9.0 * 3600.0))
    return 1.0 if (t_s % 600.0) < duty * 600.0 else 0.0


def _source(t_s: float) -> float:
    """The additional source: blocks of 5 h on and off, then 7 h."""
    hour = t_s / 3600.0
    return 1.0 if int(hour // (5.0 if hour < 30.0 else 7.0)) % 2 == 0 else 0.0


def _run(true_index: int, lead_delay_s: float, hours: float, neighbour: bool = False, sign: float = 1.0) -> ZoneFit:
    """Simulated zone fed to a fit; ``sign`` -1 for cooling, where the caller gives the sources'
    columns with a minus sign."""
    candidates = SourceCandidates(FLOOR)
    true_heat = SourceHeat(candidates.candidates[true_index])
    fit = ZoneFit(len(candidates.candidates), candidates.start_index, DELAYS_S)
    if neighbour:
        fit.set_neighbours(("hall",))

    def delayed_power(start_s: float, end_s: float, delay_s: float) -> float:
        steps = max(1, int(round((end_s - start_s) / STEP_S)))
        return sign * sum(_lead(start_s - delay_s + (i + 0.5) * STEP_S) for i in range(steps)) / steps

    temp, last_command = (19.0, None) if sign > 0 else (26.0, None)
    t_s = 0.0
    while t_s < hours * 3600.0:
        command = _source(t_s)
        if command != last_command:
            candidates.record(t_s, command)
            true_heat.record(t_s, command)
            last_command = command
        ext = (2.0 if sign > 0 else 32.0) + 3.0 * math.sin(2.0 * math.pi * t_s / 86400.0)
        hall = 22.0 + 2.0 * math.sin(2.0 * math.pi * t_s / (13.0 * 3600.0))
        if int(t_s) % 60 == 0:
            fit.add_sample(
                t_s,
                temp,
                ext,
                tuple(sign * value for value in candidates.values(t_s)),
                delayed_power,
                {"hall": hall - temp} if neighbour else None,
            )
        rate = sign * A * _lead(t_s - lead_delay_s) - B * (temp - ext) + sign * A_F * true_heat.value(t_s)
        if neighbour:
            rate += K * (hall - temp)
        temp += rate * STEP_S / 60.0
        t_s += STEP_S
    return fit


@pytest.mark.parametrize(("true_index", "delay_min"), [(4, 10.0), (7, 20.0), (2, 0.0)])
def test_the_fit_finds_the_sources_the_delay_and_the_time_scale(true_index, delay_min) -> None:
    fit = _run(true_index, delay_min * 60.0, 60.0)
    result = fit.result

    assert result is not None
    assert result.candidate == true_index
    assert result.lead_delay_s == delay_min * 60.0
    assert result.a == pytest.approx(A, rel=0.05)
    assert result.b == pytest.approx(B, rel=0.05)
    assert result.a_f == pytest.approx(A_F, rel=0.05)


def test_the_fit_learns_a_neighbour_s_exchange_with_the_sources() -> None:
    result = _run(4, 600.0, 60.0, neighbour=True).result

    assert result is not None
    assert dict(result.k)["hall"] == pytest.approx(K, rel=0.1)
    assert result.b == pytest.approx(B, rel=0.1)


def test_the_fit_waits_for_enough_windows() -> None:
    fit = _run(4, 600.0, ZONE_FIT_MIN_WINDOWS * 0.5 - 1.0)

    assert fit.windows < ZONE_FIT_MIN_WINDOWS
    assert fit.result is None


def test_the_fit_resumes_after_a_restart_and_drops_another_shape() -> None:
    fit = _run(4, 600.0, 40.0)
    state = fit.save_state()

    resumed = ZoneFit(fit._sources, 4, DELAYS_S)
    resumed.load_state(state)
    assert resumed.result == fit.result
    assert resumed.windows == pytest.approx(fit.windows, rel=1e-9)

    other = ZoneFit(fit._sources, 4, DELAYS_S[:-1])
    other.load_state(state)
    assert other.result is None
    assert other.windows == 0.0


def test_a_gap_between_calculations_starts_the_window_again() -> None:
    fit = ZoneFit(1, 0, (0.0,))

    def no_power(_start, _end, _delay):
        return 0.0

    fit.add_sample(0.0, 20.0, 5.0, (0.0,), no_power)
    fit.add_sample(600.0, 20.0, 5.0, (0.0,), no_power)
    # Three hours off: the next window starts at the first sample after the gap.
    assert not fit.add_sample(600.0 + 3 * 3600.0, 17.0, 5.0, (0.0,), no_power)
    assert fit._samples[0][0] == 600.0 + 3 * 3600.0
    assert fit.windows == 0.0


def test_an_empty_fit_saves_nothing_and_a_neighbour_s_error_is_kept() -> None:
    assert ZoneFit(10, 4, DELAYS_S).save_state() is None

    fit = _run(4, 600.0, 60.0, neighbour=True)
    resumed = ZoneFit(fit._sources, 4, DELAYS_S)
    resumed.load_state(fit.save_state())
    assert dict(resumed.result.k_se)["hall"] == pytest.approx(dict(fit.result.k_se)["hall"], rel=1e-6)
    assert dict(fit.result.k_se)["hall"] < 0.2 * K


def test_in_cooling_the_fitted_strengths_stay_positive() -> None:
    result = _run(4, 600.0, 60.0, sign=-1.0).result

    assert result is not None
    assert (result.candidate, result.lead_delay_s) == (4, 600.0)
    assert result.a == pytest.approx(A, rel=0.05)
    assert result.b == pytest.approx(B, rel=0.05)
    assert result.a_f == pytest.approx(A_F, rel=0.05)
