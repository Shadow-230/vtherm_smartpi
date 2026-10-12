"""Measurements taken on a finished simulation."""

from __future__ import annotations

from dataclasses import dataclass

from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase

from .runner import Simulation


def learning_finished(sim: Simulation) -> bool:
    """Return True once SmartPI has left its initial hysteresis (learning) phase."""
    return sim.algo.phase != SmartPIPhase.HYSTERESIS


@dataclass(frozen=True)
class Identification:
    """Learned model parameters relative to the simulated truth."""

    a_ratio: float
    b_ratio: float
    deadtime_error_min: float | None


def identification(sim: Simulation) -> Identification:
    algo = sim.algo
    deadtime_s = algo.dt_est.deadtime_heat_s
    return Identification(
        a_ratio=algo.est.a / sim.zone.a,
        b_ratio=algo.est.b / sim.zone.b,
        deadtime_error_min=(
            None if deadtime_s is None else deadtime_s / 60.0 - sim.zone.deadtime_min
        ),
    )

