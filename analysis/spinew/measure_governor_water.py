"""Measure the reference governor with water data (run from a repository checkout).

Usage: python measure_governor_water.py <repo_root> <label> <out.json>

One radiator zone (a=0.06, b=0.001, dead time 10 min, 0.1 degC sensor), outdoor
3 +/- 3 degC. 48 h learning at 20 degC, then 4 days: setback 17 degC at 22:00,
21 degC at 06:00. For each morning approach (06:00-14:00): time to reach 20.9 degC,
overshoot above 21, minutes with a governor/landing cap, cap on/off toggles,
minutes limited by the water, governor arms and retirements (with reasons).
"""

from __future__ import annotations

import asyncio
import json
import sys

repo = sys.argv[1]
sys.path[:0] = [repo, f"{repo}/tests"]

from _pytest.monkeypatch import MonkeyPatch  # noqa: E402

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE  # noqa: E402
from sim.boiler import SimBoiler  # noqa: E402
from sim.runner import Simulation, ZoneSpec  # noqa: E402
from sim.weather import Weather  # noqa: E402

try:
    from custom_components.vtherm_smartpi.smartpi import reference_governor_runtime as rgr
except ImportError:  # dev: no governor
    rgr = None

EVENTS: list[tuple[str, float, str]] = []
CLOCK = {"sim": None}


def _instrument(mp: MonkeyPatch) -> None:
    if rgr is None:
        return
    cls = rgr.ReferenceGovernorRuntime
    orig_arm, orig_retire = cls.arm, cls.retire

    def arm(self, target_temp, now_monotonic, *, force=False):
        was = (self._armed, self._target_temp)
        out = orig_arm(self, target_temp, now_monotonic, force=force)
        if (self._armed, self._target_temp) != was and self._armed:
            EVENTS.append(("arm", CLOCK["sim"].clock.elapsed_s / 3600.0, str(target_temp)))
        return out

    def retire(self, kernel_decision, reason):
        if self._armed:
            EVENTS.append(("retire", CLOCK["sim"].clock.elapsed_s / 3600.0, reason))
        return orig_retire(self, kernel_decision, reason)

    mp.setattr(cls, "arm", arm)
    mp.setattr(cls, "retire", retire)


def _cap_active(algo) -> bool:
    sp = algo.sp_mgr
    if hasattr(sp, "governor_command_cap"):
        cap, before = sp.governor_command_cap, sp.governor_u_cmd_before_cap
        return cap is not None and before is not None and cap < before - 1e-6
    # dev: landing cap
    return bool(getattr(sp, "landing_active", False)) and getattr(sp, "landing_u_cap", None) is not None


async def _setpoint(sim: Simulation, value: float) -> None:
    sim.thermostat.target_temperature = value
    sim.zone.setpoint_c = value
    await sim.thermostat.async_control_heating(force=True)


async def run(variant: str, water_c: float | None, water_data: str | None, phase_s: float) -> dict:
    EVENTS.clear()
    mp = MonkeyPatch()
    try:
        _instrument(mp)
        spec = ZoneSpec(
            a=0.06,
            b=0.001,
            deadtime_min=10.0,
            sensor_resolution_c=0.1,
            setpoint_c=20.0,
            control_phase_s=phase_s,
            boiler=SimBoiler(water_c=water_c) if water_c is not None else None,
            emitter="radiator",
            water_data=water_data,
            options={CONF_SMART_PI_HEATING_TYPE: "radiator"} if water_c is not None else {},
        )
        sim = Simulation(mp, spec, Weather(base_c=3.0, daily_amplitude_c=3.0))
        CLOCK["sim"] = sim
        await sim.run_for(48.0 - 2.0)  # ends at 46:00 = 22:00 of day 2
        approaches = []
        for day in range(4):
            await _setpoint(sim, 17.0)          # 22:00
            await sim.run_for(8.0)               # -> 06:00
            start_h = sim.clock.elapsed_s / 3600.0
            n0 = len(EVENTS)
            await _setpoint(sim, 21.0)
            samples = []

            def watch(s):
                samples.append((
                    s.clock.elapsed_s / 3600.0,
                    s.zone_c,
                    _cap_active(s.algo),
                    bool(getattr(s.algo, "_water_too_cold", False)),
                ))
                return False

            await sim.run_for(8.0, stop=watch)   # -> 14:00
            await sim.run_for(8.0)               # -> 22:00
            reach = next((t - start_h for t, z, *_ in samples if z >= 20.9), None)
            dt_min = (samples[1][0] - samples[0][0]) * 60.0 if len(samples) > 1 else 0.0
            caps = [c for *_, c, _w in samples]
            toggles = sum(1 for x, y in zip(caps, caps[1:]) if x != y)
            ev = EVENTS[n0:]
            approaches.append({
                "reach_h": None if reach is None else round(reach, 2),
                "overshoot_c": round(max(z for _, z, *_ in samples) - 21.0, 3),
                "cap_min": round(sum(1 for c in caps if c) * dt_min, 1),
                "cap_toggles": toggles,
                "water_limited_min": round(sum(1 for *_, w in samples if w) * dt_min, 1),
                "arms": sum(1 for k, *_ in ev if k == "arm"),
                "retires": [r for k, _, r in ev if k == "retire"],
            })
        return {
            "variant": variant,
            "phase_s": phase_s,
            "water_factor": round(getattr(sim.algo, "water_factor", 1.0), 3),
            "a_ratio": round(sim.algo.est.a / 0.06, 3),
            "approaches": approaches,
        }
    finally:
        mp.undo()


VARIANTS = (
    ("no_water_input", None, None),
    ("water_60C_data", 60.0, "provider"),
    ("water_45C_data", 45.0, "provider"),
    ("water_40C_data", 40.0, "provider"),
    ("water_45C_no_data", 45.0, None),
)


async def main() -> None:
    label, out = sys.argv[2], sys.argv[3]
    results = []
    for name, water_c, data in VARIANTS:
        for phase_s in (0.0, 170.0, 340.0):
            r = await run(name, water_c, data, phase_s)
            r["label"] = label
            results.append(r)
            print(label, name, phase_s, json.dumps(r["approaches"]), flush=True)
    with open(out, "w") as fh:
        json.dump(results, fh, indent=1)


asyncio.run(main())
