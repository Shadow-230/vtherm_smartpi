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



@dataclass(frozen=True)
class Tracking:
    """How the zone followed its setpoint over a period, and how the actuators worked."""

    mean_error_c: float
    max_above_c: float
    max_below_c: float
    lead_turn_ons: int
    source_turn_ons: int
    # Share of the heat delivered by the second source (0 without one).
    source_share: float


def tracking(sim: Simulation, start_h: float, end_h: float) -> Tracking:
    """Tracking error and actuator work between ``start_h`` and ``end_h``."""
    points = [p for p in sim.trace if start_h <= p.t_h < end_h]
    errors = [p.zone_c - sim.zone.setpoint_c for p in points]
    start_s, end_s = start_h * 3600.0, end_h * 3600.0
    lead_heat = sim.zone.a * sim.actuator.history.on_seconds(start_s, end_s)
    source_heat = 0.0
    source_turn_ons = 0
    if sim.source_actuator is not None:
        source_heat = sim.zone.source.a_f * sim.source_actuator.history.on_seconds(start_s, end_s)
        source_turn_ons = _turn_ons(sim.source_actuator.history, start_s, end_s)
    total = lead_heat + source_heat
    return Tracking(
        mean_error_c=sum(abs(e) for e in errors) / len(errors),
        max_above_c=max(errors),
        max_below_c=-min(errors),
        lead_turn_ons=_turn_ons(sim.actuator.history, start_s, end_s),
        source_turn_ons=source_turn_ons,
        source_share=source_heat / total if total > 0.0 else 0.0,
    )


def _turn_ons(history, start_s: float, end_s: float) -> int:
    count = 0
    previous = history.value_at(start_s)
    for t_s in history.change_times(start_s, end_s):
        value = history.value_at(t_s)
        if previous <= 0.0 < value:
            count += 1
        previous = value
    return count
