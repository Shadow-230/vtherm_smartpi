"""Probe: morning approach of a zone with an always-connected neighbour (run from a checkout)."""
import asyncio, sys
repo = sys.argv[1]
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import SimNeighbour, Simulation, ZoneSpec
from sim.weather import Weather

async def run(phase_s, layer):
    mp = MonkeyPatch()
    try:
        zone = ZoneSpec(a=0.03, b=0.001, deadtime_min=10.0, sensor_resolution_c=0.01, setpoint_c=20.0,
                        control_phase_s=phase_s,
                        neighbours=(SimNeighbour("hall", 20.0, 0.0015, mode="always", steps=((30.0, 4.0), (66.0, -4.0))),))
        sim = Simulation(mp, zone, Weather(base_c=5.0, daily_amplitude_c=3.0), dependency_layer=layer)
        await sim.run_for(70.0)  # 22:00 of day 3
        out = []
        for night in range(2):
            async def sp(v):
                sim.thermostat.target_temperature = v; sim.zone.setpoint_c = v
                await sim.thermostat.async_control_heating(force=True)
            await sp(17.0); await sim.run_for(8.0)
            start = sim.clock.elapsed_s / 3600.0
            await sp(20.0)
            pts = []
            await sim.run_for(8.0, stop=lambda s: pts.append((s.clock.elapsed_s/3600.0, s.zone_c)) and False)
            await sim.run_for(8.0)
            reach = next((t - start for t, z in pts if z >= 19.9), None)
            out.append((None if reach is None else round(reach, 2), round(max(z for _, z in pts) - 20.0, 3)))
        return out
    finally:
        mp.undo()

async def main():
    for phase in (0.0, 170.0, 340.0):
        print(sys.argv[2], phase, await run(phase, True), flush=True)
asyncio.run(main())
