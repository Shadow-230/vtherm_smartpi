"""QE: a zone with two heat sources (instances 3, 4 and 5).

On instance 3 the lounge is heated by a radiator (the main source, ``Lounge radiator``) and a
floor (``Lounge floor``), devices of the master ``Lounge`` (an over_climate VTherm with
self-regulation off); instance 4 runs the same rooms as two independent SmartPI. On instance 5 the
study's floor is a valve, and VTherm's power manager watches the house's power.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from bench import RestTransport
from flows import Form, Menu, run_flow, walk
from roles import MAX_POWER_INPUT, Thermostat
from stages import SMARTPI, VT, vt_thermostat_steps, zone_steps

from .common import (
    MARGIN_S,
    RECALC_S,
    SOURCE_SILENT_S,
    api,
    diag_entity,
    entry,
    heat,
    keep,
    live,
    log_lines,
    options_flow,
    path,
    put_back_thermostats,
    restart,
    role,
    room_temperature,
    sample,
    set_hvac,
    stored_options,
    system_log,
    wait_live,
    wait_loaded,
)
from .registry import scenario


EXTRA = "climate.lounge_extra"
EXTRA_TITLE = "Lounge extra"


def _zone() -> Any:
    return role().zone


def _title(climate: str) -> str:
    return next(t.name for t in role().thermostats if t.climate == climate)


async def _source_item(r: Any, title: str) -> dict[str, Any] | None:
    items = (await stored_options(title)).get("smart_pi_dependencies") or []
    return next((i for i in items if i.get("kind") == "source"), None)


async def _ensure_zone(r: Any) -> None:
    """The zone as the role has it: set up again from the main source's entry if it is not there."""
    zone = _zone()
    if await _source_item(r, _title(zone.main)) is None:
        await options_flow(r, _title(zone.main), zone_steps(zone))
        r.note("put back: the zone set up again from the main source")


async def _resend_master(r: Any) -> None:
    """The master sends its state to its devices again (after another master drove them)."""
    master = _zone().master
    await set_hvac(r, master, "off")
    await asyncio.sleep(5.0)
    await put_back_thermostats(r)


def zone_master_steps(has_zone: bool, master: str) -> list[Form | Menu]:
    """To the zone form's master step: through the zone's menu where the entry has a zone."""
    return [Menu("zone"), *([Menu("zone_master")] if has_zone else []), Form("zone_master", {"master": master})]


def _master(name: str, devices: tuple[str, ...], self_regulation: str = "auto_regulation_none") -> Thermostat:
    return Thermostat(name, "climate", "lounge", devices, algorithm="", heating_type=None, self_regulation=self_regulation)


