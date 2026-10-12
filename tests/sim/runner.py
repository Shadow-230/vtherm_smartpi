"""Simulation runner: the real SmartPI handler driven in virtual time.

The thermostat side reproduces what Versatile Thermostat does around the
algorithm: on each published temperature it updates its moving average and
slope, recalculates the algorithm, then runs the control cycle. Three periodic
sources run as timers of the virtual clock: VTherm's own control call every
``cycle_min``, the handler's 60 s recalculation timer and the cycle scheduler.
A valve thermostat retains integer-percent commands like VTherm's
``apply_valve_command_percent``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from custom_components.vtherm_smartpi import handler as handler_module
from custom_components.vtherm_smartpi.const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.smartpi.dependencies import (
    KIND_NEIGHBOUR,
    MODE_DOOR,
    DependencyItem,
)
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

from fakes.fake_thermostat_runtime import FakeThermostatRuntime

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


def FixedNeighbour(key: str, temperature_c: float, conductance: float) -> SimNeighbour:  # noqa: N802
    """Neighbour at a constant temperature, always connected."""
    return SimNeighbour(key=key, temperature_c=temperature_c, conductance=conductance)


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


@dataclass
class TracePoint:
    t_h: float
    zone_c: float
    sensor_c: float | None
    outdoor_c: float
    command: float
    power: float
    phase: str


class SimThermostat(FakeThermostatRuntime):
    """Thermostat runtime whose signals come from the simulation."""

    def __init__(self, spec: ZoneSpec, clock: VirtualClock, actuator: SimActuator) -> None:
        super().__init__()
        self._clock = clock
        self._actuator = actuator
        self.handler: SmartPIHandler | None = None
        self.name = spec.name
        self.unique_id = "sim-zone"
        self.entity_id = "climate.sim_zone"
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
        self.entry_infos = {
            CONF_MINIMAL_ACTIVATION_DELAY: spec.min_activation_delay_s,
            CONF_MINIMAL_DEACTIVATION_DELAY: spec.min_deactivation_delay_s,
            **spec.options,
        }

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
    ) -> None:
        self.zone = zone or ZoneSpec()
        self.weather = weather or Weather()
        self.clock = VirtualClock()
        self.clock.install(monkeypatch)
        self.queue = EventQueue(self.clock)
        monkeypatch.setattr(
            handler_module,
            "async_track_time_interval",
            lambda _hass, action, interval: self.queue.track_interval(interval.total_seconds(), action),
        )

        self.actuator = SimActuator(self.clock, valve=self.zone.valve)
        self.network = ThermalNetwork()
        self.network.add_node(ZONE_NODE, self.zone.initial_c)
        self.network.link_outdoor(ZONE_NODE, self.zone.b)
        self.network.add_heater(
            ZONE_NODE,
            self.zone.a,
            DelayedPower(self.actuator.history, self.zone.deadtime_min * 60.0),
        )
        for neighbour in self.zone.neighbours:
            self.network.link_boundary(
                ZONE_NODE, neighbour.conductance, neighbour.temperature, neighbour.schedule()
            )

        self.thermostat = SimThermostat(self.zone, self.clock, self.actuator)
        self.scheduler = SimCycleScheduler(
            self.clock,
            self.queue,
            self.thermostat,
            self.actuator,
            self.zone.cycle_min,
            self.zone.min_activation_delay_s,
            self.zone.min_deactivation_delay_s,
        )
        self.thermostat.cycle_scheduler = self.scheduler
        self.handler = SmartPIHandler(self.thermostat)
        self.thermostat.handler = self.handler
        self.handler.init_algorithm()
        # Persistence is not simulated: Store writes would need a running hass.
        self.handler._store = None
        self.handler._legacy_store = None
        self.handler.on_scheduler_ready(self.scheduler)
        if known_inputs is not None:
            self.algo.set_known_inputs_provider(lambda: known_inputs(self))
        elif dependency_layer:
            self.algo.set_known_inputs_provider(self.dependency_readings)

        self.indoor_sensor = QuantizedSensor(self.zone.sensor_resolution_c)
        self.outdoor_sensor = QuantizedSensor(0.1)
        self.trace: list[TracePoint] = []
        self._next_trace_s = 0.0
        self._started = False

    def dependency_readings(self) -> DependencyReadings:
        """Readings of the configured neighbours, as the handler would provide them."""
        t_s = self.clock.elapsed_s
        return DependencyReadings(
            neighbours=tuple(n.observation(t_s) for n in self.zone.neighbours),
            presets=dict(self.zone.presets),
        )

    @property
    def algo(self):
        return self.thermostat.prop_algorithm

    @property
    def zone_c(self) -> float:
        return self.network.temperature(ZONE_NODE)

    async def start(self) -> None:
        """Publish the first readings and start the thermostat like VTherm at startup."""
        outdoor = self.outdoor_sensor.sample(self.weather.outdoor_c(0.0))
        indoor = self.indoor_sensor.sample(self.zone_c)
        self.thermostat.current_outdoor_temperature = outdoor
        self.thermostat.current_temperature = indoor
        self.thermostat.last_temperature_measure = self.clock.now()
        self.thermostat.slope.measure(indoor, 0.0)
        await self.handler.async_startup()
        await self.thermostat.async_control_heating(force=False)
        self._start_vtherm_control_timer()
        self._record()
        self._started = True

    def _start_vtherm_control_timer(self) -> None:
        """VTherm calls its control cycle every cycle_min, with the time as timestamp."""
        interval_s = self.zone.cycle_min * 60.0

        async def control(now) -> None:
            await self.thermostat.async_control_heating(timestamp=now)

        if self.zone.control_phase_s <= 0.0:
            self.queue.track_interval(interval_s, control)
            return

        async def first(now) -> None:
            self.queue.track_interval(interval_s, control)
            await control(now)

        self.queue.call_later(self.zone.control_phase_s, first)

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
        if outdoor is not None:
            await self.thermostat.outdoor_published(outdoor)
        indoor = self.indoor_sensor.sample(self.zone_c)
        if indoor is not None:
            await self.thermostat.temperature_published(indoor)

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
            )
        )

