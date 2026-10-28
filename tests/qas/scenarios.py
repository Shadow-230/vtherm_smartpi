"""The QAS scenarios: SmartPI's learning pause below Versatile Thermostat's central boiler
threshold (documentation/en/technical_doc.md, 3.7), on a prepared instance.

West and East heat with water radiators the central boiler counts; North with an electric heater
it does not count. A scenario makes West call alone: one device, below both roles' thresholds (2
devices; 2500 W with devices of 2000 W), so VTherm's boiler stays off.

Each scenario is registered with its roles, its time, whether it is in the minimum set and its
counterpart without the bench; the catalogue in README.md lists the same (checked by
tests/test_qas_files.py). A scenario's time is the sum of its waits (``Wait``): "typical" is what
SmartPI's timing gives (two calculations for a pause, one for a resume, the steps' own holds), and
"maximum" the sum of the waits' limits. Every wait ends as soon as its condition holds. The timing
is read from the plugin on the stage: SmartPI calculates every ``RECALC_S``; VTherm applies a new
command within a cycle (5 minutes, its default).

BR1 and BR2 are the reference runs: the same case on this version and on the author's release
0.5.2 (c75d803), which has no learning pause. On 0.5.2 the runner runs with
``--author-release``: only BP0, BR1 and BR2, and BP0 then does not look for
``learning.boiler_paused``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from instance import Instance
from pack import BOILER_SWITCH, HELPER_DOMAIN, ROLES, SMARTPI, VT, WEST_WINDOW, room

_CONST = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / SMARTPI / "smartpi" / "const.py"
_spec = importlib.util.spec_from_file_location("qas_smartpi_const", _CONST)
_const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_const)

RECALC_S = float(_const.SMARTPI_RECALC_INTERVAL_SEC)
CYCLE_S = 5 * 60.0  # VTherm's default cycle
MARGIN_S = 2 * RECALC_S + 15.0
WITHIN_S = 4 * RECALC_S + CYCLE_S / 2 + MARGIN_S
DETACHED_AFTER_S = 2 * 3600.0  # the bench stops background commands after two hours
POLL_S = 10.0

WEST, EAST, NORTH = (room(name).climate for name in ("West", "East", "North"))
BASE_SETPOINT = 20.0
CALLING = 23.0  # far above the rooms: SmartPI calls at full power
STOPPED = 7.0  # far below the rooms: SmartPI does not call
STARTUP_NOTICE = "External proportional algorithm 'smartpi' not yet registered"
PAUSE_TESTS = "test_boiler_call.py"
END_TO_END = "test_boiler_call.py, test_qas_instance.py"


# --- the registry -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Wait:
    """A scenario's wait: what SmartPI's timing gives, and the wait's limit, in seconds."""

    typical_s: float
    maximum_s: float


def total(*waits: Wait) -> Wait:
    return Wait(sum(w.typical_s for w in waits), sum(w.maximum_s for w in waits))


