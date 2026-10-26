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

BR1 is the reference run: the same case on this version and on the author's release 0.5.2
(c75d803), which has no learning pause. On 0.5.2 the runner runs with ``--author-release``: only
BP0 and BR1, and BP0 then does not look for ``learning.boiler_paused``.
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
from pack import BOILER_SWITCH, HELPER_DOMAIN, ROLES, SMARTPI, VT, room

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
REFERENCE_HOURS = 24
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


@scenario("BP1", ("boiler-count",), total(PAUSE, HOLD), END_TO_END, minimum=True)
async def BP1(r: Any) -> None:
    """West calls alone below VTherm's threshold of 2 devices: it pauses its learning, VTherm's boiler stays off, East (not calling) and North (calling, not counted) do not pause, and West's command goes on."""
    await west_alone(r)
    await pause_holds(r)


@scenario("BP2", ("boiler-count",), total(PAUSE, RESUME), END_TO_END, minimum=True)
async def BP2(r: Any) -> None:
    """After West paused alone, East calls too: the threshold is reached, VTherm's boiler starts and West resumes its learning."""
    await west_alone(r)
    await east_calls(r)


@scenario("BP3", ("boiler-count",), total(PAUSE, RESUME), PAUSE_TESTS, minimum=True)
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


@scenario("BP5", ("boiler-count",), total(PUBLISHED, PAUSE, RESUME, PUBLISHED), PAUSE_TESTS)
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


@scenario("BP6", ("boiler-power",), total(PAUSE, HOLD, RESUME, PAUSE, RESUME), PAUSE_TESTS)
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


# --- the reference run ------------------------------------------------------------------------

REFERENCE_PHASE_S = 40 * 60.0
REFERENCE_EVERY_S = 10 * 60.0
# Cooling first, then West calling alone: its power step comes while the boiler is off, so a
# SmartPI without the pause can take the wait for the boiler as the room's dead time; the boiler
# starts only when East calls too.
REFERENCE_PHASES = (
    ("both cooling", {WEST: 16.0, EAST: 16.0}),
    ("West alone, boiler off", {WEST: 22.0, EAST: STOPPED}),
    ("both heating, boiler on", {WEST: 22.0, EAST: 22.0}),
)


def reference_truth() -> dict[str, float]:
    """West's true values (pack.py): a, b, and its dead time between the radiator's delay and its
    delay plus lag (the learned state's value)."""
    west = room("West")
    return {
        "a": west.gain,
        "b": west.loss,
        "deadtime_delay_s": west.delay_min * 60.0,
        "deadtime_delay_lag_s": (west.delay_min + west.lag_min) * 60.0,
    }


async def _reference_sample(r: Any, phase: str, started: float) -> dict[str, Any]:
    data = await live(r, WEST)
    return {
        "at": datetime.now(UTC).isoformat(),
        "minutes": round((time.time() - started) / 60.0, 1),
        "phase": phase,
        "control_phase": _get(data, "control.phase"),
        "a": _get(data, "model.a"),
        "b": _get(data, "model.b"),
        "deadtime_heat_s": _get(data, "model.deadtime_heat_s"),
        "deadtime_cool_s": _get(data, "model.deadtime_cool_s"),
        "accepted_updates_a": _get(data, "learning.accepted_updates_a"),
        "accepted_updates_b": _get(data, "learning.accepted_updates_b"),
        "boiler_paused": paused(data),
        "boiler": await boiler(r),
        "indoor": _get(data, "temperature.indoor"),
        "command": command(data),
    }


def _reference_file(r: Any) -> pathlib.Path:
    from ha_client import N  # noqa: PLC0415 — the bench's client
    from harness import results_file  # noqa: PLC0415 — the bench's harness

    version = "author-0.5.2" if CONTEXT.author_release else "qas"
    return results_file(r.pack).parent / f"BR1-{version}-instance{N}.json"


@scenario("BR1", ("boiler-count",), total(PUBLISHED, REFERENCE), PAUSE_TESTS, author_release=True)
async def BR1(r: Any) -> None:
    """Reference run, the same on this version and on the author's 0.5.2: West starts from its room's true model, then for 24 hours repeats 40 minutes cooling, 40 minutes calling alone with the boiler off, and 40 minutes heating with East and the boiler on; every 10 minutes its a, b and dead times are kept (BR1-<version>-instance<N>.json in the results), to compare both versions with the room's true values (run.py --compare)."""
    api = Instance(r.ha)
    await api.call(HELPER_DOMAIN, "load_learned_state", {"entity_id": WEST, "state": "west"})
    took, data = await until(lambda: live(r, WEST), lambda d: _get(d, "control.phase") == "Stable", MARGIN_S)
    r.check(took is not None, f"West starts from its true model: phase {_get(data, 'control.phase')}, a {_get(data, 'model.a')}")
    started = time.time()
    samples: list[dict[str, Any]] = []
    out = _reference_file(r)
    alone_off: list[bool] = []
    alone_paused: list[Any] = []
    for _ in range(round(REFERENCE.typical_s / (len(REFERENCE_PHASES) * REFERENCE_PHASE_S))):
        for phase, values in REFERENCE_PHASES:
            await targets(r, **values)
            phase_start = time.time()
            while time.time() - phase_start < REFERENCE_PHASE_S - 1.0:
                await asyncio.sleep(REFERENCE_EVERY_S)
                sample = await _reference_sample(r, phase, started)
                samples.append(sample)
                if phase == REFERENCE_PHASES[1][0]:
                    alone_off.append(sample["boiler"] == "off")
                    alone_paused.append(sample["boiler_paused"])
            out.write_text(
                json.dumps(
                    {"version": "author 0.5.2" if CONTEXT.author_release else "qas", "truth": reference_truth(), "samples": samples},
                    indent=1,
                ),
                encoding="utf-8",
            )
    r.note(f"kept: {out}")
    r.check(alone_off and all(alone_off), f"West calling alone: VTherm's boiler off in {sum(alone_off)} of {len(alone_off)} samples")
    if CONTEXT.author_release:
        r.note(f"the author's 0.5.2 publishes no learning.boiler_paused ({set(map(str, alone_paused))})")
    else:
        r.check(all(p is True for p in alone_paused), f"West calling alone: learning paused in {alone_paused.count(True)} of {len(alone_paused)} samples")
    last, truth = samples[-1], reference_truth()
    r.note(
        f"at the end: a {last['a']} (true {truth['a']}), b {last['b']} (true {truth['b']}), heating dead time "
        f"{last['deadtime_heat_s']} s, cooling {last['deadtime_cool_s']} s (true between {truth['deadtime_delay_s']:.0f} "
        f"and {truth['deadtime_delay_lag_s']:.0f} s)"
    )
