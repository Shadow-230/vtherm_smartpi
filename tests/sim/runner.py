"""Simulation runner: the real SmartPI handler driven in virtual time.

The thermostat side reproduces what Versatile Thermostat does around the
algorithm: on each published temperature it updates its moving average and
slope, recalculates the algorithm, then runs the control cycle. Three periodic
sources run as timers of the virtual clock: VTherm's own control call every
``cycle_min``, the handler's 60 s recalculation timer and the cycle scheduler.
A valve thermostat retains integer-percent commands like VTherm's
``apply_valve_command_percent``.

A zone may have a second thermostat (``SourceSpec``), for example a heated
floor: it has its own actuator, cycle scheduler, sensor and SmartPI handler,
shares Home Assistant with the zone thermostat, and heats the zone through a
transport delay and a first-order lag. Without ``split`` it regulates the zone
on its own, as two independent SmartPI do today; with ``split`` the zone
thermostat names it as its second source and drives it.

Several zones can share one world (``SimWorld``: clock, events, thermal network
and Home Assistant) and run together (``CoupledSimulation``); a zone reads the
other through its thermostat (``SimZoneNeighbour``), and the test links their
nodes, always or by a door's schedule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from custom_components.vtherm_smartpi import handler as handler_module
from custom_components.vtherm_smartpi.const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_WATER_SOURCE,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.heating_inputs import PROVIDERS_KEY, SOURCE_ENTITIES, provider_source
from custom_components.vtherm_smartpi.smartpi.dependencies import (
    KIND_NEIGHBOUR,
    KIND_SOURCE,
    MODE_DOOR,
    DependencyItem,
)
from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings
from custom_components.vtherm_smartpi.smartpi.dependency_inputs import (
    DependencyReadings,
    NeighbourObservation,
    NeighbourReading,
)
from custom_components.vtherm_smartpi.smartpi.effective_model import KnownInputs
from custom_components.vtherm_smartpi.hvac_mode import (
    VThermHvacMode,
    VThermHvacMode_HEAT,
    VThermHvacMode_OFF,
)

from fakes.fake_thermostat_runtime import FakeHass, FakeThermostatRuntime

from .boiler import PROVIDER_NAME, SimBoiler, SimHeatingProvider, SimStates, water_entities
from .clock import EventQueue, VirtualClock
from .plant import DelayedPower, Schedule, ThermalNetwork
from .scheduler import SimActuator, SimCycleScheduler
from .sensors import QuantizedSensor, VThermSlope
from .weather import Weather

SENSOR_STEP_S = 10.0
TRACE_STEP_S = 60.0
ZONE_NODE = "zone"
NEIGHBOUR_SENSOR_C = 0.1


def _in_intervals(t_s: float, intervals_h: tuple[tuple[float, float], ...]) -> bool:
    return any(start * 3600.0 <= t_s < end * 3600.0 for start, end in intervals_h)


@dataclass(frozen=True)
class SimNeighbour:
    """Neighbouring space at a prescribed temperature, coupled to the zone.

    ``mode`` is what SmartPI is told (``always``, ``door`` or ``none``). The
    physical exchange follows the doors in ``door`` mode and is permanent
    otherwise, so ``none`` models a neighbour the user chose to ignore.
    """

    key: str
    temperature_c: float
    conductance: float
    mode: str = "always"
    # Temperature steps as (hour, change in degC).
    steps: tuple[tuple[float, float], ...] = ()
    daily_amplitude_c: float = 0.0
    doors_open_h: tuple[tuple[float, float], ...] = ()
    windows_open_h: tuple[tuple[float, float], ...] = ()
    is_thermostat: bool = True
    available: bool = True

    def temperature(self, t_s: float) -> float:
        hours = t_s / 3600.0
        value = self.temperature_c
        if self.daily_amplitude_c:
            value -= self.daily_amplitude_c * math.cos(2.0 * math.pi * (hours - 5.0) / 24.0)
        value += sum(change for at_h, change in self.steps if hours >= at_h)
        return value

    def schedule(self) -> Schedule | None:
        if self.mode != MODE_DOOR:
            return None
        return Schedule(tuple((start * 3600.0, end * 3600.0) for start, end in self.doors_open_h))

    def observation(self, t_s: float) -> NeighbourObservation:
        """What the dependency layer reads for this neighbour, as the handler would."""
        item = DependencyItem(
            item_id=self.key,
            kind=KIND_NEIGHBOUR,
            entity=f"reg-{self.key}",
            mode=self.mode,
            door_sensors=(f"reg-door-{self.key}",) if self.mode == MODE_DOOR else (),
        )
        domain = "climate" if self.is_thermostat else "sensor"
        reading = NeighbourReading(
            item_id=self.key,
            entity_id=f"{domain}.{self.key}",
            temperature=(
                round(round(self.temperature(t_s) / NEIGHBOUR_SENSOR_C) * NEIGHBOUR_SENSOR_C, 6)
                if self.available
                else None
            ),
            available=self.available,
            is_thermostat=self.is_thermostat,
            window_open=self.is_thermostat and _in_intervals(t_s, self.windows_open_h),
        )
        return NeighbourObservation(item, reading, door_open=_in_intervals(t_s, self.doors_open_h))


@dataclass
class SimZoneNeighbour:
    """Another simulated zone, read through its zone thermostat as the handler reads a
    neighbour thermostat. The heat exchange goes through the shared thermal network."""

    key: str
    conductance: float
    mode: str = "always"
    doors_open_h: tuple[tuple[float, float], ...] = ()
    zone: Simulation | None = field(default=None, compare=False, repr=False)

    def observation(self, t_s: float) -> NeighbourObservation:
        item = DependencyItem(
            item_id=self.key,
            kind=KIND_NEIGHBOUR,
            entity=f"reg-{self.key}",
            mode=self.mode,
            door_sensors=(f"reg-door-{self.key}",) if self.mode == MODE_DOOR else (),
        )
        temperature = self.zone.thermostat.current_temperature if self.zone is not None else None
        reading = NeighbourReading(
            item_id=self.key,
            entity_id=f"climate.{self.key}",
            temperature=temperature,
            available=temperature is not None,
            is_thermostat=True,
        )
        return NeighbourObservation(item, reading, door_open=_in_intervals(t_s, self.doors_open_h))


@dataclass
class SimWorld:
    """Clock, events, thermal network and Home Assistant shared by coupled zones."""

    clock: VirtualClock
    queue: EventQueue
    network: ThermalNetwork
    hass: FakeHass

    @classmethod
    def create(cls, monkeypatch) -> SimWorld:
        clock = VirtualClock()
        clock.install(monkeypatch)
        queue = EventQueue(clock)
        monkeypatch.setattr(
            handler_module,
            "async_track_time_interval",
            lambda _hass, action, interval: queue.track_interval(interval.total_seconds(), action),
        )
        return cls(clock, queue, ThermalNetwork(), FakeHass())


def FixedNeighbour(key: str, temperature_c: float, conductance: float) -> SimNeighbour:  # noqa: N802
    """Neighbour at a constant temperature, always connected."""
    return SimNeighbour(key=key, temperature_c=temperature_c, conductance=conductance)


@dataclass(frozen=True)
class SourceSpec:
    """A second thermostat heating the same zone, in SmartPI's units.

    ``a_f`` is the zone's rate of rise at full power once the emitter has
    settled; ``tau_min`` is the emitter's first-order lag (0 for a fast one).
    """

    a_f: float = 0.03
    deadtime_min: float = 30.0
    tau_min: float = 180.0
    cycle_min: float = 10.0
    valve: bool = False
    min_activation_delay_s: int = 0
    min_deactivation_delay_s: int = 0
    # VTherm's periodic control call of the second thermostat is out of phase.
    control_phase_s: float = 150.0
    # Setpoint when the second thermostat regulates on its own (None: the zone's).
    setpoint_c: float | None = None
    options: dict = field(default_factory=dict)
    # Source item fields (rule, parameter, emitters) when the zone thermostat
    # drives this one as its second source; None: it regulates on its own.
    split: dict | None = None
    # 2: the emitter warms through two lags of tau_min / 2 in series.
    stages: int = 1
    # Water heating of this emitter, as ZoneSpec's (its water data follow ZoneSpec.water_data).
    boiler: SimBoiler | None = None
    emitter: str = "floor_water"


@dataclass
class ZoneSpec:
    """One zone heated by one actuator, described in SmartPI's own units."""

    name: str = "Sim zone"
    a: float = 0.03
    b: float = 0.001
    deadtime_min: float = 10.0
    initial_c: float = 19.0
    setpoint_c: float = 20.0
    cycle_min: float = 10.0
    valve: bool = False
    # VTherm only moves a valve when the command changes by this many points,
    # and not more often than every valve_period_min (0 = no restriction).
    valve_dpercent: int = 0
    valve_period_min: float = 0.0
    # Offset of VTherm's periodic control call within its cycle_min period.
    control_phase_s: float = 0.0
    min_activation_delay_s: int = 0
    min_deactivation_delay_s: int = 0
    sensor_resolution_c: float = 0.1
    neighbours: tuple[SimNeighbour, ...] = ()
    presets: dict = field(default_factory=lambda: {"frost_temp": 7.0, "eco_temp": 17.0})
    options: dict = field(default_factory=dict)
    source: SourceSpec | None = None
    # Water heating: the boiler feeding the zone's emitter (None: power independent of water),
    # the emitter's type for the plant, and the source of SmartPI's water data: None (no
    # water data), "provider" (SimHeatingProvider) or "entities" (a supply sensor per circuit).
    boiler: SimBoiler | None = None
    emitter: str = "radiator"
    water_data: str | None = None