PAUSE = Wait(2 * RECALC_S, WITHIN_S)  # two calculations below the threshold
RESUME = Wait(RECALC_S, WITHIN_S)  # the next calculation
# A change of the setpoint (or of the HVAC mode) makes a calculation at once: the pause comes at
# the next one, and a change that stops the call resumes at once.
PAUSE_ON_SETPOINT = Wait(RECALC_S, WITHIN_S)
RESUME_ON_SETPOINT = Wait(0.0, WITHIN_S)
RESTART = Wait(90.0, 600.0)  # Home Assistant restarts
AFTER_RESTART = Wait(30.0, 300.0)  # SmartPI's diagnostics back after a restart
WINDOW_DELAY_S = 30.0  # VTherm's default delay before it acts on an opened or closed window
WINDOW_OPENS = Wait(WINDOW_DELAY_S, WINDOW_DELAY_S + MARGIN_S)  # VTherm turns West off: resume at once
WINDOW_CLOSES = Wait(WINDOW_DELAY_S + RECALC_S, WINDOW_DELAY_S + WITHIN_S)  # West heats again: pause one calculation later
BOILER_DELAY_S = 120.0  # pack.ROLES["boiler-delay"]
BOILER_STARTS = Wait(BOILER_DELAY_S, BOILER_DELAY_S + MARGIN_S)
OBSERVE_AFTER_RESUME = Wait(900.0, 900.0)  # BP12: longer than the learned dead time (780 s)
HOLD = Wait(3 * RECALC_S, 3 * RECALC_S)  # pause_holds: three readings, a calculation apart
PUBLISHED = Wait(0.0, MARGIN_S)  # a service's change, published at once
DIAGNOSTICS = Wait(0.0, 3 * 300.0)  # BP0: each thermostat's diagnostics, there after the restart
SENSORS = Wait(40.0, 180.0)  # Home Assistant reloads VTherm's entry about 30 s after a registry change
NO_PAUSE = Wait(WITHIN_S, WITHIN_S)  # BP4 watches the whole wait for a pause
IDLE = Wait(RECALC_S, WITHIN_S)  # BP8: West and East stop calling, the boiler stops
START = Wait(5 * RECALC_S, 5 * RECALC_S)  # BP8 watches the boiler's start
HEAT_UP = Wait(0.0, 3600.0)  # BP7: the calibration's heating step (see its note)
PAUSED_READINGS = Wait(10 * RECALC_S, 10 * RECALC_S)  # BP7: five readings, two calculations apart
NEW_EPISODE = Wait(CYCLE_S / 2, CYCLE_S + MARGIN_S)  # BP7: the next cycle's start
REFERENCE_HOURS = 96
REFERENCE = Wait(REFERENCE_HOURS * 3600.0, REFERENCE_HOURS * 3600.0)


@dataclass(frozen=True)
class Scenario:
    id: str
    run: Callable[[Any], Awaitable[None]]
    roles: tuple[str, ...]
    time: Wait
    counterpart: str
    minimum: bool
    depends: str  # what its time depends on, beyond SmartPI's timing
    author_release: bool  # runs on the author's 0.5.2 too

    @property
    def detached(self) -> bool:
        return self.time.maximum_s > DETACHED_AFTER_S

    @property
    def checks(self) -> str:
        return (self.run.__doc__ or "").strip()


SCENARIOS: dict[str, Scenario] = {}


def scenario(
    id: str,  # noqa: A002
    roles: tuple[str, ...],
    time: Wait,  # noqa: A002 — the catalogue's columns
    counterpart: str,
    minimum: bool = False,
    depends: str = "",
    author_release: bool = False,
):
    def register(run: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"{id} registered twice")
        SCENARIOS[id] = Scenario(id, run, roles, time, counterpart, minimum, depends, author_release)
        return run

    return register


class Context:
    """Set by the runner: whether the instance runs the author's release 0.5.2."""

    author_release: bool = False


CONTEXT = Context()


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


@scenario(
    "BP0",
    tuple(ROLES),
    total(DIAGNOSTICS),
    "test_qas_instance.py, test_published_diagnostics_schema.py",
    minimum=True,
    author_release=True,
)
async def BP0(r: Any) -> None:
    """After the preparation's restart every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published (not on the author's 0.5.2, which has no such field)."""
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
        if CONTEXT.author_release:
            r.note(f"{climate}: the author's 0.5.2 publishes no learning.boiler_paused ({paused(data)})")
        else:
            r.check(isinstance(paused(data), bool), f"{climate}: learning.boiler_paused {paused(data)}")
    switch = (await r.ha.state(BOILER_SWITCH) or {}).get("state")
    r.check(await boiler(r) == switch, f"VTherm's central boiler {await boiler(r)}, the test rooms' boiler switch {switch}")
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