@scenario("QE1", ("zone",), 60, "test_config_flow_zone.py", minimum=True)
async def QE1(r: Any) -> None:
    """The zone form refuses each wrong master, creates the missing entry of a device restarting only that thermostat, moves a zone set up from the additional source to the main one, stops with zone_changed, and removes the zone."""
    zone = _zone()
    main_title, other_title = _title(zone.main), _title(zone.additional)
    transport = RestTransport(r.ha)
    created: list[str] = []
    faulty = (
        (_master("QAS self regulation", (zone.main, zone.additional), "auto_regulation_light"), "self-regulation on"),
        (_master("QAS one device", (zone.main,)), "not two devices"),
        (_master("QAS other sensors", (zone.main, "climate.spare")), "devices with different sensors"),
        (_master("QAS no smartpi", (zone.main, "climate.lounge_dummy")), "a device without SmartPI"),
        (_master("QAS already zoned", (EXTRA, zone.additional)), "a device already in another zone"),
    )
    try:
        for master, what in faulty:
            await run_flow(transport, "config", VT, vt_thermostat_steps(role(), master))
            created.append((await entry(r, VT, master.name))["entry_id"])
            await api(r).settle([master.climate], 120.0)
            # A device already in a zone: asked from another thermostat's entry (from the main
            # source's own entry, the same pair is its own zone).
            title = EXTRA_TITLE if master.name == "QAS already zoned" else main_title
            source_entry = (await entry(r, SMARTPI, title))["entry_id"]
            refused = await walk(
                transport, "options", source_entry, zone_master_steps(title == main_title, master.climate), refused_ok=True
            )
            if refused.get("type") in ("form", "menu"):
                await transport.abort("options", refused["flow_id"])
            r.check(bool(refused.get("errors")), f"{what}: {refused.get('errors') or refused.get('type')}")
        for entry_id in created:
            await r.ha.rest("DELETE", f"/api/config/config_entries/entry/{entry_id}")
        created.clear()
        await _resend_master(r)

        await options_flow(r, main_title, [Menu("zone"), Menu("zone_remove"), Form("zone_reset", {"reset_model": False}, if_shown=True)])
        r.check(await _source_item(r, main_title) is None, "the zone removed through its reset step")
        other_entry = await entry(r, SMARTPI, other_title)
        await r.ha.rest("DELETE", f"/api/config/config_entries/entry/{other_entry['entry_id']}")
        diag = await diag_entity(r, zone.main)
        unavailable = 0

        async def watch() -> None:
            nonlocal unavailable
            while True:
                if (await r.ha.state(diag) or {}).get("state") == "unavailable":
                    unavailable += 1
                await asyncio.sleep(2.0)

        watcher = asyncio.create_task(watch())
        try:
            await options_flow(r, main_title, zone_steps(zone))
            await asyncio.sleep(30.0)
        finally:
            watcher.cancel()
        r.check(any(e["title"] == other_title for e in await api(r).entries() if e["domain"] == SMARTPI), f"the form created {other_title!r}'s entry")
        r.check(unavailable == 0, f"the main source kept running meanwhile (diagnostics unavailable {unavailable} times)")

        await options_flow(r, main_title, [Menu("zone"), Menu("zone_remove"), Form("zone_reset", {"reset_model": False}, if_shown=True)])
        await options_flow(r, other_title, zone_steps(zone))
        r.check(await _source_item(r, main_title) is not None and await _source_item(r, other_title) is None,
                "set up from the additional source, the zone is stored in the main source's entry")  # fmt: skip

        main_entry = (await entry(r, SMARTPI, main_title))["entry_id"]
        pending = await walk(
            transport, "options", main_entry,
            [Menu("zone"), Menu("zone_master"), Form("zone_master", {"master": zone.master}),
             Form("zone_sources", {"main_source": zone.main, "split_rule": zone.rule, "custom_response": False})],
        )  # fmt: skip
        await options_flow(r, main_title, [Menu("zone"), Menu("zone_remove"), Form("zone_reset", {"reset_model": False}, if_shown=True)])
        result = await walk(
            transport, "options", main_entry,
            [Form("zone_parameter"), Form("zone_reset", {"reset_model": False}, if_shown=True)],
            resume=pending,
        )  # fmt: skip
        r.check(result.get("type") == "abort" and result.get("reason") == "zone_changed", f"a form whose zone changed meanwhile: {result.get('type')} {result.get('reason')}")
    finally:
        for entry_id in created:
            await r.ha.rest("DELETE", f"/api/config/config_entries/entry/{entry_id}")
        await _ensure_zone(r)
        await _resend_master(r)
        r.check(await wait_loaded(r, [zone.main, zone.additional]), "the zone runs again")


@scenario("QE2", ("zone",), 60, "test_smartpi_sim_second_source.py", minimum=True)
async def QE2(r: Any) -> None:
    """In the running zone the additional source learns nothing and applies the main source's command, and changing its heating type restarts the main source."""
    zone = _zone()
    took, data = await wait_live(r, zone.additional, lambda d: path(d, "dependencies.role") == "second_source", 3 * RECALC_S, 10.0)
    r.check(took is not None, f"the floor works as the additional source of {path(data, 'dependencies.lead')}")
    learned = (path(data, "learning.accepted_updates_a"), path(data, "learning.accepted_updates_b"))
    gaps = []

    async def pair() -> tuple[Any, Any]:
        lead = await live(r, zone.main)
        floor = await live(r, zone.additional)
        return path(lead, "dependencies.second_source.command"), path(floor, "dependencies.command")

    for _t, (sent, applied) in await sample(r, 30 * 60.0, 60.0, pair):
        if sent is not None and applied is not None:
            gaps.append(abs(sent - applied))
    r.check(gaps and sum(g <= 0.02 for g in gaps) >= 0.8 * len(gaps), f"the floor applies the radiator's command ({len(gaps)} samples, largest gap {max(gaps, default=None)})")
    data = await live(r, zone.additional)
    r.check((path(data, "learning.accepted_updates_a"), path(data, "learning.accepted_updates_b")) == learned, f"the floor learned nothing: {learned}")
    other_title = _title(zone.additional)
    try:
        await options_flow(r, other_title, [Form("source_role", {"smart_pi_heating_type": "floor_electric"})])
        took, data = await wait_live(r, zone.main, lambda d: path(d, "dependencies.second_source.source_emitter") == "floor_electric", 3 * MARGIN_S, 10.0)
        r.check(took is not None, f"the main source restarted with the floor's new type: {path(data, 'dependencies.second_source.source_emitter')}")
    finally:
        await options_flow(r, other_title, [Form("source_role", {"smart_pi_heating_type": "floor_water"})])
        r.note("put back: the floor's heating type floor_water")


