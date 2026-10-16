"""A1/D2 measurement: one SmartPI zone, 0.1 degC sensor, own learning (no zone fit).

Usage: python a1_measure.py <repo_root> <label> <out.json>
48 h at 20 degC, then 4 days with setback 17 degC 22:00-06:00 and 21 degC by day.
Per run: learned a/b against the truth at 48 h and at the end; mean |error| and
max overshoot over 09:00-22:00 of the last 3 days; morning time to 20.9 degC.
"""
from __future__ import annotations
import asyncio, json, sys
repo = sys.argv[1]
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, ZoneSpec
from sim.weather import Weather

ZONES = {"radiator": (0.03, 0.001, 10.0), "slow_loss": (0.02, 0.0006, 10.0), "fast": (0.05, 0.0015, 20.0)}

async def _sp(sim, v):
    sim.thermostat.target_temperature = v
    sim.zone.setpoint_c = v
    await sim.thermostat.async_control_heating(force=True)

async def run(name, phase_s):
    a, b, dt = ZONES[name]
    mp = MonkeyPatch()
    try:
        sim = Simulation(mp, ZoneSpec(a=a, b=b, deadtime_min=dt, sensor_resolution_c=0.1, control_phase_s=phase_s),
                         Weather(base_c=3.0, daily_amplitude_c=3.0))
        await sim.run_for(46.0)
        at48 = (sim.algo.est.a / a, sim.algo.est.b / b)
        errs, overs, reach = [], [], []
        for day in range(4):
            await _sp(sim, 17.0); await sim.run_for(8.0)
            await _sp(sim, 21.0); t0 = sim.clock.elapsed_s / 3600.0
            await sim.run_for(16.0)
            pts = [p for p in sim.trace if t0 <= p.t_h < t0 + 16.0]
            r = next((p.t_h - t0 for p in pts if p.zone_c >= 20.9), None)
            reach.append(r)
            if day >= 1:
                day_pts = [p for p in pts if p.t_h >= t0 + 3.0]
                errs.extend(abs(p.zone_c - 21.0) for p in day_pts)
                overs.append(max(p.zone_c for p in pts) - 21.0)
        return {"zone": name, "phase_s": phase_s, "a48": round(at48[0], 3), "b48": round(at48[1], 3),
                "a_end": round(sim.algo.est.a / a, 3), "b_end": round(sim.algo.est.b / b, 3),
                "mae_c": round(sum(errs) / len(errs), 4), "over_max_c": round(max(overs), 3),
                "reach_h": [None if x is None else round(x, 2) for x in reach]}
    finally:
        mp.undo()

async def main():
    out = []
    for name in ZONES:
        for ph in (0.0, 170.0, 340.0):
            r = await run(name, ph); r["label"] = sys.argv[2]; out.append(r)
            print(json.dumps(r), flush=True)
    json.dump(out, open(sys.argv[3], "w"), indent=1)

asyncio.run(main())