@scenario("BP1", ("boiler-count",), total(PAUSE_ON_SETPOINT, HOLD), END_TO_END, minimum=True)
async def BP1(r: Any) -> None:
    """West calls alone below VTherm's threshold of 2 devices: it pauses its learning, VTherm's boiler stays off, East (not calling) and North (calling, not counted) do not pause, and West's command goes on."""
    await west_alone(r)
    await pause_holds(r)


@scenario("BP2", ("boiler-count",), total(PAUSE_ON_SETPOINT, RESUME), END_TO_END, minimum=True)
async def BP2(r: Any) -> None:
    """After West paused alone, East calls too: the threshold is reached, VTherm's boiler starts and West resumes its learning."""
    await west_alone(r)
    await east_calls(r)


@scenario("BP3", ("boiler-count",), total(PAUSE_ON_SETPOINT, RESUME_ON_SETPOINT), PAUSE_TESTS, minimum=True)
async def BP3(r: Any) -> None:
    """After West paused alone, it stops calling: it resumes its learning though the threshold is still not reached."""
    await west_alone(r)
    await west_stops(r)


@scenario("BP4", ("boiler-count",), total(SENSORS, NO_PAUSE, SENSORS), PAUSE_TESTS)
async def BP4(r: Any) -> None:
    """With VTherm's active-device and power sensors disabled, the threshold cannot be read: West calling alone (command above 0) does not pause; the sensors are enabled again and read."""
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
        # The pause starts only while SmartPI calls: a West not calling would pass too.
        r.check((command(data) or 0) > 0, f"West calls meanwhile: command {command(data)} %")
    finally:
        await disable(None)
        took, found = await until(
            states, lambda found: all(s is not None and s["state"] not in ("unknown", "unavailable") for s in found), 180.0
        )
        r.check(took is not None, f"VTherm's sensors back: {[s and s['state'] for s in found]}")


@scenario("BP5", ("boiler-count",), total(PUBLISHED, PAUSE_ON_SETPOINT, RESUME, PUBLISHED), PAUSE_TESTS)
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


@scenario(
    "BP6", ("boiler-power",), total(PAUSE_ON_SETPOINT, HOLD, RESUME, PAUSE_ON_SETPOINT, RESUME_ON_SETPOINT), PAUSE_TESTS
)
async def BP6(r: Any) -> None:
    """With VTherm's power threshold (2500 W, devices of 2000 W): West calling alone pauses while the boiler stays off, East calling too resumes it, and West alone again resumes when it stops calling."""
    await west_alone(r)
    await pause_holds(r)
    await east_calls(r)
    await west_alone(r)
    await west_stops(r)


@scenario(
    "BP7",
    ("boiler-count",),
    total(PUBLISHED, HEAT_UP, PAUSE, PAUSED_READINGS, RESUME, NEW_EPISODE),
    PAUSE_TESTS,
    depends="typical when West starts below the calibration's low threshold (setpoint - 0.3 degC); above it, the calibration first cools the room, up to 60 min more",
)
async def BP7(r: Any) -> None:
    """With West's learned state loaded, a forced calibration's heating step below the threshold pauses learning: the dead-time detection stays idle and the learned heating dead time unchanged; once East calls and the boiler starts, learning resumes and the next heating starts a new dead-time window, its episode starting after the resume."""
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
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is False, WITHIN_S, POLL_S)
        r.check(took is not None, f"East calls, the boiler starts: West resumed after {took and round(took)} s")
        # The resume happened within the last poll: an episode that started earlier, during the
        # pause, is not the new one.
        resumed = datetime.now(UTC) - timedelta(seconds=POLL_S + 5.0)

        def new_window(d: dict[str, Any]) -> bool:
            start = _get(d, "analysis.deadtime.heat_episode_start")
            return start is not None and datetime.fromisoformat(start) >= resumed and _get(d, "control.in_deadtime_window") is True

        took, data = await until(lambda: live(r, WEST), new_window, NEW_EPISODE.maximum_s)
        r.check(
            took is not None,
            f"the next heating starts a new dead-time window: episode start {_get(data, 'analysis.deadtime.heat_episode_start')}, "
            f"resume seen at {resumed.isoformat()} (+{round(POLL_S + 5.0)} s), window open {_get(data, 'control.in_deadtime_window')}",
        )
    finally:
        # Off and on again cancels the calibration; the runner then puts the setpoints back.
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "off"})
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "heat"})


