"""Virtual time for SmartPI simulations.

``VirtualClock`` replaces the ``time`` and ``datetime`` references of the SmartPI
modules (never the asyncio clock), and ``EventQueue`` provides timers in the
shape of Home Assistant's ``async_call_later`` and ``async_track_time_interval``.
"""

from __future__ import annotations

import heapq
import importlib
import inspect
import itertools
import pkgutil
import sys
import time as _real_time
from datetime import datetime as _real_datetime
from datetime import timezone
from typing import Any, Awaitable, Callable

SIM_EPOCH = 1_767_225_600.0  # 2026-01-01T00:00:00Z
_MONOTONIC_ORIGIN = 100_000.0
SMARTPI_PACKAGE = "custom_components.vtherm_smartpi"


class _TimeModule:
    """Stand-in for the ``time`` module: virtual clocks, the rest from the real module."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock.time()

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def time_ns(self) -> int:
        return int(self._clock.time() * 1e9)

    def monotonic_ns(self) -> int:
        return int(self._clock.monotonic() * 1e9)

    perf_counter = monotonic
    perf_counter_ns = monotonic_ns

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_time, name)


def _datetime_class(clock: VirtualClock) -> type[_real_datetime]:
    """Return a ``datetime`` subclass whose ``now()`` follows the virtual clock."""

    class SimDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102 - mirrors datetime.now
            return _real_datetime.fromtimestamp(clock.time(), tz)

        @classmethod
        def today(cls):  # noqa: D102 - mirrors datetime.today
            return _real_datetime.fromtimestamp(clock.time())

        @classmethod
        def utcnow(cls):  # noqa: D102 - mirrors datetime.utcnow
            return _real_datetime.fromtimestamp(clock.time(), timezone.utc).replace(tzinfo=None)

    return SimDatetime


class VirtualClock:
    """Simulated wall and monotonic time, both advanced only by the simulation."""

    def __init__(self, start_wall: float = SIM_EPOCH) -> None:
        self._start_wall = start_wall
        self.elapsed_s = 0.0

    def time(self) -> float:
        return self._start_wall + self.elapsed_s

    def monotonic(self) -> float:
        return _MONOTONIC_ORIGIN + self.elapsed_s

    def now(self) -> _real_datetime:
        """Timezone-aware wall time, as Home Assistant passes to timer callbacks."""
        return _real_datetime.fromtimestamp(self.time(), timezone.utc)

    def time_module(self) -> _TimeModule:
        """Return a ``time`` module stand-in following this clock."""
        return _TimeModule(self)

    def advance_to(self, elapsed_s: float) -> None:
        if elapsed_s < self.elapsed_s:
            raise ValueError("virtual time cannot go backwards")
        self.elapsed_s = elapsed_s

    def install(self, monkeypatch, package: str = SMARTPI_PACKAGE) -> None:
        """Point every module of the package at this clock.

        All submodules are imported first, then each module-level ``time`` and
        ``datetime`` reference is replaced. A module that captured a clock
        function under another name, or that reaches the clock through the
        ``datetime`` module or Home Assistant's ``dt`` helpers, would escape the
        virtual clock, so that is rejected explicitly. Function-local imports
        cannot be patched and must not read the clock.
        """
        import datetime as datetime_module

        from homeassistant.util import dt as ha_dt

        root = importlib.import_module(package)
        for info in pkgutil.walk_packages(root.__path__, package + "."):
            importlib.import_module(info.name)

        time_stub = self.time_module()
        datetime_stub = _datetime_class(self)
        escaped = {
            _real_time.time,
            _real_time.monotonic,
            _real_time.time_ns,
            _real_time.monotonic_ns,
            _real_time.perf_counter,
            _real_time.perf_counter_ns,
        }
        for name, module in list(sys.modules.items()):
            if module is None or not (name == package or name.startswith(package + ".")):
                continue
            for attr, value in list(vars(module).items()):
                if value is _real_time:
                    monkeypatch.setattr(module, attr, time_stub)
                elif value is _real_datetime:
                    monkeypatch.setattr(module, attr, datetime_stub)
                elif value is datetime_module or value is ha_dt:
                    raise RuntimeError(f"{name}.{attr} reaches the clock outside the virtual clock")
                elif inspect.isbuiltin(value) and value in escaped:
                    raise RuntimeError(f"{name}.{attr} bypasses the virtual clock")


class EventQueue:
    """Timers in virtual time, fired in due order (ties in registration order)."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock
        self._heap: list[list[Any]] = []
        self._sequence = itertools.count()

    def call_later(
        self, delay_s: float, action: Callable[[Any], Awaitable[None] | None]
    ) -> Callable[[], None]:
        """Schedule ``action(now)`` after ``delay_s``; return a cancel callback."""
        entry = [self._clock.elapsed_s + max(0.0, float(delay_s)), next(self._sequence), action, True]
        heapq.heappush(self._heap, entry)

        def cancel() -> None:
            entry[3] = False

        return cancel

    def track_interval(
        self, interval_s: float, action: Callable[[Any], Awaitable[None] | None]
    ) -> Callable[[], None]:
        """Call ``action(now)`` every ``interval_s``; return a cancel callback."""
        cancel_next: list[Callable[[], None]] = []

        async def fire(now) -> None:
            cancel_next[0] = self.call_later(interval_s, fire)
            result = action(now)
            if inspect.isawaitable(result):
                await result

        cancel_next.append(self.call_later(interval_s, fire))
        return lambda: cancel_next[0]()

    def next_due(self) -> float | None:
        """Return the elapsed time of the next live timer, if any."""
        while self._heap and not self._heap[0][3]:
            heapq.heappop(self._heap)
        return self._heap[0][0] if self._heap else None

    async def run_due(self) -> None:
        """Fire every live timer due at or before the current virtual time."""
        while True:
            due = self.next_due()
            if due is None or due > self._clock.elapsed_s + 1e-9:
                return
            entry = heapq.heappop(self._heap)
            entry[3] = False
            result = entry[2](self._clock.now())
            if inspect.isawaitable(result):
                await result
