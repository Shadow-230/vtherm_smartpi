"""Candidate responses of a second source, and the heat each would give.

The emitter type gives a starting delay ``L_f`` and lag ``tau_f``. The real ones
depend on the installation (the screed, the water, the pipes). Within 30-min
measurement windows the delay and the lag cannot be told apart, but their common
time scale can: the candidates stretch the starting response by
``SOURCE_SCALE_MULTIPLIERS`` (a source without delay and lag gets lags of
``SOURCE_FAST_TAU_MIN``). Each candidate rebuilds the source's heat ``x_f`` from
the same cycles; the zone's joint fit (``zone_fit.py``) chooses the candidate
that explains the zone best.
"""

from __future__ import annotations

import math
from typing import Any

from .const import SOURCE_FAST_TAU_MIN, SOURCE_SCALE_MULTIPLIERS
from .source_model import SourceHeat, SourceResponse


class SourceCandidates:
    """The source's heat under each candidate response; one of them is in use."""

    def __init__(self, start: SourceResponse) -> None:
        self.start = start
        if start.settle_s > 0.0:
            self.candidates: tuple[SourceResponse, ...] = tuple(
                SourceResponse(start.lag_s * m, start.tau_s * m) for m in SOURCE_SCALE_MULTIPLIERS
            )
        else:
            self.candidates = tuple(SourceResponse(0.0, minutes * 60.0) for minutes in SOURCE_FAST_TAU_MIN)
        self.heats = [SourceHeat(candidate) for candidate in self.candidates]
        self.start_index = min(
            range(len(self.candidates)),
            key=lambda i: abs(self.candidates[i].lag_s - start.lag_s) + abs(self.candidates[i].tau_s - start.tau_s),
        )
        self.current_index = self.start_index

    @property
    def current(self) -> SourceResponse:
        return self.candidates[self.current_index]

    @property
    def heat(self) -> SourceHeat:
        """The heat of the response in use."""
        return self.heats[self.current_index]

    def record(self, now_s: float, command: float) -> None:
        for heat in self.heats:
            heat.record(now_s, command)

    def record_cycle(
        self, start_s: float, on_s: float, duration_s: float, power: float, switched: bool, level: float = 1.0
    ) -> None:
        for heat in self.heats:
            heat.record_cycle(start_s, on_s, duration_s, power, switched, level)

    def values(self, now_s: float) -> tuple[float, ...]:
        """x_f of each candidate at ``now_s``."""
        return tuple(heat.value(now_s) for heat in self.heats)

    def select(self, index: int) -> None:
        if 0 <= index < len(self.candidates):
            self.current_index = index

    # --- persistence -------------------------------------------------------

    def save_state(self) -> dict[str, Any]:
        return {"lag_s": round(self.current.lag_s, 1), "tau_s": round(self.current.tau_s, 1)}

    def load_state(self, state: Any, heats_state: Any, now_s: float) -> None:
        """Resume the response in use and the commands on the way."""
        if isinstance(heats_state, dict):
            for heat in self.heats:
                heat.load_state(heats_state, now_s)
        if not isinstance(state, dict):
            return
        try:
            lag_s, tau_s = float(state["lag_s"]), float(state["tau_s"])
        except (KeyError, TypeError, ValueError):
            return
        for index, candidate in enumerate(self.candidates):
            if math.isclose(candidate.lag_s, lag_s, abs_tol=1.0) and math.isclose(candidate.tau_s, tau_s, abs_tol=1.0):
                self.current_index = index
                return
