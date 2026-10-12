"""Known inputs of the zone model and their composition for the control law.

SmartPI models a zone as ``dT/dt = a*u - b*(T - T_ext)``. Known inputs extend it:

    dT/dt = a*u - b*(T - T_ext) + sum_i g_i*(T_i - T) + sum_j q_j

An exchange input ``i`` couples the zone to another temperature ``T_i`` through
a conductance ``g_i`` (1/min); a heat input ``j`` adds ``q_j`` (degC/min, either
sign) directly. The composition brings this back to the form the control law
already uses:

    b_eff = b + sum_i g_i
    T_ref = (b*T_ext + sum_i g_i*T_i) / b_eff + sum_j q_j / b_eff

so feed-forward, prediction, trajectory, twin and near-band thresholds use
``(b_eff, T_ref)`` through their existing loss coefficient and outdoor
temperature arguments. Each input has a value now and may carry a trajectory
over a horizon; without one it stays constant. Without inputs the composition
returns the raw values unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .const import KNOWN_HEAT_MAX_SHIFT_C, KNOWN_INPUTS_B_EFF_MIN, clamp


def _interpolate(points: Sequence[tuple[float, ...]], offset_min: float) -> tuple[float, ...]:
    """Linear interpolation over ``(offset, *values)`` points sorted by offset."""
    if offset_min <= points[0][0]:
        return points[0][1:]
    for before, after in zip(points, points[1:]):
        if offset_min <= after[0]:
            span = after[0] - before[0]
            weight = 0.0 if span <= 0.0 else (offset_min - before[0]) / span
            return tuple(b + weight * (a - b) for b, a in zip(before[1:], after[1:]))
    return points[-1][1:]


@dataclass(frozen=True)
class ExchangeInput:
    """Heat exchange with another temperature: ``g * (T_other - T)`` in degC/min."""

    key: str
    conductance: float
    temperature: float
    # Future values as (offset_min, conductance, temperature), offsets increasing.
    trajectory: tuple[tuple[float, float, float], ...] = ()

    def at(self, offset_min: float = 0.0) -> tuple[float, float]:
        if offset_min <= 0.0 or not self.trajectory:
            return self.conductance, self.temperature
        points = ((0.0, self.conductance, self.temperature), *self.trajectory)
        conductance, temperature = _interpolate(points, offset_min)
        return conductance, temperature


@dataclass(frozen=True)
class HeatInput:
    """Heat added directly to the zone, in degC/min (either sign)."""

    key: str
    rate: float
    # Future values as (offset_min, rate), offsets increasing.
    trajectory: tuple[tuple[float, float], ...] = ()

    def at(self, offset_min: float = 0.0) -> float:
        if offset_min <= 0.0 or not self.trajectory:
            return self.rate
        (rate,) = _interpolate(((0.0, self.rate), *self.trajectory), offset_min)
        return rate


@dataclass(frozen=True)
class KnownInputs:
    """Inputs the dependency layer hands to the algorithm for one calculation."""

    exchanges: tuple[ExchangeInput, ...] = ()
    heats: tuple[HeatInput, ...] = ()
    # Reason to pause the learning of a, b and dead times, or None.
    learning_gate: str | None = None
    # Loss coefficient fitted together with the exchanges, used by the control
    # law instead of the learned b when the learned b has absorbed them.
    loss_coefficient: float | None = None
    # Reaction to an open window at a connected neighbour ("turn_off", "frost",
    # "eco"), and the setpoint it imposes for "frost" and "eco".
    control_action: str | None = None
    action_setpoint: float | None = None
    # Per-item diagnostics prepared by the dependency layer.
    status: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class EffectiveModel:
    """Loss coefficient and reference temperature seen by the control law."""

    b: float
    ext_temp: float | None
    active: bool = False
    exchanges_now: tuple[tuple[float, float], ...] = field(default=(), repr=False)
    heat_rate: float = 0.0
    signature: tuple[str, ...] = ()

    @property
    def exchange_conductance(self) -> float:
        """Sum of the active exchange conductances (1/min)."""
        return sum(conductance for conductance, _t in self.exchanges_now)

    def known_rate(self, temperature: float | None) -> float:
        """Rate added by the known inputs at ``temperature`` (degC/min)."""
        if not self.active or temperature is None:
            return 0.0
        exchange = sum(g * (t_other - temperature) for g, t_other in self.exchanges_now)
        return exchange + self.heat_rate


def compose(
    b: float,
    ext_temp: float | None,
    inputs: KnownInputs | None,
    offset_min: float = 0.0,
) -> EffectiveModel:
    """Fold the known inputs into ``(b_eff, T_ref)`` at ``offset_min`` from now."""
    identity = EffectiveModel(b=b, ext_temp=ext_temp)
    if inputs is None or ext_temp is None or not math.isfinite(b):
        return identity
    loss = b

    exchanges = []
    for item in inputs.exchanges:
        conductance, temperature = item.at(offset_min)
        if conductance > 0.0 and math.isfinite(conductance) and math.isfinite(temperature):
            exchanges.append((item.key, conductance, temperature))
    heats = []
    for item in inputs.heats:
        rate = item.at(offset_min)
        if math.isfinite(rate) and rate != 0.0:
            heats.append((item.key, rate))
    if not exchanges and not heats:
        return identity
    if exchanges and inputs.loss_coefficient is not None and inputs.loss_coefficient > 0.0:
        loss = inputs.loss_coefficient

    b_eff = loss + sum(conductance for _key, conductance, _t in exchanges)
    if b_eff < KNOWN_INPUTS_B_EFF_MIN:
        return identity
    t_ref = (loss * ext_temp + sum(g * t for _key, g, t in exchanges)) / b_eff
    heat_rate = sum(rate for _key, rate in heats)
    shift = clamp(heat_rate / b_eff, -KNOWN_HEAT_MAX_SHIFT_C, KNOWN_HEAT_MAX_SHIFT_C)
    return EffectiveModel(
        b=b_eff,
        ext_temp=t_ref + shift,
        active=True,
        exchanges_now=tuple((g, t) for _key, g, t in exchanges),
        heat_rate=heat_rate,
        signature=tuple(sorted([key for key, _g, _t in exchanges] + [key for key, _r in heats])),
    )
