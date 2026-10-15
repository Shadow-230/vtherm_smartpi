"""QA: the plugin on a clean instance — entries, options menu, services, AutoCalib events,
restarts and heating types (roles ``base`` and ``electric``)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from bench import RestTransport
from flows import FlowError, Form, Menu, walk
from roles import PROVIDER
from stages import CALL_SOURCE, SMARTPI, VT, WATER_SOURCE, save_steps

from .common import (
    MARGIN_S,
    RECALC_S,
    api,
    diag_entity,
    entry,
    live,
    log_lines,
    options_flow,
    path,
    recorded_events,
    restart,
    role,
    stored_options,
    system_log,
    wait_live,
    wait_loaded,
)
from .registry import scenario

LIVING = "climate.living"


def _smartpi_climates() -> list[str]:
    return [t.climate for t in role().thermostats if t.smartpi]


@scenario("QA1", ("base", "electric"), 15, "test_factory.py", minimum=True)
async def QA1(r: Any) -> None:
    """After the setup's restart every SmartPI and VTherm entry is loaded, neither logs a warning or an error, each SmartPI thermostat has its diagnostics, no heating provider is registered but the helpers' where the role has it, and no entry chooses a water or a boiler's call source."""
    entries = await api(r).entries()
    for domain in (SMARTPI, VT):
        for e in (e for e in entries if e["domain"] == domain):
            r.check(e.get("state") == "loaded", f"{domain} entry {e['title']!r}: {e.get('state')}")
    titles = {e["title"] for e in entries if e["domain"] == SMARTPI}
    r.check(set(role().smartpi_titles) <= titles, f"SmartPI entries: {sorted(titles)}")
    climates = _smartpi_climates()
    r.check(await wait_loaded(r, climates, 600.0), f"thermostats and diagnostics loaded: {climates}")
    for climate in climates:
        data = await live(r, climate)
        r.check(bool(path(data, "control.phase")), f"{climate}: diagnostics published, phase {path(data, 'control.phase')}")
    lines = log_lines(await system_log(r))
    r.check(not lines, f"warnings and errors of SmartPI and VTherm since the restart: {len(lines)}")
    for line in lines[:12]:
        r.note(line)
    status = await api(r).respond("smartpi_qas_helpers", "provider_status", {})
    providers = (status or {}).get("providers")
    expected = [PROVIDER] if role().provider else []
    r.check(providers == expected, f"heating providers registered: {providers} (expected {expected})")
    for title in role().smartpi_titles:
        stored = await stored_options(title)
        chosen = {key: stored.get(key) for key in (WATER_SOURCE, CALL_SOURCE) if stored.get(key) not in (None, "none")}
        r.check(not chosen, f"{title}: no water or boiler's call source chosen: {chosen}")


@scenario("QA2", ("base",), 15, "test_config_flow_dependencies.py", minimum=True)
async def QA2(r: Any) -> None:
    """Saving a thermostat entry's settings unchanged goes through the options menu and "Save and finish" without the reset question, keeps the stored options, and reloads the entry once without a loop."""
    title = role().thermostats[0].name
    climate = role().thermostats[0].climate
    before = await stored_options(title)
    await options_flow(r, title, [Menu("settings"), Form("settings"), Form("settings_own", if_shown=True), Menu("save")])
    r.check(True, "menu → settings → Save and finish, no reset question (the flow ended at create_entry)")
    after = await stored_options(title)
    r.check(after == before, f"stored options unchanged ({len(after)} keys)")
    diag = await diag_entity(r, climate)
    unavailable, previous = 0, None
    start = time.time()
    while time.time() - start < 300.0:
        state = (await r.ha.state(diag) or {}).get("state")
        if state == "unavailable" and previous != "unavailable":
            unavailable += 1
        previous = state
        await asyncio.sleep(2.0)
    r.check(unavailable == 0, f"the diagnostics went unavailable {unavailable} times in the 5 minutes after the reload")
    r.check((await entry(r, SMARTPI, title)).get("state") == "loaded", "the entry is loaded")


