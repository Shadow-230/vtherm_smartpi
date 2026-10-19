"""QD: neighbouring zones (role ``neighbours``).

The kitchen (SmartPI) has two neighbours: the hall behind a door (``kitchen_door``; the hall's
VTherm has a window sensor), and the dining room, always open. In the simulator the kitchen–hall
exchange is g = 0.004 /min through the door and the kitchen–dining one 0.003 /min, always.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from bench import RestTransport
from flows import Form, Menu, form_defaults, walk
from roles import opening_sensor, temperature_sensor
from stages import SMARTPI, save_steps

from .common import (
    DOOR_SETTLING_S,
    MARGIN_S,
    RECALC_S,
    VT_WINDOW_DELAY_S,
    api,
    entry,
    live,
    log_lines,
    options_flow,
    path,
    restart,
    role,
    seed_learned,
    set_temperature,
    sim,
    stored_options,
    system_log,
    wait_live,
    wait_loaded,
)
from .registry import FRESH, NEIGHBOURS, RESET, SEED, Phase, changes, scenario

KITCHEN = "climate.kitchen"
HALL = "climate.hall"
DINING = "climate.dining"
KITCHEN_DOOR = opening_sensor("kitchen_door")
HALL_WINDOW = opening_sensor("hall_window")
HALL_SENSOR = temperature_sensor("hall")
TITLE = "Kitchen"
FIXTURE = "learned_room"
G_HALL = 0.004
G_DINING = 0.003


async def _items(r: Any) -> list[dict[str, Any]]:
    stored = await stored_options(TITLE)
    return [i for i in stored.get("smart_pi_dependencies") or [] if i.get("kind") == "neighbour"]


async def _item_id(r: Any, entity_id: str) -> str:
    registry_id = next(e["id"] for e in await api(r).entity_registry() if e["entity_id"] == entity_id)
    for item in await _items(r):
        if item.get("entity") == registry_id:
            return item["id"]
    raise LookupError(f"{entity_id} is not a neighbour of {TITLE}")


def _status(data: dict[str, Any], entity_id: str) -> dict[str, Any]:
    for item in path(data, "dependencies.items") or []:
        if item.get("entity") == entity_id:
            return item
    return {}


def _add() -> list[Menu]:
    return [Menu("dependencies"), Menu("neighbour_add")]


def _edit(item_id: str, details: dict[str, Any], reset: bool = False) -> list[Form | Menu]:
    return [
        Menu("dependencies"),
        Menu("dependency_edit"),
        Form("dependency_edit", {"dependency": item_id}),
        Form("neighbour_details", details),
        Form("neighbour_confirm", if_shown=True),
        *save_steps(reset),
    ]


def _remove(item_ids: list[str], reset: bool = False) -> list[Form | Menu]:
    return [
        Menu("dependencies"),
        Menu("dependency_remove"),
        Form("dependency_remove", {"remove": item_ids}),
        *save_steps(reset),
    ]


async def _entry_id(r: Any) -> str:
    return (await entry(r, SMARTPI, TITLE))["entry_id"]


async def _refused(r: Any, steps: list[Form | Menu]) -> dict[str, Any]:
    transport = RestTransport(r.ha)
    result = await walk(transport, "options", await _entry_id(r), steps, refused_ok=True)
    if result.get("type") in ("form", "menu"):
        await transport.abort("options", result["flow_id"])
    return result


async def _base_neighbours_back(r: Any) -> None:
    """The kitchen's neighbours as the role has them (the hall's window action, the dining's mode)."""
    for neighbour in role().neighbours[TITLE]:
        item_id = await _item_id(r, neighbour.entity)
        stored = next(i for i in await _items(r) if i["id"] == item_id)
        if stored.get("mode") != neighbour.mode or stored.get("neighbour_window_action", "nothing") != neighbour.window_action:
            await options_flow(
                r,
                TITLE,
                _edit(item_id, {"mode": neighbour.mode, "door_sensors": list(neighbour.door_sensors), "neighbour_window_action": neighbour.window_action}),
            )
            r.note(f"put back: {neighbour.entity} {neighbour.mode}, window action {neighbour.window_action}")


