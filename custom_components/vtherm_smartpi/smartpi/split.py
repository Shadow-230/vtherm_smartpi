"""Split of the zone's heating between the lead thermostat and a second source.

The lead's SmartPI regulates the zone with its own command ``W``, exactly as it
would without a source. The source's command ``Z`` follows a slow allocation
loop towards the steady state the rule chooses. Each rule is a curve from
``(Z, W) = (0, 0)`` to ``(1, 1)`` that gives the lead's target average
``f(Z)`` for a source command ``Z``:

- ``together``: ``Z = ratio * W``; once one of them is at 100 %, the other one
  rises alone;
- ``source_first``: the source alone up to ``alone``; above it both rise and
  reach 100 % together, ``W = (Z - alone) / (1 - alone)``;
- ``lead_band``: the lead's average stays at ``band``.

The loop integrates ``error = average(D) - f(Z)``, where ``D`` is the lead's
demand before its 0..100 % limits: a lead working more than the curve asks
moves the source up, a lead working less moves it down. The unlimited demand
also covers both ends: above 100 % the lead alone cannot keep up and the source
rises as a reserve; below 0 the zone is warmer than the lead can correct by
stopping and the source comes down. The lead keeps regulating on its own; the
source's heat reaches it as a known input.

The loop's speed follows the source's response: a source that needs hours to
show its heat moves slowly, so that the two never pull against each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .const import (
    SPLIT_AVERAGE_FRACTION,
    SPLIT_AVERAGE_MIN_S,
    SPLIT_DEFAULT_ALONE,
    SPLIT_DEFAULT_BAND,
    SPLIT_DEFAULT_RATIO,
    SPLIT_DEMAND_MAX,
    SPLIT_DEMAND_MIN,
    SPLIT_MIN_FULL_RANGE_S,
    SPLIT_MIN_TIME_S,
    SPLIT_ROLES_REVERSED_FACTOR,
    SPLIT_SETTLE_FACTOR,
    SPLIT_SLOW_SOURCE_S,
)
from .source_model import SourceResponse

RULE_TOGETHER = "together"
RULE_SOURCE_FIRST = "source_first"
RULE_LEAD_BAND = "lead_band"
RULES = (RULE_TOGETHER, RULE_SOURCE_FIRST, RULE_LEAD_BAND)

REASON_SLOW_SOURCE = "slow_source_base_load"
REASON_SIMILAR_SPEED = "similar_speed"


def _unit(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


@dataclass(frozen=True)
class SplitRule:
    """A split rule and its parameter, all as fractions of full power."""

    rule: str
    ratio: float = SPLIT_DEFAULT_RATIO
    alone: float = SPLIT_DEFAULT_ALONE
    band: float = SPLIT_DEFAULT_BAND

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"unknown split rule {self.rule!r}")
        if self.ratio <= 0.0:
            raise ValueError("the ratio must be positive")

    def lead_target(self, source: float) -> float:
        """Lead's target average for the source command ``source`` (the rule's curve)."""
        source = _unit(source)
        if self.rule == RULE_TOGETHER:
            return min(1.0, source / self.ratio)
        if self.rule == RULE_SOURCE_FIRST:
            alone = _unit(self.alone)
            if alone >= 1.0:
                return 0.0
            return max(0.0, (source - alone) / (1.0 - alone))
        return _unit(self.band)


@dataclass(frozen=True)
class AllocationTiming:
    """Speed of the allocation loop."""

    # Integration time: an error held for this long moves the source by 100 %.
    time_constant_s: float
    # Fastest change of the source command (fraction per second).
    max_rate_per_s: float
    # Time constant of the lead's demand average.
    average_s: float

    @classmethod
    def for_source(cls, response: SourceResponse, gain_ratio: float = 1.0) -> AllocationTiming:
        """Timing for a source response; ``gain_ratio`` is the source's power over the lead's.

        A stronger source moves the lead more for the same command change, so
        its loop is slowed down in proportion to stay as stable.
        """
        settle_s = response.settle_s
        time_constant_s = max(SPLIT_MIN_TIME_S, SPLIT_SETTLE_FACTOR * settle_s) * max(1.0, gain_ratio)
        return cls(
            time_constant_s=time_constant_s,
            max_rate_per_s=1.0 / max(SPLIT_MIN_FULL_RANGE_S, settle_s),
            average_s=max(SPLIT_AVERAGE_MIN_S, SPLIT_AVERAGE_FRACTION * time_constant_s),
        )


class AllocationLoop:
    """Moves the source command so that the lead's average follows the rule's curve."""

    def __init__(self, rule: SplitRule, timing: AllocationTiming, command: float = 0.0) -> None:
        self.rule = rule
        self.timing = timing
        self.command = _unit(command)
        self.lead_average: float | None = None
        self.applied: float | None = None
        self._last_s: float | None = None

    def reset(self, command: float = 0.0) -> None:
        self.command = _unit(command)
        self.lead_average = None
        self.applied = None
        self._last_s = None

    def pause(self) -> None:
        """The loop does not run for a while: it keeps its command and average, and
        does not integrate over the pause when it runs again."""
        self._last_s = None

    def error(self) -> float:
        """Current error of the loop, ``average(D) - f(Z)``."""
        if self.lead_average is None:
            return 0.0
        applied = self.command if self.applied is None else self.applied
        return self.lead_average - self.rule.lead_target(applied)

    def update(
        self,
        now_s: float,
        lead_demand: float,
        hold_up: bool = False,
        hold_down: bool = False,
        applied: float | None = None,
    ) -> float:
        """Feed the lead's demand (before its 0..1 limits) at ``now_s``; return the source command.

        ``hold_up`` and ``hold_down`` stop the source from moving in that
        direction, for example up while the zone is already too warm.
        """
        lead_demand = min(max(float(lead_demand), SPLIT_DEMAND_MIN), SPLIT_DEMAND_MAX)
        self.applied = None if applied is None else _unit(applied)
        if self._last_s is None or self.lead_average is None:
            self._last_s = now_s
            if self.lead_average is None:
                self.lead_average = lead_demand
            return self.command
        dt = now_s - self._last_s
        if dt <= 0.0:
            return self.command
        self._last_s = now_s
        weight = 1.0 - math.exp(-dt / self.timing.average_s)
        self.lead_average += weight * (lead_demand - self.lead_average)
        rate = self.error() / self.timing.time_constant_s
        limit = self.timing.max_rate_per_s
        rate = min(max(rate, -limit), limit)
        if (rate > 0.0 and hold_up) or (rate < 0.0 and hold_down):
            rate = 0.0
        self.command = _unit(self.command + rate * dt)
        return self.command


@dataclass(frozen=True)
class Recommendation:
    """Rule suggested for a pair of emitters, and whether their roles look reversed."""

    rule: SplitRule
    reason: str
    roles_reversed: bool


def recommend(lead: SourceResponse, source: SourceResponse) -> Recommendation:
    """Default rule for a lead and a source with these responses.

    A slow source next to a faster lead carries the base load and leaves the
    lead a margin to correct quickly (``lead_band``); devices of similar speed
    share a common percentage (``together``). A lead clearly slower than its
    source is flagged: the quick corrections belong to the faster device.
    """
    roles_reversed = lead.settle_s > SPLIT_ROLES_REVERSED_FACTOR * source.settle_s
    if source.settle_s >= SPLIT_SLOW_SOURCE_S and not roles_reversed and lead.settle_s < source.settle_s:
        return Recommendation(SplitRule(RULE_LEAD_BAND), REASON_SLOW_SOURCE, roles_reversed)
    return Recommendation(SplitRule(RULE_TOGETHER), REASON_SIMILAR_SPEED, roles_reversed)