@dataclass
class TracePoint:
    t_h: float
    zone_c: float
    sensor_c: float | None
    outdoor_c: float
    command: float
    power: float
    phase: str
    source_command: float = 0.0
    source_power: float = 0.0


class SimThermostat(FakeThermostatRuntime):
    """Thermostat runtime whose signals come from the simulation."""

    def __init__(
        self,
        spec: ZoneSpec,
        clock: VirtualClock,
        actuator: SimActuator,
        unique_id: str = "sim-zone",
        entity_id: str = "climate.sim_zone",
    ) -> None:
        super().__init__()
        self._clock = clock
        self._actuator = actuator
        self.handler: SmartPIHandler | None = None
        self.name = spec.name
        self.unique_id = unique_id
        self.entity_id = entity_id
        self.cycle_min = spec.cycle_min
        self.target_temperature = spec.setpoint_c
        self.vtherm_hvac_mode = VThermHvacMode_HEAT
        self.current_temperature = None
        self.current_outdoor_temperature = None
        self.last_temperature_measure: datetime | None = None
        self._valve = spec.valve
        self._valve_dpercent = spec.valve_dpercent
        self._valve_period_min = spec.valve_period_min
        self._valve_open_percent = 0
        self._valve_applied_s: float | None = None
        self.slope = VThermSlope()
        # FakeThermostatRuntime sets these as AsyncMock instance attributes.
        self.async_control_heating = self._async_control_heating
        self.async_underlying_entity_turn_off = self._async_underlying_entity_turn_off
        # The thermostat's own SmartPI entry: its delays and the options of the scenario.
        self.smartpi_options.update(
            {
                CONF_MINIMAL_ACTIVATION_DELAY: spec.min_activation_delay_s,
                CONF_MINIMAL_DEACTIVATION_DELAY: spec.min_deactivation_delay_s,
                **spec.options,
            }
        )

    @property
    def is_device_active(self) -> bool:
        actuator = getattr(self, "_actuator", None)
        return actuator.is_device_active if actuator is not None else False

    @is_device_active.setter
    def is_device_active(self, _value: bool) -> None:
        """Ignore assignments: the simulated actuator holds the device state."""

    @property
    def last_temperature_slope(self) -> float | None:
        slope = getattr(self, "slope", None)
        return slope.slope_c_per_h if slope is not None else None

    @last_temperature_slope.setter
    def last_temperature_slope(self, _value: float | None) -> None:
        """Ignore assignments: the slope follows the published measurements."""

    @property
    def valve_open_percent(self) -> int | None:
        if not self._valve:
            return None
        return 0 if self.vtherm_hvac_mode == VThermHvacMode_OFF else self._valve_open_percent

    def apply_valve_command_percent(self, on_percent: float, force: bool = False) -> float:
        """Retain the valve command as VTherm does: integer percent, threshold and period."""
        new_percent = round(max(0.0, min(on_percent, 1.0)) * 100)
        if new_percent < self._valve_dpercent:
            new_percent = 0
        change = new_percent - self._valve_open_percent
        if new_percent > 0 and -self._valve_dpercent <= change < self._valve_dpercent:
            return self._valve_open_percent / 100.0
        if new_percent == self._valve_open_percent:
            return self._valve_open_percent / 100.0
        if (
            self._valve_applied_s is not None
            and not force
            and (self._clock.elapsed_s - self._valve_applied_s) / 60.0 < self._valve_period_min
        ):
            return self._valve_open_percent / 100.0
        self._valve_open_percent = new_percent
        self._valve_applied_s = self._clock.elapsed_s
        return new_percent / 100.0

    async def _async_underlying_entity_turn_off(self) -> None:
        """Cancel the running cycle and switch the device off, as VTherm does."""
        await self.cycle_scheduler.cancel_cycle()
        await self._actuator.turn_off()

    def recalculate(self) -> None:
        """Run the algorithm once, as VTherm does right after a sensor update."""
        self.prop_algorithm.calculate(
            self.target_temperature,
            self.current_temperature,
            self.current_outdoor_temperature,
            self.last_temperature_slope,
            self.vtherm_hvac_mode,
            power_shedding=self.is_overpowering_detected,
            off_reason=self.hvac_off_reason,
        )

    async def _async_control_heating(self, timestamp=None, force=False) -> bool:
        self.slope.check_age(self._clock.elapsed_s)
        await self.handler.control_heating(timestamp, force)
        return True

    async def temperature_published(self, value_c: float) -> None:
        self.current_temperature = value_c
        self.last_temperature_measure = self._clock.now()
        self.slope.measure(value_c, self._clock.elapsed_s)
        self.recalculate()
        await self.async_control_heating(force=False)

    async def outdoor_published(self, value_c: float) -> None:
        self.current_outdoor_temperature = value_c
        self.recalculate()
        await self.async_control_heating(force=False)


