"""Log the brake's inputs in the first morning after a setback."""
import asyncio, os, sys
repo, room, weather, phase = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path[:0] = [repo, f"{repo}/tests"]
from _pytest.monkeypatch import MonkeyPatch
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather
import test_smartpi_sim_zone_set as Z
from custom_components.vtherm_smartpi.smartpi import dependency_model as DM
LOG = [False]
orig = DM.DependencyModel._predictive_command
def logged(self, ctx, fit, exchanges, allocation):
    out = orig(self, ctx, fit, exchanges, allocation)
    if LOG[0]:
        p = lambda c: self._predicted_error(ctx, fit, exchanges, c)
        s = self._source
        print(f"t={ctx.now_s/3600:.2f} zone={ctx.zone_temp:.2f} sp={ctx.target_temp} dem={ctx.lead_demand:.2f} out={ctx.lead_output:.2f} alloc={allocation:.2f} cap={out:.2f} e0={p(0.0):+.2f} e1={p(1.0):+.2f} x_f={s.x_f:.2f} boost={s.boost} settle_min={s.candidates.current.settle_s/60:.0f} pcap={s.power_cap:.2f} fade={self._fade_weight(s.key(ctx.hvac_mode), ctx.now_s):.2f}")
    return out
async def main():
    b, a = Z.ROOMS[room]
    mp = MonkeyPatch()
    mp.setattr(DM.DependencyModel, "_predictive_command", logged)
    sim = Simulation(mp, ZoneSpec(a=a, b=b, source=SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=Z.LEAD_BAND),
                                  sensor_resolution_c=0.1, control_phase_s=phase),
                     Weather(base_c=Z.WEATHERS[weather], daily_amplitude_c=3.0))
    await sim.run_for(96.0)
    await Z._setpoint(sim, 17.0)
    await sim.run_for(6.0)
    await Z._setpoint(sim, 20.0)
    LOG[0] = True
    await sim.run_for(1.5)
asyncio.run(main())
