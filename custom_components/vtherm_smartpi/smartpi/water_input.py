"""Water input: the supply water of a thermostat's circuit, from Smart Boiler.

A unit of SmartPI's command brings the heat of a unit at the reference water
only. With weather-compensated water, a mixer, burner cycling or hot water with
priority, the water changes and so does the heat per unit of command. The
emitter power factor

    phi = min((max(T_supply - T_room, 0) / (T_ref - 20)) ** n, 1)

turns the command into power at the reference water: the controller works in
that power, the emitter gets ``power / phi`` and learning sees ``command * phi``.

Rules (documentation/en/water_input.md):

- the supply is averaged over ``WATER_AVERAGE_S``;
- hot water with priority (``heat_reason == "dhw_active"``) gives ``phi = 0``
  and holds the command and the integral;
- a factor below ``WATER_NO_HEAT`` without hot water (an idle boiler that fires
  on demand, water near the room) holds the integral only: the zone must still
  be able to call for heat;
- a momentary gap (``flow_unknown``, ``flow_stale``) uses the long-term average
  of the factor while heating; so do an unknown reason and no reading at all
  after data were seen, until ``WATER_NO_DATA_LASTING_S``, after which they
  count as lasting;
- a lasting gap (``not_mapped``, ``circuit_not_measured``, ``several_circuits``)
  and no data since the start use ``phi = 1``, as without the interface;
- an electric heating type does not use the water.

This module has no Home Assistant dependency: the handler reads Smart Boiler
and passes a ``WaterReading``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from .const import (
    WATER_AVERAGE_S,
    WATER_EMITTERS,
    WATER_FACTOR_MAX,
    WATER_LONG_AVERAGE_S,
    WATER_NO_DATA_LASTING_S,
    WATER_NO_HEAT,
    WATER_ROOM_REFERENCE_C,
)

REASONS_LASTING = frozenset({"not_mapped", "circuit_not_measured", "several_circuits"})
REASONS_MOMENTARY = frozenset({"flow_unknown", "flow_stale"})
HEAT_REASON_DHW = "dhw_active"

# Where the factor in use comes from.
SOURCE_UNUSED = "unused"  # electric heating type
SOURCE_NONE = "none"  # no data since the start
SOURCE_LIVE = "live"  # the averaged supply now
SOURCE_DHW = "dhw"  # hot water with priority: no heat for the circuit
SOURCE_AVERAGE = "average"  # momentary gap: long-term average
SOURCE_LASTING = "lasting"  # lasting gap: 1

# Whether the water data are in use, for the reset offer when this changes for good.
AVAILABILITY_NONE = "none"
AVAILABILITY_USED = "used"
AVAILABILITY_LASTING = "lasting"


@dataclass(frozen=True)
class WaterReading:
    """What Smart Boiler gives for a thermostat at one moment (all optional)."""

    supply_c: float | None = None
    supply_reason: str | None = None
    heat_available: bool | None = None
    heat_reason: str | None = None


@dataclass(frozen=True)
class WaterState:
    """The water factor in use and how it was obtained."""

    factor: float = 1.0
    source: str = SOURCE_UNUSED
    uses_water: bool = False
    supply_avg_c: float | None = None
    supply_reason: str | None = None
    heat_reason: str | None = None
    # Hot water with priority: the command and the integral are held.
    no_heat: bool = False
    # The water brings next to no heat: the integral is held (the command is not).
    low_heat: bool = False
    # The water data appeared or went for good at this update.
    changed_for_good: bool = False

    @property
    def uses_data(self) -> bool:
        """The factor comes from water data (live, an average of them, or hot water)."""
        return self.uses_water and self.source in (SOURCE_LIVE, SOURCE_AVERAGE, SOURCE_DHW)

    @property
    def published_factor(self) -> float | None:
        """The factor for Smart Boiler's key; None while no water data are behind it."""
        return self.factor if self.uses_data else None


def emitter_factor(heating_type: str, supply_c: float, room_c: float) -> float:
    """Power of a unit command at ``supply_c`` relative to the reference water (at most 1)."""
    exponent, reference_c = WATER_EMITTERS[heating_type]
    span = reference_c - WATER_ROOM_REFERENCE_C
    drive = max(supply_c - room_c, 0.0) / span
    return min(drive**exponent, WATER_FACTOR_MAX)


def _ema(previous: float | None, value: float, dt_s: float, time_constant_s: float) -> float:
    if previous is None:
        return value
    weight = 1.0 - math.exp(-max(dt_s, 0.0) / time_constant_s)
    return previous + weight * (value - previous)


