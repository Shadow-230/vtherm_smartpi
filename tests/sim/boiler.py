"""A boiler for the simulator, and the water data it gives SmartPI.

``SimBoiler`` gives the water reaching a circuit: a fixed temperature or a
heating curve over the outdoor temperature, with burner cycling (a saw-tooth
around the target) and hot-water priority windows during which the circuit
gets no heat while the boiler's flow shows the water heating the tank.

Its water reaches SmartPI from one of the two sources a user can choose
(documentation/en/water_input.md):

- ``SimHeatingProvider``: a provider of SmartPI's contract (v1), registered in
  ``hass.data["vtherm_smartpi_heating_providers"]``: the measured supply rounded
  like a 0.1 degC sensor, ``heat_reason == "dhw_active"`` during hot water, and
  reasons a test can force for a time window;
- ``SimStates``: Home Assistant entities: a supply sensor per circuit and the
  boiler's hot water and heat signals (``water_entities``); a forced reason
  makes the supply sensor unavailable for its window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_SUPPLY_ENTITY,
)

# Emitters of the plant and their factor: the shared physics (tests/sim/plant.py).
from .plant import PLANT_EMITTERS, SIM_ROOM_REFERENCE_C, water_factor  # noqa: F401


@dataclass
class SimBoiler:
    """Water of one circuit: ``water_c`` fixed, or a heating curve when ``curve_slope`` is set."""

    water_c: float = 60.0
    curve_slope: float | None = None
    curve_room_c: float = 20.0
    min_water_c: float = 25.0
    max_water_c: float = 75.0
    # Burner cycling: a saw-tooth of this amplitude (degC) and period.
    cycle_amplitude_c: float = 0.0
    cycle_period_min: float = 18.0
    # Hot water with priority, as (start_h, end_h) of each day.
    dhw_daily_h: tuple[tuple[float, float], ...] = ()
    dhw_flow_c: float = 70.0
    # A boiler that fires only on the zone's demand: demand(t_s) says whether the
    # zone called for heat recently (set by the simulation); otherwise the water
    # is at the boiler room's temperature.
    on_demand: bool = False
    idle_c: float = 15.0
    demand: Callable[[float], bool] | None = None

    def target_c(self, outdoor_c: float) -> float:
        if self.curve_slope is None:
            return self.water_c
        water = self.curve_room_c + self.curve_slope * (self.curve_room_c - outdoor_c)
        return min(max(water, self.min_water_c), self.max_water_c)

    def dhw_active(self, t_s: float) -> bool:
        hour = (t_s / 3600.0) % 24.0
        return any(start <= hour < end for start, end in self.dhw_daily_h)

    def circuit_c(self, t_s: float, outdoor_c: float) -> float | None:
        """Water reaching the circuit (None while hot water has priority: no flow to it)."""
        if self.dhw_active(t_s):
            return None
        if self.on_demand and self.demand is not None and not self.demand(t_s):
            return self.idle_c
        water = self.target_c(outdoor_c)
        if self.cycle_amplitude_c > 0.0:
            phase = (t_s / 60.0 % self.cycle_period_min) / self.cycle_period_min
            water += self.cycle_amplitude_c * (2.0 * abs(2.0 * phase - 1.0) - 1.0)
        return water

    def flow_c(self, t_s: float, outdoor_c: float) -> float:
        """What a sensor on the boiler's flow shows."""
        if self.dhw_active(t_s):
            return self.dhw_flow_c
        return self.circuit_c(t_s, outdoor_c)

    def emitter_factor(self, emitter: str) -> Callable[[float, float, float], float]:
        """Power of the emitter relative to the reference water: ((water - T) / (ref - 20)) ** n."""
        if emitter not in PLANT_EMITTERS:
            raise KeyError(emitter)

        def factor(t_s: float, outdoor_c: float, temp_c: float) -> float:
            return water_factor(emitter, self.circuit_c(t_s, outdoor_c), temp_c)

        return factor


PROVIDER_NAME = "sim"


@dataclass
class SimHeatingProvider:
    """A provider of SmartPI's contract (v1) for the simulated thermostats."""

    clock: object
    weather: object
    zones: dict[str, SimBoiler] = field(default_factory=dict)
    # Reasons forced for a time window: (start_h, end_h, supply_reason).
    forced_reasons: list[tuple[float, float, str]] = field(default_factory=list)
    name: str = PROVIDER_NAME
    version: int = 1
    calls: int = 0

    def heating_inputs(self, entity_id: str):
        self.calls += 1
        boiler = self.zones.get(entity_id)
        if boiler is None:
            return None
        t_s = self.clock.elapsed_s
        reason = forced_reason(self.forced_reasons, t_s)
        if reason is not None:
            return {"supply_temperature": None, "supply_reason": reason, "heat_available": None, "heat_reason": reason}
        flow = boiler.flow_c(t_s, self.weather.outdoor_c(t_s))
        dhw = boiler.dhw_active(t_s)
        return {
            "supply_temperature": round(flow, 1),
            "supply_reason": None,
            "heat_available": not dhw,
            "heat_reason": "dhw_active" if dhw else None,
        }


def forced_reason(forced: list[tuple[float, float, str]], t_s: float) -> str | None:
    hour = t_s / 3600.0
    return next((reason for start, end, reason in forced if start <= hour < end), None)


@dataclass
class SimState:
    state: str
    attributes: dict = field(default_factory=dict)


class SimStates:
    """Home Assistant's states for the simulation: each entity's state computed when read."""

    def __init__(self) -> None:
        self._entities: dict[str, Callable[[], SimState | None]] = {}
        self.reads = 0

    def set(self, entity_id: str, state: Callable[[], SimState | None]) -> None:
        self._entities[entity_id] = state

    def get(self, entity_id: str) -> SimState | None:
        self.reads += 1
        state = self._entities.get(entity_id)
        return state() if state is not None else None


def water_entities(states: SimStates, clock, weather, boiler: SimBoiler, object_id: str, forced) -> dict[str, str]:
    """The supply sensor of a circuit and the boiler's signals as entities; their options."""
    supply = f"sensor.{object_id}_supply"
    heat = f"binary_sensor.{object_id}_heat_available"
    dhw = f"binary_sensor.{object_id}_dhw"

    def supply_state() -> SimState:
        t_s = clock.elapsed_s
        if forced_reason(forced(), t_s) is not None:
            return SimState("unavailable")
        return SimState(str(round(boiler.flow_c(t_s, weather.outdoor_c(t_s)), 1)), {"unit_of_measurement": "°C"})

    states.set(supply, supply_state)
    states.set(heat, lambda: SimState("off" if boiler.dhw_active(clock.elapsed_s) else "on"))
    states.set(dhw, lambda: SimState("on" if boiler.dhw_active(clock.elapsed_s) else "off"))
    return {
        CONF_SMART_PI_SUPPLY_ENTITY: supply,
        CONF_SMART_PI_BOILER_SIGNALS: "own",
        CONF_SMART_PI_HEAT_AVAILABLE_ENTITY: heat,
        CONF_SMART_PI_DHW_ENTITY: dhw,
    }
