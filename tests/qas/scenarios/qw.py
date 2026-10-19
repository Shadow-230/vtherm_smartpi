"""QW: the inputs from the heating system — water data and the boiler's call (role ``water``; QW8 on
the role ``electric``).

The simulated boiler fires from the start at 40 degC. The studio's radiator reads its water from
entities (its supply sensor, the boiler's signals from SmartPI's defaults) and the boiler's call
from the helpers' heating provider; the nursery's floor and the lounge zone (a radiator and a
floor) read their water from the provider. The provider follows the simulator: its boiler's
water, hot water with priority, the boiler off or in a fault; a scenario can unregister it or force
a reason (``smartpi_qas_helpers.set_provider``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from flows import Form, Menu
from roles import PROVIDER_SOURCE, supply_sensor
from stages import SMARTPI, SUPPLY_ENTITY, WATER_SOURCE

from .common import (
    CYCLE_S,
    MARGIN_S,
    RECALC_S,
    api,
    boiler_grace_s,
    climate_uid,
    live,
    log_lines,
    options_flow,
    path,
    role,
    seed_learned,
    set_temperature,
    settle_s,
    sim,
    stored_options,
    system_log,
    thermostat,
    wait_live,
)
from .registry import FRESH, RESET, SEED, WITH_WATER, Phase, changes, scenario

STUDIO = "climate.studio"
NURSERY = "climate.nursery"
LOUNGE_FLOOR = "climate.lounge_floor"
HELPERS = "smartpi_qas_helpers"
ISSUE_PREFIX = "water_data_changed_"
WATER_C = 40.0  # the boiler's water (roles.py, the role water)
REFERENCE = {"radiator": (1.3, 60.0), "floor_water": (1.1, 45.0)}  # smartpi/const.py WATER_EMITTERS


def expected_factor(heating_type: str, room_c: float) -> float:
    """The emitter's factor at the boiler's water, as SmartPI computes it."""
    exponent, reference_c = REFERENCE[heating_type]
    return min((max(WATER_C - room_c, 0.0) / (reference_c - 20.0)) ** exponent, 1.0)


async def _provider(r: Any, **data: Any) -> None:
    await api(r).call(HELPERS, "set_provider", data)


async def _provider_status(r: Any) -> dict[str, Any]:
    return await api(r).respond(HELPERS, "provider_status", {}) or {}


async def _issue(r: Any, climate: str) -> dict[str, Any] | None:
    """SmartPI's repair issue of a thermostat's water data, if any."""
    issue_id = f"{ISSUE_PREFIX}{await climate_uid(r, climate)}"
    result = await r.ha.ws_cmd({"type": "repairs/list_issues"})
    return next(
        (i for i in (result or {}).get("issues", []) if i.get("domain") == SMARTPI and i.get("issue_id") == issue_id),
        None,
    )


async def _fix(r: Any, climate: str, reset: bool) -> dict[str, Any]:
    """Go through the repair's fix flow: reset the learned model or keep it."""
    issue_id = f"{ISSUE_PREFIX}{await climate_uid(r, climate)}"
    _status, flow = await r.ha.rest("POST", "/api/repairs/issues/fix", {"handler": SMARTPI, "issue_id": issue_id})
    _status, done = await r.ha.rest("POST", f"/api/repairs/issues/fix/{flow['flow_id']}", {"reset_model": reset})
    return done or {}


async def _seed(r: Any, climate: str) -> None:
    """A learned model, so that the repair is offered (it is not while SmartPI learns)."""
    await seed_learned(r, climate, "learned_room")


async def _calls_for_heat(r: Any, climate: str) -> None:
    await set_temperature(r, climate, 23.0)
    took, data = await wait_live(r, climate, lambda d: (path(d, "power.current_cycle_percent") or 0) > 0, 3 * RECALC_S + CYCLE_S, 10.0)
    r.check(took is not None, f"{climate} calls for heat: {path(data, 'power.current_cycle_percent')} %")


def _factor_near(data: dict[str, Any], heating_type: str, tolerance: float = 0.12) -> bool:
    room = path(data, "temperature.indoor") or 20.0
    factor = path(data, "water.factor")
    return factor is not None and abs(factor - expected_factor(heating_type, room)) <= tolerance


async def _water_published(r: Any, climate: str) -> float | None:
    state = await r.ha.state(climate)
    return ((state or {}).get("attributes") or {}).get("specific_states", {}).get("water_factor")


# --- water data ----------------------------------------------------------------------------------


