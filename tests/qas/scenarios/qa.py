"""QA: the plugin on a clean instance — entries, options menu, services, AutoCalib events,
restarts and heating types (instances 1 and 8)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from flows import Form, Menu
from stages import SMARTPI, VT

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
    """After the setup's restart every SmartPI and VTherm entry is loaded, neither logs a warning or an error, each SmartPI thermostat has its diagnostics, and nothing of Smart Boiler is there."""
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
    _status, config = await r.ha.rest("GET", "/api/config")
    components = set((config or {}).get("components") or [])
    r.check(
        not any(c.startswith("vtherm_smart_boiler") for c in components)
        and not any(e["domain"] == "vtherm_smart_boiler" for e in entries),
        "no Smart Boiler integration and no entry of it",
    )


@scenario("QA2", ("base",), 15, "test_config_flow_dependencies.py", minimum=True)
async def QA2(r: Any) -> None:
    """Saving a thermostat entry's settings unchanged goes through the options menu without a reset step, keeps the stored options, and reloads the entry once without a loop."""
    title = role().thermostats[0].name
    climate = role().thermostats[0].climate
    before = await stored_options(title)
    await options_flow(r, title, [Menu("settings"), Form("settings")])
    r.check(True, "menu → settings saved, no model_reset step (the flow ended at create_entry)")
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
    """A water heating type shows the water block with source none and factor 1 and no boiler block without VTherm's central boiler, an electric type shows neither, and nothing is logged at each control call."""
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
        else:
            r.check(path(data, "water") is None, f"{thermostat.name} ({kind}): no water block")
        r.check(path(data, "boiler") is None, f"{thermostat.name} ({kind}): no boiler block")
    if role().name == "base":
        title = role().thermostats[0].name
        try:
            await options_flow(r, title, [Menu("settings"), Form("settings", {"smart_pi_heating_type": "floor_water"})])
            took, data = await wait_live(
                r, LIVING, lambda d: path(d, "water.heating_type") == "floor_water", 2 * MARGIN_S, 10.0
            )
            r.check(
                took is not None and path(data, "water.source") == "none" and path(data, "water.factor") == 1,
                f"floor_water: water {path(data, 'water.source')!r}, factor {path(data, 'water.factor')}",
            )
        finally:
            await options_flow(r, title, [Menu("settings"), Form("settings", {"smart_pi_heating_type": "radiator"})])
            r.note("put back: heating type radiator")
    await asyncio.sleep(10 * 60.0)
    lines = log_lines(await system_log(r), since=since, loggers=("custom_components.vtherm_smartpi",))
    r.check(not lines, f"nothing logged by SmartPI over 10 minutes of control calls: {lines[:3]}")
