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

WITHOUT_SB = "without Smart Boiler"
WITH_SB = "with Smart Boiler"
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
    set: str = WITHOUT_SB

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
) -> Callable[[Callable[[Any], Awaitable[None]]], Callable[[Any], Awaitable[None]]]:
    def register(fn: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"scenario {id} registered twice")
        SCENARIOS[id] = Scenario(id, fn, roles, minutes, twin, minimum, short_of)
        return fn

    return register


@dataclass(frozen=True)
class Planned:
    """A scenario of the set with Smart Boiler: listed, not built (the method is still open)."""

    id: str
    what: str
    twin: str
    set: str = WITH_SB
    built: bool = False


PLANNED: tuple[Planned, ...] = (
    Planned("QW1", "water data present for 48 h: source live, a within 0.5–1.2 of the truth and over 1.5 times the value learned without the data", "water with and without data"),
    Planned("QW2", "hot water with priority (dhw_active): φ = 0, command and integral held", "hot water"),
    Planned("QW3", "data missing for 2 h, then for good: the long average kept, then φ = 1 and the repair water_data_gone once; its fix resets the model", "gaps; test_config_flow_heating_type.py"),
    Planned("QW4", "data appear after learning: the repair water_data_appeared once", "test_smartpi_water_handler.py"),
    Planned("QW5", "Smart Boiler's call (below_threshold, no_demand, activation_delay, boiler_fault): source smart_boiler, the hold as in QC2, the demand fields", "B2; test_smartpi_boiler_handler.py"),
    Planned("QW6", "a zone's floor on water from Smart Boiler: the additional source turns the main source's power into its own command with its own water data", "floor on water"),
    Planned("QW7", "thermostat not mapped, or Smart Boiler removed: lasting, φ = 1", "test_smartpi_water_input.py"),
    Planned("QW8", "electric heating type with Smart Boiler present: never reads Smart Boiler", "test_smartpi_water_handler.py"),
)  # fmt: skip