async def _live_water(r: Any, minutes: float) -> None:
    """Both water sources live for ``minutes``: the factor, the inputs, the published factor."""
    since = time.time()
    checks = {STUDIO: ("radiator", "entity"), NURSERY: ("floor_water", PROVIDER_SOURCE)}
    samples = {climate: [] for climate in checks}
    start = time.time()
    while time.time() - start < minutes * 60.0:
        for climate, (heating_type, source) in checks.items():
            data = await live(r, climate)
            samples[climate].append(
                path(data, "water.source") == "live"
                and _factor_near(data, heating_type)
                and path(data, "water.inputs.supply.source") == source
                and path(data, "water.inputs.supply.data") is True
            )
        await asyncio.sleep(60.0)
    for climate, ok in samples.items():
        share = sum(ok) / max(len(ok), 1)
        r.check(share >= 0.9, f"{climate}: water live from its source with the boiler's factor in {sum(ok)} of {len(ok)} samples")
        published = await _water_published(r, climate)
        r.check(published is not None and 0.0 < published < 1.0, f"{climate}: water_factor published {published}")
    data = await live(r, STUDIO)
    r.check(
        path(data, "water.inputs.heat.source") == "entity" and path(data, "water.inputs.dhw.data") is True,
        f"the studio's boiler signals from the defaults: {path(data, 'water.inputs')}",
    )
    errors = log_lines(await system_log(r), since=since, levels=("ERROR",))
    r.check(not errors, f"no error: {errors[:3]}")


@scenario("QW1", ("water",), 48 * 60, "W1 (test_smartpi_sim_water.py)", set=WITH_WATER, phase=Phase(FRESH))
async def QW1(r: Any) -> None:
    """With water data for 48 h (the studio from entities, the nursery from the provider), both water blocks read source live with the factor of the boiler's 40 °C water, and each learned a is within 0.5–1.2 of its emitter's true a at the reference water."""
    await _live_water(r, 48 * 60.0)
    heaters = role().sim["heaters"]
    for climate, heater in ((STUDIO, "studio_radiator"), (NURSERY, "nursery_floor")):
        true_a = heaters[heater]["a"]
        a = path(await live(r, climate), "model.a")
        ratio = (a or 0.0) / true_a
        r.check(a is not None and 0.5 <= ratio <= 1.2, f"{climate}: a {a} is {ratio:.2f} of the truth {true_a} at the reference water")


@scenario("QW1s", ("water",), 45, "W1 (test_smartpi_sim_water.py)", minimum=True, short_of=("QW1",), set=WITH_WATER)
async def QW1s(r: Any) -> None:
    """For 45 minutes both water blocks read source live from their sources with data, with the factor of the boiler's 40 °C water, water_factor is published on each thermostat and nothing logs an error."""
    await _live_water(r, 45.0)


@scenario("QW2", ("water",), 30, "hot water (test_smartpi_sim_water.py)", minimum=True, set=WITH_WATER)
async def QW2(r: Any) -> None:
    """Domestic hot water for 20 minutes: the studio's water block reads source dhw, factor 0 and no_heat, its command stays as it was (and its integral, once SmartPI regulates), its hot-water input shows it; afterwards the source is live again."""
    await _calls_for_heat(r, STUDIO)
    await sim(r, "start_hot_water", minutes=20.0)
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "water.source") == "dhw", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"hot water: source {path(data, 'water.source')}")
    r.check(path(data, "water.factor") == 0 and path(data, "water.no_heat") is True, f"factor {path(data, 'water.factor')}, no_heat {path(data, 'water.no_heat')}")
    r.check(path(await live(r, STUDIO), "water.inputs.dhw.data") is True, "the hot-water entity is read")
    await asyncio.sleep(2 * RECALC_S)
    samples = []
    start = time.time()
    while time.time() - start < 12 * 60.0:
        data = await live(r, STUDIO)
        if path(data, "water.source") == "dhw":
            samples.append(
                (path(data, "power.current_cycle_percent"), path(data, "temperature.integral_error"), path(data, "control.phase"))
            )
        await asyncio.sleep(30.0)
    commands = {c for c, _i, _p in samples}
    integrals = {i for _c, i, p in samples if p != "Hysteresis"}
    r.check(len(samples) >= 10 and len(commands) == 1, f"the command held during hot water: {sorted(map(str, commands))}")
    if any(p != "Hysteresis" for _c, _i, p in samples):
        r.check(len(integrals) <= 1, f"the integral held while SmartPI regulates: {sorted(map(str, integrals))}")
    else:
        r.note("the integral not checked: SmartPI still learns (Hysteresis) — QL5 checks it on a learned model")
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "water.source") == "live", 10 * 60.0, 15.0)
    r.check(took is not None, f"after hot water the source is live again: {path(data, 'water.source')}")


