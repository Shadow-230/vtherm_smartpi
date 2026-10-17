"""The reference governor during a humidity event (a shower or cooking).

The event holds the integral. The governor releases a braking episode only on
a cycle whose integral runs freely, so it waits for the event's end. A FF trim
window that holds the event's heat is rejected on both of the author's paths:
the causal window at a cycle's end and the periodic window.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.vtherm_smartpi.smartpi.ff_trim import CausalFFTrimResult
from custom_components.vtherm_smartpi.smartpi.humidity_event import GATE_HUMIDITY_EVENT
from tests.test_smartpi_humidity_event import MIN, _run, _shower


def _admissible() -> CausalFFTrimResult:
    return CausalFFTrimResult(
        admissible=True,
        reason="window_ready",
        correction=-0.01,
        target_trim=-0.01,
        mean_causal_power=0.04,
        mean_ff1=0.08,
        mean_temperature=20.0,
        mean_error=0.0,
        mean_slope_h=0.0,
        observed_hold_power=0.04,
        duration_s=1800.0,
        measurement_count=7,
        alignment_delay_s=120.0,
        power_coverage_ratio=1.0,
        transfer_eligible=False,
        transfer_reason="equilibrium",
        transfer_quality="valve_segmented_linear",
        observation_mode="stationary",
    )


def test_a_humidity_event_blocks_the_braking_release(monkeypatch) -> None:
    """The release check gets the held integral during the event and releases nothing."""
    clock, algo = _shower(monkeypatch)
    seen = []
    release = algo._try_release_terminal_braking

    def recording(**kwargs):
        result = release(**kwargs)
        seen.append((kwargs["integrator_hold"], result))
        return result

    monkeypatch.setattr(algo, "_try_release_terminal_braking", recording)
    # No hold of the governance's own (the cycle's regimes change in this short run).
    gains_and_ff = algo._apply_gains_and_ff
    monkeypatch.setattr(algo, "_apply_gains_and_ff", lambda *a, **kw: (gains_and_ff(*a, **kw)[0], False))
    clock.advance(MIN)
    _run(algo, temp=20.1)

    assert algo.ctl.last_integral_hold_source == GATE_HUMIDITY_EVENT
    assert seen == [(True, None)]


@pytest.mark.parametrize("path", ["cycle_end", "periodic"])
def test_a_humidity_event_keeps_its_window_out_of_the_feed_forward_trim(monkeypatch, path) -> None:
    """An admissible window from either observer path is rejected while the event lasts."""
    _clock, algo = _shower(monkeypatch)
    if path == "cycle_end":
        monkeypatch.setattr(algo._fftrim_observer, "try_complete_window", lambda **kw: _admissible())
        algo._try_complete_fftrim_window(deadtime_s=600.0, deadtime_reliable=True, allow_bumpless=True)
    else:
        periodic = algo._fftrim_periodic_observer
        window = SimpleNamespace(samples=(), deadtime_s=600.0)
        monkeypatch.setattr(periodic, "try_close_window", lambda **kw: window)
        monkeypatch.setattr(periodic, "complete_window", lambda *args, **kw: None)
        monkeypatch.setattr(algo._fftrim_observer, "evaluate_periodic_window", lambda *args, **kw: _admissible())
        algo._try_complete_periodic_fftrim_window(deadtime_s=600.0, deadtime_reliable=True)

    assert algo._last_fftrim_cycle_admissible is False
    assert algo._last_fftrim_reject_reason == GATE_HUMIDITY_EVENT
    assert algo._ff_trim.last_transaction is None
