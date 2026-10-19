"""QL: SmartPI with a learned model (roles ``base`` and ``water``), the learned set ``L``.

The minimum set checks SmartPI mostly just after the setup, while it still learns (phase
Hysteresis). QL0 loads the learned models (the helpers' fixtures, made by
``tests/qas/build_fixtures.py`` from each room of ``roles.py``) and waits until the diagnostics
show them; the other QL scenarios need them (phase Stable, and the lounge zone's model published)
and are NOT RUN without them. A learned model SmartPI reached on its own (after QB1, QW1) does as
well. Their in-process twins are in tests/test_smartpi_sim_learned.py.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from roles import PROVIDER_SOURCE

from .common import (
    MARGIN_S,
    RECALC_S,
    SEED_TOLERANCE,
    api,
    boiler_grace_s,
    entry,
    keep_state,
    live,
    log_lines,
    path,
    recorded_events,
    restart,
    restore_state,
    role,
    seed_learned,
    set_temperature,
    settle_s,
    sim,
    system_log,
    thermostat,
    wait_live,
    wait_loaded,
    zone_fit,
)
from .registry import (
    CALIBRATED,
    LEARNED,
    RESET,
    SEED,
    WITH_WATER,
    Phase,
    changes,
    scenario,
)

LIVING = "climate.living"
STUDIO = "climate.studio"
LOUNGE = "climate.lounge_radiator"
LOUNGE_FLOOR = "climate.lounge_floor"
HELPERS = "smartpi_qas_helpers"
SMARTPI = "vtherm_smartpi"
WATER_HOLD = "no_heat"  # the integral's hold by the water (algo.py WATER_INTEGRAL_HOLD)
# The learned model of each thermostat, by role: (climate, fixture, with the zone's model).
LEARNED_MODELS = {
    "base": ((LIVING, "learned_room", False),),
    "water": ((STUDIO, "learned_studio", False), (LOUNGE, "learned_lounge_zone", True)),
}
CALIBRATION_MAX_S = 4 * 3600.0  # a forced calibration in the living room takes about two hours


def _model(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "a": path(data, "model.a"),
        "b": path(data, "model.b"),
        "updates_a": path(data, "learning.accepted_updates_a"),
        "updates_b": path(data, "learning.accepted_updates_b"),
        "deadtime_heat_s": path(data, "model.deadtime_heat_s"),
    }


def _close(value: Any, expected: Any, rel: float) -> bool:
    if value is None or expected is None:
        return value == expected
    return abs(float(value) - float(expected)) <= rel * max(abs(float(expected)), 1e-12)


@scenario(
    "QL0", ("base", "water"), 15, "test_smartpi_sim_learned.py (QL0)",
    state=changes(SEED), learned_set=True,
)  # fmt: skip
async def QL0(r: Any) -> None:
    """The role's SmartPI thermostats get their learned models (the living room's; the studio's and the lounge zone's main source with the zone's model) and the diagnostics show them: phase Stable, a and b the fixture's within 1 %, the zone's model published."""
    for climate, fixture, zone in LEARNED_MODELS[role().name]:
        await seed_learned(r, climate, fixture, zone=zone)
    lines = log_lines(await system_log(r), levels=("ERROR",))
    r.check(not lines, f"no error: {lines[:3]}")