@scenario("QW3", ("water",), 50, "gaps (test_smartpi_sim_water.py); test_smartpi_water_handler.py", minimum=True, set=WITH_WATER, state=changes(SEED, RESET))
async def QW3(r: Any) -> None:
    """With a learned model, the provider's data missing (flow_stale) keep the nursery's long-term average; lasting (not_mapped) give the factor 1 and the repair water_data_gone once; its fix resets the model."""
    await _seed(r, NURSERY)
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "live", 10 * 60.0, 15.0)
    r.check(took is not None, f"the nursery's water live: {path(data, 'water.source')}")
    before = path(data, "water.factor")
    await _provider(r, supply_reason="flow_stale")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "average", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"a momentary gap: source {path(data, 'water.source')}, reason {path(data, 'water.supply_reason')}")
    factor = path(data, "water.factor")
    r.check(factor is not None and 0.0 < factor < 1.0, f"the long-term average in use: {factor} (live before: {before})")
    r.check(await _issue(r, NURSERY) is None, "no repair for a momentary gap")
    await _provider(r, supply_reason="not_mapped")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "lasting", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None and path(data, "water.factor") == 1, f"a lasting gap: source {path(data, 'water.source')}, factor {path(data, 'water.factor')}")
    issue = await _issue(r, NURSERY)
    r.check(issue is not None and issue.get("translation_key") == "water_data_gone", f"the repair: {issue}")
    done = await _fix(r, NURSERY, reset=True)
    r.check(done.get("type") == "create_entry", f"the repair's fix: {done.get('type')}")
    took, data = await wait_live(r, NURSERY, lambda d: (path(d, "learning.accepted_updates_a") or 0) == 0, 2 * MARGIN_S, 10.0)
    r.check(took is not None, f"the model reset: accepted updates of a {path(data, 'learning.accepted_updates_a')}, phase {path(data, 'control.phase')}")
    r.check(await _issue(r, NURSERY) is None, "the repair is gone")
    await _provider(r, supply_reason="")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "live", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None and await _issue(r, NURSERY) is None, "the data back while SmartPI learns again: no repair")


@scenario("QW4", ("water",), 40, "test_smartpi_water_handler.py", minimum=True, set=WITH_WATER, state=changes(SEED))
async def QW4(r: Any) -> None:
    """With a learned model, the nursery's data lost for good and back: the repair water_data_gone, then water_data_appeared once; kept through its fix, the model stays."""
    await _seed(r, NURSERY)
    await _provider(r, supply_reason="not_mapped")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "lasting", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"lasting: {path(data, 'water.source')}")
    gone = await _issue(r, NURSERY)
    r.check(gone is not None and gone.get("translation_key") == "water_data_gone", f"the repair: {gone}")
    updates = path(await live(r, NURSERY), "learning.accepted_updates_a")
    await _provider(r, supply_reason="")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "live", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"the data back: {path(data, 'water.source')}")
    appeared = await _issue(r, NURSERY)
    r.check(appeared is not None and appeared.get("translation_key") == "water_data_appeared", f"the repair: {appeared}")
    done = await _fix(r, NURSERY, reset=False)
    r.check(done.get("type") == "create_entry" and await _issue(r, NURSERY) is None, "kept through the repair's fix: the repair is gone")
    await asyncio.sleep(2 * RECALC_S)
    after = path(await live(r, NURSERY), "learning.accepted_updates_a")
    r.check(after is not None and updates is not None and after >= updates, f"the model kept: accepted updates of a {updates} → {after}")


# --- the boiler's call ---------------------------------------------------------------------------


