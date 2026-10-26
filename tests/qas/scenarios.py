"""The QAS scenarios: SmartPI's learning pause below Versatile Thermostat's central boiler
threshold (documentation/en/technical_doc.md, 3.7), on a prepared instance.

West and East heat with water radiators the central boiler counts; North with an electric heater
it does not count. A scenario makes West call alone: one device, below both roles' thresholds (2
devices; 2500 W with devices of 2000 W), so VTherm's boiler stays off.

Each scenario is registered with its roles, its duration in real time, whether it is in the
minimum set and its in-process counterpart; the catalogue in README.md lists the same (checked by
tests/test_qas_files.py). Waits follow SmartPI's timing, read from the plugin on the stage: the
pause starts when the threshold is not reached at two consecutive calculations and is published at
once; VTherm applies a new command within a cycle (5 minutes, its default).
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from instance import Instance
from pack import HELPER_DOMAIN, ROLES, SMARTPI, VT, room

_CONST = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / SMARTPI / "smartpi" / "const.py"
_spec = importlib.util.spec_from_file_location("qas_smartpi_const", _CONST)
_const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_const)

RECALC_S = float(_const.SMARTPI_RECALC_INTERVAL_SEC)
CYCLE_S = 5 * 60.0  # VTherm's default cycle
MARGIN_S = 2 * RECALC_S + 15.0
WITHIN_S = 4 * RECALC_S + CYCLE_S / 2 + MARGIN_S
DETACHED_AFTER_MIN = 120  # the bench stops background commands after two hours

WEST, EAST, NORTH = (room(name).climate for name in ("West", "East", "North"))
BASE_SETPOINT = 20.0
CALLING = 23.0  # far above the rooms: SmartPI calls at full power
STOPPED = 7.0  # far below the rooms: SmartPI does not call
STARTUP_NOTICE = "External proportional algorithm 'smartpi' not yet registered"
PAUSE_TESTS = "test_boiler_call.py"
END_TO_END = "test_boiler_call.py, test_qas_instance.py"


# --- the registry -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    id: str
    run: Callable[[Any], Awaitable[None]]
    roles: tuple[str, ...]
    minutes: int
    counterpart: str
    minimum: bool

    @property
    def detached(self) -> bool:
        return self.minutes > DETACHED_AFTER_MIN

    @property
    def checks(self) -> str:
        return (self.run.__doc__ or "").strip()


SCENARIOS: dict[str, Scenario] = {}


def scenario(id: str, roles: tuple[str, ...], minutes: int, counterpart: str, minimum: bool = False):  # noqa: A002
    def register(run: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"{id} registered twice")
        SCENARIOS[id] = Scenario(id, run, roles, minutes, counterpart, minimum)
        return run

    return register


# --- reading the instance ---------------------------------------------------------------------


def _get(data: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


async def live(r: Any, climate: str) -> dict[str, Any]:
    """SmartPI's diagnostics of a thermostat (its sensor's ``live`` attribute)."""
    api = Instance(r.ha)
    uid = None
    for entry in await r.ha.ws_cmd({"type": "config/entity_registry/list"}):
        if entry["entity_id"] == climate:
            uid = entry.get("unique_id")
    sensor = await api.entity_id("sensor", SMARTPI, f"smartpi_diag_{uid}") if uid else None
    state = await r.ha.state(sensor) if sensor else None
    return ((state or {}).get("attributes") or {}).get("live") or {}


async def boiler(r: Any) -> str | None:
    """VTherm's central boiler: "on" or "off"."""
    entity_id = await Instance(r.ha).entity_id("binary_sensor", VT, "central_boiler_state")
    state = await r.ha.state(entity_id) if entity_id else None
    return state["state"] if state else None


async def until(read: Callable[[], Awaitable[Any]], holds: Callable[[Any], bool], within_s: float, every_s: float = 10.0):
    """Seconds until ``holds(await read())``, or None after ``within_s``; and the last value read."""
    start = time.time()
    value = None
    while time.time() - start <= within_s:
        value = await read()
        if holds(value):
            return time.time() - start, value
        await asyncio.sleep(every_s)
    return None, value


def paused(data: dict[str, Any]) -> Any:
    return _get(data, "learning.boiler_paused")


