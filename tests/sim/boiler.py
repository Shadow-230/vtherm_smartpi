"""A boiler for the simulator, and a stand-in of Smart Boiler's water interface.

``SimBoiler`` gives the water reaching a circuit: a fixed temperature or a
heating curve over the outdoor temperature, with burner cycling (a saw-tooth
around the target) and hot-water priority windows during which the circuit
gets no heat while the boiler's flow shows the water heating the tank.

``SimSmartBoiler`` answers ``zone_water(entity_id)`` like Smart Boiler's public
API (its documentation/en/water-interface.md, section 4): the measured supply
rounded like a 0.1 degC sensor, ``heat_reason == "dhw_active"`` during hot water,
and reasons a test can force for a time window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# Emitters of the plant: exponent and reference water, as SmartPI's water types.
PLANT_EMITTERS: dict[str, tuple[float, float]] = {
    "radiator": (1.3, 60.0),
    "floor_water": (1.1, 45.0),
}
SIM_ROOM_REFERENCE_C = 20.0


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
        exponent, reference_c = PLANT_EMITTERS[emitter]
        span = reference_c - SIM_ROOM_REFERENCE_C

        def factor(t_s: float, outdoor_c: float, temp_c: float) -> float:
            water = self.circuit_c(t_s, outdoor_c)
            if water is None:
                return 0.0
            return (max(water - temp_c, 0.0) / span) ** exponent

        return factor


@dataclass
class SimSmartBoiler:
    """Stand-in of ``hass.data["vtherm_smart_boiler_api"]`` for the simulated thermostats."""

    clock: object
    weather: object
    zones: dict[str, SimBoiler] = field(default_factory=dict)
    # Reasons forced for a time window: (start_h, end_h, supply_reason).
    forced_reasons: list[tuple[float, float, str]] = field(default_factory=list)
    calls: int = 0

    def zone_water(self, entity_id: str):
        self.calls += 1
        boiler = self.zones.get(entity_id)
        if boiler is None:
            return None
        t_s = self.clock.elapsed_s
        hour = t_s / 3600.0
        for start, end, reason in self.forced_reasons:
            if start <= hour < end:
                return {"supply_temperature": None, "supply_reason": reason, "heat_available": None, "heat_reason": reason}
        flow = boiler.flow_c(t_s, self.weather.outdoor_c(t_s))
        dhw = boiler.dhw_active(t_s)
        return {
            "supply_temperature": round(flow, 1),
            "supply_reason": None,
            "heat_available": not dhw,
            "heat_reason": "dhw_active" if dhw else None,
        }