@scenario("BP8", ("boiler-count", "boiler-power"), total(IDLE, START), END_TO_END, minimum=True)
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


LEARNED = ("model.a", "model.b", "model.deadtime_heat_s", "model.deadtime_cool_s", "learning.accepted_updates_a", "learning.accepted_updates_b")


def learned(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _get(data, key) for key in LEARNED}


async def number(r: Any, unique_id: str) -> tuple[str, float]:
    entity_id = await Instance(r.ha).entity_id("number", VT, unique_id)
    return entity_id, float((await r.ha.state(entity_id))["state"])


@scenario(
    "BP9",
    ("boiler-count", "boiler-power"),
    total(PAUSE_ON_SETPOINT, RESTART, AFTER_RESTART, PAUSE),
    PAUSE_TESTS,
)
async def BP9(r: Any) -> None:
    """Home Assistant restarts while West is paused: once SmartPI runs again, the pause returns within two calculations; nothing is learned in between (accepted updates unchanged, dead-time detection idle, no heating episode from before the restart); a, b and the dead times are kept."""
    before = learned(await west_alone(r))
    restarted_at = datetime.now(UTC)
    took = await Instance(r.ha).restart()
    r.note(f"Home Assistant restarted in {took:.0f} s")
    took, data = await until(lambda: live(r, WEST), lambda d: bool(_get(d, "control.phase")), AFTER_RESTART.maximum_s)
    r.check(took is not None, f"SmartPI's diagnostics back after {took and round(took)} s")
    seen: list[dict[str, Any]] = []

    async def watch() -> dict[str, Any]:
        data = await live(r, WEST)
        seen.append(learned(data))
        return data

    took, data = await until(watch, lambda d: paused(d) is True, PAUSE.maximum_s)
    r.check(took is not None, f"the pause returns after {took and round(took)} s")
    for key in ("learning.accepted_updates_a", "learning.accepted_updates_b"):
        values = {s[key] for s in seen}
        r.check(values == {before[key]}, f"{key}: {before[key]} before, {sorted(values, key=str)} since")
    r.check(learned(data) == before, f"a, b and the dead times kept: {learned(data)} (before {before})")
    r.check(_get(data, "analysis.deadtime.state") == "OFF", f"dead-time detection: {_get(data, 'analysis.deadtime.state')}")
    start = _get(data, "analysis.deadtime.heat_episode_start")
    r.check(
        start is None or datetime.fromisoformat(start) >= restarted_at,
        f"heating episode: {start} (restart at {restarted_at.isoformat()})",
    )


@scenario("BP10", ("boiler-count", "boiler-power"), total(PAUSE_ON_SETPOINT, RESUME, PAUSE), PAUSE_TESTS)
async def BP10(r: Any) -> None:
    """VTherm's threshold changed while West is paused: lowered to 1 device (or 500 W), West resumes at its next calculation and the boiler starts, though only West calls; back to the role's value, West pauses again after two calculations and the boiler stops."""
    await west_alone(r)
    count_entity, count = await number(r, "boiler_activation_threshold")
    power_entity, power = await number(r, "boiler_power_activation_threshold")
    entity, original, lowered = (count_entity, count, 1.0) if count > 0 else (power_entity, power, 500.0)
    api = Instance(r.ha)
    try:
        await api.call("number", "set_value", {"entity_id": entity, "value": lowered})
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is False, RESUME.maximum_s)
        r.check(took is not None, f"{entity} = {lowered}: West resumed after {took and round(took)} s")
        r.check(await boiler(r) == "on", f"VTherm's central boiler: {await boiler(r)}")
        await api.call("number", "set_value", {"entity_id": entity, "value": original})
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is True, PAUSE.maximum_s)
        r.check(took is not None, f"{entity} = {original} again: West paused after {took and round(took)} s")
        r.check(await boiler(r) == "off", f"VTherm's central boiler: {await boiler(r)}")
    finally:
        await api.call("number", "set_value", {"entity_id": entity, "value": original})


