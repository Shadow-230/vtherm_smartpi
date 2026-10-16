"""Zone recovery measurement: per case reach, overshoot, steady, night mean/min, heat per day."""
import asyncio, json, os, sys
repo, label, out = sys.argv[1:4]; sys.path[:0]=[repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
from sim.metrics import tracking
import test_smartpi_sim_zone_set as Z
CASES = [(r, w) for r in Z.ROOMS for w in Z.WEATHERS]
async def run(room, weather, split):
    mp=MonkeyPatch(); b,a=Z.ROOMS[room]
    sim=Simulation(mp, ZoneSpec(a=a,b=b,source=SourceSpec(a_f=a,deadtime_min=30.0,tau_min=180.0,split=split),sensor_resolution_c=0.1), Weather(base_c=Z.WEATHERS[weather],daily_amplitude_c=3.0))
    await sim.run_for(96.0)
    steady = tracking(sim, 48.0, 96.0).mean_error_c
    reach, over, nmean, nmin = [], [], [], []
    t0 = sim.clock.elapsed_s
    for hours in Z.NIGHTS_H:
        await Z._setpoint(sim, 17.0); s = sim.clock.elapsed_s/3600
        await sim.run_for(hours); e = sim.clock.elapsed_s/3600
        pts=[p for p in sim.trace if s<=p.t_h<e]; nmean.append(sum(p.zone_c for p in pts)/len(pts)); nmin.append(min(p.zone_c for p in pts))
        await Z._setpoint(sim, 20.0); st = sim.clock.elapsed_s/3600
        await sim.run_for(24.0-hours)
        after=[p for p in sim.trace if p.t_h>=st]
        reach.append(next((p.t_h-st for p in after if p.zone_c>=19.8), None)); over.append(max(p.zone_c for p in after)-20.0)
    t1 = sim.clock.elapsed_s
    heat = a*sim.actuator.history.on_seconds(t0,t1) + a*sim.source_actuator.history.on_seconds(t0,t1)
    mp.undo()
    return dict(steady=steady, reach=reach, over=over, nmean=sum(nmean)/3, nmin=min(nmin), heat_per_day=heat/3/3600*60)
async def main():
    only = os.environ.get("ZR_ONLY")
    res = {}
    for room, weather in CASES:
        key=f"{room} {weather}"
        if only and key not in only.split(","): continue
        r = {"split": await run(room, weather, Z.LEAD_BAND)}
        if os.environ.get("ZR_INDEP") == "1": r["indep"] = await run(room, weather, None)
        res[key]=r; print(label, key, json.dumps(r), flush=True)
    json.dump(res, open(out, "w"), indent=1)
asyncio.run(main())