@scenario("QD1", ("neighbours",), 45, "test_config_flow_neighbours.py", minimum=True, state=changes(NEIGHBOURS))
async def QD1(r: Any) -> None:
    """The neighbour forms show each error and warning, suggest the door sensor of the two zones' areas, edit and remove with the reset question at save, refuse a neighbour that is gone on the form, go back to the menu when nothing is chosen to remove, and stop the save with zone_changed."""
    transport = RestTransport(r.ha)
    entry_id = await _entry_id(r)
    removed_sensor = False
    try:
        for entity, what in ((KITCHEN, "the thermostat itself"), (DINING, "a neighbour already listed")):
            result = await _refused(r, _add() + [Form("neighbour_add", {"entity": entity})])
            r.check(bool(result.get("errors")), f"neighbour_add refuses {what}: {result.get('errors')}")
        for doors, what in (([], "door mode without a door sensor"), ([HALL_WINDOW], "a window sensor as a door sensor")):
            result = await _refused(
                r, _add() + [Form("neighbour_add", {"entity": HALL_SENSOR}), Form("neighbour_details", {"mode": "door", "door_sensors": doors})]
            )
            r.check(bool(result.get("errors")), f"neighbour_details refuses {what}: {result.get('errors')}")

        await options_flow(r, TITLE, _remove([await _item_id(r, HALL)], reset=False))
        r.check(not [i for i in await _items(r) if i.get("entity") and i["mode"] == "door"], "the hall removed, through the reset question at save")
        result = await walk(transport, "options", entry_id, _add() + [Form("neighbour_add", {"entity": HALL})])
        suggested = (result.get("description_placeholders") or {}).get("suggestions") or ""
        r.check(KITCHEN_DOOR in suggested, f"the details form lists the door sensor of the zones' areas: {suggested.strip()}")
        r.check(not form_defaults(result).get("door_sensors"), "and does not preselect it")
        result = await walk(
            transport, "options", entry_id,
            [Form("neighbour_details", {"mode": "door", "door_sensors": [KITCHEN_DOOR], "neighbour_window_action": "nothing"})],
            resume=result,
        )  # fmt: skip
        r.check(result.get("step_id") == "neighbour_confirm", f"a warning to confirm (the hall's own entry does not list the kitchen): {result.get('description_placeholders')}")
        result = await walk(
            transport, "options", entry_id,
            [Form("neighbour_confirm", if_shown=True), *save_steps()],
            resume=result,
        )  # fmt: skip
        r.check(result.get("type") == "create_entry", f"the hall added back: {result.get('type')}")
        await asyncio.sleep(15.0)

        dining = await _item_id(r, DINING)
        await options_flow(r, TITLE, _edit(dining, {"mode": "always", "door_sensors": [], "neighbour_window_action": "eco"}))
        stored = next(i for i in await _items(r) if i["id"] == dining)
        r.check(stored.get("neighbour_window_action") == "eco", "editing the window action saves without the reset question")

        result = await walk(transport, "options", entry_id, [Menu("dependencies"), Menu("dependency_remove"), Form("dependency_remove", {"remove": []})])
        note = (result.get("description_placeholders") or {}).get("note") or ""
        r.check(result.get("step_id") == "thermostat_menu" and bool(note.strip()), f"removing nothing goes back to the menu, which says so: {result.get('step_id')} {note.strip()!r}")
        await transport.abort("options", result["flow_id"])

        pending = await walk(
            transport, "options", entry_id,
            [Menu("dependencies"), Menu("dependency_remove"), Form("dependency_remove", {"remove": [dining]})],
        )  # fmt: skip
        r.check(pending.get("step_id") == "thermostat_menu", f"a removal waits in the open window: {pending.get('step_id')}")
        await options_flow(r, TITLE, _edit(dining, {"mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"}))
        result = await walk(transport, "options", entry_id, [Menu("save")], resume=pending)
        r.check(result.get("type") == "abort" and result.get("reason") == "zone_changed", f"a window whose list changed meanwhile: {result.get('type')} {result.get('reason')}")

        await options_flow(r, TITLE, _add() + [
            Form("neighbour_add", {"entity": HALL_SENSOR}),
            Form("neighbour_details", {"mode": "always", "door_sensors": []}),
            Form("neighbour_confirm", if_shown=True),
            *save_steps(),
        ])  # fmt: skip
        sensor_item = await _item_id(r, HALL_SENSOR)
        await r.ha.ws_cmd({"type": "config/entity_registry/remove", "entity_id": HALL_SENSOR})
        removed_sensor = True
        result = await walk(
            transport, "options", entry_id,
            [Menu("dependencies"), Menu("dependency_edit"), Form("dependency_edit", {"dependency": sensor_item})],
            refused_ok=True,
        )  # fmt: skip
        r.check((result.get("errors") or {}).get("dependency") == "neighbour_missing", f"editing a neighbour whose entity is gone is refused on the form: {result.get('errors')}")
        if result.get("type") in ("form", "menu"):
            await transport.abort("options", result["flow_id"])
        await options_flow(r, TITLE, _remove([sensor_item], reset=False))
    finally:
        await _base_neighbours_back(r)
        if removed_sensor:
            await restart(r)
            r.check(await wait_loaded(r, [KITCHEN]), "put back: the hall's sensor registered again after a restart")
    items = await _items(r)
    r.check(len(items) == 2, f"the kitchen has its two neighbours again: {[(i.get('mode'), i.get('neighbour_window_action')) for i in items]}")


