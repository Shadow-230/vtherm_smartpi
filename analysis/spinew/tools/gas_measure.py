"""Heat (gas) per scenario from the zone's energy balance over three day/night cycles."""
import asyncio, json, os, sys
repo, label, out = sys.argv[1:4]; sys.path[:0]=[repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.boiler import SimBoiler
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE
import test_smartpi_sim_zone_set as Z
def spec(case, split):
    if case in ("valve", "two_stage", "water"):
        b, a = Z.ROOMS["base"]; zone = {}; weather = Z.WEATHERS["mild"]
        source = {"a_f": a, "deadtime_min": 30.0, "tau_min": 180.0, "split": split}
        if case == "valve": source["valve"] = True
        elif case == "two_stage": source["stages"] = 2
        else:
            zone = {"boiler": SimBoiler(curve_slope=1.2), "emitter": "radiator", "water_data": "entities",
                    "options": {CONF_SMART_PI_HEATING_TYPE: "radiator"}}
            source.update(boiler=SimBoiler(curve_slope=0.6, max_water_c=45.0), emitter="floor_water",
                          options={CONF_SMART_PI_HEATING_TYPE: "floor_water"})
        return b, ZoneSpec(a=a, b=b, source=SourceSpec(**source), sensor_resolution_c=0.1, **zone), weather
    room, w = case.split(" "); b, a = Z.ROOMS[room]
    return b, ZoneSpec(a=a, b=b, source=SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=split), sensor_resolution_c=0.1), Z.WEATHERS[w]
async def run(case, split):
    mp = MonkeyPatch(); b, zone, base_c = spec(case, split)
    sim = Simulation(mp, zone, Weather(base_c=base_c, daily_amplitude_c=3.0))
    await sim.run_for(96.0)
    reach, over = [], []
    for hours in Z.NIGHTS_H:
        await Z._setpoint(sim, 17.0); await sim.run_for(hours)
        await Z._setpoint(sim, 20.0); st = sim.clock.elapsed_s / 3600
        boost = float(os.environ.get("ZR_CURVE_BOOST", "0"))
        boilers = [x for x in (zone.boiler, zone.source.boiler if zone.source else None) if x is not None]
        if boost and boilers:
            for bo in boilers: bo.curve_room_c += boost
            await sim.run_for(3.0, stop=lambda s_: s_.zone_c >= 19.8)
            for bo in boilers: bo.curve_room_c -= boost
            await sim.run_for(24.0 - hours - (sim.clock.elapsed_s / 3600 - st))
        else:
            await sim.run_for(24.0 - hours)
        after = [p for p in sim.trace if p.t_h >= st]
        reach.append(next((p.t_h - st for p in after if p.zone_c >= 19.8), None)); over.append(max(p.zone_c for p in after) - 20.0)
    pts = [p for p in sim.trace if p.t_h >= 96.0]
    loss = sum(b * (p.zone_c - p.outdoor_c) * (q.t_h - p.t_h) * 60.0 for p, q in zip(pts, pts[1:]))
    heat = loss + (pts[-1].zone_c - pts[0].zone_c)
    mp.undo()
    return dict(heat=heat, reach=reach, over=over)
async def main():
    cases = [f"{r} {w}" for r in Z.ROOMS for w in Z.WEATHERS] + ["valve", "two_stage", "water"]
    if os.environ.get("ZR_CASES"): cases = os.environ["ZR_CASES"].split(",")
    res = {}
    for case in cases:
        res[case] = await run(case, None if label == "indep" else Z.LEAD_BAND)
        print(label, case, json.dumps(res[case]), flush=True)
    json.dump(res, open(out, "w"), indent=1)
asyncio.run(main())
