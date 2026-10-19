"""The scenarios and their catalogue entries, registered where they are written.

Each scenario says, next to its code: its roles, its duration in real time, whether it runs
detached (longer than the bench's two-hour limit for a background command), whether it is in the
minimum set (``M``) or the learned set (``L``), its in-process twin, which long scenario it is the
short variant of, what it does to SmartPI's learned state (``State``) and the phase SmartPI must
be in for it to run (``Phase``; the runner reports it NOT RUN otherwise). The catalogue in
documentation/en/testing.md (the QAS section) lists the same; a test checks they agree
(tests/test_qas_pack.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

WITHOUT_WATER = "without water data"
WITH_WATER = "with water data"
DETACHED_AFTER_MIN = 120  # the bench stops background commands after two hours

# SmartPI's phases, as its diagnostics publish them (control.phase; smartpi/const.py SmartPIPhase).
HYSTERESIS = "Hysteresis"
STABLE = "Stable"
CALIBRATION = "Calibration"

# What a scenario may change in SmartPI's learned state (State.changes).
RESET = "reset"  # the learned model reset (a service, a form's reset, a repair's fix)
SEED = "seed"  # a learned state loaded by the helpers (seed_state)
CALIBRATED = "calibration"  # a calibration of the dead times forced
HEATING_TYPE = "heating type"
ZONE = "zone"  # the zone set up again, moved or removed
ZONE_MODEL = "zone model"  # the zone's learned model dropped
NEIGHBOURS = "neighbours"  # the neighbours set up again (their learned exchanges)
PAUSED = "learning paused"  # left paused for the next step
CHANGES = (RESET, SEED, CALIBRATED, HEATING_TYPE, ZONE, ZONE_MODEL, NEIGHBOURS, PAUSED)


@dataclass(frozen=True)
class State:
    """What a scenario does to SmartPI's learned state: nothing (it keeps it), or the changes it
    makes, and whether it puts the learned state it found back at its end."""

    changes: tuple[str, ...] = ()
    restored: bool = False

    def __post_init__(self) -> None:
        unknown = set(self.changes) - set(CHANGES)
        if unknown:
            raise ValueError(f"unknown changes {sorted(unknown)}")

    @property
    def keeps(self) -> bool:
        return not self.changes

    @property
    def label(self) -> str:
        if self.keeps:
            return "keeps"
        return "changes: " + ", ".join(self.changes) + (" (put back)" if self.restored else "")


KEEPS = State()


def changes(*what: str, restored: bool = False) -> State:
    return State(tuple(what), restored)


FRESH = "fresh"  # still learning: phase Hysteresis, as after the setup
LEARNED = "learned"  # phase Stable with a learned a and b
ANY = "any"


@dataclass(frozen=True)
class Phase:
    """The phase the role's SmartPI thermostats must be in for a scenario to run.

    ``thermostats``: their titles (those of the scenario's role count); none named: every SmartPI
    thermostat of the role but a zone's additional source, which learns nothing. ``zone_model``:
    with ``learned``, the zone's main source must also have published the zone's model.
    """

    need: str = ANY
    thermostats: tuple[str, ...] = ()
    zone_model: bool = False

    def __post_init__(self) -> None:
        if self.need not in (FRESH, LEARNED, ANY):
            raise ValueError(f"unknown phase need {self.need!r}")
        if self.zone_model and self.need != LEARNED:
            raise ValueError("the zone's model is asked of a learned phase only")

    @property
    def label(self) -> str:
        if self.need == LEARNED and self.zone_model:
            return "learned, zone model"
        return self.need

    def problem(self, lives: Mapping[str, Mapping[str, Any] | None], zone_main: str | None = None) -> str | None:
        """Why the thermostats' diagnostics (``live``, by title) do not meet the need, or None.

        ``zone_main``: the title of the zone's main source in the role, if it has a zone.
        """
        if self.need == ANY:
            return None
        problems = []
        for title, data in lives.items():
            control = (data or {}).get("control") or {}
            phase = control.get("phase")
            model = (data or {}).get("model") or {}
            if not data or phase is None:
                problems.append(f"{title}: no SmartPI diagnostics")
            elif phase == CALIBRATION:
                problems.append(f"{title}: phase {phase} (a calibration runs)")
            elif self.need == FRESH and phase != HYSTERESIS:
                problems.append(f"{title}: phase {phase}, the scenario needs a fresh SmartPI (set the instance up again)")
            elif self.need == LEARNED and (phase != STABLE or model.get("a") is None or model.get("b") is None):
                problems.append(f"{title}: phase {phase}, the scenario needs a learned SmartPI (run QL0 first)")
            elif self.need == LEARNED and self.zone_model and title == zone_main:
                fit = (((data.get("dependencies") or {}).get("second_source") or {}).get("zone_fit")) or {}
                if not fit.get("published"):
                    problems.append(f"{title}: the zone's model is not published (run QL0 first)")
        return "; ".join(problems) or None


@dataclass(frozen=True)
class Scenario:
    id: str
    fn: Callable[[Any], Awaitable[None]]
    roles: tuple[str, ...]
    minutes: int
    twin: str
    minimum: bool = False
    short_of: tuple[str, ...] = ()
    built: bool = True
    set: str = WITHOUT_WATER
    state: State = KEEPS
    phase: Phase = Phase()
    learned_set: bool = False

    @property
    def run_set(self) -> str:
        """The set it is run in: the minimum set M (from a fresh setup), the learned set L, or none."""
        return "M" if self.minimum else ("L" if self.learned_set else "—")

    @property
    def detached(self) -> bool:
        return self.minutes > DETACHED_AFTER_MIN

    @property
    def doc(self) -> str:
        return (self.fn.__doc__ or "").strip()


SCENARIOS: dict[str, Scenario] = {}


def scenario(
    id: str,
    roles: tuple[str, ...],
    minutes: int,
    twin: str,
    *,
    minimum: bool = False,
    short_of: tuple[str, ...] = (),
    set: str = WITHOUT_WATER,  # noqa: A002 — the catalogue's column
    state: State = KEEPS,
    phase: Phase = Phase(),
    learned_set: bool = False,
) -> Callable[[Callable[[Any], Awaitable[None]]], Callable[[Any], Awaitable[None]]]:
    def register(fn: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"scenario {id} registered twice")
        if minimum and learned_set:
            raise ValueError(f"scenario {id}: in the minimum set or the learned set, not both")
        SCENARIOS[id] = Scenario(
            id, fn, roles, minutes, twin, minimum, short_of, set=set, state=state, phase=phase, learned_set=learned_set
        )
        return fn

    return register


@dataclass(frozen=True)
class Planned:
    """A scenario listed in the catalogue but not built yet."""

    id: str
    what: str
    twin: str
    set: str = WITH_WATER
    built: bool = False


PLANNED: tuple[Planned, ...] = ()
