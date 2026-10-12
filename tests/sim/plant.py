"""Linear thermal network for SmartPI simulations.

Temperatures are in degC and rates in degC/min. With a node capacity of 1, a heater
gain reads directly as SmartPI's ``a`` (degC/min at full power) and a link to the
outdoors reads as SmartPI's ``b`` (1/min), so a single-node zone is the exact 1R1C
model that SmartPI identifies.

A lagged heater passes its delayed power through a first-order lag before it
reaches the node, like an emitter with thermal mass (a heated floor): its
output follows ``x(t+h) = u + (x(t) - u)*exp(-h/tau)``.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Callable

MAX_STEP_S = 10.0


class PowerHistory:
    """Piecewise-constant actuator power (0..1) over virtual time."""

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

    def on_seconds(self, start_s: float, end_s: float) -> float:
        """Return the integral of power over ``[start_s, end_s]`` in seconds."""
        total = 0.0
        points = [start_s, *self.change_times(start_s, end_s), end_s]
        for seg_start, seg_end in zip(points, points[1:]):
            total += self.value_at(seg_start) * (seg_end - seg_start)
        return total


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


@dataclass(frozen=True)
class Schedule:
    """Open intervals of a connection, as (start_s, end_s) pairs."""

    intervals: tuple[tuple[float, float], ...] = ()

    def is_open(self, t_s: float) -> bool:
        return any(start <= t_s < end for start, end in self.intervals)

    def changes(self, start_s: float, end_s: float) -> list[float]:
        """Return the opening and closing instants strictly inside (start_s, end_s)."""
        return [t for interval in self.intervals for t in interval if start_s < t < end_s]


@dataclass
class _BoundaryLink:
    node: int
    conductance: float
    temperature: Callable[[float], float]
    schedule: Schedule | None


@dataclass
class _Heater:
    node: int
    gain: float
    power: DelayedPower


@dataclass
class _LaggedHeater:
    node: int
    gain: float
    power: DelayedPower
    tau_min: float
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
    """Nodes linked by conductances, to the outdoors and to delayed heaters."""

    names: list[str] = field(default_factory=list)
    capacities: list[float] = field(default_factory=list)
    temperatures: list[float] = field(default_factory=list)
    links: list[tuple[int, int, float]] = field(default_factory=list)
    outdoor_links: list[tuple[int, float]] = field(default_factory=list)
    boundary_links: list[_BoundaryLink] = field(default_factory=list)
    heaters: list[_Heater] = field(default_factory=list)
    lagged_heaters: list[_LaggedHeater] = field(default_factory=list)

    def add_node(self, name: str, temperature: float, capacity: float = 1.0) -> int:
        self.names.append(name)
        self.capacities.append(float(capacity))
        self.temperatures.append(float(temperature))
        return len(self.names) - 1

    def index(self, name: str) -> int:
        return self.names.index(name)

    def temperature(self, name: str) -> float:
        return self.temperatures[self.index(name)]

    def link(self, first: str, second: str, conductance: float) -> None:
        self.links.append((self.index(first), self.index(second), float(conductance)))

    def link_outdoor(self, name: str, conductance: float) -> None:
        self.outdoor_links.append((self.index(name), float(conductance)))

    def link_boundary(
        self,
        name: str,
        conductance: float,
        temperature: Callable[[float], float],
        schedule: Schedule | None = None,
    ) -> None:
        """Couple a node to a prescribed temperature, open always or by schedule."""
        self.boundary_links.append(_BoundaryLink(self.index(name), float(conductance), temperature, schedule))

    def add_heater(self, name: str, gain: float, power: DelayedPower) -> None:
        self.heaters.append(_Heater(self.index(name), float(gain), power))

    def add_lagged_heater(self, name: str, gain: float, power: DelayedPower, tau_min: float) -> _LaggedHeater:
        heater = _LaggedHeater(self.index(name), float(gain), power, float(tau_min))
        self.lagged_heaters.append(heater)
        return heater

    def _derivative(
        self,
        temps: list[float],
        outdoor_c: float,
        heat: list[float],
        boundaries: list[tuple[int, float, float]] = (),
    ) -> list[float]:
        flows = list(heat)
        for node, conductance, temperature in boundaries:
            flows[node] += conductance * (temperature - temps[node])
        for first, second, conductance in self.links:
            flow = conductance * (temps[second] - temps[first])
            flows[first] += flow
            flows[second] -= flow
        for node, conductance in self.outdoor_links:
            flows[node] += conductance * (outdoor_c - temps[node])
        return [flow / capacity for flow, capacity in zip(flows, self.capacities)]

    def _rk4(self, step_min: float, outdoor_c: float, heat: list[float], boundaries=()) -> None:
        temps = self.temperatures
        k1 = self._derivative(temps, outdoor_c, heat, boundaries)
        k2 = self._derivative([t + 0.5 * step_min * k for t, k in zip(temps, k1)], outdoor_c, heat, boundaries)
        k3 = self._derivative([t + 0.5 * step_min * k for t, k in zip(temps, k2)], outdoor_c, heat, boundaries)
        k4 = self._derivative([t + step_min * k for t, k in zip(temps, k3)], outdoor_c, heat, boundaries)
        self.temperatures = [
            t + step_min / 6.0 * (a + 2.0 * b + 2.0 * c + d)
            for t, a, b, c, d in zip(temps, k1, k2, k3, k4)
        ]

    def advance(self, start_s: float, end_s: float, outdoor: Callable[[float], float]) -> None:
        """Integrate from ``start_s`` to ``end_s``.

        Heater power is constant between its delayed change instants, so the
        interval is split there and integrated in steps of at most ``MAX_STEP_S``.
        """
        if end_s <= start_s:
            return
        cuts = {start_s, end_s}
        for heater in (*self.heaters, *self.lagged_heaters):
            cuts.update(heater.power.breakpoints(start_s, end_s))
        for link in self.boundary_links:
            if link.schedule is not None:
                cuts.update(link.schedule.changes(start_s, end_s))
        points = sorted(cuts)
        for seg_start, seg_end in zip(points, points[1:]):
            heat = [0.0] * len(self.names)
            for heater in self.heaters:
                heat[heater.node] += heater.gain * heater.power.value_at(seg_start)
            open_links = [
                link
                for link in self.boundary_links
                if link.schedule is None or link.schedule.is_open(seg_start)
            ]
            lagged_power = [heater.power.value_at(seg_start) for heater in self.lagged_heaters]
            t_s = seg_start
            while t_s < seg_end - 1e-9:
                step_s = min(MAX_STEP_S, seg_end - t_s)
                mid_s = t_s + 0.5 * step_s
                boundaries = [(link.node, link.conductance, link.temperature(mid_s)) for link in open_links]
                step_heat = list(heat)
                for heater, power in zip(self.lagged_heaters, lagged_power):
                    step_heat[heater.node] += heater.gain * heater.step(power, step_s / 60.0)
                self._rk4(step_s / 60.0, outdoor(mid_s), step_heat, boundaries)
                t_s += step_s