class Simulation:
    """One zone with the real SmartPI handler, run in virtual time."""

    def __init__(
        self,
        monkeypatch,
        zone: ZoneSpec | None = None,
        weather: Weather | None = None,
        known_inputs: Callable[[Simulation], KnownInputs | None] | None = None,
        dependency_layer: bool = False,
        world: SimWorld | None = None,
        node: str = ZONE_NODE,
        suffix: str = "",
    ) -> None:
        self.zone = zone or ZoneSpec()
        self.weather = weather or Weather()
        self.world = world
        self.node = node
        self.suffix = suffix
        if world is None:
            self.clock = VirtualClock()
            self.clock.install(monkeypatch)
            self.queue = EventQueue(self.clock)
            monkeypatch.setattr(
                handler_module,
                "async_track_time_interval",
                lambda _hass, action, interval: self.queue.track_interval(interval.total_seconds(), action),
            )
            self.network = ThermalNetwork()
        else:
            self.clock, self.queue, self.network = world.clock, world.queue, world.network

        self.actuator = SimActuator(self.clock, valve=self.zone.valve)
        self.network.add_node(node, self.zone.initial_c)
        self.network.link_outdoor(node, self.zone.b)
        self.network.add_heater(
            node,
            self.zone.a,
            DelayedPower(self.actuator.history, self.zone.deadtime_min * 60.0),
            self.zone.boiler.emitter_factor(self.zone.emitter) if self.zone.boiler is not None else None,
        )
        for neighbour in self.zone.neighbours:
            if isinstance(neighbour, SimNeighbour):
                self.network.link_boundary(
                    node, neighbour.conductance, neighbour.temperature, neighbour.schedule()
                )

        self.thermostat = SimThermostat(
            self.zone, self.clock, self.actuator, unique_id=f"sim-zone{suffix}", entity_id=f"climate.sim_zone{suffix}"
        )
        if world is not None:
            self.thermostat.hass = world.hass
        _boiler_on_demand(self.zone.boiler, self.actuator)
        # Zones of one world share Home Assistant: its states and the registered provider.
        hass = self.thermostat.hass
        if not isinstance(getattr(hass, "states", None), SimStates):
            hass.states = SimStates()
        self.states: SimStates = hass.states
        self.provider: SimHeatingProvider | None = None
        self.forced_reasons: list[tuple[float, float, str]] = []
        if self.zone.water_data == "provider" and (
            self.zone.boiler is not None or (self.zone.source is not None and self.zone.source.boiler is not None)
        ):
            providers = hass.data.setdefault(PROVIDERS_KEY, {})
            existing = providers.get(PROVIDER_NAME)
            if isinstance(existing, SimHeatingProvider):
                self.provider = existing
                self.forced_reasons = existing.forced_reasons
            else:
                self.provider = SimHeatingProvider(self.clock, self.weather, forced_reasons=self.forced_reasons)
            providers[PROVIDER_NAME] = self.provider
        self._choose_water(self.thermostat, self.zone.boiler)
        self.scheduler, self.handler = self._attach_handler(self.thermostat, self.actuator, self.zone)
        if known_inputs is not None:
            self.algo.set_known_inputs_provider(lambda: known_inputs(self))
        elif dependency_layer:
            self.algo.set_known_inputs_provider(self.dependency_readings)

        self.source_actuator: SimActuator | None = None
        self.source_thermostat: SimThermostat | None = None
        self.source_scheduler: SimCycleScheduler | None = None
        self.source_handler: SmartPIHandler | None = None
        self.source_heater = None
        self.source_indoor_sensor: QuantizedSensor | None = None
        if self.zone.source is not None:
            self._add_source(self.zone.source)
            if self.zone.source.split is not None:
                self._drive_source(monkeypatch, self.zone.source.split)

        self.indoor_sensor = QuantizedSensor(self.zone.sensor_resolution_c)
        self.outdoor_sensor = QuantizedSensor(0.1)
        self.trace: list[TracePoint] = []
        self._next_trace_s = 0.0
        self._started = False

    def _choose_water(self, thermostat: SimThermostat, boiler: SimBoiler | None) -> None:
        """The water data source in the thermostat's options, as the user would choose it."""
        if boiler is None or self.zone.water_data is None:
            return
        if self.zone.water_data == "provider":
            self.provider.zones[thermostat.entity_id] = boiler
            thermostat.smartpi_options[CONF_SMART_PI_WATER_SOURCE] = provider_source(PROVIDER_NAME)
            return
        object_id = thermostat.entity_id.split(".", 1)[1]
        thermostat.smartpi_options.update(
            water_entities(self.states, self.clock, self.weather, boiler, object_id, lambda: self.forced_reasons)
        )
        thermostat.smartpi_options[CONF_SMART_PI_WATER_SOURCE] = SOURCE_ENTITIES

    def force_water_reasons(self, reasons: list[tuple[float, float, str]]) -> None:
        """Reasons of a gap for time windows: the provider gives them, an entity is unavailable."""
        self.forced_reasons[:] = reasons

    def _attach_handler(
        self, thermostat: SimThermostat, actuator: SimActuator, spec: ZoneSpec
    ) -> tuple[SimCycleScheduler, SmartPIHandler]:
        scheduler = SimCycleScheduler(
            self.clock,
            self.queue,
            thermostat,
            actuator,
            spec.cycle_min,
            spec.min_activation_delay_s,
            spec.min_deactivation_delay_s,
        )
        thermostat.cycle_scheduler = scheduler
        handler = SmartPIHandler(thermostat)
        thermostat.handler = handler
        handler.init_algorithm()
        # Persistence is not simulated: Store writes would need a running hass.
        handler._store = None
        handler._legacy_store = None
        handler.on_scheduler_ready(scheduler)
        return scheduler, handler

    def _add_source(self, source: SourceSpec) -> None:
        """Second thermostat of the zone, heating through its delay and lag."""
        self.source_actuator = SimActuator(self.clock, valve=source.valve)
        self.source_heater = self.network.add_lagged_heater(
            self.node,
            source.a_f,
            DelayedPower(self.source_actuator.history, source.deadtime_min * 60.0),
            source.tau_min,
            source.boiler.emitter_factor(source.emitter) if source.boiler is not None else None,
            stages=source.stages,
        )
        _boiler_on_demand(source.boiler, self.source_actuator)
        spec = replace(
            self.zone,
            name="Sim source",
            cycle_min=source.cycle_min,
            valve=source.valve,
            valve_dpercent=0,
            valve_period_min=0.0,
            control_phase_s=source.control_phase_s,
            min_activation_delay_s=source.min_activation_delay_s,
            min_deactivation_delay_s=source.min_deactivation_delay_s,
            setpoint_c=source.setpoint_c if source.setpoint_c is not None else self.zone.setpoint_c,
            neighbours=(),
            options=dict(source.options),
            source=None,
        )
        self.source_thermostat = SimThermostat(
            spec,
            self.clock,
            self.source_actuator,
            unique_id=f"sim-source{self.suffix}",
            entity_id=f"climate.sim_source{self.suffix}",
        )
        # Both thermostats live in the same Home Assistant.
        self.source_thermostat.hass = self.thermostat.hass
        self._choose_water(self.source_thermostat, source.boiler)
        self.source_scheduler, self.source_handler = self._attach_handler(
            self.source_thermostat, self.source_actuator, spec
        )
        self.source_indoor_sensor = QuantizedSensor(self.zone.sensor_resolution_c)

    def _drive_source(self, monkeypatch, split: dict) -> None:
        """Name the second thermostat as the zone thermostat's source, as its options would."""
        item = DependencyItem(item_id="source", kind=KIND_SOURCE, entity="reg-sim-source", extra=dict(split))
        self.handler._dependency_items = [*self.handler._dependency_items, item]
        self.handler._source_settings = SourceSettings.from_item(item)
        self.algo.configure_source(self.handler._source_settings)
        source_id = f"sim-source{self.suffix}"
        monkeypatch.setattr(self.handler, "_resolve_unique_id", lambda _registry_id: source_id)
        self.handler._attach_source()
        self.algo.set_known_inputs_provider(self.dependency_readings)

    @property
    def drives_source(self) -> bool:
        return self.handler._source_settings is not None

    def _thermostats(self) -> list[tuple[SimThermostat, QuantizedSensor]]:
        units = [(self.thermostat, self.indoor_sensor)]
        if self.source_thermostat is not None:
            units.append((self.source_thermostat, self.source_indoor_sensor))
        return units

    @property
    def source_algo(self):
        return self.source_thermostat.prop_algorithm if self.source_thermostat is not None else None

    def dependency_readings(self) -> DependencyReadings:
        """Readings of the configured neighbours, as the handler would provide them."""
        t_s = self.clock.elapsed_s
        return DependencyReadings(
            neighbours=tuple(n.observation(t_s) for n in self.zone.neighbours),
            presets=dict(self.zone.presets),
            source=self.handler._read_source(),
        )

    @property
    def algo(self):
        return self.thermostat.prop_algorithm

    @property
    def zone_c(self) -> float:
        return self.network.temperature(self.node)

    async def start(self) -> None:
        """Publish the first readings and start the thermostats like VTherm at startup."""
        outdoor = self.outdoor_sensor.sample(self.weather.outdoor_c(0.0))
        for thermostat, sensor in self._thermostats():
            indoor = sensor.sample(self.zone_c)
            thermostat.current_outdoor_temperature = outdoor
            thermostat.current_temperature = indoor
            thermostat.last_temperature_measure = self.clock.now()
            thermostat.slope.measure(indoor, 0.0)
            await thermostat.handler.async_startup()
            await thermostat.async_control_heating(force=False)
            self._start_vtherm_control_timer(thermostat, thermostat.cycle_min, self._control_phase(thermostat))
        self._record()
        self._started = True

    def _control_phase(self, thermostat: SimThermostat) -> float:
        if thermostat is self.source_thermostat:
            return self.zone.source.control_phase_s
        return self.zone.control_phase_s

    def _start_vtherm_control_timer(self, thermostat: SimThermostat, cycle_min: float, phase_s: float) -> None:
        """VTherm calls its control cycle every cycle_min, with the time as timestamp."""
        interval_s = cycle_min * 60.0

        async def control(now) -> None:
            await thermostat.async_control_heating(timestamp=now)

        if phase_s <= 0.0:
            self.queue.track_interval(interval_s, control)
            return

        async def first(now) -> None:
            self.queue.track_interval(interval_s, control)
            await control(now)

        self.queue.call_later(phase_s, first)

    async def set_hvac_mode(self, mode: VThermHvacMode) -> None:
        """Change the HVAC mode the way VTherm propagates it to the algorithm handler."""
        self.thermostat.vtherm_hvac_mode = mode
        await self.handler.on_state_changed(True)
        await self.thermostat.async_control_heating(force=True)

    async def run_for(self, hours: float, stop: Callable[[Simulation], bool] | None = None) -> None:
        """Advance the simulation, optionally stopping early when ``stop`` returns True."""
        if not self._started:
            await self.start()
        end_s = self.clock.elapsed_s + hours * 3600.0
        while self.clock.elapsed_s < end_s - 1e-9:
            target_s = min(end_s, self.clock.elapsed_s + SENSOR_STEP_S)
            due = self.queue.next_due()
            if due is not None and due < target_s:
                target_s = max(due, self.clock.elapsed_s)
            self.network.advance(self.clock.elapsed_s, target_s, self.weather.outdoor_c)
            self.clock.advance_to(target_s)
            await self._publish_sensors()
            await self.queue.run_due()
            self._record()
            if stop is not None and stop(self):
                return

    async def _publish_sensors(self) -> None:
        outdoor = self.outdoor_sensor.sample(self.weather.outdoor_c(self.clock.elapsed_s))
        for thermostat, sensor in self._thermostats():
            if outdoor is not None:
                await thermostat.outdoor_published(outdoor)
            indoor = sensor.sample(self.zone_c)
            if indoor is not None:
                await thermostat.temperature_published(indoor)

    def _source_command(self) -> float:
        if self.source_algo is None:
            return 0.0
        if self.drives_source:
            return float(self.algo.source_command or 0.0)
        return float(self.source_algo.on_percent)

    def _record(self) -> None:
        if self.clock.elapsed_s + 1e-9 < self._next_trace_s:
            return
        self._next_trace_s = self.clock.elapsed_s + TRACE_STEP_S
        self.trace.append(
            TracePoint(
                t_h=self.clock.elapsed_s / 3600.0,
                zone_c=self.zone_c,
                sensor_c=self.indoor_sensor.value,
                outdoor_c=self.weather.outdoor_c(self.clock.elapsed_s),
                command=float(self.algo.on_percent),
                power=self.actuator.power,
                phase=str(getattr(self.algo.phase, "value", self.algo.phase)),
                source_command=self._source_command(),
                source_power=self.source_actuator.power if self.source_actuator is not None else 0.0,
            )
        )