@scenario(
    "QL1", ("water",), 80, "test_smartpi_sim_learned.py (QL1); test_smartpi_sim_second_source.py",
    set=WITH_WATER, phase=Phase(LEARNED, ("Lounge radiator",), zone_model=True), learned_set=True,
)  # fmt: skip
async def QL1(r: Any) -> None:
    """In the learned lounge zone on 40 °C water the floor (the additional source) applies the main source's power over its water factor, and below 100 %: in at least 10 samples its command is between 0 and 99 %, and in 90 % of them it is the power over the factor within 1.5 points."""
    took, data = await wait_live(r, LOUNGE_FLOOR, lambda d: path(d, "dependencies.role") == "second_source", 10 * 60.0, 15.0)
    r.check(took is not None, f"the floor follows the main source: {path(data, 'dependencies')}")
    # After QL0 the zone may still heat at full power for a while: wait for a partial command.
    took, data = await wait_live(r, LOUNGE_FLOOR, lambda d: 0.0 < (path(d, "power.next_cycle_percent") or 0.0) < 99.0, 30 * 60.0, 30.0)
    r.note(f"the floor's command below 100 % after {took if took is None else round(took)} s: {path(data, 'power.next_cycle_percent')} %")
    ok = below = total = 0
    start = time.time()
    while time.time() - start < 40 * 60.0:
        data = await live(r, LOUNGE_FLOOR)
        command = path(data, "dependencies.command")
        factor = path(data, "water.factor")
        applied = path(data, "power.next_cycle_percent")
        if path(data, "water.source") == "live" and command is not None and factor and applied is not None:
            total += 1
            ok += abs(applied - 100.0 * min(1.0, command / factor)) <= 1.5
            below += 0.0 < applied < 99.0
        await asyncio.sleep(60.0)
    r.check(total >= 10 and ok >= 0.9 * total, f"the floor's command is the main power over its factor in {ok} of {total} samples")
    r.check(below >= 10, f"the floor's command between 0 and 99 % in {below} of {total} samples")
    data = await live(r, LOUNGE)
    r.check(path(data, "control.phase") == "Stable" and zone_fit(data).get("published"), f"the zone still learned: phase {path(data, 'control.phase')}, zone model {zone_fit(data).get('published')}")


async def _calibration_seen(r: Any, since_events: int) -> tuple[bool, list[dict[str, Any]]]:
    """A forced calibration: the phase Calibration, and the AutoCalib event naming the living
    room's climate entity."""
    took, data = await wait_live(r, LIVING, lambda d: path(d, "control.phase") == "Calibration", MARGIN_S, 5.0)
    r.check(took is not None, f"the calibration runs: phase {path(data, 'control.phase')}, state {path(data, 'calibration.state')}")
    events = (await recorded_events(r))[since_events:]
    autocalib = [e for e in events if e["event_type"].startswith("smartpi_autocalib_")]
    r.check(
        any((e.get("data") or {}).get("entity_id") == LIVING for e in autocalib),
        f"an AutoCalib event for {LIVING}: {[(e['event_type'], (e.get('data') or {}).get('entity_id')) for e in autocalib][:3]}",
    )
    return took is not None, events


async def _reset(r: Any) -> None:
    await api(r).call(SMARTPI, "reset_smartpi_learning", {"entity_id": LIVING})
    took, data = await wait_live(
        r,
        LIVING,
        lambda d: path(d, "control.phase") == "Hysteresis" and (path(d, "learning.accepted_updates_a") or 0) == 0,
        MARGIN_S,
        5.0,
    )
    r.check(took is not None, f"the reset: phase {path(data, 'control.phase')}, accepted updates of a {path(data, 'learning.accepted_updates_a')}")


@scenario(
    "QL2", ("base",), 240, "test_smartpi_sim_learned.py (QL2); test_smartpi_calibration.py, test_algo_persistence.py",
    state=changes(CALIBRATED, RESET, restored=True), phase=Phase(LEARNED, ("Living",)),
)  # fmt: skip
async def QL2(r: Any) -> None:
    """On the learned living room a forced calibration runs (phase Calibration, its AutoCalib event naming the climate entity) and ends back in Stable within 4 h with a and b within 15 % of before; a reset then starts learning again (Hysteresis, no accepted update) and a restart keeps it reset; the model found is put back."""
    before = _model(await live(r, LIVING))
    await keep_state(r, LIVING, "ql2")
    events = len(await recorded_events(r))
    try:
        await api(r).call(SMARTPI, "force_smartpi_calibration", {"entity_id": LIVING})
        started, _events = await _calibration_seen(r, events)
        if started:
            t0 = time.time()
            took, data = await wait_live(r, LIVING, lambda d: path(d, "control.phase") == "Stable", CALIBRATION_MAX_S, 60.0)
            r.check(took is not None, f"the calibration ended after {(time.time() - t0) / 60.0:.0f} min: phase {path(data, 'control.phase')}")
            after = _model(data)
            r.check(_close(after["a"], before["a"], 0.15) and _close(after["b"], before["b"], 0.15), f"a {before['a']} → {after['a']}, b {before['b']} → {after['b']}")
            r.check(path(data, "model.deadtime_heat_reliable") is True, f"the dead time measured: {after['deadtime_heat_s']} s")
        await _reset(r)
        await restart(r)
        r.check(await wait_loaded(r, [LIVING]), "the living room is back after the restart")
        took, data = await wait_live(r, LIVING, lambda d: bool(path(d, "control.phase")), 600.0, 10.0)
        r.check(
            path(data, "control.phase") == "Hysteresis" and (path(data, "learning.accepted_updates_a") or 0) == 0,
            f"after the restart still reset: phase {path(data, 'control.phase')}, accepted updates of a {path(data, 'learning.accepted_updates_a')}",
        )
    finally:
        await restore_state(r, LIVING, "ql2", before["a"])


