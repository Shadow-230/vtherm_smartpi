"""Heat of a second source in the zone, as a known input of the lead's model.

The source's applied command ``u_f`` reaches the zone after a transport delay
``L_f`` and a first-order lag ``tau_f``, the thermal mass of its emitter:

    x_f(t + dt) = u_d + (x_f(t) - u_d) * exp(-dt / tau_f),   u_d = u_f(t - L_f)

``x_f`` is in 0..1 of the source's full power and its heat is ``a_f * x_f``
(degC/min). The commands already sent make ``x_f`` known now and predictable
over a horizon: they keep arriving for ``L_f``, and the last one is assumed to
hold after that.

The emitter type gives the starting values of ``L_f`` and ``tau_f``; a custom
emitter brings its own.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque

EMITTER_ELECTRIC_FAST = "electric_fast"
EMITTER_RADIATOR = "radiator"
EMITTER_FLOOR_ELECTRIC = "floor_electric"
EMITTER_FLOOR_WATER = "floor_water"
EMITTER_CUSTOM = "custom"
EMITTERS = (
    EMITTER_RADIATOR,
    EMITTER_FLOOR_WATER,
    EMITTER_ELECTRIC_FAST,
    EMITTER_FLOOR_ELECTRIC,
    EMITTER_CUSTOM,
)

# Starting values of the transport delay and the lag, in minutes. They are
# typical orders of magnitude, to be refined with field data.
EMITTER_RESPONSE_MIN: dict[str, tuple[float, float]] = {
    EMITTER_ELECTRIC_FAST: (2.0, 3.0),
    EMITTER_RADIATOR: (5.0, 20.0),
    EMITTER_FLOOR_ELECTRIC: (10.0, 90.0),
    EMITTER_FLOOR_WATER: (30.0, 180.0),
}


@dataclass(frozen=True)
class SourceResponse:
    """How the source's command reaches the zone."""

    lag_s: float
    tau_s: float

    @classmethod
    def for_emitter(
        cls, emitter: str, lag_min: float | None = None, tau_min: float | None = None
    ) -> SourceResponse:
        """Response of an emitter type; a custom emitter needs its own values."""
        if emitter == EMITTER_CUSTOM:
            if lag_min is None or tau_min is None:
                raise ValueError("a custom emitter needs its delay and its lag")
            return cls(max(0.0, lag_min) * 60.0, max(0.0, tau_min) * 60.0)
        default_lag, default_tau = EMITTER_RESPONSE_MIN[emitter]
        return cls(default_lag * 60.0, default_tau * 60.0)

    @property
    def settle_s(self) -> float:
        """Characteristic time for a command change to show in the zone."""
        return self.lag_s + self.tau_s


class SourceHeat:
    """``x_f``: the source's applied command after its delay and its lag."""

    def __init__(self, response: SourceResponse) -> None:
        self._response = response
        # Applied commands as (time, command), in time order; the first one
        # holds from the start of the history.
        self._commands: Deque[tuple[float, float]] = deque()
        self._state_s: float | None = None
        self._state = 0.0

    @property
    def response(self) -> SourceResponse:
        return self._response

    def reset(self) -> None:
        self._commands.clear()
        self._state_s = None
        self._state = 0.0

    def record(self, now_s: float, command: float) -> None:
        """Note the command the source applies from ``now_s`` on.

        It replaces whatever was planned from ``now_s`` on, such as the end of a
        cycle that has been cut short.
        """
        command = min(max(float(command), 0.0), 1.0)
        while self._commands and self._commands[-1][0] >= now_s:
            self._commands.pop()
        if self._commands and self._commands[-1][1] == command:
            return
        if self._state_s is None:
            self._state_s = now_s
        self._commands.append((now_s, command))

    def record_cycle(
        self, start_s: float, on_s: float, duration_s: float, power: float, switched: bool, level: float = 1.0
    ) -> None:
        """Note a cycle starting at ``start_s``.

        A switched actuator runs at its ``level`` (its water factor, 1 without
        water data) for ``on_s`` from the start of the cycle; a valve holds
        ``power`` for the whole cycle.
        """
        if not switched:
            self.record(start_s, power)
            return
        self.record(start_s, level if on_s > 0.0 else 0.0)
        if 0.0 < on_s < duration_s:
            self.record(start_s + on_s, 0.0)

    def save_state(self) -> dict:
        return {"x_f": self._state}

    def load_state(self, state: dict, now_s: float) -> None:
        """Resume from a saved ``x_f``, as if it had been reached at ``now_s`` with the source off."""
        value = state.get("x_f") if isinstance(state, dict) else None
        if isinstance(value, (int, float)) and math.isfinite(value):
            self.reset()
            self._state = min(max(float(value), 0.0), 1.0)
            self._state_s = now_s

    def value(self, now_s: float) -> float:
        """``x_f`` at ``now_s``."""
        self._advance(now_s)
        self._prune()
        return self._state

    def at(self, now_s: float, offset_s: float) -> float:
        """``x_f`` expected ``offset_s`` after ``now_s``, with no new command."""
        state_s, state = self._evolve(self._state_s, self._state, now_s + max(0.0, offset_s))
        del state_s
        return state

    def forecast(self, now_s: float, offsets_s: list[float], command: float) -> list[float]:
        """``x_f`` expected at each offset after ``now_s`` if the source applies
        ``command`` from ``now_s`` on, after the commands already on the way."""
        trial = SourceHeat(self._response)
        trial._commands = deque(self._commands)
        trial._state_s, trial._state = self._state_s, self._state
        trial._advance(now_s)
        trial.record(now_s, command)
        values = []
        for offset_s in sorted(offsets_s):
            trial._advance(now_s + max(0.0, offset_s))
            values.append(trial._state)
        return values

    def _command_at(self, t_s: float) -> float:
        """Command in effect at ``t_s`` (0 before the first one)."""
        value = 0.0
        for change_s, command in self._commands:
            if change_s > t_s:
                break
            value = command
        return value

    def _evolve(self, state_s: float | None, state: float, end_s: float) -> tuple[float | None, float]:
        if state_s is None or end_s <= state_s:
            return state_s, state
        lag, tau = self._response.lag_s, self._response.tau_s
        cuts = sorted({end_s, *(t + lag for t, _c in self._commands if state_s < t + lag < end_s)})
        t_s = state_s
        for cut_s in cuts:
            delayed = self._command_at(t_s - lag)
            span = cut_s - t_s
            if tau <= 0.0:
                state = delayed
            else:
                state = delayed + (state - delayed) * math.exp(-span / tau)
            t_s = cut_s
        return t_s, state

    def _advance(self, now_s: float) -> None:
        self._state_s, self._state = self._evolve(self._state_s, self._state, now_s)

    def _prune(self) -> None:
        """Drop commands that can no longer reach the zone after the state time."""
        if self._state_s is None:
            return
        horizon = self._state_s - self._response.lag_s
        while len(self._commands) > 1 and self._commands[1][0] <= horizon:
            self._commands.popleft()