@scenario("QA3", ("base",), 30, "test_services.py")
async def QA3(r: Any) -> None:
    """SmartPI's four services work on VTherm 10.5.1, and a paused learning stays paused across a restart."""
    target = {"entity_id": LIVING}
    try:
        await api(r).call(SMARTPI, "set_smartpi_learning", target | {"learning_enabled": False})
        took, data = await wait_live(r, LIVING, lambda d: path(d, "learning.enabled") is False, MARGIN_S, 5.0)
        r.check(took is not None, f"learning paused: learning.enabled {path(data, 'learning.enabled')}")
        await api(r).call(SMARTPI, "reset_smartpi_integral", target)
        took, data = await wait_live(
            r, LIVING, lambda d: abs(path(d, "temperature.integral_error", 1.0) or 0.0) < 1e-6, MARGIN_S, 5.0
        )
        r.check(took is not None, f"integral reset: {path(data, 'temperature.integral_error')}")
        before = path(await live(r, LIVING), "calibration.state")
        await api(r).call(SMARTPI, "force_smartpi_calibration", target)
        took, data = await wait_live(r, LIVING, lambda d: path(d, "calibration.state") != before, MARGIN_S, 5.0)
        r.check(took is not None, f"calibration forced: state {before} → {path(data, 'calibration.state')}")
        await api(r).call(SMARTPI, "reset_smartpi_learning", target)
        took, data = await wait_live(
            r, LIVING, lambda d: (path(d, "learning.accepted_updates_a") or 0) == 0, MARGIN_S, 5.0
        )
        r.check(took is not None, f"learning reset: accepted updates a {path(data, 'learning.accepted_updates_a')}")
        await restart(r)
        r.check(await wait_loaded(r, [LIVING]), "the thermostat is back after the restart")
        took, data = await wait_live(r, LIVING, lambda d: path(d, "learning.enabled") is not None, 600.0, 10.0)
        r.check(path(data, "learning.enabled") is False, f"after the restart learning is still paused: {path(data, 'learning.enabled')}")
        lines = log_lines(await system_log(r), levels=("ERROR",))
        r.check(not lines, f"no error since the restart: {lines[:3]}")
    finally:
        await api(r).call(SMARTPI, "set_smartpi_learning", target | {"learning_enabled": True})
        r.note("put back: learning enabled")


@scenario("QA4", ("base",), 60, "test_smartpi_autocalib_events.py", minimum=True)
async def QA4(r: Any) -> None:
    """AutoCalib events carry the thermostat's climate entity in entity_id and its name in name, and neither the recorder nor the logbook logs an invalid entity id."""
    since = time.time()
    await recorded_events(r, clear=True)
    climate, name = LIVING, role().thermostats[0].name

    def autocalib(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in events if e["event_type"].startswith("smartpi_autocalib_")]

    found: list[dict[str, Any]] = []
    deadline = time.time() + 40 * 60.0
    while time.time() < deadline and not found:
        found = autocalib(await recorded_events(r))
        if not found:
            await asyncio.sleep(60.0)
    origin = "on its own"
    if not found:
        await api(r).call(SMARTPI, "force_smartpi_calibration", {"entity_id": climate})
        await asyncio.sleep(RECALC_S)
        found = autocalib(await recorded_events(r))
        origin = "after force_smartpi_calibration"
    if not found:
        phase = path(await live(r, climate), "control.phase")
        await api(r).call("smartpi_qas_helpers", "fire_autocalib_event", {"entity_id": climate})
        await asyncio.sleep(5.0)
        found = autocalib(await recorded_events(r))
        origin = f"through the helper (SmartPI in phase {phase})"
    r.check(bool(found), f"an AutoCalib event came {origin}")
    for event in found:
        data = event["data"]
        r.check(
            data.get("entity_id") == climate and data.get("name") == name,
            f"{event['event_type']}: entity_id {data.get('entity_id')!r}, name {data.get('name')!r}",
        )
    await asyncio.sleep(30.0)
    lines = log_lines(
        await system_log(r),
        since=since,
        loggers=("homeassistant.components.recorder", "homeassistant.components.logbook", "homeassistant.core"),
        levels=("ERROR", "WARNING"),
        contains="Invalid entity ID",
    )
    r.check(not lines, f"no 'Invalid entity ID' from the recorder or the logbook: {lines[:2]}")
    if origin.startswith("after force"):
        r.note("a calibration was forced; it ends on its own")


@scenario("QA5", ("base",), 20, "test_algo_persistence.py", minimum=True)
async def QA5(r: Any) -> None:
    """Two restarts keep the learned state (the model, its counts and the applied reset token), and a reset token is not applied again."""
    climate = LIVING
    before = await live(r, climate)
    keys = ("model.a", "model.b", "learning.accepted_updates_a", "learning.accepted_updates_b", "learning.emea_samples_b")
    snapshot = {k: path(before, k) for k in keys}
    r.note(f"before: {snapshot}")
    token = (await stored_options(role().thermostats[0].name)).get("smart_pi_reset_token")
    for n in (1, 2):
        await restart(r)
        r.check(await wait_loaded(r, [climate]), f"restart {n}: the thermostat is back")
        took, after = await wait_live(r, climate, lambda d: path(d, "model.a") is not None or snapshot["model.a"] is None, 300.0, 10.0)
        for key in ("model.a", "model.b"):
            r.check(_close(path(after, key), snapshot[key]), f"restart {n}: {key} {snapshot[key]} → {path(after, key)}")
        for key in keys[2:]:
            r.check(
                (path(after, key) or 0) >= (snapshot[key] or 0),
                f"restart {n}: {key} {snapshot[key]} → {path(after, key)} (not reset)",
            )
    r.note(f"reset token in the options: {token!r}")
    lines = log_lines(await system_log(r), levels=("WARNING", "ERROR"), contains="format")
    r.check(not lines, f"no warning about the saved state's format: {lines[:2]}")


def _close(a: Any, b: Any, rel: float = 1e-6) -> bool:
    if a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) <= rel * max(abs(float(b)), 1e-12)


