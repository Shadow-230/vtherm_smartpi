"""The room simulation behind the entities, without Home Assistant (tests/qas, QAS only).

It builds a thermal network from the role's configuration (``physics.py``, the same physics as
the in-process simulator) and advances it in real time, one tick at a time:

- rooms with a loss to the outdoors ``b`` (1/min) and a capacity, linked always or through a
  door, and windows to the outdoors;
- heaters (switches) and valves (0..1) with a gain ``a`` (degC/min at full power), a transport
  delay and an emitter lag (one stage, or two for a slab);
- water emitters heat only with the boiler's water: it warms towards ``fire_c`` while the boiler
  switch is on and cools towards ``idle_c`` (below the rooms) while it is off;
- an outdoor temperature with a daily swing and a small wiggle, so a sensor of it never holds
  one value for hours;
- doors and windows open by a daily schedule, or as a scenario sets them;
- a device can stop responding: it keeps what it was doing (``ignore``) or also goes
  unavailable (``unavailable``).

Periods are chosen so that none is a multiple of SmartPI's own (60 s, the cycle): the tick is
7 s and the outdoor wiggle 2838 s.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .physics import (
    PLANT_EMITTERS,
    DelayedPower,
    PowerHistory,
    ThermalNetwork,
    water_factor,
)

DEFAULT_STEP_S = 7.0
WIGGLE_PERIOD_S = 2838.0
DEVICE_MODES = ("ok", "ignore", "unavailable")
ELECTRIC = "electric"


@dataclass(frozen=True)
class RoomSpec:
    name: str
    temperature: float = 19.0
    b: float = 0.001
    capacity: float = 1.0
    resolution: float = 0.1


@dataclass(frozen=True)
class HeaterSpec:
    name: str
    room: str
    kind: str = "switch"  # "switch" (on/off) or "valve" (0..100 %)
    a: float = 0.03
    delay_min: float = 0.0
    lag_min: float = 0.0
    stages: int = 1
    emitter: str = ELECTRIC  # "electric", "radiator" or "floor_water"
    power_w: float = 1000.0


@dataclass(frozen=True)
class LinkSpec:
    first: str
    second: str
    g: float
    door: str | None = None


@dataclass(frozen=True)
class OpeningSpec:
    name: str
    device_class: str = "door"  # "door" or "window"
    room: str | None = None  # a window: the room it opens to the outdoors
    g: float = 0.0  # a window: its conductance to the outdoors while open
    schedule: tuple[tuple[float, float], ...] = ()  # open (start_h, end_h) of each day


@dataclass(frozen=True)
class BoilerSpec:
    fire_c: float = 60.0
    idle_c: float = 12.0
    tau_up_min: float = 4.0
    tau_down_min: float = 20.0


@dataclass(frozen=True)
class OutdoorSpec:
    mean: float = 3.0
    swing: float = 3.0
    peak_hour: float = 15.0
    wiggle: float = 0.06
    resolution: float = 0.1


@dataclass(frozen=True)
class SimSpec:
    rooms: tuple[RoomSpec, ...]
    heaters: tuple[HeaterSpec, ...] = ()
    links: tuple[LinkSpec, ...] = ()
    openings: tuple[OpeningSpec, ...] = ()
    boiler: BoilerSpec | None = None
    outdoor: OutdoorSpec = OutdoorSpec()
    base_power_w: float = 250.0
    step_s: float = DEFAULT_STEP_S

    def validate(self) -> None:
        rooms = {r.name for r in self.rooms}
        openings = {o.name: o for o in self.openings}
        names = [*rooms, *(h.name for h in self.heaters), *openings]
        if len(names) != len(set(names)):
            raise ValueError("room, heater and opening names must be unique")
        for heater in self.heaters:
            if heater.room not in rooms:
                raise ValueError(f"heater {heater.name}: no room {heater.room}")
            if heater.kind not in ("switch", "valve"):
                raise ValueError(f"heater {heater.name}: kind {heater.kind}")
            if heater.emitter != ELECTRIC and heater.emitter not in PLANT_EMITTERS:
                raise ValueError(f"heater {heater.name}: emitter {heater.emitter}")
            if heater.emitter != ELECTRIC and self.boiler is None:
                raise ValueError(f"heater {heater.name}: a water emitter needs the boiler")
            if heater.stages not in (1, 2):
                raise ValueError(f"heater {heater.name}: stages {heater.stages}")
        for link in self.links:
            if link.first not in rooms or link.second not in rooms:
                raise ValueError(f"link {link.first}-{link.second}: unknown room")
            if link.door is not None and openings.get(link.door, None) is None:
                raise ValueError(f"link {link.first}-{link.second}: no door {link.door}")
        for opening in self.openings:
            if opening.device_class == "window" and opening.room not in rooms:
                raise ValueError(f"window {opening.name}: no room {opening.room}")


def spec_from_config(config: Mapping[str, Any]) -> SimSpec:
    """The simulation described by the integration's YAML (see ``__init__.CONFIG_SCHEMA``)."""
    rooms = tuple(RoomSpec(name=name, **(values or {})) for name, values in config["rooms"].items())
    heaters = tuple(
        HeaterSpec(name=name, **(values or {})) for name, values in config.get("heaters", {}).items()
    )
    links = tuple(
        LinkSpec(first=link["rooms"][0], second=link["rooms"][1], g=link["g"], door=link.get("door"))
        for link in config.get("links", [])
    )
    openings = tuple(
        OpeningSpec(
            name=name,
            device_class=values.get("device_class", "door"),
            room=values.get("room"),
            g=values.get("g", 0.0),
            schedule=tuple(tuple(interval) for interval in values.get("schedule", [])),
        )
        for name, values in config.get("openings", {}).items()
    )
    boiler = BoilerSpec(**config["boiler"]) if config.get("boiler") is not None else None
    outdoor = OutdoorSpec(**config.get("outdoor", {}))
    spec = SimSpec(
        rooms=rooms,
        heaters=heaters,
        links=links,
        openings=openings,
        boiler=boiler,
        outdoor=outdoor,
        base_power_w=config.get("base_power_w", 250.0),
        step_s=config.get("step_s", DEFAULT_STEP_S),
    )
    spec.validate()
    return spec


