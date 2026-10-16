"""Bathroom with a daily shower: heat for 15 min and a humidity pulse; with and without humidity."""
import asyncio, math, os, sys
repo = sys.argv[1]; sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, ZoneSpec
from sim.plant import Schedule
from sim.weather import Weather
from sim.metrics import tracking
SHOWER_H = 7.0; DUR_MIN = float(os.environ.get("DUR", 15)); DAYS = range(int(os.environ.get("D0", 3)), int(os.environ.get("D1", 8)))
def humidity(t_h):
    h = 55.0
    for d in DAYS:
        start = d * 24 + SHOWER_H
        if t_h < start: continue
        dt_min = (t_h - start) * 60
        rise = 25.0 * min(dt_min / 10.0, 1.0)
        if dt_min > DUR_MIN:
            rise = 25.0 * math.exp(-(dt_min - DUR_MIN) / 40.0)
        h = max(h, 55.0 + rise)
    return h
async def run(with_humidity, a, b, g):
    mp = MonkeyPatch()
    zone = ZoneSpec(a=a, b=b, sensor_resolution_c=0.1)
    sim = Simulation(mp, zone, Weather(base_c=3.0, daily_amplitude_c=3.0))
    showers = tuple(((d * 24 + SHOWER_H) * 3600, (d * 24 + SHOWER_H) * 3600 + DUR_MIN * 60) for d in DAYS)
    sim.network.link_boundary(sim.node, g, lambda t: 40.0, Schedule(showers))
    def watch(s):
        s.thermostat.current_humidity = humidity(s.clock.elapsed_s / 3600) if with_humidity else None
        th = s.clock.elapsed_s / 3600
        if os.environ.get("TRACE") and with_humidity and any(d*24+SHOWER_H-0.2 <= th <= d*24+SHOWER_H+3 for d in DAYS):
            al = s.algo
            print(f"{th:7.2f} T={s.trace[-1].zone_c if s.trace else 0:5.2f} H={s.thermostat.current_humidity:5.1f} ev={al.humidity_event.state:8s} gate={al.learning_gate} i={al.ctl.integral:7.3f} imode={al.ctl.last_i_mode} u={al.calculated_on_percent:.2f}")
        return False
    await sim.run_for(DAYS.stop * 24 + 2, stop=watch)
    res = []
    for d in DAYS:
        end = d * 24 + SHOWER_H + DUR_MIN / 60
        pts = [p for p in sim.trace if end <= p.t_h <= end + 6]
        res.append((max(p.zone_c for p in sim.trace if d*24+SHOWER_H <= p.t_h <= end + 1) - 20.0, min(p.zone_c for p in pts) - 20.0))
    st = tracking(sim, DAYS.start * 24, DAYS.stop * 24).mean_error_c
    out = dict(a=sim.algo.est.a / a, b=sim.algo.est.b / b, steady=st, peaks=[round(r[0], 2) for r in res], dips=[round(r[1], 2) for r in res],
               events=sim.algo.humidity_event.count, i=round(sim.algo.Ki * sim.algo.ctl.integral, 3))
    mp.undo(); return out
async def main():
    a, b, g = float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    for w in (False, True):
        print("humidity" if w else "no humidity", await run(w, a, b, g), flush=True)
asyncio.run(main())