def command(data: dict[str, Any]) -> Any:
    return _get(data, "power.current_cycle_percent")


async def targets(r: Any, **values: float) -> None:
    for climate, value in values.items():
        await Instance(r.ha).call("climate", "set_temperature", {"entity_id": climate, "temperature": value})


async def base(r: Any) -> None:
    """Every thermostat heating at its base setpoint (the runner, before and after each scenario)."""
    api = Instance(r.ha)
    for climate in (WEST, EAST, NORTH):
        state = await r.ha.state(climate)
        if state is None:
            continue
        if state["state"] != "heat":
            await api.call("climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "heat"})
        if state["attributes"].get("temperature") != BASE_SETPOINT:
            await api.call("climate", "set_temperature", {"entity_id": climate, "temperature": BASE_SETPOINT})


# --- steps shared by the scenarios ------------------------------------------------------------


async def west_alone(r: Any) -> dict[str, Any]:
    """West calls alone (North calls too, not counted): West pauses. Its diagnostics then."""
    await targets(r, **{WEST: CALLING, EAST: STOPPED, NORTH: CALLING})
    took, data = await until(lambda: live(r, WEST), lambda d: paused(d) is True, WITHIN_S)
    r.check(took is not None, f"West alone: learning paused after {took and round(took)} s (boiler_paused {paused(data)})")
    return data


async def pause_holds(r: Any) -> None:
    """VTherm's boiler is off, East and North are not paused, and West's command goes on."""
    r.check(await boiler(r) == "off", f"VTherm's central boiler: {await boiler(r)}")
    for climate in (EAST, NORTH):
        r.check(paused(await live(r, climate)) is False, f"{climate}: boiler_paused {paused(await live(r, climate))}")
    for _ in range(3):
        data = await live(r, WEST)
        r.check(paused(data) is True and (command(data) or 0) > 0, f"West: boiler_paused {paused(data)}, command {command(data)} %")
        await asyncio.sleep(RECALC_S)


async def east_calls(r: Any) -> None:
    """East calls too: the threshold is reached, VTherm's boiler starts and West resumes."""
    await targets(r, **{EAST: CALLING})
    took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is False, WITHIN_S)
    r.check(took is not None, f"East calls too: West resumed after {took and round(took)} s")
    r.check(await boiler(r) == "on", f"VTherm's central boiler: {await boiler(r)}")


async def west_stops(r: Any) -> None:
    """West stops calling: it resumes though the threshold is still not reached."""
    await targets(r, **{WEST: STOPPED})
    took, data = await until(lambda: live(r, WEST), lambda d: paused(d) is False and (command(d) or 0) == 0, WITHIN_S)
    r.check(took is not None, f"West stops calling: resumed after {took and round(took)} s, command {command(data)} %")
    r.check(await boiler(r) == "off", f"VTherm's central boiler: {await boiler(r)}")


# --- the scenarios ----------------------------------------------------------------------------


@scenario("BP0", tuple(ROLES), 5, "test_qas_instance.py, test_published_diagnostics_schema.py", minimum=True)
async def BP0(r: Any) -> None:
    """After the preparation's restart every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published."""
    api = Instance(r.ha)
    for domain in (SMARTPI, VT):
        for entry in await api.entries(domain):
            r.check(entry.get("state") == "loaded", f"{domain} {entry['title']!r}: {entry.get('state')}")
    titles = [e["title"] for e in await api.entries(SMARTPI)]
    r.check(titles == ["SmartPI defaults"], f"SmartPI entries: {titles}")
    for climate in (WEST, EAST, NORTH):
        took, data = await until(lambda c=climate: live(r, c), lambda d: bool(_get(d, "control.phase")), 300.0)
        r.check(took is not None, f"{climate}: SmartPI's diagnostics, phase {_get(data, 'control.phase')}")
        r.check(_get(data, "learning.enabled") is True, f"{climate}: learning.enabled {_get(data, 'learning.enabled')}")
        r.check(isinstance(paused(data), bool), f"{climate}: learning.boiler_paused {paused(data)}")
    issues = (await r.ha.ws_cmd({"type": "repairs/list_issues"}) or {}).get("issues", [])
    repairs = sorted(i["issue_id"] for i in issues if i.get("domain") == SMARTPI)
    r.check(not repairs, f"SmartPI's repairs: {repairs}")
    lines = []
    for entry in await r.ha.ws_cmd({"type": "system_log/list"}):
        message = " | ".join(entry.get("message") or [])
        if str(entry.get("name", "")).startswith((f"custom_components.{SMARTPI}", f"custom_components.{VT}")):
            if STARTUP_NOTICE in message:
                r.note(f"known at startup: {message[:120]}")
            else:
                lines.append(f"{entry.get('level')} {entry.get('name')}: {message[:160]}")
    r.check(not lines, f"warnings and errors of SmartPI and VTherm: {len(lines)}")
    for line in lines[:10]:
        r.note(line)


