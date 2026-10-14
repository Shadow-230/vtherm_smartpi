"""Whether the boiler heats a zone that calls for heat (documentation/en/water_input.md, section 4).

A boiler fired only above a start threshold (zones calling, their total power,
the widest valve opening) or after an activation delay leaves a zone under it
calling for heat that does not come. Taking its command for heat, SmartPI would
learn a weaker emitter and a wrong dead time. The boiler's call comes from the
source chosen in the thermostat's options (heating_inputs.py): Home Assistant
entities, a provider (an integration that implements SmartPI's contract), or
Versatile Thermostat's own central boiler.

A boiler started within BOILER_CALL_GRACE_S of the zone's call answers it, as
an on-demand boiler does (VT's activation delay is at most 10 min): nothing
changes. Once the boiler has not been called for longer while the zone calls,
no heat comes: the zone is held, and SmartPI's learning sees the power 0.

Around a hold the emitter is out of step with the zone's command, while the
command itself does not change (the valve stays open): when the boiler stops,
the circuit's pump stops a few minutes later and the emitter gives the heat it
holds; when the boiler starts again, its water has to reach the emitter and warm
it. Learning waits the emitter's settling time (its delay and three lags, from
the heating type) from the start of the zone's unanswered call, and again from
the boiler's start. The boiler's start after a hold is no step of the zone's
command either: its delay is the water's, not the valve's, so SmartPI's dead
time does not learn from it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import BOILER_CALL_GRACE_S
from .source_model import EMITTER_RADIATOR, EMITTER_RESPONSE_MIN

SOURCE_VTHERM = "vtherm_central_boiler"

# The reason given for a boiler that is not called when its source does not say why.
REASON_NOT_CALLED = "not_called"

MODE_COOLING = "emitter_cooling"  # the boiler stopped under the calling zone: learning waits
MODE_HELD = "held"  # the zone calls, the boiler is not called: no heat comes
MODE_WARMING = "emitter_warming"  # the boiler started after a hold: learning waits
# The learning gate while the emitter cools or warms around a hold.
GATE_BOILER_SETTLING = "boiler_settling"


def settle_s(heating_type: str | None) -> float:
    """The emitter's settling time: its delay and three lags, from its heating type.

    After three lags less than 5 % of the change is left.
    """
    lag_min, tau_min = EMITTER_RESPONSE_MIN.get(heating_type or "", EMITTER_RESPONSE_MIN[EMITTER_RADIATOR])
    return (lag_min + 3.0 * tau_min) * 60.0


@dataclass(frozen=True)
class BoilerReading:
    """The boiler's call as read for one calculation (``called`` None: not known).

    ``source``: the source chosen in the options (``entities``, ``provider:<name>``,
    ``vtherm_central_boiler``). ``demand``: the demand against the boiler's start
    criteria, for the diagnostics only (the ``demand_*`` and threshold fields given).
    """

    called: bool | None
    reason: str | None = None
    source: str | None = None
    demand: Mapping[str, Any] | None = None


class BoilerCall:
    """The boiler's call for one thermostat and what it means for learning."""

    def __init__(self) -> None:
        self.settle_s = settle_s(None)
        self.reading: BoilerReading | None = None
        # The source chosen in the options and where each of its signals comes from,
        # for the diagnostics (also while it gives no reading).
        self.source: str | None = None
        self.inputs: Mapping[str, Any] | None = None
        self.mode: str | None = None
        self._calling_since_s: float | None = None
        self._warming_until_s: float | None = None

    def configure(self, heating_type: str | None) -> None:
        self.settle_s = settle_s(heating_type)

    def set_reading(
        self,
        reading: BoilerReading | None,
        source: str | None = None,
        inputs: Mapping[str, Any] | None = None,
    ) -> None:
        self.reading = reading
        self.source = reading.source if reading is not None and reading.source else source
        self.inputs = inputs

    def update(
        self, calling: bool, heating: bool, now_s: float, grace_s: float = BOILER_CALL_GRACE_S
    ) -> str | None:
        """The learning mode now: ``emitter_cooling``, ``held``, ``emitter_warming`` or
        None (learning as usual).

        ``calling``: the zone's device has a command above 0; ``heating``: the HVAC
        mode is heat; ``grace_s``: how long the boiler may take to answer the call (a
        switched device is counted only while on: its cycle is added). A call not
        known counts as given: SmartPI learns as before, after the emitter warms if
        it was held.
        """
        called = self.reading.called if self.reading is not None else None
        if not heating or not calling:
            # The zone's own command is off: its emitter follows it, as usual.
            self._calling_since_s = self._warming_until_s = None
            self.mode = None
            return None
        if called is False:
            if self._calling_since_s is None:
                self._calling_since_s = now_s
            self._warming_until_s = None
            waited = now_s - self._calling_since_s
            # Stopping again while the emitter warms after a hold: no grace, it cools.
            if self.mode in (MODE_COOLING, MODE_HELD, MODE_WARMING) or waited >= grace_s:
                self.mode = MODE_HELD if waited >= self.settle_s else MODE_COOLING
            else:
                self.mode = None
            return self.mode
        self._calling_since_s = None
        if self.mode in (MODE_COOLING, MODE_HELD):
            # The boiler starts after a hold: its water reaches the emitter and warms it.
            self._warming_until_s = now_s + self.settle_s
        if self._warming_until_s is not None and now_s < self._warming_until_s:
            self.mode = MODE_WARMING
        else:
            self._warming_until_s = None
            self.mode = None
        return self.mode

    def diagnostics(self) -> dict | None:
        """The ``boiler`` block of the diagnostics (None without a source, a reading and a mode)."""
        reading = self.reading
        if reading is None and self.mode is None and self.source is None:
            return None
        block = {
            "source": self.source,
            "called": reading.called if reading is not None else None,
            "reason": reading.reason if reading is not None else None,
            "learning": self.mode,
        }
        if reading is not None and reading.demand:
            block["demand"] = dict(reading.demand)
        if self.inputs:
            block["inputs"] = {name: dict(signal) for name, signal in self.inputs.items()}
        return block