@scenario("QE3", ("zone",), 60, "Z6", minimum=True)
async def QE3(r: Any) -> None:
    """When the main source goes silent, the additional source regulates the zone alone after 15 minutes with the room above 19.3 °C, and follows the main source again once it is back."""
    zone = _zone()
    since = time.time()
    lows: list[float] = []
    try:
        await api(r).call("smartpi_qas_helpers", "silence_lead", {"entity_id": zone.main, "silent": True})
        took, _data = await wait_live(r, zone.additional, lambda d: path(d, "dependencies.role") != "second_source", SOURCE_SILENT_S + 3 * RECALC_S + MARGIN_S, 20.0)
        r.check(took is not None and took >= SOURCE_SILENT_S - MARGIN_S, f"the floor regulates alone {took and round(took)} s after the silence (limit {SOURCE_SILENT_S:.0f} s)")
        warnings = log_lines(await system_log(r), since=since, levels=("WARNING",), loggers=("custom_components.vtherm_smartpi",))
        r.check(bool(warnings), f"a warning says so: {warnings[:1]}")
        for _t, value in await sample(r, 20 * 60.0, 60.0, lambda: room_temperature(r, "lounge")):
            if value is not None:
                lows.append(value)
        r.check(lows and min(lows) > 19.3, f"the room stays above 19.3 °C: lowest {min(lows, default=None)}")
    finally:
        await api(r).call("smartpi_qas_helpers", "silence_lead", {"entity_id": zone.main, "silent": False})
    took, _data = await wait_live(r, zone.additional, lambda d: path(d, "dependencies.role") == "second_source", 3 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, "the floor follows the main source again")


async def _set_master_devices(r: Any, devices: list[str]) -> None:
    master = next(e for e in await api(r).entries() if e["domain"] == VT and e["title"] == _title(_zone().master))
    await run_flow(
        RestTransport(r.ha), "options", master["entry_id"],
        [Menu("type"), Form("type", {"underlying_entity_ids": devices}), Menu("finalize")],
    )  # fmt: skip


@scenario("QE4", ("zone",), 15, "test_smartpi_dependencies_handler.py", minimum=True)
async def QE4(r: Any) -> None:
    """When the master's devices change in VTherm, SmartPI logs a warning at start and the zone keeps running."""
    zone = _zone()
    try:
        await _set_master_devices(r, [zone.main, "climate.lounge_dummy"])
        await restart(r)
        since = time.time() - 600.0
        r.check(await wait_loaded(r, [zone.main, zone.additional]), "the zone's thermostats are back")
        warnings = log_lines(await system_log(r), since=since, levels=("WARNING",), loggers=("custom_components.vtherm_smartpi",))
        r.check(bool(warnings), f"a warning at start about the master: {warnings[:2]}")
        took, data = await wait_live(r, zone.main, lambda d: path(d, "dependencies.second_source") is not None, 3 * RECALC_S, 10.0)
        r.check(took is not None, "the zone keeps running")
    finally:
        await _set_master_devices(r, [zone.main, zone.additional])
        await restart(r)
        r.check(await wait_loaded(r, [zone.main, zone.additional]), "put back: the master's two devices, after a restart")


async def _zone_run(r: Any, hours: float, name: str, restart_at_h: float | None) -> None:
    room = "lounge"
    setpoint = next(t.setpoint for t in role().thermostats if t.kind == "climate")
    heaters = (f"{room}_radiator", f"{room}_floor")
    heat0 = {h: await heat(r, h) for h in heaters}
    published_at: float | None = None
    errors: list[tuple[float, float]] = []
    start = time.time()
    restarted = False
    while time.time() - start < hours * 3600.0:
        elapsed = time.time() - start
        if restart_at_h is not None and not restarted and elapsed >= restart_at_h * 3600.0:
            await restart(r)
            await wait_loaded(r, [t.climate for t in role().thermostats if t.smartpi])
            restarted = True
        temperature = await room_temperature(r, room)
        if temperature is not None:
            errors.append((elapsed, setpoint - temperature))
        if role().zone is not None and published_at is None:
            if path(await live(r, role().zone.main), "dependencies.second_source.zone_fit.published"):
                published_at = elapsed
        await asyncio.sleep(120.0)
    delivered = {h: await heat(r, h) - heat0[h] for h in heaters}
    total = sum(delivered.values())
    late = [e for t, e in errors if t >= hours * 3600.0 / 2.0]
    measured = {
        "hours": hours,
        "role": role().name,
        "mean_error_second_half": sum(late) / len(late) if late else None,
        "floor_share": delivered[heaters[1]] / total if total > 0 else None,
        "fit_published_h": None if published_at is None else published_at / 3600.0,
    }
    if role().zone is not None:
        measured["fit_published_after_restart"] = bool(
            path(await live(r, role().zone.main), "dependencies.second_source.zone_fit.published")
        )
    where = keep(name, measured)
    r.note(f"kept for tests/qas/compare.py: {where.name} {measured}")
    errors_logged = log_lines(await system_log(r), levels=("ERROR",))
    r.check(not errors_logged, f"no error over the run: {errors_logged[:3]}")


