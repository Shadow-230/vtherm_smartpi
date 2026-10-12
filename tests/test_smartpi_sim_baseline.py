"""SmartPI identifies a simulated zone whose model is known exactly.

The zone is the 1R1C model SmartPI itself assumes, heated through a relay with a
pure transport delay, so the learned ``a``, ``b`` and heating dead time can be
compared with the simulated truth.
"""

from __future__ import annotations

import itertools
import os

import pytest

from sim.metrics import identification, learning_finished
from sim.runner import Simulation, ZoneSpec
from sim.weather import Weather

LOCAL_ONLY = pytest.mark.skipif(
    os.environ.get("SMARTPI_SIM_LOCAL") != "1",
    reason="long simulation, run locally with SMARTPI_SIM_LOCAL=1",
)

# A sensor resolution of 0.01 degC isolates the estimator from quantization.
FINE_SENSOR_C = 0.01
# Learned a and b must match the truth within 5 %.
AB_TOLERANCE = 0.05
# The dead time is detected once the temperature has visibly risen; today the
# estimate comes 1-3 minutes late. The bound is symmetric so that a more precise
# estimator still passes.
DEADTIME_TOLERANCE_MIN = 4.0


async def _identify(monkeypatch, zone: ZoneSpec, weather: Weather, max_hours: float):
    sim = Simulation(monkeypatch, zone, weather)
    finished_at_h: list[float] = []

    def stop(s: Simulation) -> bool:
        if not finished_at_h and learning_finished(s):
            finished_at_h.append(s.clock.elapsed_s / 3600.0)
        return bool(finished_at_h)

    await sim.run_for(max_hours, stop=stop)
    return sim, finished_at_h


def _assert_identified(sim: Simulation) -> None:
    result = identification(sim)
    assert result.a_ratio == pytest.approx(1.0, abs=AB_TOLERANCE)
    assert result.b_ratio == pytest.approx(1.0, abs=AB_TOLERANCE)
    assert result.deadtime_error_min is not None
    assert abs(result.deadtime_error_min) <= DEADTIME_TOLERANCE_MIN


@pytest.mark.parametrize(
    "zone",
    [
        ZoneSpec(a=0.03, b=0.001, deadtime_min=10.0, sensor_resolution_c=FINE_SENSOR_C),
        ZoneSpec(a=0.05, b=0.0015, deadtime_min=20.0, sensor_resolution_c=FINE_SENSOR_C),
    ],
    ids=["radiator_10min", "slow_emitter_20min"],
)
async def test_smartpi_identifies_known_zone(monkeypatch, zone) -> None:
    """With a constant outdoor temperature SmartPI leaves its learning phase with the true model."""
    sim, finished_at_h = await _identify(monkeypatch, zone, Weather(base_c=5.0), max_hours=12.0)

    assert finished_at_h, "SmartPI did not finish its learning phase within 12 h"
    _assert_identified(sim)


async def test_learning_completes_with_a_coarse_sensor(monkeypatch) -> None:
    """A 0.1 degC sensor still lets SmartPI finish learning with a usable model.

    Quantization at slow temperature rates scatters the learned a and b (about
    -50 % to +25 % across zones), so only the order of magnitude is checked.
    """
    zone = ZoneSpec(a=0.03, b=0.001, deadtime_min=10.0, sensor_resolution_c=0.1)
    sim, finished_at_h = await _identify(monkeypatch, zone, Weather(base_c=5.0), max_hours=24.0)

    assert finished_at_h, "SmartPI did not finish its learning phase within 24 h"
    result = identification(sim)
    assert 0.4 <= result.a_ratio <= 1.6
    assert 0.4 <= result.b_ratio <= 1.6


@LOCAL_ONLY
@pytest.mark.parametrize(
    ("ab", "deadtime_min", "outdoor_c"),
    list(itertools.product([(0.03, 0.001), (0.05, 0.0015), (0.02, 0.0006)], [5.0, 20.0], [0.0, 10.0])),
)
async def test_identification_grid(monkeypatch, ab, deadtime_min, outdoor_c) -> None:
    """The identification holds across emitter speeds, dead times and outdoor temperatures."""
    zone = ZoneSpec(a=ab[0], b=ab[1], deadtime_min=deadtime_min, sensor_resolution_c=FINE_SENSOR_C)
    sim, finished_at_h = await _identify(monkeypatch, zone, Weather(base_c=outdoor_c), max_hours=24.0)

    assert finished_at_h
    _assert_identified(sim)
