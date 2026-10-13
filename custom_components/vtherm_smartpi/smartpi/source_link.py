"""Link between a lead thermostat and its second source in one Home Assistant.

The lead publishes the command it gives its source; the source reports each
cycle it starts. The link lives in ``hass.data`` and is keyed by the
thermostats' unique ids. Times are the monotonic clock's seconds, given by the
callers.

A source follows one lead only. It applies 0 when its lead's command is for
another HVAC mode (the lead is switched off, for example). A lead that publishes
nothing at all for a while no longer works: the source then regulates the zone
on its own until the commands come back. The source publishes the most power it
can give (its water factor), which the lead uses in its forecasts.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque

# Cycles kept per source, enough for the delay and lag of any emitter.
_CYCLES_KEPT = 512


@dataclass(frozen=True)
class SourceCommand:
    lead: str
    command: float
    issued_s: float
    hvac_mode: str | None


@dataclass(frozen=True)
class SourceCycle:
    start_s: float
    on_s: float
    duration_s: float
    # Mean power over the cycle (power at the reference water with water data).
    power: float
    switched: bool
    # Power of a switched actuator while it is on: its water factor (1 without water data).
    level: float = 1.0


class SourceLink:
    """Commands from leads to their sources, and the cycles the sources start."""

    def __init__(self) -> None:
        self._lead_of: dict[str, str] = {}
        self._commands: dict[str, SourceCommand] = {}
        self._cycles: dict[str, Deque[SourceCycle]] = {}
        # When each source last said it applies its lead's commands.
        self._active_s: dict[str, float] = {}
        # When each source was attached to its lead.
        self._attached_s: dict[str, float] = {}
        # Most power each source can give at full command (1 without water data).
        self._power_cap: dict[str, float] = {}

    def attach(self, lead: str, source: str, now_s: float = 0.0) -> bool:
        """Make ``source`` work for ``lead``; False when it already follows another lead."""
        current = self._lead_of.get(source)
        if current is not None and current != lead:
            return False
        for other_source, other_lead in list(self._lead_of.items()):
            if other_lead == lead and other_source != source:
                self.detach_source(other_source)
        if current != lead:
            self._attached_s[source] = now_s
        self._lead_of[source] = lead
        return True

    def detach_lead(self, lead: str) -> None:
        for source, owner in list(self._lead_of.items()):
            if owner == lead:
                self.detach_source(source)

    def detach_source(self, source: str) -> None:
        self._lead_of.pop(source, None)
        self._commands.pop(source, None)
        self._cycles.pop(source, None)
        self._active_s.pop(source, None)
        self._attached_s.pop(source, None)
        self._power_cap.pop(source, None)

    def lead_of(self, source: str) -> str | None:
        return self._lead_of.get(source)

    def source_of(self, lead: str) -> str | None:
        return next((source for source, owner in self._lead_of.items() if owner == lead), None)

    def publish_command(self, lead: str, command: float, now_s: float, hvac_mode: str | None) -> None:
        source = self.source_of(lead)
        if source is None:
            return
        self._commands[source] = SourceCommand(lead, min(max(float(command), 0.0), 1.0), now_s, hvac_mode)

    def command_for(self, source: str, now_s: float, max_age_s: float, hvac_mode: str | None) -> float:
        """Command the source applies now: its lead's, if fresh and for the same HVAC mode, else 0."""
        command = self._commands.get(source)
        if command is None or command.lead != self._lead_of.get(source):
            return 0.0
        if now_s - command.issued_s > max_age_s or command.hvac_mode != hvac_mode:
            return 0.0
        return command.command

    def report_cycle(self, source: str, cycle: SourceCycle) -> None:
        if source not in self._lead_of:
            return
        self._cycles.setdefault(source, deque(maxlen=_CYCLES_KEPT)).append(cycle)

    def cycles_since(self, source: str, after_s: float) -> list[SourceCycle]:
        """Cycles the source started after ``after_s``."""
        return [cycle for cycle in self._cycles.get(source, ()) if cycle.start_s > after_s]

    def lead_silent(self, source: str, now_s: float, max_age_s: float) -> bool:
        """The source's lead has published no command, for any HVAC mode, for ``max_age_s``.

        Counted from the attachment until the first command: a lead that just
        attached its source is not silent yet.
        """
        lead = self._lead_of.get(source)
        if lead is None:
            return False
        command = self._commands.get(source)
        if command is not None and command.lead == lead:
            last_s = command.issued_s
        else:
            last_s = self._attached_s.get(source, now_s)
        return now_s - last_s > max_age_s

    def set_power_cap(self, source: str, cap: float) -> None:
        """The most power ``source`` can give at full command now, 0..1."""
        if source in self._lead_of:
            self._power_cap[source] = min(max(float(cap), 0.0), 1.0)

    def power_cap(self, source: str) -> float:
        return self._power_cap.get(source, 1.0)

    def set_active(self, source: str, active: bool, now_s: float) -> None:
        """The source applies its lead's commands now (``active``), or no longer."""
        if source not in self._lead_of:
            return
        if active:
            self._active_s[source] = now_s
        else:
            self._active_s.pop(source, None)

    def is_active(self, source: str, now_s: float, max_age_s: float) -> bool:
        """The source has said it applies its lead's commands within ``max_age_s``."""
        seen_s = self._active_s.get(source)
        return seen_s is not None and now_s - seen_s <= max_age_s