@scenario("QE5", ("zone", "zone-ref"), 48 * 60, "Z1 compared")
async def QE5(r: Any) -> None:
    """Over 48 h the zone publishes its model before about 30 h, regulates no more than 0.05 °C worse than the independent pair, the floor gives over 20 % of the heat, and a restart at 36 h keeps the model; compare.py qe5 judges it."""
    await _zone_run(r, 48.0, "qe5", 36.0)
    if role().zone is not None:
        r.note("the zone's own checks are in compare.py qe5, with instance 4")


@scenario("QE5s", ("zone", "zone-ref"), 45, "Z1 compared", minimum=True, short_of=("QE5",))
async def QE5s(r: Any) -> None:
    """For 45 minutes the zone (or the independent pair) regulates and nothing logs an error, the floor following the radiator in the zone."""
    await _zone_run(r, 0.75, "qe5s", None)
    if role().zone is not None:
        data = await live(r, role().zone.additional)
        r.check(path(data, "dependencies.role") == "second_source", f"the floor works as the additional source: {path(data, 'dependencies.role')}")


def _overpowering(state: dict[str, Any] | None) -> bool:
    attributes = (state or {}).get("attributes") or {}
    return any("overpower" in key and value is True for key, value in attributes.items())


@scenario("QE6", ("zone-valve",), 60, "power shedding")
async def QE6(r: Any) -> None:
    """When VTherm's power manager sheds the zone's devices, a shed main source still sends its command and a shed additional source stays off."""
    zone = _zone()
    floor_valve = role().thermostat(_title(zone.additional)).underlying[0]
    try:
        await api(r).call("input_number", "set_value", {"entity_id": MAX_POWER_INPUT, "value": 600})
        seen_main = seen_floor = False
        commands, openings = [], []
        start = time.time()
        while time.time() - start < 30 * 60.0:
            main_shed = _overpowering(await r.ha.state(zone.main))
            floor_shed = _overpowering(await r.ha.state(zone.additional))
            if main_shed:
                seen_main = True
                commands.append(path(await live(r, zone.main), "dependencies.second_source.command"))
            if floor_shed:
                seen_floor = True
                openings.append(float((await r.ha.state(floor_valve) or {}).get("state") or 0.0))
            await asyncio.sleep(30.0)
        r.check(seen_main or seen_floor, f"VTherm shed the zone's devices (main {seen_main}, floor {seen_floor})")
        if seen_main:
            r.check(all(c is not None for c in commands), f"the shed main source still sends its command: {commands[:5]}")
        if seen_floor:
            r.check(all(o == 0.0 for o in openings), f"the shed floor stays off: {openings[:5]}")
    finally:
        await api(r).call("input_number", "set_value", {"entity_id": MAX_POWER_INPUT, "value": role().max_power_w})
        r.note(f"put back: {MAX_POWER_INPUT} = {role().max_power_w}")


@scenario("QE7", ("zone-valve",), 120, "valve as additional source")
async def QE7(r: Any) -> None:
    """Over 2 h the valve, as the additional source, follows the main source's command."""
    zone = _zone()
    floor_valve = role().thermostat(_title(zone.additional)).underlying[0]

    async def pair() -> tuple[Any, Any]:
        sent = path(await live(r, zone.main), "dependencies.second_source.command")
        opening = (await r.ha.state(floor_valve) or {}).get("state")
        return sent, opening

    gaps = []
    for t, (sent, opening) in await sample(r, 2 * 3600.0 - 120.0, 60.0, pair):
        if t < 600.0 or sent is None or opening in (None, "unknown", "unavailable"):
            continue
        gaps.append(abs(sent * 100.0 - float(opening)))
    r.check(gaps and sum(gaps) / len(gaps) <= 5.0, f"the valve follows the command: mean gap {sum(gaps) / len(gaps) if gaps else None} points over {len(gaps)} samples")