class WaterInput:
    """Turns Smart Boiler's water data into the factor SmartPI applies."""

    def __init__(self, heating_type: str | None = None) -> None:
        self.heating_type = heating_type
        self.state = WaterState()
        self.availability = AVAILABILITY_NONE
        self._supply_avg: float | None = None
        self._supply_s: float | None = None
        self._long_avg: float | None = None
        self._long_s: float | None = None
        # Start of a gap without any reading (or with an unknown reason).
        self._no_data_since_s: float | None = None
        # Length of such a gap saved before a restart (it goes on after it).
        self._no_data_loaded_s: float | None = None
        self._last_s: float | None = None
        # A state loaded before the heating type was known.
        self._pending_state: dict | None = None

    @property
    def uses_water(self) -> bool:
        return self.heating_type in WATER_EMITTERS

    def configure(self, heating_type: str | None) -> None:
        """Set the heating type; a change drops the averages (another emitter)."""
        if heating_type != self.heating_type:
            self.heating_type = heating_type
            self._supply_avg = self._supply_s = None
            self._long_avg = self._long_s = None
            self._no_data_since_s = None
            self.availability = AVAILABILITY_NONE
            self.state = WaterState(uses_water=self.uses_water, source=SOURCE_NONE if self.uses_water else SOURCE_UNUSED)
        if self._pending_state is not None:
            pending, self._pending_state = self._pending_state, None
            self.load_state(pending)

    def update(self, reading: WaterReading | None, room_c: float | None, now_s: float) -> WaterState:
        """Process one reading (None: no data now); return the factor to apply."""
        if not self.uses_water:
            self.state = WaterState()
            return self.state
        reading = reading or WaterReading()
        if reading.supply_c is not None and room_c is None:
            # The water is known but not the room: nothing to compute, nothing missing.
            self.state = replace(self.state, changed_for_good=False)
            return self.state
        self._last_s = now_s
        reason = reading.supply_reason
        previous = self.availability
        dhw = reading.heat_reason == HEAT_REASON_DHW

        if reading.supply_c is not None and math.isfinite(reading.supply_c) and room_c is not None:
            self.availability = AVAILABILITY_USED
            self._no_data_since_s = self._no_data_loaded_s = None
            if dhw:
                # The boiler's flow shows the water heating the tank, not this circuit's.
                state = self._state(0.0, SOURCE_DHW, reading, no_heat=True)
            else:
                dt_s = now_s - self._supply_s if self._supply_s is not None else 0.0
                self._supply_avg = _ema(self._supply_avg, reading.supply_c, dt_s, WATER_AVERAGE_S)
                self._supply_s = now_s
                factor = emitter_factor(self.heating_type, self._supply_avg, room_c)
                heating = reading.heat_available if reading.heat_available is not None else factor >= WATER_NO_HEAT
                if heating:
                    # The average used during gaps describes the water while the boiler heats.
                    self._remember(factor, now_s)
                state = self._state(factor, SOURCE_LIVE, reading)
        elif reason in REASONS_LASTING or self._no_data_too_long(reading, now_s):
            self.availability = AVAILABILITY_LASTING
            self._supply_avg = self._supply_s = None
            state = self._state(1.0, SOURCE_LASTING, reading)
        elif self.availability == AVAILABILITY_USED:
            # Momentary: a known momentary reason, an unknown one (interface spec,
            # section 8), or no reading at all after data were seen.
            if dhw:
                state = self._state(0.0, SOURCE_DHW, reading, no_heat=True)
            else:
                factor = self._long_avg if self._long_avg is not None else 1.0
                state = self._state(factor, SOURCE_AVERAGE, reading)
        else:
            state = self._state(1.0, SOURCE_NONE if self.availability == AVAILABILITY_NONE else SOURCE_LASTING, reading)

        changed = (previous == AVAILABILITY_USED) != (self.availability == AVAILABILITY_USED) and not (
            previous == AVAILABILITY_NONE and self.availability == AVAILABILITY_LASTING
        )
        if changed:
            state = replace(state, changed_for_good=True)
        self.state = state
        return state

    def _no_data_too_long(self, reading: WaterReading, now_s: float) -> bool:
        """No reading, or an unknown reason, for WATER_NO_DATA_LASTING_S after data were seen."""
        known = reading.supply_reason in REASONS_MOMENTARY or reading.supply_reason in REASONS_LASTING
        if self.availability != AVAILABILITY_USED or known:
            self._no_data_since_s = self._no_data_loaded_s = None
            return False
        if self._no_data_since_s is None:
            self._no_data_since_s = now_s - (self._no_data_loaded_s or 0.0)
            self._no_data_loaded_s = None
        return now_s - self._no_data_since_s >= WATER_NO_DATA_LASTING_S

    def _remember(self, factor: float, now_s: float) -> None:
        dt_s = now_s - self._long_s if self._long_s is not None else 0.0
        self._long_avg = _ema(self._long_avg, factor, dt_s, WATER_LONG_AVERAGE_S)
        self._long_s = now_s

    def _state(self, factor: float, source: str, reading: WaterReading, no_heat: bool = False) -> WaterState:
        return WaterState(
            factor=factor,
            source=source,
            uses_water=True,
            supply_avg_c=self._supply_avg,
            supply_reason=reading.supply_reason,
            heat_reason=reading.heat_reason,
            no_heat=no_heat,
            low_heat=not no_heat and factor < WATER_NO_HEAT,
        )

    # --- persistence -------------------------------------------------------

    def save_state(self) -> dict[str, Any]:
        no_data_s = None
        if self._no_data_since_s is not None and self._last_s is not None:
            no_data_s = max(self._last_s - self._no_data_since_s, 0.0)
        return {
            "heating_type": self.heating_type,
            "availability": self.availability,
            "long_average": self._long_avg,
            "no_data_s": no_data_s,
        }

    def load_state(self, state: Any) -> None:
        """Restore the long-term average and the availability (same heating type only)."""
        if not isinstance(state, dict):
            return
        if self.heating_type is None:
            # Loaded before the heating type is configured: applied at configure().
            self._pending_state = dict(state)
            return
        if state.get("heating_type") != self.heating_type:
            return
        availability = state.get("availability")
        if availability in (AVAILABILITY_NONE, AVAILABILITY_USED, AVAILABILITY_LASTING):
            self.availability = availability
        long_average = state.get("long_average")
        if isinstance(long_average, (int, float)) and math.isfinite(long_average) and long_average >= 0.0:
            self._long_avg = min(float(long_average), WATER_FACTOR_MAX)
            self._long_s = None
        no_data_s = state.get("no_data_s")
        if isinstance(no_data_s, (int, float)) and math.isfinite(no_data_s) and no_data_s >= 0.0:
            self._no_data_loaded_s = float(no_data_s)
