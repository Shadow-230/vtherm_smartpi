"""The simulated physics of the QAS rooms: each room a single thermal node (tests/qas only).

It imports nothing from Home Assistant. Temperatures are in degC and rates in degC/min. With a
node capacity of 1, a heater's gain reads directly as SmartPI's ``a`` (degC/min at full power)
and the room's loss to the outdoors as SmartPI's ``b`` (1/min): a room is the 1R1C model that
SmartPI identifies.

A heater's power reaches its room after a transport delay. A lagged heater then passes it through
a first-order lag, like an emitter with thermal mass: its output follows
``x(t+h) = u + (x(t) - u)*exp(-h/tau)``. A water emitter's power is scaled by the water that
reaches it (``water_factor``): no water, no heat.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Callable

MAX_STEP_S = 10.0

# A water radiator: exponent and reference water.
RADIATOR_EXPONENT = 1.3
RADIATOR_REFERENCE_C = 60.0
ROOM_REFERENCE_C = 20.0


def water_factor(water_c: float | None, temp_c: float) -> float:
    """Power of a water radiator relative to its reference water: ((water - T) / (ref - 20)) ** n.

    No water reaching the radiator (``None``) gives 0.
    """
    if water_c is None:
        return 0.0
    return (max(water_c - temp_c, 0.0) / (RADIATOR_REFERENCE_C - ROOM_REFERENCE_C)) ** RADIATOR_EXPONENT


class PowerHistory:
    """Piecewise-constant actuator power (0..1) over time."""

    def __init__(self, initial: float = 0.0) -> None:
        self._times: list[float] = [float("-inf")]
        self._values: list[float] = [initial]

    def set(self, t_s: float, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if value == self._values[-1]:
            return
        if t_s < self._times[-1]:
            raise ValueError("power history must be recorded in time order")
        if t_s == self._times[-1]:
            self._values[-1] = value
            return
        self._times.append(t_s)
        self._values.append(value)

    def value_at(self, t_s: float) -> float:
        return self._values[bisect.bisect_right(self._times, t_s) - 1]

    def change_times(self, start_s: float, end_s: float) -> list[float]:
        """Return the change instants strictly inside ``(start_s, end_s)``."""
        lo = bisect.bisect_right(self._times, start_s)
        hi = bisect.bisect_left(self._times, end_s)
        return self._times[lo:hi]


@dataclass
class DelayedPower:
    """Actuator power seen by the room after a pure transport delay."""

    history: PowerHistory
    deadtime_s: float = 0.0

    def value_at(self, t_s: float) -> float:
        return self.history.value_at(t_s - self.deadtime_s)

    def breakpoints(self, start_s: float, end_s: float) -> list[float]:
        shifted = self.history.change_times(start_s - self.deadtime_s, end_s - self.deadtime_s)
        return [t + self.deadtime_s for t in shifted]


@dataclass
class _Heater:
    node: int
    gain: float
    power: DelayedPower
    # Power factor of the emitter, factor(t_s, temp_c) (water heating); None: 1.
    factor: Callable[[float, float], float] | None = None
    tau_min: float = 0.0
    # Output of the lag, 0..1 of the heater's full power.
    output: float = 0.0

    def step(self, power: float, step_min: float) -> float:
        """Advance the lag over a step of constant input; return the step's mean output."""
        if self.tau_min <= 0.0:
            self.output = power
            return power
        decay = math.exp(-step_min / self.tau_min)
        mean = power + (self.output - power) * self.tau_min / step_min * (1.0 - decay)
        self.output = power + (self.output - power) * decay
        return mean


@dataclass
class ThermalNetwork:
    """Rooms, each linked to the outdoors and heated by delayed heaters."""

    names: list[str] = field(default_factory=list)
    capacities: list[float] = field(default_factory=list)
    temperatures: list[float] = field(default_factory=list)
    outdoor_links: list[tuple[int, float]] = field(default_factory=list)
    heaters: list[_Heater] = field(default_factory=list)

    def add_node(self, name: str, temperature: float, capacity: float = 1.0) -> int:
        self.names.append(name)
        self.capacities.append(float(capacity))
        self.temperatures.append(float(temperature))
        return len(self.names) - 1

    def index(self, name: str) -> int:
        return self.names.index(name)

    def temperature(self, name: str) -> float:
        return self.temperatures[self.index(name)]

    def link_outdoor(self, name: str, conductance: float) -> None:
        self.outdoor_links.append((self.index(name), float(conductance)))

    def add_heater(
        self,
        name: str,
        gain: float,
        power: DelayedPower,
        tau_min: float = 0.0,
        factor: Callable[[float, float], float] | None = None,
    ) -> _Heater:
        heater = _Heater(self.index(name), float(gain), power, factor, float(tau_min))
        self.heaters.append(heater)
        return heater

    def _derivative(self, temps: list[float], outdoor_c: float, heat: list[float]) -> list[float]:
        flows = list(heat)
        for node, conductance in self.outdoor_links:
            flows[node] += conductance * (outdoor_c - temps[node])
        return [flow / capacity for flow, capacity in zip(flows, self.capacities)]

    def _rk4(self, step_min: float, outdoor_c: float, heat: list[float]) -> None:
        temps = self.temperatures
        k1 = self._derivative(temps, outdoor_c, heat)
        k2 = self._derivative([t + 0.5 * step_min * k for t, k in zip(temps, k1)], outdoor_c, heat)
        k3 = self._derivative([t + 0.5 * step_min * k for t, k in zip(temps, k2)], outdoor_c, heat)
        k4 = self._derivative([t + step_min * k for t, k in zip(temps, k3)], outdoor_c, heat)
        self.temperatures = [
            t + step_min / 6.0 * (a + 2.0 * b + 2.0 * c + d)
            for t, a, b, c, d in zip(temps, k1, k2, k3, k4)
        ]

    def advance(self, start_s: float, end_s: float, outdoor: Callable[[float], float]) -> None:
        """Integrate from ``start_s`` to ``end_s``.

        Heater power is constant between its delayed change instants, so the interval is split
        there and integrated in steps of at most ``MAX_STEP_S``.
        """
        if end_s <= start_s:
            return
        cuts = {start_s, end_s}
        for heater in self.heaters:
            cuts.update(heater.power.breakpoints(start_s, end_s))
        points = sorted(cuts)
        for seg_start, seg_end in zip(points, points[1:]):
            powers = [heater.power.value_at(seg_start) for heater in self.heaters]
            t_s = seg_start
            while t_s < seg_end - 1e-9:
                step_s = min(MAX_STEP_S, seg_end - t_s)
                mid_s = t_s + 0.5 * step_s
                heat = [0.0] * len(self.names)
                for heater, power in zip(self.heaters, powers):
                    if heater.factor is not None:
                        # The water changes within a segment: its factor is taken at each step.
                        power *= heater.factor(mid_s, self.temperatures[heater.node])
                    heat[heater.node] += heater.gain * heater.step(power, step_s / 60.0)
                self._rk4(step_s / 60.0, outdoor(mid_s), heat)
                t_s += step_s
