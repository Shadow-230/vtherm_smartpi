"""Morning after a setback in one room: zone, lead, source command, x_f, boost, brake, every 10 min."""
import asyncio, os, sys
repo, room, weather, phase = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
import test_smartpi_sim_zone_set as Z
async def main():
    b, a = Z.ROOMS[room]
    split = {**Z.LEAD_BAND, **({"recovery": os.environ["ZR_REC"]} if os.environ.get("ZR_REC") else {})}
    mp = MonkeyPatch()
    sim = Simulation(mp, ZoneSpec(a=a, b=b, source=SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=split),
                                  sensor_resolution_c=0.1, control_phase_s=phase),
                     Weather(base_c=Z.WEATHERS[weather], daily_amplitude_c=3.0))
    rows = []
    last = [-1e9]
    def watch(s):
        t = s.clock.elapsed_s
        if t - last[0] >= 600:
            last[0] = t
            d = s.algo.dependency_diagnostics()["second_source"]
            rows.append((t / 3600, s.zone_c, float(s.algo.on_percent), d["command"], d["x_f"], d["recovery_boost"], d.get("setback"), getattr(s.algo.dependency_model._source, "braked", None)))
        return False
    await sim.run_for(96.0)
    for hours in Z.NIGHTS_H:
        await Z._setpoint(sim, 17.0)
        start = sim.clock.elapsed_s / 3600
        await sim.run_for(hours, stop=watch)
        await Z._setpoint(sim, 20.0)
        up = sim.clock.elapsed_s / 3600
        await sim.run_for(24.0 - hours, stop=watch)
        print(f"--- night {hours} h, setpoint up at {up:.2f}")
        for r in rows:
            if up - 2.0 <= r[0] <= up + 4.0 or (start <= r[0] < up and int((r[0]-start)*6) % 6 == 0):
                print("%7.2f zone %.2f lead %.2f src %.2f x_f %.2f boost %s setback %s braked %s" % r)
        rows.clear()
asyncio.run(main())
