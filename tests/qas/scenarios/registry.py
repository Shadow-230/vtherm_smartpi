"""The scenarios and their catalogue entries, registered where they are written.

Each scenario says, next to its code: its roles, its duration in real time, whether it runs
detached (longer than the bench's two-hour limit for a background command), whether it is in the
minimum set, its in-process twin, and which long scenario it is the short variant of. The
catalogue in documentation/en/testing.md (the QAS section) lists the same; a test checks they
agree (tests/test_qas_pack.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

WITHOUT_WATER = "without water data"
WITH_WATER = "with water data"
DETACHED_AFTER_MIN = 120  # the bench stops background commands after two hours


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
) -> Callable[[Callable[[Any], Awaitable[None]]], Callable[[Any], Awaitable[None]]]:
    def register(fn: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"scenario {id} registered twice")
        SCENARIOS[id] = Scenario(id, fn, roles, minutes, twin, minimum, short_of, set=set)
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
