"""The reference governor with the water input.

The FF trim's causal power trace and the zone's dependency model record a
cycle's power at the reference water (the command times the water factor).
When cool water limits the command, the governor's cap does not bind: it would
only cut a command the water already cuts, so it promises nothing a later
measurement could falsify.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE
from custom_components.vtherm_smartpi.smartpi import reference_governor_runtime
from custom_components.vtherm_smartpi.smartpi.governor_falsification import RETIRE_REASON
from sim.boiler import SimBoiler
from sim.runner import Simulation, ZoneSpec
from sim.weather import Weather
from tests.test_smartpi_water_algo import live, make_smartpi


def test_the_causal_power_trace_records_power_at_the_reference_water(monkeypatch) -> None:
    algo = make_smartpi()
    algo.update_water(live(40.0), 20.0)
    recorded = {}
    monkeypatch.setattr(algo._causal_power_trace, "complete_applied_cycle", lambda **kw: recorded.update(kw))

    algo._record_completed_fftrim_power(cycle_end_now=100.0, e_eff=1.4)

    # The applied command is clamped to a full cycle, then scaled to the reference water.
    assert recorded["realized_linear_power"] == pytest.approx(algo.water_factor)
    assert recorded["now_monotonic"] == 100.0

    algo._record_completed_fftrim_power(cycle_end_now=200.0, e_eff=None)
    assert recorded["realized_linear_power"] is None


async def test_the_dependency_model_still_records_the_cycle_power(monkeypatch) -> None:
    algo = make_smartpi()
    algo.update_water(live(40.0), 20.0)
    recorded = []
    monkeypatch.setattr(
        algo.dependency_model, "record_realized_power", lambda *args, **kw: recorded.append((args, kw))
    )

    await algo.on_cycle_completed(e_eff=0.5, elapsed_ratio=1.0, cycle_duration_min=10.0)

    assert len(recorded) == 1
    (start, end, power), options = recorded[0]
    assert end - start == pytest.approx(600.0)
    assert power == pytest.approx(algo._to_linear_power(0.5))
    assert options["level"] == pytest.approx(algo.water_factor)


async def _approach(monkeypatch, phase_s: float) -> dict:
    """48 h at 20 degC, a night at 17 degC, then a morning approach to 21 degC with 45 degC water."""
    retired: list[str] = []
    armed: list[float] = []
    runtime = reference_governor_runtime.ReferenceGovernorRuntime
    arm, retire = runtime.arm, runtime.retire

    def arming(self, target_temp, now_monotonic, *, force=False):
        before = (self._armed, self._target_temp)
        result = arm(self, target_temp, now_monotonic, force=force)
        if self._armed and (self._armed, self._target_temp) != before:
            armed.append(target_temp)
        return result

    def retiring(self, kernel_decision, reason):
        if self._armed:
            retired.append(reason)
        return retire(self, kernel_decision, reason)

    monkeypatch.setattr(runtime, "arm", arming)
    monkeypatch.setattr(runtime, "retire", retiring)
    spec = ZoneSpec(
        a=0.06,
        b=0.001,
        deadtime_min=10.0,
        sensor_resolution_c=0.1,
        setpoint_c=20.0,
        control_phase_s=phase_s,
        boiler=SimBoiler(water_c=45.0),
        emitter="radiator",
        water_data="provider",
        options={CONF_SMART_PI_HEATING_TYPE: "radiator"},
    )
    sim = Simulation(monkeypatch, spec, Weather(base_c=3.0, daily_amplitude_c=3.0))
    await sim.run_for(46.0)

    async def setpoint(value: float) -> None:
        sim.thermostat.target_temperature = value
        sim.zone.setpoint_c = value
        await sim.thermostat.async_control_heating(force=True)

    await setpoint(17.0)
    await sim.run_for(8.0)
    await setpoint(21.0)
    limited_and_capped = limited = 0

    def watch(s) -> bool:
        nonlocal limited_and_capped, limited
        manager = s.algo.sp_mgr
        cap, before = manager.governor_command_cap, manager.governor_u_cmd_before_cap
        capped = cap is not None and before is not None and cap < before - 1e-6
        if s.algo._water_too_cold:
            limited += 1
            limited_and_capped += capped
        return False

    await sim.run_for(8.0, stop=watch)
    return {"armed": armed, "retired": retired, "limited": limited, "limited_and_capped": limited_and_capped}


async def test_a_water_limited_approach_arms_no_constraining_cap(monkeypatch) -> None:
    """With 45 degC water the morning approach is limited by the water, the cap never binds
    then, and no episode is retired as a falsified prediction (in three control phases)."""
    results = []
    for phase_s in (0.0, 170.0, 340.0):
        with monkeypatch.context() as patched:
            results.append(await _approach(patched, phase_s))

    for result in results:
        assert result["limited"] > 0
        assert result["limited_and_capped"] == 0
        assert RETIRE_REASON not in result["retired"]
    # The governor did take part: at least one approach armed an episode.
    assert any(result["armed"] for result in results)