@scenario("QD2", ("neighbours",), 20, "test_smartpi_dependencies_handler.py", minimum=True, state=changes(SEED, RESET))
async def QD2(r: Any) -> None:
    """A reset chosen in a neighbour form resets the kitchen's model once, and the next restart does not reset it again."""
    dining = await _item_id(r, DINING)
    try:
        seeded = path(await seed_learned(r, KITCHEN, FIXTURE), "model.a")
        await options_flow(r, TITLE, _edit(dining, {"mode": "none", "door_sensors": [], "neighbour_window_action": "nothing"}, reset=True))
        took, data = await wait_live(r, KITCHEN, lambda d: path(d, "model.a") != seeded, 2 * MARGIN_S, 10.0)
        r.check(took is not None, f"the form's reset: a {seeded} → {path(data, 'model.a')}")
        again = path(await seed_learned(r, KITCHEN, FIXTURE), "model.a")
        await restart(r)
        r.check(await wait_loaded(r, [KITCHEN]), "the kitchen is back after the restart")
        after = path(await live(r, KITCHEN), "model.a")
        r.check(after == again, f"the restart does not reset it again: a {again} → {after}")
    finally:
        await options_flow(r, TITLE, _edit(dining, {"mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"}, reset=False))
        r.note("put back: the dining room always open")


@scenario("QD3", ("neighbours",), 30, "N1, N4", minimum=True)
async def QD3(r: Any) -> None:
    """A neighbour that is unavailable, or in mode none, takes no part: its connection is inactive and the kitchen's model has no known input from it."""
    dining_entry = next(e for e in await api(r).entries() if e["domain"] == "versatile_thermostat" and e["title"] == "Dining")
    try:
        await r.ha.ws_cmd({"type": "config_entries/disable", "entry_id": dining_entry["entry_id"], "disabled_by": "user"})
        took, data = await wait_live(r, KITCHEN, lambda d: _status(d, DINING).get("active") is False, 3 * RECALC_S + MARGIN_S, 10.0)
        status = _status(data, DINING)
        r.check(took is not None and status.get("available") is False, f"the dining room unavailable: {status}")
        r.check(path(data, "dependencies.inputs_active") is False, f"no known input with the door closed: {path(data, 'dependencies.inputs_active')}")
    finally:
        await r.ha.ws_cmd({"type": "config_entries/disable", "entry_id": dining_entry["entry_id"], "disabled_by": None})
        back = await wait_loaded(r, [KITCHEN], 300.0) and await r.ha.until(lambda: _available(r, DINING), 300.0, 5.0)
        r.check(back is not None, "put back: the dining room's VTherm enabled")
    dining = await _item_id(r, DINING)
    try:
        await options_flow(r, TITLE, _edit(dining, {"mode": "none", "door_sensors": [], "neighbour_window_action": "nothing"}))
        took, data = await wait_live(r, KITCHEN, lambda d: _status(d, DINING).get("mode") == "none", 2 * MARGIN_S, 10.0)
        status = _status(data, DINING)
        r.check(took is not None and status.get("active") is False, f"mode none: {status.get('mode')}, active {status.get('active')}")
    finally:
        await options_flow(r, TITLE, _edit(dining, {"mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"}))
        r.note("put back: the dining room always open")