@scenario("QW5", ("water",), 95, "B2 (test_smartpi_sim_boiler_call.py); test_smartpi_boiler_handler.py", minimum=True, set=WITH_WATER)
async def QW5(r: Any) -> None:
    """The provider says the boiler is not called (below_threshold) while the studio calls: its boiler block reads source provider:qas, called false, the reason and the provider's demand, emitter_cooling after the grace, held after the settling time without learning a; the other reasons show; called again, emitter_warming."""
    studio = thermostat("Studio")
    grace = boiler_grace_s(studio)
    settle = settle_s(studio.heating_type)
    await _calls_for_heat(r, STUDIO)
    await _provider(r, call_reason="below_threshold")
    t0 = time.time()
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.called") is False, 2 * RECALC_S + MARGIN_S, 10.0)
    block = path(data, "boiler") or {}
    r.check(took is not None and block.get("source") == PROVIDER_SOURCE, f"the boiler block: {block}")
    r.check(block.get("reason") == "below_threshold", f"the reason: {block.get('reason')}")
    r.check((block.get("demand") or {}).get("count_by") == "zones", f"the provider's demand: {block.get('demand')}")
    updates = path(data, "learning.accepted_updates_a") or 0
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.learning") == "emitter_cooling", grace + MARGIN_S, 20.0)
    r.check(took is not None and time.time() - t0 >= grace - MARGIN_S, f"emitter_cooling {time.time() - t0:.0f} s after the call (grace {grace:.0f} s)")
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.learning") == "held", settle - grace + 2 * MARGIN_S, 30.0)
    r.check(took is not None, f"held {time.time() - t0:.0f} s after the call (settle {settle:.0f} s)")
    for reason in ("no_demand", "activation_delay", "boiler_fault"):
        await _provider(r, call_reason=reason)
        took, data = await wait_live(r, STUDIO, lambda d, want=reason: path(d, "boiler.reason") == want, 2 * RECALC_S + MARGIN_S, 10.0)
        r.check(took is not None and path(data, "boiler.learning") == "held", f"{reason}: reason {path(data, 'boiler.reason')}, learning {path(data, 'boiler.learning')}")
    r.check((path(data, "learning.accepted_updates_a") or 0) == updates, f"no a learned since the call: {updates} → {path(data, 'learning.accepted_updates_a')}")
    await _provider(r, call_reason="")
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "boiler.learning") == "emitter_warming", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None and path(data, "boiler.called") is True, f"called again: emitter_warming {path(data, 'boiler.learning')}")


# --- a zone on water ---------------------------------------------------------------------------


@scenario("QW6", ("water",), 60, "test_smartpi_sim_second_source.py", minimum=True, set=WITH_WATER)
async def QW6(r: Any) -> None:
    """In the lounge zone the floor (the additional source, water from the provider) applies the main source's power with its own water factor: its water block is live with the floor's factor, and its command is the main source's power over that factor (at most 100 %)."""
    took, data = await wait_live(r, LOUNGE_FLOOR, lambda d: path(d, "dependencies.role") == "second_source", 10 * 60.0, 15.0)
    r.check(took is not None, f"the floor follows the main source: {path(data, 'dependencies')}")
    ok = total = 0
    start = time.time()
    while time.time() - start < 40 * 60.0:
        data = await live(r, LOUNGE_FLOOR)
        command = path(data, "dependencies.command")
        factor = path(data, "water.factor")
        applied = path(data, "power.next_cycle_percent")
        if path(data, "water.source") == "live" and command and factor and applied is not None:
            total += 1
            ok += abs(applied - 100.0 * min(1.0, command / factor)) <= 1.5 and _factor_near(data, "floor_water")
        await asyncio.sleep(60.0)
    r.check(total >= 10 and ok >= 0.9 * total, f"the floor's command is the main source's power over its factor in {ok} of {total} samples")


# --- gaps of the source ------------------------------------------------------------------------


@scenario("QW7", ("water",), 15, "test_smartpi_water_input.py", minimum=True, set=WITH_WATER)
async def QW7(r: Any) -> None:
    """The provider reports the nursery's circuit not mapped: its water is lasting at once with the factor 1 and the provider's reason; mapped again, it is live."""
    await _provider(r, supply_reason="not_mapped")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "lasting", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None and path(data, "water.factor") == 1, f"lasting: {path(data, 'water.source')}, factor {path(data, 'water.factor')}")
    r.check(path(data, "water.supply_reason") == "not_mapped", f"the reason: {path(data, 'water.supply_reason')}")
    r.check(await _water_published(r, NURSERY) is None, "no water_factor published without water data")
    await _provider(r, supply_reason="")
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "live", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"mapped again: {path(data, 'water.source')}")