@scenario(
    "BP11",
    ("boiler-count",),
    total(PAUSE_ON_SETPOINT, RESUME_ON_SETPOINT, PAUSE_ON_SETPOINT, WINDOW_OPENS, WINDOW_CLOSES),
    PAUSE_TESTS,
)
async def BP11(r: Any) -> None:
    """While West is paused: turned off, its pause ends within a calculation (SmartPI does not call); heating again, the pause returns a calculation later. The same with its window: opened, VTherm turns West off and the pause ends; closed, West heats and pauses again."""
    api = Instance(r.ha)
    await west_alone(r)
    try:
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "off"})
        took, data = await until(lambda: live(r, WEST), lambda d: paused(d) is False, RESUME_ON_SETPOINT.maximum_s, 5.0)
        r.check(took is not None and took <= RECALC_S, f"West off: the pause ends after {took and round(took)} s, command {command(data)} %")
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "heat"})
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is True, PAUSE_ON_SETPOINT.maximum_s)
        r.check(took is not None, f"West heating again: paused after {took and round(took)} s")
        await api.call("input_boolean", "turn_on", {"entity_id": WEST_WINDOW})
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is False, WINDOW_OPENS.maximum_s, 5.0)
        state = (await r.ha.state(WEST) or {}).get("state")
        r.check(took is not None, f"window open: the pause ends after {took and round(took)} s (West {state})")
        await api.call("input_boolean", "turn_off", {"entity_id": WEST_WINDOW})
        took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is True, WINDOW_CLOSES.maximum_s)
        r.check(took is not None, f"window closed: paused again after {took and round(took)} s")
    finally:
        await api.call("input_boolean", "turn_off", {"entity_id": WEST_WINDOW})
        await api.call("climate", "set_hvac_mode", {"entity_id": WEST, "hvac_mode": "heat"})


@scenario(
    "BP12",
    ("boiler-delay",),
    total(PUBLISHED, PAUSE_ON_SETPOINT, RESUME, BOILER_STARTS, NEW_EPISODE, OBSERVE_AFTER_RESUME),
    PAUSE_TESTS,
)
async def BP12(r: Any) -> None:
    """Measures the limit of a central boiler with an activation delay (120 s): with West's learned state, after a pause, East calls and West resumes at once while the boiler starts 120 s later; it records that delay, when the next heating episode (the dead-time window) starts against the boiler's start, and the heating dead time for 15 minutes after. It passes on the measurement."""
    api = Instance(r.ha)
    await api.call(HELPER_DOMAIN, "load_learned_state", {"entity_id": WEST, "state": "west"})
    took, data = await until(lambda: live(r, WEST), lambda d: _get(d, "control.phase") == "Stable", MARGIN_S)
    r.check(took is not None, f"West learned: phase {_get(data, 'control.phase')}, heating dead time {_get(data, 'model.deadtime_heat_s')} s")
    before = _get(data, "model.deadtime_heat_s")
    await west_alone(r)
    await targets(r, **{EAST: CALLING})
    took, _ = await until(lambda: live(r, WEST), lambda d: paused(d) is False, RESUME.maximum_s, 5.0)
    resumed = datetime.now(UTC)
    r.check(took is not None, f"East calls: West resumed at {resumed.isoformat()}")

    async def switch() -> Any:
        return (await r.ha.state(BOILER_SWITCH) or {}).get("state")

    took, _ = await until(switch, lambda state: state == "on", BOILER_STARTS.maximum_s, 5.0)
    boiler_on = datetime.now(UTC)
    r.check(took is not None, f"the boiler's switch on {took and round(took)} s after the resume was seen")
    window_from = resumed - timedelta(seconds=10.0)

    def episode_after_resume(d: dict[str, Any]) -> bool:
        start = _get(d, "analysis.deadtime.heat_episode_start")
        return start is not None and datetime.fromisoformat(start) >= window_from

    took, data = await until(lambda: live(r, WEST), episode_after_resume, NEW_EPISODE.maximum_s, 5.0)
    episode = _get(data, "analysis.deadtime.heat_episode_start")
    r.check(took is not None, f"the next heating episode starts at {episode}")
    if episode is not None:
        lead = (boiler_on - datetime.fromisoformat(episode)).total_seconds()
        r.note(f"the dead-time window starts {lead:.0f} s before the boiler's switch is seen on")
    readings = []
    for _ in range(round(OBSERVE_AFTER_RESUME.typical_s / RECALC_S)):
        await asyncio.sleep(RECALC_S)
        data = await live(r, WEST)
        readings.append((_get(data, "model.deadtime_heat_s"), _get(data, "analysis.deadtime.state"), _get(data, "control.in_deadtime_window")))
    after = readings[-1][0] if readings else None
    r.note(f"heating dead time: {before} s before, {after} s after; detection states {sorted({str(x[1]) for x in readings})}")
    r.note(f"window open in {sum(1 for x in readings if x[2])} of {len(readings)} readings, a calculation apart")
    r.check(len(readings) == round(OBSERVE_AFTER_RESUME.typical_s / RECALC_S), f"measured for {len(readings)} calculations")