async def _available(r: Any, entity_id: str) -> bool:
    state = await r.ha.state(entity_id)
    return bool(state) and state["state"] not in ("unavailable", "unknown")


@scenario("QD4", ("neighbours",), 60, "N6", minimum=True)
async def QD4(r: Any) -> None:
    """With the door open, the hall's open window makes the kitchen react as its setting says (nothing, off, frost, eco) without learning any k meanwhile, and the kitchen is back to normal once the window and the door close."""
    hall = await _item_id(r, HALL)
    try:
        for action in ("nothing", "turn_off", "frost", "eco"):
            await options_flow(r, TITLE, _edit(hall, {"mode": "door", "door_sensors": [KITCHEN_DOOR], "neighbour_window_action": action}))
            await sim(r, "set_opening", opening="kitchen_door", state="open")
            took, data = await wait_live(r, KITCHEN, lambda d: _status(d, HALL).get("active") is True, 2 * RECALC_S + 30.0, 10.0)
            r.check(took is not None, f"{action}: the door connects the hall")
            samples = {e: _status(data, e).get("k_samples") for e in (HALL, DINING)}
            await sim(r, "set_opening", opening="hall_window", state="open")
            expected = None if action == "nothing" else action
            took, data = await wait_live(
                r, KITCHEN, lambda d: path(d, "dependencies.neighbour_window_action") == expected and _status(d, HALL).get("neighbour_window") is True,
                VT_WINDOW_DELAY_S + 3 * RECALC_S + 30.0, 10.0,
            )  # fmt: skip
            r.check(took is not None, f"{action}: the reaction applied: {path(data, 'dependencies.neighbour_window_action')!r}")
            await asyncio.sleep(4 * 60.0)
            data = await live(r, KITCHEN)
            if action == "turn_off":
                r.check((path(data, "power.next_cycle_percent") or 0) == 0, f"off: the kitchen's next cycle {path(data, 'power.next_cycle_percent')} %")
            after = {e: _status(data, e).get("k_samples") for e in (HALL, DINING)}
            r.check(after == samples, f"{action}: no k learned with the window open: {samples} → {after}")
            r.check(path(data, "dependencies.learning_gate") == "neighbour_window", f"{action}: learning paused by the neighbour's window: {path(data, 'dependencies.learning_gate')}")
            await sim(r, "set_opening", opening="hall_window", state="closed")
            await sim(r, "set_opening", opening="kitchen_door", state="closed")
            took, data = await wait_live(r, KITCHEN, lambda d: path(d, "dependencies.neighbour_window_action") is None, VT_WINDOW_DELAY_S + 3 * RECALC_S + 30.0, 10.0)
            r.check(took is not None, f"{action}: back to normal once closed")
            await asyncio.sleep(60.0)
    finally:
        await sim(r, "set_opening", opening="hall_window", state="schedule")
        await sim(r, "set_opening", opening="kitchen_door", state="schedule")
        await options_flow(r, TITLE, _edit(hall, {"mode": "door", "door_sensors": [KITCHEN_DOOR], "neighbour_window_action": "nothing"}))
        r.note("put back: the hall's window action nothing, door and window closed")