@scenario("QA6", ("base", "electric"), 20, "test_smartpi_water_handler.py, test_smartpi_boiler_handler.py", minimum=True)
async def QA6(r: Any) -> None:
    """A water heating type without a water data source shows the water block with source none, factor 1 and its inputs from no source, and no boiler block without a call source, an electric type shows neither, and nothing is logged at each control call."""
    since = time.time()
    for thermostat in (t for t in role().thermostats if t.smartpi):
        took, data = await wait_live(r, thermostat.climate, lambda d: bool(path(d, "control.phase")), MARGIN_S, 10.0)
        kind = thermostat.heating_type or "radiator"
        if kind in ("radiator", "floor_water"):
            water = path(data, "water") or {}
            r.check(
                water.get("source") == "none" and water.get("factor") == 1,
                f"{thermostat.name} ({kind}): water {water.get('source')!r}, factor {water.get('factor')}",
            )
            r.check(
                path(water, "inputs.supply.source") == "none",
                f"{thermostat.name} ({kind}): the supply from no source: {path(water, 'inputs.supply')}",
            )
        else:
            r.check(path(data, "water") is None, f"{thermostat.name} ({kind}): no water block")
        r.check(path(data, "boiler") is None, f"{thermostat.name} ({kind}): no boiler block")
    if role().name == "base":
        title = role().thermostats[0].name
        try:
            await options_flow(r, title, [Menu("settings"), Form("settings", {"smart_pi_heating_type": "floor_water"}), Form("settings_own", if_shown=True), *save_steps()])
            took, data = await wait_live(
                r, LIVING, lambda d: path(d, "water.heating_type") == "floor_water", 2 * MARGIN_S, 10.0
            )
            r.check(
                took is not None and path(data, "water.source") == "none" and path(data, "water.factor") == 1,
                f"floor_water: water {path(data, 'water.source')!r}, factor {path(data, 'water.factor')}",
            )
        finally:
            await options_flow(r, title, [Menu("settings"), Form("settings", {"smart_pi_heating_type": "radiator"}), Form("settings_own", if_shown=True), *save_steps()])
            r.note("put back: heating type radiator")
    await asyncio.sleep(10 * 60.0)
    lines = log_lines(await system_log(r), since=since, loggers=("custom_components.vtherm_smartpi",))
    r.check(not lines, f"nothing logged by SmartPI over 10 minutes of control calls: {lines[:3]}")


@scenario("QA7", ("base",), 15, "test_config_flow_save.py", minimum=True)
async def QA7(r: Any) -> None:
    """A change made in the options and the window closed stores nothing and reloads nothing; the menu names the change in the instance's language (English where SmartPI has no text of its own); the same change saved is stored and reloads the entry once."""
    title = role().thermostats[0].name
    found = await entry(r, SMARTPI, title)
    before = await stored_options(title)
    deadband = 0.11 if before.get("smart_pi_deadband") != 0.11 else 0.12
    change = [
        Menu("settings"),
        Form("settings", {"smart_pi_use_defaults": False}),
        Form("settings_own", {"smart_pi_deadband": deadband}),
    ]
    transport = RestTransport(r.ha)
    config = await r.ha.rest("GET", "/api/config")
    language = (config or {}).get("language") or "en"
    texts = await r.ha.ws_cmd(
        {"type": "frontend/get_translations", "language": language, "category": "options", "integration": [SMARTPI]}
    )
    label = ((texts or {}).get("resources") or {}).get(
        "component.vtherm_smartpi.options.step.thermostat_menu.menu_options.settings"
    )
    reloads_before = await _reloads(r, found["entry_id"])
    result = await walk(transport, "options", found["entry_id"], change)
    changes = (result.get("description_placeholders") or {}).get("changes")
    r.check(result.get("step_id") == "thermostat_menu" and changes == label, f"the menu names the change in {language!r}: {changes!r} (expected {label!r})")
    await transport.abort("options", result["flow_id"])
    await asyncio.sleep(15.0)
    r.check(await stored_options(title) == before, "closing the window stored nothing")
    r.check(await _reloads(r, found["entry_id"]) == reloads_before, "closing the window reloaded nothing")
    try:
        await options_flow(r, title, [*change, *save_steps()])
        stored = await stored_options(title)
        r.check(stored.get("smart_pi_deadband") == deadband, f"saved: deadband {stored.get('smart_pi_deadband')}")
        r.check(await _reloads(r, found["entry_id"]) == reloads_before + 1, "the save reloaded the entry once")
    except FlowError as err:
        r.check(False, f"the save went another way: {err}")
    finally:
        restore = [Menu("settings"), Form("settings", {"smart_pi_use_defaults": before.get("smart_pi_use_defaults", False)})]
        if not before.get("smart_pi_use_defaults", False):
            restore.append(Form("settings_own", {k: before[k] for k in ("smart_pi_deadband",) if k in before}))
        await options_flow(r, title, [*restore, *save_steps()])
        r.note("put back: the settings as before")


async def _reloads(r: Any, entry_id: str) -> int:
    """How many times SmartPI's entry was set up since the start (the helpers count it)."""
    status = await api(r).respond("smartpi_qas_helpers", "entry_setups", {"entry_id": entry_id})
    return int((status or {}).get("count") or 0)
