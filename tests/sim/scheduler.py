"""Cycle scheduler for SmartPI simulations.

``SimCycleScheduler`` extends the handler-facing contract of
``tests/fakes/fake_cycle_scheduler.py`` with virtual time, PWM, minimal
activation and deactivation delays and valve mode. It follows the behaviour of
the Versatile Thermostat cycle scheduler for one underlying device, without
importing it: that class is internal to Versatile Thermostat and changes with
its ``master`` branch, which the CI checks out unpinned.

Valve commands go through the thermostat's ``apply_valve_command_percent`` when it
has one, as in VTherm. Difference on purpose: exceptions raised by cycle
callbacks propagate instead of being logged, so a simulation fails loudly.
"""

from __future__ import annotations

from typing import Any, Callable

from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_OFF

from .clock import EventQueue, VirtualClock
from .plant import PowerHistory

_NO_HISTORY_S = 999_999.0


class SimActuator:
    """One underlying device: a switch (on/off) or a valve (opening 0..1)."""

    def __init__(self, clock: VirtualClock, valve: bool = False) -> None:
        self._clock = clock
        self.valve = valve
        self.history = PowerHistory(0.0)
        self.last_change_s: float | None = None
        self.turn_on_count = 0

    @property
    def power(self) -> float:
        return self.history.value_at(self._clock.elapsed_s)

    @property
    def is_device_active(self) -> bool:
        return self.power > 0.0

    def _set(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if value == self.power:
            return
        if not self.is_device_active and value > 0.0:
            self.turn_on_count += 1
        self.history.set(self._clock.elapsed_s, value)
        self.last_change_s = self._clock.elapsed_s

    async def turn_on(self) -> None:
        self._set(1.0)

    async def turn_off(self) -> None:
        self._set(0.0)

    async def set_opening(self, fraction: float) -> None:
        self._set(fraction)


def cycle_times(
    on_percent: float, cycle_s: float, min_activation_s: int, min_deactivation_s: int
) -> tuple[int, int]:
    """Convert a command into on/off seconds, honouring the minimal delays."""
    on_percent = max(0.0, min(1.0, on_percent))
    on_time = on_percent * cycle_s
    if 0 < on_time < min_activation_s:
        on_time = 0
    off_time = cycle_s - on_time
    if on_time < cycle_s and off_time < min_deactivation_s:
        on_time, off_time = cycle_s, 0
    return int(on_time), int(off_time)


def _target_state(on_t: float, off_t: float, current_t: float, cycle_s: float) -> tuple[bool, float]:
    """Return the scheduled device state and the time of its next change."""
    if off_t > on_t:
        if current_t < on_t:
            return False, on_t
        if current_t < off_t:
            return True, off_t
        return False, cycle_s
    if current_t < off_t:
        return True, off_t
    if current_t < on_t:
        return False, on_t
    return True, cycle_s


class SimCycleScheduler:
    """Master-cycle scheduler driving one ``SimActuator`` in virtual time."""

    def __init__(
        self,
        clock: VirtualClock,
        queue: EventQueue,
        thermostat: Any,
        actuator: SimActuator,
        cycle_min: float,
        min_activation_delay: int = 0,
        min_deactivation_delay: int = 0,
    ) -> None:
        self._clock = clock
        self._queue = queue
        self._thermostat = thermostat
        self._actuator = actuator
        self._cycle_duration_sec = float(cycle_min) * 60.0
        self.min_activation_delay = int(min_activation_delay)
        self.min_deactivation_delay = int(min_deactivation_delay)
        self._start_callbacks: list[Callable] = []
        self._end_callbacks: list[Callable] = []
        self._tick_cancel: Callable[[], None] | None = None
        self._cycle_end_cancel: Callable[[], None] | None = None
        self._is_cancelling = False
        self._is_starting = False
        self._pending: tuple[Any, float, float, float] = (None, 0, 0, 0.0)
        self._active: tuple[Any, float, float, float] = (None, 0, 0, 0.0)
        self._cycle_start_s: float | None = None
        self._ticking = False
        self._on_t = 0.0
        self._off_t = 0.0
        self._penalty_s = 0.0
        self._valve_trace: list[tuple[float, float]] = []
        self.cycle_count = 0

    @property
    def is_cycle_running(self) -> bool:
        return (
            self._tick_cancel is not None
            or self._cycle_end_cancel is not None
            or self._is_cancelling
            or self._is_starting
        )

    @property
    def is_valve_mode(self) -> bool:
        return self._actuator.valve

    def register_cycle_start_callback(self, callback: Callable) -> None:
        self._start_callbacks.append(callback)

    def register_cycle_end_callback(self, callback: Callable) -> None:
        self._end_callbacks.append(callback)

    async def start_cycle(self, hvac_mode, on_percent: float, force: bool = False) -> None:
        """Start a master cycle, or update the running one like the VTherm scheduler."""
        on_percent = max(0.0, min(1.0, float(on_percent)))
        if self.is_valve_mode:
            on_percent = self._resolve_valve_on_percent(on_percent, force)
        on_time, off_time = cycle_times(
            on_percent,
            self._cycle_duration_sec,
            self.min_activation_delay,
            self.min_deactivation_delay,
        )
        if self.is_valve_mode:
            realized = on_percent
        else:
            realized = on_time / self._cycle_duration_sec
        self._thermostat._on_time_sec = on_time
        self._thermostat._off_time_sec = off_time
        request = (hvac_mode, on_time, off_time, realized)

        if self.is_cycle_running and not force:
            if self.is_valve_mode:
                self._pending = self._active = request
                await self._apply_valve(realized)
                self._append_valve_trace(realized)
                return
            if self._active[1] > 0 or self._same_request(self._active, request):
                self._pending = request
                return

        await self._cancel()
        self._pending = self._active = request
        self._is_starting = True
        try:
            for callback in self._start_callbacks:
                await callback(
                    on_time_sec=on_time,
                    off_time_sec=off_time,
                    on_percent=realized,
                    hvac_mode=hvac_mode,
                )
            self.cycle_count += 1
            if self.is_valve_mode:
                await self._start_valve(realized)
            else:
                await self._start_switch(hvac_mode, on_time, realized)
        finally:
            self._is_starting = False

    async def cancel_cycle(self) -> None:
        await self._cancel()

    def _resolve_valve_on_percent(self, on_percent: float, force: bool) -> float:
        """Return the valve command retained by the thermostat, if it filters commands."""
        resolver = getattr(type(self._thermostat), "apply_valve_command_percent", None)
        if resolver is None:
            return on_percent
        return max(0.0, min(1.0, float(resolver(self._thermostat, on_percent, force=force))))

    @staticmethod
    def _same_request(first: tuple, second: tuple) -> bool:
        return (
            first[0] == second[0]
            and first[1] == second[1]
            and first[2] == second[2]
            and abs(first[3] - second[3]) <= 1e-9
        )

    async def _start_switch(self, hvac_mode, on_time: float, on_percent: float) -> None:
        self._cycle_start_s = self._clock.elapsed_s
        if hvac_mode == VThermHvacMode_OFF or on_time <= 0:
            if self._actuator.is_device_active:
                await self._actuator.turn_off()
        elif on_time >= self._cycle_duration_sec:
            await self._actuator.turn_on()
        else:
            self._ticking = True
            self._penalty_s = 0.0
            self._on_t = 0.0
            self._off_t = (self._on_t + self._cycle_duration_sec * on_percent) % self._cycle_duration_sec
            await self._tick(initial=True)
        self._cycle_end_cancel = self._queue.call_later(self._cycle_duration_sec, self._on_cycle_end)

    async def _tick_callback(self, _now) -> None:
        await self._tick(initial=False)

    async def _tick(self, initial: bool) -> None:
        """Apply the scheduled state, delaying changes that would break the minimal delays."""
        current_t = 0.0 if initial else self._clock.elapsed_s - self._cycle_start_s
        if not initial and current_t >= self._cycle_duration_sec:
            return
        next_tick = self._cycle_duration_sec - current_t
        target_on, change_at = _target_state(self._on_t, self._off_t, current_t, self._cycle_duration_sec)
        state_duration = change_at - current_t
        if 0 < state_duration < next_tick:
            next_tick = state_duration
        last_change = self._actuator.last_change_s
        under_dt = _NO_HISTORY_S if last_change is None else self._clock.elapsed_s - last_change

        if target_on and not self._actuator.is_device_active:
            if under_dt >= self.min_deactivation_delay and state_duration > self.min_activation_delay:
                await self._actuator.turn_on()
            else:
                new_on_t = max(0.0, self._on_t - state_duration)
                penalty = state_duration
                if new_on_t - current_t < self.min_deactivation_delay - under_dt:
                    new_on_t = current_t + (self.min_deactivation_delay - under_dt)
                    penalty = min(new_on_t - current_t, state_duration)
                self._on_t = new_on_t
                self._penalty_s += penalty
                if 0 < new_on_t - current_t < next_tick:
                    next_tick = new_on_t - current_t
        elif not target_on and self._actuator.is_device_active:
            if under_dt >= self.min_activation_delay and state_duration > self.min_deactivation_delay:
                await self._actuator.turn_off()
            else:
                new_off_t = max(0.0, self._off_t - state_duration)
                penalty = -state_duration
                if new_off_t - current_t < self.min_activation_delay - under_dt:
                    new_off_t = current_t + (self.min_activation_delay - under_dt)
                    penalty = -min(new_off_t - current_t, state_duration)
                self._off_t = new_off_t
                self._penalty_s += penalty
                if 0 < new_off_t - current_t < next_tick:
                    next_tick = new_off_t - current_t

        self._tick_cancel = self._queue.call_later(max(0.1, next_tick), self._tick_callback)

    async def _start_valve(self, on_percent: float) -> None:
        self._cycle_start_s = self._clock.elapsed_s
        await self._apply_valve(on_percent)
        self._valve_trace = [(0.0, on_percent)]
        self._cycle_end_cancel = self._queue.call_later(self._cycle_duration_sec, self._on_cycle_end)

    async def _apply_valve(self, on_percent: float) -> None:
        await self._actuator.set_opening(on_percent)

    def _append_valve_trace(self, on_percent: float) -> None:
        if self._cycle_start_s is None:
            self._valve_trace = [(0.0, on_percent)]
            return
        last_offset = 0.0
        if self._valve_trace:
            last_offset, last_value = self._valve_trace[-1]
            if abs(last_value - on_percent) <= 1e-9:
                return
        offset = min(max(0.0, self._clock.elapsed_s - self._cycle_start_s), self._cycle_duration_sec)
        self._valve_trace.append((max(offset, last_offset), on_percent))

    async def _on_cycle_end(self, _now) -> None:
        if not self.is_cycle_running:
            return
        await self.start_cycle(self._pending[0], self._pending[3], force=True)

    def _realized_e_eff(self, elapsed_s: float) -> float:
        """Power realized over the elapsed part of the cycle, as VTherm reports it."""
        if elapsed_s <= 0:
            return 0.0
        if self.is_valve_mode:
            if not self._valve_trace:
                return max(0.0, min(1.0, self._active[3]))
            weighted = 0.0
            for idx, (start, value) in enumerate(self._valve_trace):
                if start >= elapsed_s:
                    break
                end = self._valve_trace[idx + 1][0] if idx + 1 < len(self._valve_trace) else elapsed_s
                end = min(end, elapsed_s)
                if end > start:
                    weighted += (end - start) * value
            return max(0.0, min(1.0, weighted / elapsed_s))
        if not self._ticking:
            return 1.0 if self._active[1] >= self._cycle_duration_sec else 0.0
        if self._off_t >= self._on_t:
            on_s = max(0.0, min(self._off_t, elapsed_s) - min(self._on_t, elapsed_s))
        else:
            on_s = min(self._off_t, elapsed_s) + max(0.0, elapsed_s - min(self._on_t, elapsed_s))
        return max(0.0, min(1.0, max(0.0, on_s - self._penalty_s) / elapsed_s))

    async def _cancel(self) -> None:
        was_running = self.is_cycle_running
        self._is_cancelling = True
        if self._tick_cancel is not None:
            self._tick_cancel()
            self._tick_cancel = None
        if self._cycle_end_cancel is not None:
            self._cycle_end_cancel()
            self._cycle_end_cancel = None
        elapsed_s = 0.0 if self._cycle_start_s is None else self._clock.elapsed_s - self._cycle_start_s
        if was_running and elapsed_s > 1.0:
            e_eff = self._realized_e_eff(elapsed_s)
            elapsed_ratio = min(1.0, elapsed_s / self._cycle_duration_sec)
            for callback in self._end_callbacks:
                await callback(
                    e_eff=e_eff,
                    elapsed_ratio=elapsed_ratio,
                    cycle_duration_min=self._cycle_duration_sec / 60.0,
                )
        self._ticking = False
        self._valve_trace = []
        self._pending = self._active = (None, 0, 0, 0.0)
        self._cycle_start_s = None
        self._is_cancelling = False