@scenario(
    "QL2s", ("base",), 20, "test_smartpi_sim_learned.py (QL2); test_smartpi_calibration.py, test_algo_persistence.py",
    short_of=("QL2",), state=changes(CALIBRATED, RESET, restored=True), phase=Phase(LEARNED, ("Living",)),
    learned_set=True,
)  # fmt: skip
async def QL2s(r: Any) -> None:
    """On the learned living room a forced calibration starts (phase Calibration, its AutoCalib event naming the climate entity); the model found put back, a reset starts learning again (Hysteresis, no accepted update); put back again, the model is the one found."""
    before = _model(await live(r, LIVING))
    await keep_state(r, LIVING, "ql2s")
    events = len(await recorded_events(r))
    try:
        await api(r).call(SMARTPI, "force_smartpi_calibration", {"entity_id": LIVING})
        await _calibration_seen(r, events)
        await restore_state(r, LIVING, "ql2s", before["a"])
        await _reset(r)
    finally:
        data = await restore_state(r, LIVING, "ql2s", before["a"])
    r.check(_close(path(data, "model.b"), before["b"], SEED_TOLERANCE), f"b put back: {before['b']} → {path(data, 'model.b')}")


async def _snapshot(r: Any, climates: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for climate in climates:
        data = await live(r, climate)
        out[climate] = _model(data) | {"zone": {k: zone_fit(data).get(k) for k in ("published", "a", "b", "a_f")}}
    return out


async def _same_model(r: Any, before: dict[str, dict[str, Any]], what: str) -> dict[str, dict[str, Any]]:
    """At the first diagnostics after ``what``: the phase Stable, a and b as before (exactly while
    nothing was learned meanwhile, within 5 % otherwise), the counts not lower, the dead time as
    before, and a zone's model as before."""
    after = {}
    for climate, old in before.items():
        took, data = await wait_live(r, climate, lambda d: bool(path(d, "control.phase")), 600.0, 10.0)
        new = _model(data) | {"zone": {k: zone_fit(data).get(k) for k in ("published", "a", "b", "a_f")}}
        after[climate] = new
        learned = (new["updates_a"] or 0) > (old["updates_a"] or 0) or (new["updates_b"] or 0) > (old["updates_b"] or 0)
        rel = 0.05 if learned else 1e-6
        r.check(path(data, "control.phase") == "Stable", f"{what}: {climate} phase {path(data, 'control.phase')}")
        r.check(
            _close(new["a"], old["a"], rel) and _close(new["b"], old["b"], rel),
            f"{what}: {climate} a {old['a']} → {new['a']}, b {old['b']} → {new['b']}" + (" (learned meanwhile)" if learned else ""),
        )
        r.check(
            (new["updates_a"] or 0) >= (old["updates_a"] or 0) and (new["updates_b"] or 0) >= (old["updates_b"] or 0),
            f"{what}: {climate} accepted updates a {old['updates_a']} → {new['updates_a']}, b {old['updates_b']} → {new['updates_b']} (not reset)",
        )
        r.check(new["deadtime_heat_s"] == old["deadtime_heat_s"], f"{what}: {climate} dead time {old['deadtime_heat_s']} → {new['deadtime_heat_s']} s")
        if old["zone"]["published"]:
            zone_ok = new["zone"]["published"] and all(_close(new["zone"][k], old["zone"][k], 1e-3) for k in ("a", "b", "a_f"))
            r.check(zone_ok, f"{what}: the zone's model {old['zone']} → {new['zone']}")
    return after


@scenario(
    "QL3", ("base", "water"), 30, "test_smartpi_sim_learned.py (QL3); test_algo_persistence.py, test_smartpi_sim_second_source.py",
    phase=Phase(LEARNED, ("Living", "Studio", "Lounge radiator"), zone_model=True), learned_set=True,
)  # fmt: skip
async def QL3(r: Any) -> None:
    """Two restarts, a reload of each learned thermostat's entry and of the defaults entry keep the learned models: at the first diagnostics after each, phase Stable, a and b as before (within 5 % if SmartPI learned meanwhile), the counts not lower, the same dead time, and the lounge zone's published model the same."""
    climates = [climate for climate, _fixture, _zone in LEARNED_MODELS[role().name]]
    before = await _snapshot(r, climates)
    r.note(f"before: {before}")
    for n in (1, 2):
        await restart(r)
        r.check(await wait_loaded(r, climates), f"restart {n}: the thermostats are back")
        before = await _same_model(r, before, f"restart {n}")
    for title in [thermostat_title(c) for c in climates] + ["SmartPI defaults"]:
        found = await entry(r, SMARTPI, title)
        await r.ha.rest("POST", f"/api/config/config_entries/entry/{found['entry_id']}/reload")
        r.check(await wait_loaded(r, climates), f"the entry {title} reloaded: the thermostats are back")
        await asyncio.sleep(2 * RECALC_S)
        before = await _same_model(r, before, f"reload of {title}")
    lines = log_lines(await system_log(r), levels=("ERROR",))
    r.check(not lines, f"no error: {lines[:3]}")


def thermostat_title(climate: str) -> str:
    return next(t.name for t in role().thermostats if t.climate == climate)


async def _studio_called_not(r: Any) -> tuple[float, dict[str, Any], dict[str, Any]]:
    """The studio calls for heat, then the provider says the boiler is not called
    (below_threshold): returns when, its model then, and its diagnostics once held."""
    studio = thermostat("Studio")
    await set_temperature(r, STUDIO, 21.0)
    took, data = await wait_live(r, STUDIO, lambda d: (path(d, "power.current_cycle_percent") or 0) > 0, 3 * RECALC_S + 600.0, 10.0)
    r.check(took is not None, f"the studio calls for heat: {path(data, 'power.current_cycle_percent')} %")
    before = _model(data)
    await api(r).call(HELPERS, "set_provider", {"call_reason": "below_threshold"})
    t0 = time.time()
    held_within = settle_s(studio.heating_type) + 2 * MARGIN_S
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.learning") == "held", held_within, 30.0)
    r.check(took is not None, f"held {time.time() - t0:.0f} s after the call (grace {boiler_grace_s(studio):.0f} s, settle {settle_s(studio.heating_type):.0f} s)")
    return t0, before, data


async def _stable_and_no_a(r: Any, before: dict[str, Any], seconds: float) -> None:
    """For ``seconds``: every sample in Stable and no a learned."""
    phases, updates = set(), set()
    start = time.time()
    while time.time() - start < seconds:
        data = await live(r, STUDIO)
        phases.add(path(data, "control.phase"))
        updates.add(path(data, "learning.accepted_updates_a"))
        await asyncio.sleep(60.0)
    r.check(phases == {"Stable"}, f"the studio stays learned: phases {sorted(map(str, phases))}")
    r.check(updates == {before["updates_a"]}, f"no a learned while not called: {before['updates_a']} → {sorted(map(str, updates))}")


@scenario(
    "QL4", ("water",), 180, "test_smartpi_sim_learned.py (QL4); test_smartpi_sim_boiler_call.py",
    set=WITH_WATER, phase=Phase(LEARNED, ("Studio",)),
)  # fmt: skip
async def QL4(r: Any) -> None:
    """The learned studio calls while the provider says the boiler is not called (below_threshold): held, it stays Stable and learns no a for 30 minutes; called again (emitter_warming), one settling time later its a and b are within 5 % of before and it is still Stable."""
    studio = thermostat("Studio")
    try:
        _t0, before, _data = await _studio_called_not(r)
        await _stable_and_no_a(r, before, 30 * 60.0)
        await api(r).call(HELPERS, "set_provider", {"call_reason": ""})
        took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.learning") == "emitter_warming", 2 * RECALC_S + MARGIN_S, 10.0)
        r.check(took is not None and path(data, "boiler.called") is True, f"called again: {path(data, 'boiler.learning')}")
        await asyncio.sleep(settle_s(studio.heating_type) + MARGIN_S)
        after = _model(await live(r, STUDIO))
        r.check(_close(after["a"], before["a"], 0.05) and _close(after["b"], before["b"], 0.05), f"a {before['a']} → {after['a']}, b {before['b']} → {after['b']}")
        r.check(path(await live(r, STUDIO), "control.phase") == "Stable", "still Stable")
    finally:
        await api(r).call(HELPERS, "set_provider", {"call_reason": ""})


@scenario(
    "QL4s", ("water",), 100, "test_smartpi_sim_learned.py (QL4); test_smartpi_sim_boiler_call.py",
    short_of=("QL4",), set=WITH_WATER, phase=Phase(LEARNED, ("Studio",)), learned_set=True,
)  # fmt: skip
async def QL4s(r: Any) -> None:
    """The learned studio calls while the provider says the boiler is not called (below_threshold): its boiler block reads source provider:qas, held, it stays Stable and learns no a for 10 minutes; called again, emitter_warming."""
    try:
        _t0, before, data = await _studio_called_not(r)
        r.check(path(data, "boiler.source") == PROVIDER_SOURCE, f"the boiler block's source: {path(data, 'boiler.source')}")
        await _stable_and_no_a(r, before, 10 * 60.0)
        await api(r).call(HELPERS, "set_provider", {"call_reason": ""})
        took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.learning") == "emitter_warming", 2 * RECALC_S + MARGIN_S, 10.0)
        r.check(took is not None and path(data, "boiler.called") is True, f"called again: {path(data, 'boiler.learning')}")
    finally:
        await api(r).call(HELPERS, "set_provider", {"call_reason": ""})


@scenario(
    "QL5", ("water",), 35, "test_smartpi_sim_learned.py (QL5); test_smartpi_sim_water.py",
    set=WITH_WATER, phase=Phase(LEARNED, ("Studio",)), learned_set=True,
)  # fmt: skip
async def QL5(r: Any) -> None:
    """Domestic hot water for 20 minutes while the learned studio regulates: in every sample it is Stable, its integral is held by the water (hold no_heat) and does not move, and its command does not move; afterwards the hold is gone."""
    await sim(r, "start_hot_water", minutes=20.0)
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "water.source") == "dhw", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"hot water: source {path(data, 'water.source')}")
    await asyncio.sleep(2 * RECALC_S)  # the hold starts at the next control call
    samples = []
    start = time.time()
    while time.time() - start < 14 * 60.0:
        data = await live(r, STUDIO)
        if path(data, "water.source") == "dhw":
            samples.append(
                (
                    path(data, "control.phase"),
                    path(data, "temperature.integral_error"),
                    path(data, "power.current_cycle_percent"),
                    path(data, "temperature.integral_hold_mode"),
                )
            )
        await asyncio.sleep(30.0)
    r.check(len(samples) >= 10, f"{len(samples)} samples during hot water")
    r.check({p for p, *_ in samples} == {"Stable"}, f"Stable throughout: {sorted({str(p) for p, *_ in samples})}")
    r.check({m for *_, m in samples} == {WATER_HOLD}, f"the integral held by the water: {sorted({str(m) for *_, m in samples})}")
    r.check(len({i for _p, i, _c, _m in samples}) == 1, f"the integral does not move: {sorted({str(i) for _p, i, _c, _m in samples})}")
    r.check(len({c for _p, _i, c, _m in samples}) == 1, f"the command does not move: {sorted({str(c) for _p, _i, c, _m in samples})}")
    took, data = await wait_live(
        r, STUDIO, lambda d: path(d, "water.source") == "live" and path(d, "temperature.integral_hold_mode") != WATER_HOLD, 10 * 60.0, 15.0
    )
    r.check(took is not None, f"afterwards: source {path(data, 'water.source')}, hold {path(data, 'temperature.integral_hold_mode')}")