# --- the reference runs -----------------------------------------------------------------------
#
# The same case on this version and on the author's 0.5.2, from a fresh start (README.md, section
# 5). Two inputs, each switched by its own pseudo-random binary sequence (IEA EBC Annex 58,
# appendix E, p. 54-55): West's setpoint, and East's call, which starts and stops the boiler. A
# maximum-length sequence of order n repeats after 2**n - 1 steps and holds a level for at most n
# steps. The levels, the steps and the length were chosen in the fork's simulator.

REFERENCE_EVERY_S = 10 * 60.0  # a sample kept in the run's file
SEQUENCE_TAPS = {5: (5, 3), 6: (6, 5)}  # feedback taps of maximum-length sequences
STARTS_FOR_VALIDITY = 6  # SmartPI's dead time is the median of its last 6 detections (technical_doc.md, 3.6)
CHECKPOINT_HOURS = (48, REFERENCE_HOURS)  # run.py --compare


@dataclass(frozen=True)
class Sequence:
    """A maximum-length pseudo-random binary sequence of ``order`` bits, each held ``step_s``."""

    order: int
    step_s: float
    seed: int

    def bits(self) -> tuple[int, ...]:
        state, out = self.seed, []
        for _ in range(2**self.order - 1):
            out.append(state & 1)
            feedback = 0
            for tap in SEQUENCE_TAPS[self.order]:
                feedback ^= (state >> (self.order - tap)) & 1
            state = (state >> 1) | (feedback << (self.order - 1))
        return tuple(out)

    def high(self, elapsed_s: float) -> bool:
        bits = self.bits()
        return bits[int(elapsed_s // self.step_s) % len(bits)] == 1


WEST_SEQUENCE = Sequence(order=6, step_s=30 * 60.0, seed=0b000001)  # repeats after 31.5 h
EAST_SEQUENCE = Sequence(order=5, step_s=40 * 60.0, seed=0b10110)  # repeats after 20.7 h
WEST_LOW, WEST_HIGH = 19.0, 21.0
BOILER_PHASE_S = 3 * 3600.0  # BR2: the boiler off and on in fixed phases


def west_setpoint(elapsed_s: float) -> float:
    return WEST_HIGH if WEST_SEQUENCE.high(elapsed_s) else WEST_LOW


def east_by_sequence(elapsed_s: float) -> float:
    return CALLING if EAST_SEQUENCE.high(elapsed_s) else STOPPED


def east_by_phases(elapsed_s: float) -> float:
    """Off first, then on, each for ``BOILER_PHASE_S``."""
    return CALLING if int(elapsed_s // BOILER_PHASE_S) % 2 == 1 else STOPPED


def reference_truth() -> dict[str, float]:
    """West's true values (pack.py): a, b, and its dead time between the radiator's delay and its
    delay plus lag."""
    west = room("West")
    return {
        "a": west.gain,
        "b": west.loss,
        "deadtime_delay_s": west.delay_min * 60.0,
        "deadtime_delay_lag_s": (west.delay_min + west.lag_min) * 60.0,
    }


def _reference_sample(data: dict[str, Any], boiler_state: Any, elapsed_s: float, wanted: dict[str, float]) -> dict[str, Any]:
    return {
        "at": datetime.now(UTC).isoformat(),
        "hours": round(elapsed_s / 3600.0, 3),
        "west_setpoint": wanted[WEST],
        "east_setpoint": wanted[EAST],
        "control_phase": _get(data, "control.phase"),
        "deadtime_heat_s": _get(data, "model.deadtime_heat_s"),
        "deadtime_heat_reliable": _get(data, "model.deadtime_heat_reliable"),
        "a": _get(data, "model.a"),
        "b": _get(data, "model.b"),
        "accepted_updates_a": _get(data, "learning.accepted_updates_a"),
        "accepted_updates_b": _get(data, "learning.accepted_updates_b"),
        "boiler_paused": paused(data),
        "boiler": boiler_state,
        "indoor": _get(data, "temperature.indoor"),
        "command": command(data),
    }


def _reference_file(r: Any, sid: str) -> pathlib.Path:
    from ha_client import N  # noqa: PLC0415 — the bench's client
    from harness import results_file  # noqa: PLC0415 — the bench's harness

    version = "author-0.5.2" if CONTEXT.author_release else "qas"
    return results_file(r.pack).parent / f"{sid}-{version}-instance{N}.json"


async def _reference_run(r: Any, sid: str, east: Callable[[float], float]) -> None:
    """West from a fresh start for ``REFERENCE`` hours, its setpoint by ``WEST_SEQUENCE`` and East's
    by ``east``; West's model kept every ``REFERENCE_EVERY_S`` in the run's file."""
    api = Instance(r.ha)
    uid = next((e.get("unique_id") for e in await r.ha.ws_cmd({"type": "config/entity_registry/list"}) if e["entity_id"] == WEST), None)
    sensor = await api.entity_id("sensor", SMARTPI, f"smartpi_diag_{uid}") if uid else None
    boiler_entity = await api.entity_id("binary_sensor", VT, "central_boiler_state")
    r.check(sensor is not None and boiler_entity is not None, f"West's diagnostics {sensor}, VTherm's boiler {boiler_entity}")
    if sensor is None or boiler_entity is None:
        return

    async def west() -> dict[str, Any]:
        state = await r.ha.state(sensor)
        return ((state or {}).get("attributes") or {}).get("live") or {}

    await api.call(SMARTPI, "reset_smartpi_learning", {"entity_id": WEST})
    took, data = await until(
        west,
        lambda d: _get(d, "control.phase") == "Hysteresis" and _get(d, "model.deadtime_heat_reliable") is False and _get(d, "learning.accepted_updates_a") == 0,
        MARGIN_S,
    )
    r.check(
        took is not None,
        f"West starts fresh: phase {_get(data, 'control.phase')}, heating dead time reliable "
        f"{_get(data, 'model.deadtime_heat_reliable')}, accepted updates of a {_get(data, 'learning.accepted_updates_a')}",
    )
    out = _reference_file(r, sid)
    version = "author 0.5.2" if CONTEXT.author_release else "qas"
    samples: list[dict[str, Any]] = []
    starts = {"boiler_off": 0, "boiler_on": 0}
    alone = {"readings": 0, "boiler_off": 0, "paused": 0}
    set_to: dict[str, float] = {}
    east_stopped_at: float | None = None
    calling_readings = 0
    started = time.time()
    next_sample_s = REFERENCE_EVERY_S
    while (elapsed := time.time() - started) < REFERENCE.typical_s:
        wanted = {WEST: west_setpoint(elapsed), EAST: east(elapsed)}
        changed = {climate: value for climate, value in wanted.items() if set_to.get(climate) != value}
        if changed:
            await targets(r, **changed)
            set_to.update(changed)
            if EAST in changed:
                east_stopped_at = elapsed if changed[EAST] == STOPPED else None
        data = await west()
        state = await r.ha.state(boiler_entity)
        boiler_state = state["state"] if state else None
        if (command(data) or 0) > 0:
            if calling_readings == 0:
                starts["boiler_on" if boiler_state == "on" else "boiler_off"] += 1
            calling_readings += 1
        else:
            calling_readings = 0
        # West calling alone for three readings (its pause comes at the second calculation), East
        # stopped for longer than VTherm takes to stop the boiler.
        if calling_readings >= 3 and east_stopped_at is not None and elapsed - east_stopped_at > WITHIN_S:
            alone["readings"] += 1
            alone["boiler_off"] += boiler_state == "off"
            alone["paused"] += paused(data) is True
        if elapsed >= next_sample_s:
            samples.append(_reference_sample(data, boiler_state, elapsed, wanted))
            next_sample_s += REFERENCE_EVERY_S
            out.write_text(
                json.dumps({"scenario": sid, "version": version, "truth": reference_truth(), "starts": starts, "samples": samples}, indent=1),
                encoding="utf-8",
            )
        await asyncio.sleep(RECALC_S)
    r.note(f"kept: {out}")
    r.check(
        alone["readings"] > 0 and alone["boiler_off"] == alone["readings"],
        f"West calling alone: VTherm's boiler off in {alone['boiler_off']} of {alone['readings']} readings",
    )
    if CONTEXT.author_release:
        r.note("the author's 0.5.2 publishes no learning.boiler_paused")
    else:
        r.check(alone["paused"] == alone["readings"], f"West calling alone: learning paused in {alone['paused']} of {alone['readings']} readings")
    r.note(f"West's heating starts: {starts['boiler_off']} with the boiler off, {starts['boiler_on']} with it on")
    for hours in CHECKPOINT_HOURS:
        kept = [s for s in samples if s["hours"] <= hours + 0.01]
        if kept:
            s = kept[-1]
            r.note(
                f"at {s['hours']:g} h: heating dead time {s['deadtime_heat_s']} s (reliable {s['deadtime_heat_reliable']}), "
                f"phase {s['control_phase']}, a {s['a']}, b {s['b']}"
            )


@scenario("BR1", ("boiler-count", "boiler-power"), total(PUBLISHED, REFERENCE), PAUSE_TESTS, author_release=True)
async def BR1(r: Any) -> None:
    """Reference run, the same on this version and on the author's 0.5.2: from a fresh start (learning reset), for 96 hours West's setpoint switches between 19 and 21 degC and East's call (so the boiler) on and off, each by its own pseudo-random sequence (steps of 30 and 40 minutes); every 10 minutes West's dead time, a and b are kept (BR1-<version>-instance<N>.json in the results), and run.py --compare judges both versions' heating dead time at 48 and 96 hours against the room's true value."""
    await _reference_run(r, "BR1", east_by_sequence)


@scenario("BR2", ("boiler-count",), total(PUBLISHED, REFERENCE), PAUSE_TESTS, author_release=True)
async def BR2(r: Any) -> None:
    """Measures a limit of the pause, the same on this version and on the author's 0.5.2: as BR1, but East calls in fixed phases of 3 hours, off first, so West mostly starts heating while the boiler is off; kept as BR2-<version>-instance<N>.json and shown by run.py --compare, not judged. It passes on the measurement."""
    await _reference_run(r, "BR2", east_by_phases)