def _boiler_on_demand(boiler: SimBoiler | None, actuator: SimActuator) -> None:
    """A boiler firing on demand fires while its circuit called for heat in the last 20 minutes."""
    if boiler is not None and boiler.on_demand:
        history = actuator.history
        boiler.demand = lambda t_s: history.on_seconds(t_s - 1200.0, t_s) > 0.0


class CoupledSimulation:
    """Zones sharing one world, advanced together."""

    def __init__(self, world: SimWorld, zones: list[Simulation]) -> None:
        self.world = world
        self.zones = zones
        self.clock = world.clock

    async def run_for(self, hours: float, stop: Callable[[CoupledSimulation], bool] | None = None) -> None:
        for zone in self.zones:
            if not zone._started:
                await zone.start()
        weather = self.zones[0].weather
        end_s = self.clock.elapsed_s + hours * 3600.0
        while self.clock.elapsed_s < end_s - 1e-9:
            target_s = min(end_s, self.clock.elapsed_s + SENSOR_STEP_S)
            due = self.world.queue.next_due()
            if due is not None and due < target_s:
                target_s = max(due, self.clock.elapsed_s)
            self.world.network.advance(self.clock.elapsed_s, target_s, weather.outdoor_c)
            self.clock.advance_to(target_s)
            for zone in self.zones:
                await zone._publish_sensors()
            await self.world.queue.run_due()
            for zone in self.zones:
                zone._record()
            if stop is not None and stop(self):
                return
