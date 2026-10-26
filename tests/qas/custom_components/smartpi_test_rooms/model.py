"""The rooms' physics, without Home Assistant.

Each room is one thermal mass: ``dT/dt = gain * p - loss * (T - T_out)`` in degC/min, the model
SmartPI learns (``gain`` is its ``a``, ``loss`` its ``b``). ``p`` is the heater's output, 0 to 1:
what the thermostat asks, after the heater's delay and through its lag (an emitter that warms
and cools slowly). A water radiator gets heat only while the boiler is on: its input is the
valve's opening while the boiler is on, and 0 while it is off.

The outdoor temperature moves by +/- 0.2 degC around its mean, so its sensor never holds one value
for hours. The tick (7 s) and the outdoor period (2837 s) are not multiples of SmartPI's 60 s.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

STEP_S = 7.0
MAX_SUBSTEP_S = 10.0
OUTDOOR_PERIOD_S = 2837.0
OUTDOOR_SWING_C = 0.2


@dataclass
class RoomModel:
    temperature: float
    loss: float
    gain: float
    delay_s: float
    lag_min: float
    water: bool
    command: float = 0.0
    output: float = 0.0
    _times: list[float] = field(default_factory=lambda: [float("-inf")])
    _inputs: list[float] = field(default_factory=lambda: [0.0])

    def record(self, t_s: float, boiler_on: bool) -> None:
        """The heater's input from now on: the command, or 0 for a radiator without the boiler."""
        value = self.command if (boiler_on or not self.water) else 0.0
        if value == self._inputs[-1]:
            return
        if t_s == self._times[-1]:
            self._inputs[-1] = value
        else:
            self._times.append(t_s)
            self._inputs.append(value)

    def input_at(self, t_s: float) -> float:
        return self._inputs[bisect.bisect_right(self._times, t_s) - 1]

    def advance(self, t0_s: float, t1_s: float, outdoor_c: float) -> None:
        t_s = t0_s
        while t_s < t1_s - 1e-9:
            h_s = min(MAX_SUBSTEP_S, t1_s - t_s)
            h_min = h_s / 60.0
            delayed = self.input_at(t_s - self.delay_s)
            if self.lag_min > 0.0:
                self.output += (delayed - self.output) * (1.0 - math.exp(-h_min / self.lag_min))
            else:
                self.output = delayed
            self.temperature += h_min * (self.gain * self.output - self.loss * (self.temperature - outdoor_c))
            t_s += h_s


@dataclass
class Simulation:
    """The rooms and the boiler; ``t_s`` counts seconds from the start."""

    rooms: dict[str, RoomModel]
    outdoor_mean: float
    t_s: float = 0.0
    boiler_on: bool = False

    def outdoor_c(self, t_s: float) -> float:
        phase = (t_s / OUTDOOR_PERIOD_S) % 1.0
        return self.outdoor_mean + OUTDOOR_SWING_C * (4.0 * abs(phase - 0.5) - 1.0)

    def advance(self, t_s: float) -> None:
        if t_s <= self.t_s:
            return
        outdoor = self.outdoor_c(0.5 * (self.t_s + t_s))
        for model in self.rooms.values():
            model.advance(self.t_s, t_s, outdoor)
        self.t_s = t_s

    def set_command(self, room: str, value: float) -> None:
        model = self.rooms[room]
        model.command = min(max(float(value), 0.0), 1.0)
        model.record(self.t_s, self.boiler_on)

    def set_boiler(self, on: bool) -> None:
        self.boiler_on = bool(on)
        for model in self.rooms.values():
            model.record(self.t_s, self.boiler_on)

    @property
    def has_radiators(self) -> bool:
        return any(model.water for model in self.rooms.values())


def reading(value: float, resolution: float = 0.1) -> float:
    """A sensor's reading: rounded to its resolution."""
    return round(round(value / resolution) * resolution, 6)
