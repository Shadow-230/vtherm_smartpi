"""Steady days of one room: zone, lead, source, x_f, brake, boost, every 10 min in a window."""
import asyncio, os, sys
repo, room, weather, phase, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6])
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
import test_smartpi_sim_zone_set as Z
async def main():
    b, a = Z.ROOMS[room]
    mp = MonkeyPatch()
    sim = Simulation(mp, ZoneSpec(a=a, b=b, source=SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=Z.LEAD_BAND),
                                  sensor_resolution_c=0.1, control_phase_s=phase),
                     Weather(base_c=Z.WEATHERS[weather], daily_amplitude_c=3.0))
    last = [-1e9]; rows = []
    def watch(s):
        t = s.clock.elapsed_s
        if t0 * 3600 <= t <= t1 * 3600 and t - last[0] >= float(os.environ.get('STEP','600')):
            last[0] = t
            d = s.algo.dependency_diagnostics()["second_source"]
            src = s.algo.dependency_model._source
            c = s.algo.ctl; e = s.algo.est
            ki = s.algo._known_inputs
            heat = sum(h.rate for h in ki.heats) if ki is not None and ki.heats else 0.0
            rows.append((t/3600, s.zone_c, float(s.algo.on_percent), d["command"], d["x_f"], d["recovery_boost"], c.u_ff, c.u_i, getattr(c, "u_p", float("nan")), e.a, e.b, heat, str(getattr(c, "last_i_mode", "?")) + "|" + str(c.integral_hold_mode) + "|" + str(c.last_integral_hold_source)))
        return False
    await sim.run_for(t1 + 0.1, stop=watch)
    for r in rows:
        print("%6.2f zone %.2f lead %.2f src %.2f x_f %.2f boost %s ff %+.3f i %+.3f p %+.3f a %.4f b %.5f heat %.4f %s" % r)
asyncio.run(main())
