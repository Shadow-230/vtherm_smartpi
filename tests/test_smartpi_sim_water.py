"""Simulated rooms heated by water: SmartPI with Smart Boiler's water data.

The boiler follows a heating curve (and, where stated, cycles its burner and
gives hot water priority twice a day). The room's radiator gives ``a`` at the
reference water (60 degC); the curve's water is cooler, so without the water
data SmartPI learns a weaker radiator than it is.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.vtherm_smartpi.algo import WATER_INTEGRAL_HOLD
from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE
from sim.boiler import SimBoiler
from sim.metrics import tracking
from sim.runner import Simulation, ZoneSpec
from sim.weather import Weather

A_REFERENCE = 0.06
DHW_H = ((6.5, 7.2), (19.0, 19.7))


def _sim(monkeypatch, water_data: bool, boiler: SimBoiler) -> Simulation:
    spec = ZoneSpec(
        a=A_REFERENCE,
        b=0.001,
        sensor_resolution_c=0.1,
        boiler=boiler,
        emitter="radiator",
        smart_boiler=water_data,
        options={CONF_SMART_PI_HEATING_TYPE: "radiator"},
    )
    return Simulation(monkeypatch, spec, Weather(base_c=3.0, daily_amplitude_c=3.0))


async def test_water_data_let_smartpi_learn_the_radiator_at_the_reference_water(monkeypatch):
    """On a heating curve, SmartPI learns the radiator's power at the reference water."""
    results = {}
    for water_data in (False, True):
        with monkeypatch.context() as patched:
            sim = _sim(patched, water_data, SimBoiler(curve_slope=1.2))
            await sim.run_for(48.0)
            results[water_data] = sim.algo.est.a
            if water_data:
                assert sim.algo.water.state.source == "live"
                assert 0.3 < sim.algo.water_factor < 0.7
                assert sim.smart_boiler.calls > 0
    # Without the data the curve's cooler water looks like a weaker radiator.
    assert results[False] < 0.5 * A_REFERENCE
    assert results[True] > 1.5 * results[False]
    assert 0.5 * A_REFERENCE < results[True] < 1.2 * A_REFERENCE


async def test_hot_water_priority_holds_the_integral_and_the_command(monkeypatch):
    sim = _sim(monkeypatch, True, SimBoiler(curve_slope=1.2, cycle_amplitude_c=5.0, dhw_daily_h=DHW_H))
    windows: dict[int, list[tuple[float, float, str, str]]] = {}

    def watch(s_):
        hour = s_.clock.elapsed_s / 3600.0
        day, within = divmod(hour, 24.0)
        for index, (start, end) in enumerate(DHW_H):
            # Skip the first minutes: the hold starts at the next control call.
            if start + 0.1 <= within < end:
                windows.setdefault(int(day) * 10 + index, []).append(
                    (s_.algo.ctl.integral, s_.algo.on_percent, s_.algo.ctl.last_i_mode, s_.algo.phase.value)
                )
        return False

    await sim.run_for(36.0, stop=watch)
    assert len(windows) >= 3
    assert any(phase != "Hysteresis" for samples in windows.values() for *_, phase in samples)
    for samples in windows.values():
        # Held by the water, or frozen by SmartPI's deadband: the integral never runs
        # (in the learning phase the controller is not used at all).
        assert all(
            mode in (f"I:HOLD({WATER_INTEGRAL_HOLD})", "I:FREEZE(deadband)")
            for _, _, mode, phase in samples
            if phase != "Hysteresis"
        )
        assert max(s[0] for s in samples) - min(s[0] for s in samples) < 1e-9
        assert max(s[1] for s in samples) - min(s[1] for s in samples) < 1e-9
    assert sim.algo.ctl.integral_hold_mode != WATER_INTEGRAL_HOLD or sim.algo.water.state.no_heat
    assert tracking(sim, 24.0, 36.0).mean_error_c < 0.3


async def test_gaps_keep_the_units_and_a_lasting_one_offers_the_reset(monkeypatch):
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    sim = _sim(monkeypatch, True, SimBoiler(curve_slope=1.2))
    sim.smart_boiler.forced_reasons = [(24.0, 26.0, "flow_stale"), (30.0, 99.0, "not_mapped")]
    await sim.run_for(23.9)
    learned_factor = sim.algo.water.state.factor
    long_average = sim.algo.water._long_avg
    await sim.run_for(1.0)
    assert sim.algo.water.state.source == "average"
    assert sim.algo.water_factor == pytest.approx(long_average, rel=0.05)
    assert sim.algo.water_factor == pytest.approx(learned_factor, rel=0.2)
    assert not create_issue.called
    await sim.run_for(7.0)
    assert sim.algo.water.state.source == "lasting"
    assert sim.algo.water_factor == 1.0
    assert create_issue.call_count == 1
    assert create_issue.call_args.kwargs["translation_key"] == "water_data_gone"


async def test_a_boiler_firing_on_demand_does_not_lock_the_zone(monkeypatch):
    """The boiler heats only while the zone calls for heat: between calls its water is at the
    boiler room's temperature. That cold water must not hold the zone off."""
    sim = _sim(monkeypatch, True, SimBoiler(curve_slope=1.2, on_demand=True, idle_c=15.0))
    await sim.run_for(36.0)
    after_start = [p.zone_c for p in sim.trace if p.t_h >= 6.0]
    assert min(after_start) > 19.0
    assert tracking(sim, 12.0, 36.0).mean_error_c < 0.3
    assert sim.algo.water.state.source == "live"
    # SmartPI leaves its learning phase: it learns its dead time from the command step
    # even though the boiler is cold when the zone first calls.
    assert sim.algo.phase.value == "Stable"
    assert sim.algo.dt_est.deadtime_heat_reliable
    assert 0.5 * A_REFERENCE < sim.algo.est.a < 1.2 * A_REFERENCE