@scenario("QW8", ("electric",), 15, "test_smartpi_water_handler.py", minimum=True, set=WITH_WATER)
async def QW8(r: Any) -> None:
    """With the helpers' heating provider registered, the electric types never read it: it is asked nothing for their thermostats, and their diagnostics have no water and no boiler block."""
    before = (await _provider_status(r)).get("calls") or {}
    await asyncio.sleep(10 * 60.0)
    status = await _provider_status(r)
    r.check(status.get("registered") is True, f"the provider registered: {status.get('providers')}")
    for t in role().thermostats:
        asked = (status.get("calls") or {}).get(t.climate, 0) - before.get(t.climate, 0)
        r.check(asked == 0, f"{t.climate}: asked {asked} times")
        data = await live(r, t.climate)
        r.check(path(data, "water") is None and path(data, "boiler") is None, f"{t.climate}: no water and no boiler block")


# --- the options and the provider ---------------------------------------------------------------


def _studio_water(source: str, keep: bool) -> list[Form | Menu]:
    steps: list[Form | Menu] = [Menu("heating_inputs"), Form("heating_inputs", {WATER_SOURCE: source})]
    if source == "entities":
        steps.append(Form("heating_entities", {SUPPLY_ENTITY: supply_sensor("studio_radiator"), "smart_pi_boiler_signals": "defaults"}))
    steps.append(Menu("save"))
    steps.append(Form("model_reset", {"reset_model": not keep}))
    return steps


@scenario("QW9", ("water",), 25, "test_config_flow_heating_inputs.py, test_smartpi_water_handler.py", minimum=True, set=WITH_WATER)
async def QW9(r: Any) -> None:
    """The studio's water source changed in the options: to none, the reset is asked (kept), the factor is 1 at once and no repair comes; back to entities, asked again (kept), the data start live without a repair."""
    try:
        await options_flow(r, "Studio", _studio_water("none", keep=True))
        stored = await stored_options("Studio")
        r.check(stored.get(WATER_SOURCE) == "none" and not stored.get("smart_pi_reset_token"), f"stored: water {stored.get(WATER_SOURCE)}, no reset")
        took, data = await wait_live(r, STUDIO, lambda d: path(d, "water.source") == "none", 2 * MARGIN_S, 10.0)
        r.check(took is not None and path(data, "water.factor") == 1, f"no source: {path(data, 'water.source')}, factor {path(data, 'water.factor')}")
        await asyncio.sleep(2 * RECALC_S)
        r.check(await _issue(r, STUDIO) is None, "no repair for the change made in the options")
    finally:
        await options_flow(r, "Studio", _studio_water("entities", keep=True))
    stored = await stored_options("Studio")
    r.check(stored.get("smart_pi_model_kept_for_water", "").startswith("entities|"), f"kept for {stored.get('smart_pi_model_kept_for_water')}")
    took, data = await wait_live(r, STUDIO, lambda d: path(d, "water.source") == "live", 2 * MARGIN_S, 10.0)
    r.check(took is not None, f"back to entities: {path(data, 'water.source')}")
    await asyncio.sleep(2 * RECALC_S)
    r.check(await _issue(r, STUDIO) is None, "the data starting from the inputs the model was kept for: no repair")


@scenario("QW10", ("water",), 25, "test_smartpi_water_handler.py, test_smartpi_heating_inputs.py", minimum=True, set=WITH_WATER)
async def QW10(r: Any) -> None:
    """The provider unregistered: the nursery's inputs show it not available, its long-term average stays in use, and SmartPI warns once per thermostat after 10 minutes; registered again, the data are live."""
    since = time.time()
    try:
        await _provider(r, registered=False)
        took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.inputs.supply.available") is False, 2 * RECALC_S + MARGIN_S, 10.0)
        r.check(took is not None, f"not available: {path(data, 'water.inputs.supply')}")
        r.check(path(data, "water.source") == "average" and path(data, "water.supply_reason") == "provider_missing", f"the average in use: {path(data, 'water.source')}, {path(data, 'water.supply_reason')}")
        await asyncio.sleep(11 * 60.0 + MARGIN_S)
        warnings = log_lines(await system_log(r), since=since, loggers=("custom_components.vtherm_smartpi",), contains="is not registered")
        r.check(len(warnings) >= 1, f"warned: {warnings[:3]}")
        counts = [int(line.rsplit("(x", 1)[1].rstrip(")")) for line in warnings]
        users = sum(1 for t in role().thermostats if t.water == "provider" or t.call == "provider")
        r.check(sum(counts) == users, f"once per thermostat using the provider ({users}): {sum(counts)}")
    finally:
        await _provider(r, registered=True)
    took, data = await wait_live(r, NURSERY, lambda d: path(d, "water.source") == "live", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None and path(data, "water.inputs.supply.available") is True, f"registered again: {path(data, 'water.source')}")
