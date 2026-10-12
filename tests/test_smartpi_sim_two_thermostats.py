"""Two thermostats heating one zone: the simulator side and today's behaviour.

The zone has a fast lead (a radiator) and a second thermostat whose emitter has
a transport delay and a first-order lag (a heated floor, or a second radiator).
Without a split, each SmartPI regulates the zone on its own and sees the other
one's heat as a disturbance: that is the baseline the split is compared with.
"""

from __future__ import annotations

import math
import os

import pytest

from sim.metrics import tracking
from sim.plant import DelayedPower, PowerHistory, ThermalNetwork
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather

LOCAL_ONLY = pytest.mark.skipif(
    os.environ.get("SMARTPI_SIM_LOCAL") != "1",
    reason="long simulation, run locally with SMARTPI_SIM_LOCAL=1",
)
OUTDOOR = Weather(base_c=3.0, daily_amplitude_c=3.0)
FLOOR = SourceSpec(a_f=0.03, deadtime_min=30.0, tau_min=180.0)
RADIATOR = SourceSpec(a_f=0.03, deadtime_min=10.0, tau_min=0.0)


def test_lagged_heater_follows_its_delay_and_lag() -> None:
    """Heat reaches a node after the delay, through a first-order lag."""
    history = PowerHistory(0.0)
    history.set(0.0, 1.0)
    network = ThermalNetwork()
    network.add_node("zone", 20.0)
    heater = network.add_lagged_heater("zone", 0.03, DelayedPower(history, 1800.0), tau_min=180.0)
    outdoor = lambda _t: 20.0  # noqa: E731

    network.advance(0.0, 1790.0, outdoor)
    assert heater.output == 0.0
    network.advance(1790.0, 1800.0 + 10800.0, outdoor)
    assert heater.output == pytest.approx(1.0 - math.exp(-1.0), abs=1e-6)
    # With no loss, the node gains the lag's integrated output: tau * exp(-1) minutes at full power.
    expected_gain = 0.03 * 180.0 * math.exp(-1.0)
    assert network.temperature("zone") == pytest.approx(20.0 + expected_gain, abs=1e-6)


async def test_two_thermostats_heat_one_zone(monkeypatch) -> None:
    """Both thermostats run their own SmartPI, cycles and sensor on the same zone."""
    sim = Simulation(monkeypatch, ZoneSpec(source=FLOOR, initial_c=17.0), OUTDOOR)
    await sim.run_for(8.0)

    assert sim.source_handler is not sim.handler
    assert sim.source_thermostat.hass is sim.thermostat.hass
    assert sim.actuator.turn_on_count > 0
    assert sim.source_actuator.turn_on_count > 0
    assert sim.source_heater.output > 0.0
    assert any(p.source_power > 0.0 for p in sim.trace)
    assert max(p.zone_c for p in sim.trace) > 19.0


@LOCAL_ONLY
@pytest.mark.parametrize("source", [FLOOR, RADIATOR], ids=["Z1_floor", "Z4_radiator"])
async def test_baseline_two_independent_smartpi(monkeypatch, source) -> None:
    """Today: two SmartPI on one zone, each regulating alone. Report how the zone is held."""
    sim = Simulation(monkeypatch, ZoneSpec(source=source, sensor_resolution_c=0.1), OUTDOOR)
    await sim.run_for(96.0)
    result = tracking(sim, 48.0, 96.0)
    print(
        f"\nbaseline {source.tau_min:.0f} min lag: mean error {result.mean_error_c:.3f} degC,"
        f" above {result.max_above_c:.2f}, below {result.max_below_c:.2f},"
        f" turn-ons lead {result.lead_turn_ons} source {result.source_turn_ons},"
        f" source share {result.source_share:.2f}"
    )

    assert result.max_below_c < 2.0
