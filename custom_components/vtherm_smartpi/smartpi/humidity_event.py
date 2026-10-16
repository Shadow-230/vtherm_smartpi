"""Humidity events: a shower or cooking heats the room with heat SmartPI does not know.

Versatile Thermostat publishes the room's relative humidity (its humidity feature,
``current_humidity``). A quick rise marks heat from another source: while it lasts and
for a while after, SmartPI does not learn from the room and holds its integral, which
would learn the event as a smaller heating need (documentation/en/technical_doc.md, 4.7).

The thresholds come from eight weeks of two bathrooms and a kitchen (a shower rises
20-30 %RH within 2-16 min; the humidity falls back to a quarter of the rise 40-90 min
after its peak).
"""

from __future__ import annotations

from collections import deque
from math import isfinite

from .const import (
    HUMIDITY_EVENT_END_SHARE,
    HUMIDITY_EVENT_MAX_S,
    HUMIDITY_EVENT_RISE,
    HUMIDITY_EVENT_SETTLE_S,
    HUMIDITY_EVENT_WINDOW_S,
)

# The learning gate of an event and its settling (as the dependencies' gates).
GATE_HUMIDITY_EVENT = "humidity_event"

STATE_IDLE = "idle"
STATE_EVENT = "event"
STATE_SETTLING = "settling"


class HumidityEvent:
    """Follows the room's humidity and tells whether a humidity event runs now.

    An event starts when the humidity rises by at least HUMIDITY_EVENT_RISE within
    HUMIDITY_EVENT_WINDOW_S. It ends when the humidity has fallen below its level before
    the rise plus HUMIDITY_EVENT_END_SHARE of the rise, and at the latest after
    HUMIDITY_EVENT_MAX_S. HUMIDITY_EVENT_SETTLE_S of settling follow.
    """

    def __init__(self) -> None:
        self._history: deque[tuple[float, float]] = deque()
        self.state = STATE_IDLE
        self.humidity: float | None = None
        self.baseline: float | None = None
        self.peak: float | None = None
        self._started_s: float | None = None
        self._settled_s: float | None = None
        self.count = 0

    @property
    def active(self) -> bool:
        """An event or its settling runs now."""
        return self.state != STATE_IDLE

    def update(self, now_s: float, humidity: float | None) -> bool:
        """Take the humidity of this calculation; return whether an event is active."""
        usable = isinstance(humidity, (int, float)) and not isinstance(humidity, bool)
        value = float(humidity) if usable and isfinite(humidity) else None
        self.humidity = value
        if self.state == STATE_SETTLING and self._settled_s is not None and now_s >= self._settled_s:
            self._reset()
        if self.state == STATE_EVENT:
            self._follow_event(now_s, value)
        if value is None:
            # Without a reading nothing starts; an event ends by its own clock.
            return self.active
        while self._history and now_s - self._history[0][0] > HUMIDITY_EVENT_WINDOW_S:
            self._history.popleft()
        self._history.append((now_s, value))
        if self.state == STATE_IDLE:
            lowest = min(level for _, level in self._history)
            if value - lowest >= HUMIDITY_EVENT_RISE:
                self.state = STATE_EVENT
                self.baseline, self.peak = lowest, value
                self._started_s = now_s
                self.count += 1
        return self.active

    def _follow_event(self, now_s: float, value: float | None) -> None:
        if value is not None and value > (self.peak or value):
            self.peak = value
        rise = (self.peak or 0.0) - (self.baseline or 0.0)
        back = value is not None and value <= (self.baseline or 0.0) + HUMIDITY_EVENT_END_SHARE * rise
        if back or now_s - (self._started_s or now_s) >= HUMIDITY_EVENT_MAX_S:
            self.state = STATE_SETTLING
            self._settled_s = now_s + HUMIDITY_EVENT_SETTLE_S

    def _reset(self) -> None:
        self.state = STATE_IDLE
        self.baseline = self.peak = None
        self._started_s = self._settled_s = None
        # The settled room's levels start a new history: the event's tail is no rise.
        self._history.clear()

    def diagnostics(self) -> dict:
        return {
            "humidity": self.humidity,
            "state": self.state,
            "baseline": self.baseline,
            "peak": self.peak,
            "events": self.count,
        }