async def _door_run(r: Any, hours: float, schedule: list[list[float]] | None, opens: int) -> None:
    # The dead times seen during each opening of the door: one list per opening.
    openings: list[list[tuple[Any, Any]]] = []
    was_open = False
    gates: set[str | None] = set()
    if schedule is not None:
        await sim(r, "set_opening", opening="kitchen_door", state="schedule", schedule=schedule)
    start = time.time()
    previous = None
    opened = 0
    while time.time() - start < hours * 3600.0:
        if schedule is None:
            minute = (time.time() - start) / 60.0
            want = opened < opens and (minute % 20.0) < 8.0
            if want != previous:
                await sim(r, "set_opening", opening="kitchen_door", state="open" if want else "closed")
                opened += want
                previous = want
        data = await live(r, KITCHEN)
        door = (await r.ha.state(KITCHEN_DOOR) or {}).get("state")
        gates.add(path(data, "dependencies.learning_gate"))
        if door == "on":
            if not was_open:
                openings.append([])
            openings[-1].append((path(data, "model.deadtime_heat_s"), path(data, "model.deadtime_cool_s")))
        was_open = door == "on"
        await asyncio.sleep(60.0)
    changed = [n for n, seen in enumerate(openings, 1) if len(set(seen)) > 1]
    r.check(openings and not changed, f"no dead time learned while the door is open ({len(openings)} openings; changed in {changed})")
    r.check({"door_open"} <= gates, f"learning gates seen: {sorted(map(str, gates))}")


@scenario("QD5", ("neighbours",), 72 * 60, "N2, N5", phase=Phase(FRESH))
async def QD5(r: Any) -> None:
    """With the door open every day 7–9, 12–13:30 and 18–22 for 72 h, the kitchen learns the hall's k within ±25 % of the true exchange and no dead time while the door is open."""
    try:
        await _door_run(r, 72.0, [[7.0, 9.0], [12.0, 13.5], [18.0, 22.0]], 0)
        status = _status(await live(r, KITCHEN), HALL)
        k = status.get("k")
        r.check(k is not None and abs(k / G_HALL - 1.0) <= 0.25, f"the hall's k {k} against the true {G_HALL}")
    finally:
        await sim(r, "reset", rooms=False)


@scenario("QD5s", ("neighbours",), 40, "N2", minimum=True, short_of=("QD5",))
async def QD5s(r: Any) -> None:
    """Opened twice for 8 minutes, the door pauses the kitchen's learning (door_open, then door_settling) and no dead time is learned while it is open."""
    try:
        await _door_run(r, 40.0 / 60.0, None, 2)
        await asyncio.sleep(DOOR_SETTLING_S / 2.0)
    finally:
        await sim(r, "set_opening", opening="kitchen_door", state="schedule")


async def _dining_steps(r: Any, hours: float, step_s: float) -> None:
    dining = role().thermostat("Dining")
    start = time.time()
    high = False
    try:
        while time.time() - start < hours * 3600.0:
            high = not high
            await set_temperature(r, DINING, 22.5 if high else 18.0)
            await asyncio.sleep(min(step_s, max(hours * 3600.0 - (time.time() - start), 0.0)))
    finally:
        await set_temperature(r, DINING, dining.setpoint)


@scenario("QD6", ("neighbours",), 72 * 60, "N3", phase=Phase(FRESH))
async def QD6(r: Any) -> None:
    """With the dining room always open and its temperature stepping on its own for 72 h, the kitchen learns its k within ±20 % of the true exchange, or flags it indistinguishable."""
    await _dining_steps(r, 72.0, 5 * 3600.0 + 13 * 60.0)
    status = _status(await live(r, KITCHEN), DINING)
    k = status.get("k")
    r.check(
        status.get("indistinguishable") is True or (k is not None and abs(k / G_DINING - 1.0) <= 0.20),
        f"the dining room's k {k} against the true {G_DINING}, indistinguishable {status.get('indistinguishable')}",
    )


@scenario("QD6s", ("neighbours",), 30, "N3", minimum=True, short_of=("QD6",))
async def QD6s(r: Any) -> None:
    """When the always-open dining room's temperature steps, the kitchen keeps it connected without pausing its learning, and logs no error."""
    since = time.time()
    await _dining_steps(r, 0.5, 20 * 60.0)
    data = await live(r, KITCHEN)
    status = _status(data, DINING)
    r.check(status.get("active") is True, f"the dining room stays connected: {status}")
    r.check(path(data, "dependencies.learning_gate") in (None, ""), f"no learning gate from an always-open neighbour: {path(data, 'dependencies.learning_gate')}")
    errors = log_lines(await system_log(r), since=since, levels=("ERROR",))
    r.check(not errors, f"no error: {errors[:3]}")