@scenario("BP1", ("boiler-count",), 10, END_TO_END, minimum=True)
async def BP1(r: Any) -> None:
    """West calls alone below VTherm's threshold of 2 devices: it pauses its learning, VTherm's boiler stays off, East (not calling) and North (calling, not counted) do not pause, and West's command goes on."""
    await west_alone(r)
    await pause_holds(r)


@scenario("BP2", ("boiler-count",), 12, END_TO_END, minimum=True)
async def BP2(r: Any) -> None:
    """After West paused alone, East calls too: the threshold is reached, VTherm's boiler starts and West resumes its learning."""
    await west_alone(r)
    await east_calls(r)


@scenario("BP3", ("boiler-count",), 12, PAUSE_TESTS, minimum=True)
async def BP3(r: Any) -> None:
    """After West paused alone, it stops calling: it resumes its learning though the threshold is still not reached."""
    await west_alone(r)
    await west_stops(r)


@scenario("BP4", ("boiler-count",), 12, PAUSE_TESTS)
async def BP4(r: Any) -> None:
    """With VTherm's active-device and power sensors disabled, the threshold cannot be read: West calling alone does not pause; the sensors are enabled again and read."""
    api = Instance(r.ha)
    sensors = [await api.entity_id("sensor", VT, uid) for uid in ("nb_device_active_boiler", "total_power_active_boiler")]

    async def disable(by: str | None) -> None:
        for entity_id in sensors:
            await r.ha.ws_cmd({"type": "config/entity_registry/update", "entity_id": entity_id, "disabled_by": by})

    async def states() -> list[Any]:
        return [await r.ha.state(entity_id) for entity_id in sensors]

    try:
        await disable("user")
        # Home Assistant reloads VTherm's central entry about 30 s after the change.
        took, _ = await until(states, lambda found: all(s is None for s in found), 180.0)
        r.check(took is not None, f"VTherm's sensors gone after {took and round(took)} s: {sensors}")
        await targets(r, **{WEST: CALLING, EAST: STOPPED, NORTH: CALLING})
        took, data = await until(lambda: live(r, WEST), lambda d: paused(d) is not False, WITHIN_S)
        r.check(took is None, f"West alone, threshold unreadable: boiler_paused {paused(data)} for {round(WITHIN_S)} s")
    finally:
        await disable(None)
        took, found = await until(
            states, lambda found: all(s is not None and s["state"] not in ("unknown", "unavailable") for s in found), 180.0
        )
        r.check(took is not None, f"VTherm's sensors back: {[s and s['state'] for s in found]}")


@scenario("BP5", ("boiler-count",), 15, PAUSE_TESTS)
async def BP5(r: Any) -> None:
    """With the user's learning paused (set_smartpi_learning off), West's boiler pause starts and ends while learning.enabled stays off; the user's setting is put back."""
    api = Instance(r.ha)

    async def learning(enabled: bool) -> None:
        await api.call(SMARTPI, "set_smartpi_learning", {"entity_id": WEST, "learning_enabled": enabled})
        took, _ = await until(lambda: live(r, WEST), lambda d: _get(d, "learning.enabled") is enabled, MARGIN_S)
        r.check(took is not None, f"the user's learning setting is {'on' if enabled else 'off'}")

    try:
        await learning(False)
        data = await west_alone(r)
        r.check(_get(data, "learning.enabled") is False, f"paused: learning.enabled {_get(data, 'learning.enabled')}")
        await east_calls(r)
        data = await live(r, WEST)
        r.check(_get(data, "learning.enabled") is False, f"resumed: learning.enabled {_get(data, 'learning.enabled')}")
    finally:
        await learning(True)


