"""The scenarios and their catalogue entries, registered where they are written.

Each scenario says, next to its code: its roles, its duration in real time, whether it runs
detached (longer than the bench's two-hour limit for a background command), whether it is in
the minimum set (``M``) and its in-process counterpart. The catalogue in tests/qas/README.md
lists the same; a test checks they agree (tests/test_qas_pack.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

DETACHED_AFTER_MIN = 120  # the bench stops background commands after two hours


@dataclass(frozen=True)
class Scenario:
    id: str
    fn: Callable[[Any], Awaitable[None]]
    roles: tuple[str, ...]
    minutes: int
    twin: str
    minimum: bool = False

    @property
    def detached(self) -> bool:
        return self.minutes > DETACHED_AFTER_MIN

    @property
    def doc(self) -> str:
        return (self.fn.__doc__ or "").strip()


SCENARIOS: dict[str, Scenario] = {}


def scenario(
    id: str,  # noqa: A002 — the catalogue's column
    roles: tuple[str, ...],
    minutes: int,
    twin: str,
    *,
    minimum: bool = False,
) -> Callable[[Callable[[Any], Awaitable[None]]], Callable[[Any], Awaitable[None]]]:
    def register(fn: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"scenario {id} registered twice")
        SCENARIOS[id] = Scenario(id, fn, roles, minutes, twin, minimum)
        return fn

    return register
