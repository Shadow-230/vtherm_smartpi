"""Log each change of the lead's integral in a window."""
import asyncio, os, sys
repo, room, weather, phase, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6])
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
import test_smartpi_sim_zone_set as Z
from custom_components.vtherm_smartpi.algo import SmartPI as _A
_orig=_A.update_realized_power
CLOCK=[None]
def _upd(self, *a, **k):
    i0=self.Ki*self.ctl.integral
    r=_orig(self, *a, **k)
    t=CLOCK[0].elapsed_s/3600 if CLOCK[0] else 0
    if T0<=t<=T1 and self._name.startswith("Sim zone") and not "source" in self._name.lower():
        print(f"   AW t={t:.3f} {self._name} args={a} kw={ {kk:(round(v,3) if isinstance(v,float) else v) for kk,v in k.items()} } du={self._last_aw_du:+.3f} dUi={self.Ki*self.ctl.integral-i0:+.4f} imode={self.ctl.last_i_mode} uff={self.ctl.u_ff:.3f}")
    return r
_A.update_realized_power=_upd
T0,T1=t0,t1
async def main():
    b, a = Z.ROOMS[room]
    mp = MonkeyPatch()
    sim = Simulation(mp, ZoneSpec(a=a, b=b, source=SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=Z.LEAD_BAND),
                                  sensor_resolution_c=0.1, control_phase_s=phase),
                     Weather(base_c=Z.WEATHERS[weather], daily_amplitude_c=3.0))
    last = [None]; CLOCK[0]=sim.clock
    def watch(s):
        t = s.clock.elapsed_s / 3600
        c = s.algo.ctl
        ui = s.algo.Ki * c.integral
        if t0 <= t <= t1 and (last[0] is None or abs(ui - last[0]) > 1e-4):
            print(f"{t:.3f} zone {s.zone_c:.2f} sens {s.indoor_sensor.value} u_i {ui:+.4f} (d {ui-(last[0] or ui):+.4f}) mode {c.last_i_mode} hold {c.last_integral_hold_source} err {getattr(c,'last_error',float('nan')):+.3f} ucmd {getattr(c,'u_cmd',float('nan')):.3f} on {s.algo.on_percent:.2f} pow {s.actuator.power:.2f} Ki {s.algo.Ki:.5f}")
        last[0] = ui
        return False
    await sim.run_for(t1 + 0.05, stop=watch)
asyncio.run(main())