@scenario("BP6", ("boiler-power",), 35, PAUSE_TESTS)
async def BP6(r: Any) -> None:
    """With VTherm's power threshold (2500 W, devices of 2000 W): West calling alone pauses while the boiler stays off, East calling too resumes it, and West alone again resumes when it stops calling."""
    await west_alone(r)
    await pause_holds(r)
    await east_calls(r)
    await west_alone(r)
    await west_stops(r)


@scenario("BP7", ("boiler-count",), 75, PAUSE_TESTS)
async def BP7(r: Any) -> None:
    """With West's learned state loaded, a forced calibration's heating step below the threshold pauses learning: the dead-time detection stays idle and the learned heating dead time unchanged; once East calls and the boiler starts, learning resumes and the next heating starts a new dead-time window."""
    api = Instance(r.ha)
    await api.call(HELPER_DOMAIN, "load_learned_state", {"entity_id": WEST, "state": "west"})
    took, data = await until(lambda: live(r, WEST), lambda d: _get(d, "control.phase") == "Stable", MARGIN_S)
    r.check(took is not None, f"West learned: phase {_get(data, 'control.phase')}, a {_get(data, 'model.a')}")
    learned = _get(data, "model.deadtime_heat_s")
    try:
        await targets(r, **{EAST: STOPPED})
        await api.call(SMARTPI, "force_smartpi_calibration", {"entity_id": WEST})
        took, data = await until(lambda: live(r, WEST), lambda d: _get(d, "calibration.state") == "HeatUp", 3600.0, 30.0)
        r.check(took is not None, f"the calibration's heating step after {took and round(took)} s")
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is True, WITHIN_S)
        r.check(took is not None, f"heating alone below the threshold: paused after {took and round(took)} s")
        for _ in range(5):
            data = await live(r, WEST)
            r.check(
                _get(data, "analysis.deadtime.state") == "OFF" and _get(data, "model.deadtime_heat_s") == learned,
                f"paused: dead-time detection {_get(data, 'analysis.deadtime.state')}, "
                f"heating dead time {_get(data, 'model.deadtime_heat_s')} s (learned {learned} s)",
            )
            await asyncio.sleep(2 * RECALC_S)
        await targets(r, **{EAST: CALLING})
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is False, WITHIN_S)
        r.check(took is not None, f"East calls, the boiler starts: West resumed after {took and round(took)} s")
        took, _ = await until(lambda: live(r, WEST), lambda d: _get(d, "control.in_deadtime_window") is True, CYCLE_S + MARGIN_S)
        r.check(took is not None, f"the next heating starts a dead-time window after {took and round(took)} s")
    finally:
        # Off and on again cancels the calibration; the runner then puts the setpoints back.
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "off"})
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "heat"})


@scenario("BP8", ("boiler-count", "boiler-power"), 12, END_TO_END, minimum=True)
async def BP8(r: Any) -> None:
    """West and East start calling together and VTherm's boiler starts: neither pauses its learning, though VTherm counts their devices a moment after SmartPI first reads the threshold."""
    await targets(r, **{WEST: STOPPED, EAST: STOPPED})

    async def idle() -> list[Any]:
        return [command(await live(r, WEST)), command(await live(r, EAST)), await boiler(r)]

    took, found = await until(idle, lambda v: v[0] == 0 and v[1] == 0 and v[2] == "off", WITHIN_S)
    r.check(took is not None, f"West and East not calling, boiler {found and found[2]}")
    await targets(r, **{WEST: CALLING, EAST: CALLING})
    seen: list[str] = []
    start = time.time()
    while time.time() - start < 5 * RECALC_S:
        for climate in (WEST, EAST):
            if paused(await live(r, climate)) is True:
                seen.append(f"{climate} at {round(time.time() - start)} s")
        await asyncio.sleep(5.0)
    r.check(not seen, f"no learning pause while the boiler starts: {seen or 'none seen'}")
    r.check(await boiler(r) == "on", f"VTherm's central boiler: {await boiler(r)}")
