"""Zone set rooms with other control phases: reach and overshoot, split (fast) vs independent."""
import asyncio, os, sys
repo, room, weather, phase = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
import test_smartpi_sim_zone_set as Z
async def main():
    b, a = Z.ROOMS[room]
    out = {}
    for name, split in (("split", {**Z.LEAD_BAND, **({"recovery": os.environ["ZR_REC"]} if os.environ.get("ZR_REC") else {})}), ("indep", None)):
        mp = MonkeyPatch()
        sim = Simulation(mp, ZoneSpec(a=a, b=b, source=SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=split),
                                      sensor_resolution_c=0.1, control_phase_s=phase),
                         Weather(base_c=Z.WEATHERS[weather], daily_amplitude_c=3.0))
        r = await Z.run_room(sim)
        mp.undo()
        out[name] = (sum(r.reach_h) / 3, max(r.overshoots), r.steady)
    s, i = out["split"], out["indep"]
    print(f"{room} {weather} phase={phase:.0f}: reach {s[0]:.2f}/{i[0]:.2f} ({s[0]-i[0]:+.2f}) over {s[1]:+.2f}/{i[1]:+.2f} steady {s[2]:.3f}/{i[2]:.3f}", flush=True)
asyncio.run(main())