class _OpeningState:
    """A connection open while its opening is (duck-typed as physics.Schedule)."""

    def __init__(self, sim: Simulation, name: str) -> None:
        self._sim = sim
        self._name = name

    def is_open(self, _t_s: float) -> bool:
        return self._sim.is_open(self._name)

    def changes(self, _start_s: float, _end_s: float) -> list[float]:
        return []  # openings change only between ticks


@dataclass
class _Ramp:
    start_s: float
    end_s: float
    start_c: float
    end_c: float

    def value(self, t_s: float) -> float:
        if t_s >= self.end_s:
            return self.end_c
        share = (t_s - self.start_s) / (self.end_s - self.start_s)
        return self.start_c + share * (self.end_c - self.start_c)


@dataclass
class _OutdoorState:
    mean: float
    swing: float
    ramp: _Ramp | None = None


@dataclass
class Simulation:
    """The rooms in real time; ``t_s`` counts seconds from the start."""

    spec: SimSpec
    start_hour: float = 0.0
    t_s: float = 0.0
    network: ThermalNetwork = field(init=False)
    commands: dict[str, float] = field(init=False)
    applied: dict[str, float] = field(init=False)
    modes: dict[str, str] = field(init=False)
    heat: dict[str, float] = field(init=False)
    boiler_on: bool = False
    water_c: float | None = None
    _histories: dict[str, PowerHistory] = field(init=False)
    _overrides: dict[str, bool] = field(init=False)
    _ramps: dict[str, _Ramp] = field(init=False)
    _outdoor: _OutdoorState = field(init=False)

    def __post_init__(self) -> None:
        self.spec.validate()
        self.reset()

    # --- set up and reset ---------------------------------------------------------------

    def reset(self, rooms: bool = True) -> None:
        """Back to the configured state.

        ``rooms`` True: everything — temperatures, devices, boiler, openings and outdoor.
        ``rooms`` False: only what a scenario sets — openings and their schedules, devices that
        stopped responding, ramps and the outdoor temperature. The temperatures go on, and the
        heaters and the boiler stay as Versatile Thermostat drives them: a runner between
        scenarios uses this (an instant jump of a room could look like an open window).
        """
        if not rooms and hasattr(self, "network"):
            self._overrides = {}
            self._schedules = {}
            self._ramps = {}
            self._outdoor = _OutdoorState(self.spec.outdoor.mean, self.spec.outdoor.swing)
            for heater in self.modes:
                self.set_mode(heater, "ok")
            return
        spec = self.spec
        net = ThermalNetwork()
        for room in spec.rooms:
            net.add_node(room.name, room.temperature, room.capacity)
            net.link_outdoor(room.name, room.b)
        for link in spec.links:
            if link.door is None:
                net.link(link.first, link.second, link.g)
            else:
                net.link(link.first, link.second, link.g, schedule=_OpeningState(self, link.door))
        for opening in spec.openings:
            if opening.device_class == "window" and opening.g > 0.0:
                net.link_boundary(
                    opening.room, opening.g, self.outdoor_c, schedule=_OpeningState(self, opening.name)
                )
        self._histories = {}
        for heater in spec.heaters:
            history = PowerHistory(0.0)
            self._histories[heater.name] = history
            power = DelayedPower(history, heater.delay_min * 60.0)
            factor = None if heater.emitter == ELECTRIC else self._factor(heater.emitter)
            if heater.lag_min > 0.0:
                net.add_lagged_heater(
                    heater.room, heater.a, power, heater.lag_min, factor, stages=heater.stages
                )
            else:
                net.add_heater(heater.room, heater.a, power, factor)
        self.network = net
        self.commands = {h.name: 0.0 for h in spec.heaters}
        self.applied = dict(self.commands)
        self.modes = {h.name: "ok" for h in spec.heaters}
        self.heat = {h.name: 0.0 for h in spec.heaters}
        self._overrides = {}
        self._schedules: dict[str, tuple[tuple[float, float], ...]] = {}
        self._ramps = {}
        self._outdoor = _OutdoorState(spec.outdoor.mean, spec.outdoor.swing)
        self.boiler_on = False
        self.water_c = spec.boiler.idle_c if spec.boiler is not None else None
        self._water_t_s = self.t_s

    # --- inputs -------------------------------------------------------------------------

    def set_command(self, heater: str, value: float) -> None:
        """What the thermostat asks of a device (0..1); a device that stops responding keeps
        what it applied."""
        value = min(max(float(value), 0.0), 1.0)
        self.commands[heater] = value
        if self.modes[heater] == "ok":
            self._apply(heater, value)

    def set_mode(self, heater: str, mode: str) -> None:
        if mode not in DEVICE_MODES:
            raise ValueError(f"mode {mode}")
        self.modes[heater] = mode
        if mode == "ok":
            self._apply(heater, self.commands[heater])

    def _apply(self, heater: str, value: float) -> None:
        self.applied[heater] = value
        self._histories[heater].set(self.t_s, value)

    def set_boiler(self, on: bool) -> None:
        self._advance_water(self.t_s)
        self.boiler_on = bool(on)

    def set_opening(
        self,
        name: str,
        is_open: bool | None,
        schedule: tuple[tuple[float, float], ...] | None = None,
    ) -> None:
        """Open or close an opening; ``None`` hands it back to its schedule, which ``schedule``
        replaces (until a reset)."""
        if name not in {o.name for o in self.spec.openings}:
            raise KeyError(name)
        if schedule is not None:
            self._schedules[name] = tuple(tuple(interval) for interval in schedule)
        if is_open is None:
            self._overrides.pop(name, None)
        else:
            self._overrides[name] = bool(is_open)

    def set_room(self, room: str, temperature: float) -> None:
        self._ramps.pop(room, None)
        index = self.network.index(room)
        self.network.temperatures[index] = float(temperature)

    def ramp_room(self, room: str, temperature: float, minutes: float) -> None:
        """Change a room's temperature gently, in a straight line over ``minutes``."""
        index = self.network.index(room)
        start = self.network.temperatures[index]
        self._ramps[room] = _Ramp(self.t_s, self.t_s + max(minutes, 0.1) * 60.0, start, float(temperature))

    def set_outdoor(self, mean: float, swing: float | None = None, minutes: float = 0.0) -> None:
        """Move the outdoor mean, in a straight line over ``minutes``; the swing at once."""
        current = self._outdoor_mean(self.t_s)
        if minutes > 0.0:
            ramp = _Ramp(self.t_s, self.t_s + minutes * 60.0, current, float(mean))
        else:
            ramp = None
        self._outdoor = _OutdoorState(float(mean), self._outdoor.swing if swing is None else float(swing), ramp)

    # --- state --------------------------------------------------------------------------

    def hour(self, t_s: float) -> float:
        return (self.start_hour + t_s / 3600.0) % 24.0

    def is_open(self, name: str) -> bool:
        if name in self._overrides:
            return self._overrides[name]
        opening = next(o for o in self.spec.openings if o.name == name)
        schedule = self._schedules.get(name, opening.schedule)
        hour = self.hour(self.t_s)
        return any(start <= hour < end for start, end in schedule)

    def _outdoor_mean(self, t_s: float) -> float:
        ramp = self._outdoor.ramp
        return ramp.value(t_s) if ramp is not None else self._outdoor.mean

    def outdoor_c(self, t_s: float) -> float:
        spec = self.spec.outdoor
        hour = self.hour(t_s)
        daily = self._outdoor.swing * math.cos(2.0 * math.pi * (hour - spec.peak_hour) / 24.0)
        phase = (t_s / WIGGLE_PERIOD_S) % 1.0
        wiggle = spec.wiggle * (4.0 * abs(phase - 0.5) - 1.0)
        return self._outdoor_mean(t_s) + daily + wiggle

    def temperature(self, room: str) -> float:
        return self.network.temperature(room)

    def house_power_w(self) -> float:
        heaters = {h.name: h for h in self.spec.heaters}
        return self.spec.base_power_w + sum(
            heaters[name].power_w * value for name, value in self.applied.items()
        )

    def water_at(self, t_s: float) -> float | None:
        boiler = self.spec.boiler
        if boiler is None or self.water_c is None:
            return None
        target = boiler.fire_c if self.boiler_on else boiler.idle_c
        tau = boiler.tau_up_min if self.boiler_on else boiler.tau_down_min
        elapsed_min = max(t_s - self._water_t_s, 0.0) / 60.0
        return target + (self.water_c - target) * math.exp(-elapsed_min / tau)

    def _advance_water(self, t_s: float) -> None:
        if self.water_c is not None:
            self.water_c = self.water_at(t_s)
            self._water_t_s = t_s

    def _factor(self, emitter: str) -> Callable[[float, float, float], float]:
        def factor(t_s: float, _outdoor_c: float, temp_c: float) -> float:
            return water_factor(emitter, self.water_at(t_s), temp_c)

        return factor

    # --- time ---------------------------------------------------------------------------

    def advance(self, t_s: float) -> None:
        """Move the rooms on to ``t_s``."""
        if t_s <= self.t_s:
            return
        start = self.t_s
        self._account_heat(start, t_s)
        self.network.advance(start, t_s, self.outdoor_c)
        self._advance_water(t_s)
        self.t_s = t_s
        for room, ramp in list(self._ramps.items()):
            self.network.temperatures[self.network.index(room)] = ramp.value(t_s)
            if t_s >= ramp.end_s:
                del self._ramps[room]

    def _account_heat(self, start_s: float, end_s: float) -> None:
        """Heat each heater sends into its emitter (degC of its room's capacity), by the
        delayed power at the start of the tick and the water's factor at its middle."""
        mid_s = 0.5 * (start_s + end_s)
        minutes = (end_s - start_s) / 60.0
        for heater in self.spec.heaters:
            power = self._histories[heater.name].value_at(start_s - heater.delay_min * 60.0)
            if heater.emitter != ELECTRIC:
                power *= water_factor(heater.emitter, self.water_at(mid_s), self.temperature(heater.room))
            self.heat[heater.name] += heater.a * power * minutes

    def truth(self) -> dict[str, Any]:
        """The true parameters a scenario compares SmartPI's learning with."""
        return {
            "rooms": {r.name: {"b": r.b, "capacity": r.capacity} for r in self.spec.rooms},
            "heaters": {
                h.name: {
                    "a": h.a,
                    "delay_min": h.delay_min,
                    "lag_min": h.lag_min,
                    "stages": h.stages,
                    "emitter": h.emitter,
                    "power_w": h.power_w,
                }
                for h in self.spec.heaters
            },
            "links": [
                {"rooms": [link.first, link.second], "g": link.g, "door": link.door}
                for link in self.spec.links
            ],
        }


def quantize(value: float, resolution: float) -> float:
    """A sensor's reading: rounded to its resolution."""
    return round(round(value / resolution) * resolution, 6)
