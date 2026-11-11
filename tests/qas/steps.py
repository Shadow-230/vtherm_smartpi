"""What the QAS scenarios share: the timing, reading an instance, the start state, the steps with
their checks, and the trace of a run (README.md, section 4).

**The start state.** Every scenario starts, and leaves the instance, in the same state: every
thermostat heats (HVAC mode heat); West and East do not call (setpoint 7 degC, their valves closed,
0 %); North at the setpoint the scenario declares; VTherm's boiler off for longer than the
role's activation delay plus a margin; no thermostat paused. West's and East's learning starts as
the scenario declares: ``fresh`` (reset) or ``learned`` (the helper's learned state, loaded before
the start state is reached).

**The steps.** Each step changes one thing (a setpoint, a mode, the window, the threshold, a
restart), waits for what the role makes of it, and then checks the same things every time:
West's and East's pause, North never paused, VTherm's boiler and the boiler's switch, West's and
East's valve opening, and VTherm's active-device count and power against the valve openings.

**The valve opening.** For a valve, SmartPI's command is the valve's opening in %: 0 % closed, the
thermostat not calling; 100 % fully open. The pack calls it the valve opening (``opening``, the
cycle's, from which SmartPI pauses; ``next_opening``, the next cycle's, from which VTherm counts
the power) and reads the valve's own state too (``valve``).
What a step expects follows from the role (``Bench.reaches``): the thermostats calling at full
power, the devices they count, their powers, the threshold.

**The timing of a step** (``Wait``): SmartPI pauses once it has called with VTherm's boiler off
without a break for a calculation period (``PAUSE_CONFIRM_S``), at its first calculation after
it. Below the threshold the boiler is off; with an activation delay it stays off for that delay
after the threshold is reached, so a calling thermostat pauses meanwhile. A paused thermostat
resumes at the moment the boiler starts. A change of a thermostat's own setpoint or mode makes a calculation at once: its pause comes
a calculation period and a part of the next one later, its resume at once. A change from outside
(East, the threshold) reaches it at its next calculation: a pause two calculations later, a
resume one. A closed window: VTherm's delay of 30 s, then two calculations.

**The trace.** While a scenario runs, every ``TRACE_S`` the trace reads the instance once and
keeps what changed: VTherm's count, power and boiler, the boiler's switch, each thermostat's
pause, valve opening, learning counters and dead-time detection. It is written next to the
results, a short timeline goes into them, and every pause in it is checked: nothing learned
while paused.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from instance import Instance
from pack import BOILER_SWITCH, HELPER_DOMAIN, ROLES, ROOMS_DOMAIN, SMARTPI, VT, WEST_WINDOW, Role, room

_CONST = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / SMARTPI / "smartpi" / "const.py"
_spec = importlib.util.spec_from_file_location("qas_smartpi_const", _CONST)
_const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_const)

RECALC_S = float(_const.SMARTPI_RECALC_INTERVAL_SEC)
CYCLE_S = 5 * 60.0  # VTherm's default cycle
MARGIN_S = 2 * RECALC_S + 15.0
WITHIN_S = 4 * RECALC_S + CYCLE_S / 2 + MARGIN_S
DETACHED_AFTER_S = 2 * 3600.0  # the bench stops background commands after two hours
POLL_S = 5.0
TRACE_S = 5.0

WEST, EAST, NORTH = (room(name).climate for name in ("West", "East", "North"))
COUNTED = (WEST, EAST)  # VTherm's central boiler counts their devices
KEYS = {WEST: "west", EAST: "east", NORTH: "north"}
NAMES = {WEST: "West", EAST: "East", NORTH: "North"}
CALLING = 23.0  # far above the rooms: SmartPI opens the valve fully (100 %)
STOPPED = 7.0  # far below the rooms: SmartPI does not call
BASE_SETPOINT = 20.0
STARTS = ("fresh", "learned")
WAITING_HEAT = "WAITING_HEAT_RESPONSE"  # deadtime_estimator.py: a heating detection waits for the room
WAITING_COOL = "WAITING_COOL_RESPONSE"  # in heating, a detection of the room's passive response after a stop
LEARNED = ("accepted_a", "accepted_b", "learn_ok", "deadtime")  # what the learning changes


# --- the timing -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Wait:
    """A wait: what SmartPI's timing gives, and the wait's limit, in seconds."""

    typical_s: float
    maximum_s: float


def total(*waits: Wait) -> Wait:
    return Wait(sum(w.typical_s for w in waits), sum(w.maximum_s for w in waits))


def times(wait: Wait, n: int) -> Wait:
    return Wait(n * wait.typical_s, n * wait.maximum_s)


# SmartPI pauses once it has called with the boiler off without a break for a calculation period, less
# 1 s for the timer (boiler_call.BOILER_PAUSE_CONFIRM_S), at its first calculation after that.
PAUSE_CONFIRM_S = RECALC_S - 1.0
# The thermostat's own change: below from its setpoint's own calculation, paused at the first check a
# period later, 60 s typically on the bench; at most two periods, when a check falls just short of the
# period (East in BP6 on boiler-delay: 116 s), well within the limit.
PAUSE_ON_SETPOINT = Wait(RECALC_S, WITHIN_S)
RESUME_ON_SETPOINT = Wait(0.0, WITHIN_S)  # at once
# A learned thermostat (past SmartPI's first phase) that stops calling commits its 0 % only after the
# calculation of its setpoint: its resume comes at SmartPI's next calculation (48-56 s on the bench).
RESUME_ON_STOP_LEARNED = Wait(RECALC_S, WITHIN_S)
PAUSE_EXTERNAL = Wait(2 * RECALC_S, WITHIN_S)  # a change from outside: below from its next calculation, paused a period later
RESUME_EXTERNAL = Wait(RECALC_S, WITHIN_S)  # one calculation
WINDOW_DELAY_S = 30.0  # VTherm's default delay before it acts on an opened or closed window
WINDOW_OPENS = Wait(WINDOW_DELAY_S, WINDOW_DELAY_S + MARGIN_S)  # VTherm turns West off: resume at once
WINDOW_CLOSES = Wait(WINDOW_DELAY_S + 2 * RECALC_S, WINDOW_DELAY_S + WITHIN_S)  # 150 s on the bench, 11 of 11
BOILER_DELAY_S = 120.0  # pack.ROLES["boiler-delay"]
BOILER_STARTS = Wait(0.0, MARGIN_S)  # without a delay: at VTherm's next count
BOILER_STARTS_LATE = Wait(BOILER_DELAY_S, BOILER_DELAY_S + MARGIN_S)
BOILER_STOPS = Wait(0.0, MARGIN_S)  # at once, also with a delay
VALVE_CLOSES = Wait(0.0, MARGIN_S)  # East's valve, at East's own calculation
NO_PAUSE = Wait(2 * RECALC_S + 15.0, 2 * RECALC_S + 15.0)  # watched: no pause where none is due
HOLD = Wait(3 * RECALC_S, 3 * RECALC_S)  # three readings, a calculation apart
PUBLISHED = Wait(0.0, MARGIN_S)  # a service's change, published at once
SETTLED_S = 30.0  # the boiler off at least this long, beyond the role's delay, before a start
START_STATE = Wait(0.0, BOILER_DELAY_S + SETTLED_S + 2 * WITHIN_S)
RESTART = Wait(20.0, 600.0)  # Home Assistant restarts: about 20 s on the bench
AFTER_RESTART = Wait(30.0, 300.0)  # SmartPI's diagnostics back after a restart
# A thermostat calling with the boiler off pauses again a calculation period after SmartPI runs again;
# its diagnostics are back at once (0 s on the bench), so both are one wait.
PAUSE_AFTER_RESTART = Wait(RECALC_S, AFTER_RESTART.maximum_s + PAUSE_EXTERNAL.maximum_s)


def boiler_starts(role: Role) -> Wait:
    return BOILER_STARTS_LATE if role.boiler_delay_s else BOILER_STARTS


def resume_when_the_boiler_starts(role: Role) -> Wait:
    """A paused thermostat resumes at the moment VTherm's boiler starts (SmartPI follows the
    boiler's state): with an activation delay, that delay after the threshold is reached."""
    return Wait(role.boiler_delay_s, role.boiler_delay_s + MARGIN_S)


class Context:
    """Set by the runner: the instance's role, the scenario, and the valves' report delay of every
    scenario (``--valve-delay``)."""

    role: Role = ROLES["boiler-count"]
    scenario: str = ""
    valve_delay_s: float = 0.0


CONTEXT = Context()


# --- reading the instance ---------------------------------------------------------------------


def get(data: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


# What a snapshot keeps of each thermostat's SmartPI diagnostics (its sensor's ``live``).
LIVE = {
    "paused": "learning.boiler_paused",
    "enabled": "learning.enabled",
    # SmartPI's command, for a valve its opening in %: the cycle's, from which SmartPI pauses, and
    # the next cycle's, from which VTherm counts the power (for North: its heater's share of a cycle).
    "opening": "power.current_cycle_percent",
    "next_opening": "power.next_cycle_percent",
    "phase": "control.phase",
    "calibration": "calibration.state",
    "dt_state": "analysis.deadtime.state",
    "heat_outcome": "analysis.deadtime.heat_last_outcome",
    "cool_outcome": "analysis.deadtime.cool_last_outcome",
    "dt_wait": "analysis.deadtime.waiting_elapsed_s",
    "accepted_a": "learning.accepted_updates_a",
    "accepted_b": "learning.accepted_updates_b",
    "learn_ok": "analysis.learning.learn_ok_count",
    "outcomes": "analysis.learning.window_outcomes_24h",
    "deadtime": "model.deadtime_heat_s",
    "deadtime_cool": "model.deadtime_cool_s",  # in heating, the room's passive response after a stop
    "deadtime_reliable": "model.deadtime_heat_reliable",
    "a": "model.a",
    "b": "model.b",
    "indoor": "temperature.indoor",
}


@dataclass(frozen=True)
class Ids:
    """The entities a snapshot reads, found once in the entity registry."""

    diag: dict[str, str | None]  # thermostat -> its SmartPI diagnostics sensor
    boiler: str | None
    count: str | None
    power: str | None
    count_threshold: str | None
    power_threshold: str | None


async def read_ids(r: Any) -> Ids:
    registry = await r.ha.ws_cmd({"type": "config/entity_registry/list"})

    def find(domain: str, platform: str, unique_id: str) -> str | None:
        for entry in registry:
            if entry.get("platform") == platform and entry.get("unique_id") == unique_id and entry["entity_id"].startswith(f"{domain}."):
                return entry["entity_id"]
        return None

    uids = {entry["entity_id"]: entry.get("unique_id") for entry in registry}
    return Ids(
        diag={c: find("sensor", SMARTPI, f"smartpi_diag_{uids[c]}") if uids.get(c) else None for c in KEYS},
        boiler=find("binary_sensor", VT, "central_boiler_state"),
        count=find("sensor", VT, "nb_device_active_boiler"),
        power=find("sensor", VT, "total_power_active_boiler"),
        count_threshold=find("number", VT, "boiler_activation_threshold"),
        power_threshold=find("number", VT, "boiler_power_activation_threshold"),
    )


def _number(state: dict[str, Any] | None) -> float | None:
    try:
        return float(state["state"]) if state else None
    except (TypeError, ValueError):
        return None


def snapshot_of(states: dict[str, dict[str, Any]], ids: Ids, now: float) -> dict[str, Any]:
    """One reading of the instance, from all its states: flat keys, ``west.paused`` and the like."""
    snap: dict[str, Any] = {"t": now}
    boiler = states.get(ids.boiler or "")
    snap["boiler"] = boiler["state"] if boiler else None
    snap["boiler_since"] = boiler.get("last_changed") if boiler else None
    snap["switch"] = (states.get(BOILER_SWITCH) or {}).get("state")
    for key, entity_id in (("count", ids.count), ("power", ids.power), ("count_threshold", ids.count_threshold), ("power_threshold", ids.power_threshold)):
        snap[key] = _number(states.get(entity_id or ""))
    snap["window"] = (states.get(WEST_WINDOW) or {}).get("state")
    for climate, key in KEYS.items():
        live = ((states.get(ids.diag[climate] or "") or {}).get("attributes") or {}).get("live") or {}
        for name, dotted in LIVE.items():
            snap[f"{key}.{name}"] = get(live, dotted)
        thermostat = states.get(climate) or {}
        snap[f"{key}.hvac"] = thermostat.get("state")
        snap[f"{key}.setpoint"] = (thermostat.get("attributes") or {}).get("temperature")
        r = room(NAMES[climate])
        device = states.get(r.device) or {}
        if r.heater == "radiator":
            snap[f"{key}.valve"] = _number(device)  # the valve's own opening, in %
            snap[f"{key}.valve_since"] = device.get("last_changed")
        else:
            snap[f"{key}.heater"] = device.get("state")
    return snap


def of(snap: dict[str, Any], climate: str, name: str) -> Any:
    return snap.get(f"{KEYS[climate]}.{name}")


def calls(snap: dict[str, Any], climate: str) -> bool:
    """The thermostat heating with its valve open: an opening of the cycle above 0 %."""
    return of(snap, climate, "hvac") == "heat" and (of(snap, climate, "opening") or 0) > 0


def reached(snap: dict[str, Any]) -> bool | None:
    """Whether the demand reaches VTherm's threshold, as SmartPI reads it (boiler_call.py); None
    when it cannot be read. The pack's powers are in W."""
    known = False
    count, count_threshold = snap.get("count"), snap.get("count_threshold")
    if count is not None and count_threshold is not None:
        known = True
        if count_threshold > 0 and count >= count_threshold:
            return True
    power, power_threshold = snap.get("power"), snap.get("power_threshold")
    if power is not None and power_threshold is not None:
        known = True
        if power_threshold > 0 and power >= power_threshold:
            return True
    return False if known else None


def demand_agrees(snap: dict[str, Any], role: Role) -> bool:
    """VTherm's count is the open valves of the counted rooms; its power, each counted room's
    device power times the valve opening SmartPI asks for the next cycle (VTherm's
    ``mean_cycle_power``)."""
    valves = [of(snap, c, "valve") for c in COUNTED]
    active = sum(1 for v in valves if isinstance(v, (int, float)) and v > 0)
    expected = sum(role.device_power_w(NAMES[c]) * (of(snap, c, "next_opening") or 0) / 100.0 for c in COUNTED)
    full = sum(role.device_power_w(NAMES[c]) for c in COUNTED)
    power = snap.get("power")
    return snap.get("count") == active and power is not None and abs(power - expected) <= 0.01 * full + 5.0


def clock(snap: dict[str, Any]) -> str:
    return datetime.fromtimestamp(snap["t"], UTC).strftime("%H:%M:%S")


def seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


def published_at(value: Any) -> float | None:
    """A time SmartPI or Home Assistant published (ISO), as the bench's clock."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


# --- SmartPI's log -----------------------------------------------------------------------------

LOG_MARGIN_S = 60.0  # the bench's clock against Home Assistant's, when reading the log
CLOCK_MARGIN_S = 10.0  # the bench's reading against SmartPI's calculation and Home Assistant's clock
BOILER_PAUSE_LINE = "(central boiler off)"  # algo.py, set_boiler_learning_paused: "<name> - SmartPI learning paused (...)"
AT_ONCE_S = 1.0  # a resume logged at the boiler's start: within 0.001 s on the bench
HEAT_STOP_LINE = "heat stopped with the central boiler"  # algo.py, heat_stopped_with_the_boiler
COOL_DETECTED_LINE = "Cool Deadtime detected"  # deadtime_estimator.py: names no thermostat
_HEAT_STOP = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) .*?\] (.+?) - SmartPI heat stopped with the central boiler( \(heating dead-time detection aborted\))?")
_COOL_DETECTED = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) .*Cool Deadtime detected = ([0-9.]+)s")
_LOGGED = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) .*?\] (.+?) - SmartPI learning (paused|resumed) \(central boiler off\)")


def instance_log(pattern: str, since: datetime) -> list[str]:
    """The instance's log lines with ``pattern`` since ``since``, through the bench (as its
    harness reads a failure's evidence)."""
    from ha_client import CONTAINER, ssh  # noqa: PLC0415 — the bench's client

    start = since.timestamp() - LOG_MARGIN_S
    stamp = datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = ssh(f"docker logs --since {stamp} {CONTAINER} 2>&1 | grep -a -F '{pattern}'", timeout=120)
    return out.splitlines()


def logged_pauses(lines: Iterable[str], name: str) -> list[tuple[float, bool]]:
    """A thermostat's pauses (True) and resumes (False) in SmartPI's info lines, with their times;
    Home Assistant logs in UTC on the instances."""
    out: list[tuple[float, bool]] = []
    for line in lines:
        found = _LOGGED.search(line)
        if found and found.group(2) == name:
            at = datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC).timestamp()
            out.append((at, found.group(3) == "paused"))
    return sorted(out)


def _logged_at(stamp: str) -> float:
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC).timestamp()


def logged_heat_stops(lines: Iterable[str], name: str) -> list[tuple[float, bool]]:
    """A thermostat's stops of heat with the central boiler in SmartPI's info lines: (time,
    whether a heating dead-time detection was dropped), in UTC."""
    out = []
    for line in lines:
        found = _HEAT_STOP.search(line)
        if found and found.group(2) == name:
            out.append((_logged_at(found.group(1)), found.group(3) is not None))
    return sorted(out)


def logged_cool_dead_times(lines: Iterable[str]) -> list[tuple[float, float]]:
    """The passive (cooling) dead times detected in SmartPI's info lines: (time, seconds)."""
    out = []
    for line in lines:
        found = _COOL_DETECTED.search(line)
        if found:
            out.append((_logged_at(found.group(1)), float(found.group(2))))
    return sorted(out)


def waited_for_the_boiler(events: Sequence[tuple[float, bool]], since: float, on_at: float | None) -> tuple[float | None, float | None]:
    """A thermostat's pause while VTherm's boiler waited, and its resume, from its logged pauses
    (``logged_pauses``): its last pause from ``since`` up to the boiler's start ``on_at``, and its
    first resume after that pause (or after ``since``). A pause of a few seconds, shorter than a
    reading, is seen too."""
    if on_at is None:
        return None, None
    paused = [t for t, p in events if p and since <= t <= on_at]
    after = paused[-1] if paused else since
    resumed = [t for t, p in events if not p and t >= after]
    return (paused[-1] if paused else None), (resumed[0] if resumed else None)


# --- what a trace shows -----------------------------------------------------------------------


def transitions(samples: Sequence[dict[str, Any]], key: str) -> list[tuple[float, Any]]:
    """Each change of ``key`` in the samples: (time, new value); the first sample counts."""
    out: list[tuple[float, Any]] = []
    for s in samples:
        if key in s and (not out or s[key] != out[-1][1]):
            out.append((s["t"], s[key]))
    return out


def spans(samples: Sequence[dict[str, Any]], key: str, value: Any) -> list[tuple[int, int]]:
    """The runs of consecutive samples whose ``key`` is ``value``: (first index, last index)."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, s in enumerate(samples):
        if s.get(key) == value:
            start = i if start is None else start
        elif start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(samples) - 1))
    return out


def learned_in_pauses(samples: Sequence[dict[str, Any]], key: str) -> tuple[int, list[str]]:
    """The pauses of a thermostat (``key``: "west" or "east") in the samples, and what went wrong
    in them: a learning counter or the heating dead time that changed while paused, or a dead-time
    detection armed while paused."""
    problems: list[str] = []
    runs = spans(samples, f"{key}.paused", True)
    for first, last in runs:
        pause = samples[first : last + 1]
        when = f"{key}'s pause {clock(samples[first])}–{clock(samples[last])}"
        for name in LEARNED:
            values = [s.get(f"{key}.{name}") for s in pause if s.get(f"{key}.{name}") is not None]
            if len(set(values)) > 1:
                problems.append(f"{when}: {name} changed, {values[0]} → {values[-1]}")
        armed = [clock(s) for s in pause if s.get(f"{key}.dt_state") == WAITING_HEAT]
        if armed:
            problems.append(f"{when}: a dead-time detection armed at {armed[0]}")
    return len(runs), problems


# --- the bench of one scenario ----------------------------------------------------------------


class Bench:
    """One scenario on one instance: its readings, its steps and its trace. ``calling``: the
    thermostats the scenario has calling with their valves fully open, from which ``reaches`` gives what the
    role makes of them."""

    def __init__(self, r: Any, ids: Ids, role: Role) -> None:
        self.r = r
        self.api = Instance(r.ha)
        self.ids = ids
        self.role = role
        self.calling: set[str] = set()
        self.count_threshold = float(role.count_threshold)
        self.power_threshold = float(role.power_threshold_w)
        self.trace: Trace | None = None
        self.started = 0.0  # when the last step's action started
        self.learning = "fresh"  # West's and East's learning at the start (``start``)
        # When each thermostat's learning was last reset or loaded: SmartPI's dead-time estimator
        # measures no start in the 600 s after it (its minimum time without heat).
        self.reset_at: dict[str, float] = {}

    @classmethod
    async def open(cls, r: Any) -> Bench:
        return cls(r, await read_ids(r), CONTEXT.role)

    def check(self, cond: object, what: str) -> bool:
        return self.r.check(cond, what)

    def note(self, what: str) -> None:
        self.r.note(what)

    async def snapshot(self) -> dict[str, Any]:
        return snapshot_of(await self.r.ha.states(), self.ids, time.time())

    async def live(self, climate: str) -> dict[str, Any]:
        """SmartPI's diagnostics of a thermostat, all of them."""
        state = await self.r.ha.state(self.ids.diag[climate]) if self.ids.diag[climate] else None
        return ((state or {}).get("attributes") or {}).get("live") or {}

    async def until(self, holds: Callable[[dict[str, Any]], bool], within_s: float, every_s: float = POLL_S) -> tuple[float | None, dict[str, Any]]:
        """Seconds until a snapshot ``holds``, or None after ``within_s``; and the last snapshot."""
        start = time.time()
        snap: dict[str, Any] = {}
        while True:
            snap = await self.snapshot()
            if holds(snap):
                return snap["t"] - start, snap
            if time.time() - start > within_s:
                return None, snap
            await asyncio.sleep(every_s)

    async def watch(self, seconds_: float, every_s: float = POLL_S) -> list[dict[str, Any]]:
        """The snapshots of ``seconds_``."""
        start = time.time()
        seen = []
        while time.time() - start < seconds_:
            seen.append(await self.snapshot())
            await asyncio.sleep(every_s)
        return seen

    # --- what the role makes of the calls

    def reaches(self) -> bool:
        """Whether the thermostats calling with their valves fully open reach the role's threshold."""
        counted = [c for c in self.calling if c in COUNTED]
        if self.count_threshold > 0 and len(counted) >= self.count_threshold:
            return True
        power = sum(self.role.device_power_w(NAMES[c]) for c in counted)
        return self.power_threshold > 0 and power >= self.power_threshold

    def due_paused(self, climate: str) -> bool:
        return climate in COUNTED and climate in self.calling and not self.reaches()

    def paused_is(self, climate: str, value: bool) -> Callable[[dict[str, Any]], bool]:
        return lambda s: of(s, climate, "paused") is value

    # --- the checks after every step

    async def after(self, step: str) -> dict[str, Any]:
        """The same checks after every step; the snapshot they read."""
        snap = await self.snapshot()
        expected = "on" if self.reaches() else "off"
        for climate in COUNTED:
            due = self.due_paused(climate)
            self.check(of(snap, climate, "paused") is due, f"{step}: {NAMES[climate]} {'paused' if due else 'not paused'} ({of(snap, climate, 'paused')})")
        self.check(of(snap, NORTH, "paused") is False, f"{step}: North, not counted, not paused ({of(snap, NORTH, 'paused')})")
        self.check(snap["boiler"] == expected, f"{step}: VTherm's boiler {snap['boiler']} (expected {expected})")
        self.check(snap["switch"] == snap["boiler"], f"{step}: the boiler's switch {snap['switch']}, VTherm's boiler {snap['boiler']}")
        for climate in COUNTED:
            if of(snap, climate, "hvac") != "heat":
                self.check(climate not in self.calling, f"{step}: {NAMES[climate]} {of(snap, climate, 'hvac')}")
                continue
            due = climate in self.calling
            self.check(calls(snap, climate) is due, f"{step}: {NAMES[climate]}'s valve opening {of(snap, climate, 'opening')} % (calling: {due})")
        took, agreed = await self.until(lambda s: demand_agrees(s, self.role), 3 * POLL_S)
        self.check(
            took is not None,
            f"{step}: VTherm counts {agreed.get('count')} active devices and {agreed.get('power')} W; valves "
            f"{[of(agreed, c, 'valve') for c in COUNTED]} %, next openings {[of(agreed, c, 'next_opening') for c in COUNTED]} %",
        )
        return snap

    # --- the steps

    async def change(
        self,
        step: str,
        action: Callable[[], Awaitable[None]],
        own: str | None = None,
        waits: dict[str, Wait] | None = None,
        goals: dict[str, tuple[Callable[[dict[str, Any]], bool], Wait]] | None = None,
    ) -> dict[str, float | None]:
        """A step: ``action`` changes the calls or the threshold (and ``calling`` with them); then
        everything the role makes of it is waited for at once, each against its own wait: VTherm's
        boiler, each counted thermostat's pause or resume (a change of ``own``'s own setpoint or
        mode, or from outside), and the step's own ``goals``; a thermostat that starts calling
        with the threshold reached is watched for a pause it must not take. With an activation
        delay the boiler stays off for that delay once the threshold is reached: every calling
        thermostat is then paused (or pauses) while the boiler waits, and resumes at its start
        (``waited``, from SmartPI's log). Then ``after``."""
        reached_before = self.reaches()
        paused_before = {c: self.due_paused(c) for c in COUNTED}
        calling_before = set(self.calling)
        self.note(f"— {step}")
        started = self.started = time.time()
        await action()
        wanted: dict[str, tuple[Callable[[dict[str, Any]], bool], Wait]] = dict(goals or {})
        if self.reaches() != reached_before:
            if self.reaches():
                wanted["VTherm's boiler on"] = (lambda s: s["boiler"] == "on", boiler_starts(self.role))
            else:
                wanted["VTherm's boiler off"] = (lambda s: s["boiler"] == "off", BOILER_STOPS)
        delayed_start = self.reaches() and not reached_before and self.role.boiler_delay_s >= 2 * RECALC_S
        for climate in COUNTED:
            due = self.due_paused(climate)
            if delayed_start and climate in self.calling:
                continue
            if due == paused_before[climate]:
                continue
            if climate in (waits or {}):
                wait = waits[climate]
            elif climate == own:
                wait = PAUSE_ON_SETPOINT if due else RESUME_ON_SETPOINT
            elif due:
                wait = PAUSE_EXTERNAL
            else:
                wait = resume_when_the_boiler_starts(self.role) if self.reaches() and not reached_before else RESUME_EXTERNAL
            wanted[f"{NAMES[climate]} {'paused' if due else 'resumed'}"] = (self.paused_is(climate, due), wait)
        took = await self.reach(wanted, started)
        if delayed_start:
            took |= await self.waited(started, paused_before)
        elif own in COUNTED and own in self.calling and own not in calling_before and not self.due_paused(own):
            await self.no_pause([own], NO_PAUSE.maximum_s, f"{NAMES[own]} starts calling with the threshold reached")
        await self.after(step)
        return took

    async def waited(self, started: float, paused_before: dict[str, bool]) -> dict[str, float | None]:
        """With an activation delay: each calling counted thermostat paused while VTherm's boiler
        waited and resumed at its start, from SmartPI's info lines, as a pause can last seconds,
        less than a reading (East's 3.9 s in BP6 on the bench). The seconds from ``started``, the
        boiler's from its state's change."""
        snap = await self.snapshot()
        on_at = published_at(snap.get("boiler_since")) if snap.get("boiler") == "on" else None
        lines = await asyncio.to_thread(instance_log, BOILER_PAUSE_LINE, datetime.fromtimestamp(started, UTC))
        took: dict[str, float | None] = {"VTherm's boiler on": None if on_at is None else on_at - started}
        for climate in sorted(self.calling & set(COUNTED)):
            name = NAMES[climate]
            paused_at, resumed_at = waited_for_the_boiler(logged_pauses(lines, name), started - CLOCK_MARGIN_S, on_at)
            if not paused_before[climate]:
                pause = None if paused_at is None else paused_at - started
                took[f"{name} paused while the boiler waits"] = pause
                self.check(
                    pause is not None and pause <= self.role.boiler_delay_s + MARGIN_S,
                    f"{name} paused while the boiler waits after {seconds(pause)} s, before its start "
                    f"(typical {PAUSE_ON_SETPOINT.typical_s:.0f} s; SmartPI's log)",
                )
            back = None if resumed_at is None or on_at is None else resumed_at - on_at
            took[f"{name} resumed once the boiler is on"] = None if resumed_at is None else resumed_at - started
            self.check(
                back is not None and abs(back) <= AT_ONCE_S,
                f"{name} resumed at VTherm's boiler start ({'—' if back is None else f'{back:+.3f}'} s from it; SmartPI's log)",
            )
        return took

    async def reach(self, goals: dict[str, tuple[Callable[[dict[str, Any]], bool], Wait]], since: float) -> dict[str, float | None]:
        """Each goal's seconds from ``since`` until it held, all watched at once, each checked
        against its wait's limit."""
        took: dict[str, float | None] = dict.fromkeys(goals)
        limit = max((w.maximum_s for _, w in goals.values()), default=0.0)
        while goals:
            snap = await self.snapshot()
            for name, (holds, _) in goals.items():
                if took[name] is None and holds(snap):
                    took[name] = snap["t"] - since
            if all(v is not None for v in took.values()) or snap["t"] - since > limit:
                break
            await asyncio.sleep(POLL_S)
        for name, (_, wait) in goals.items():
            value = took[name]
            self.check(value is not None and value <= wait.maximum_s, f"{name} after {seconds(value)} s (typical {wait.typical_s:.0f} s)")
        return took

    async def no_pause(self, climates: Iterable[str], seconds_: float, what: str) -> None:
        seen = [f"{NAMES[c]} at {clock(s)}" for s in await self.watch(seconds_) for c in climates if of(s, c, "paused") is True]
        self.check(not seen, f"{what}: no pause for {seconds_:.0f} s ({', '.join(seen[:5]) or 'none seen'})")

    async def setpoints(self, **values: float) -> None:
        for climate, value in values.items():
            await self.api.call("climate", "set_temperature", {"entity_id": climate, "temperature": value})

    async def valve_delay(self, seconds_: float) -> None:
        """The test rooms' valves take and report a new opening ``seconds_`` after it is set."""
        await self.api.call(ROOMS_DOMAIN, "set_valve_report_delay", {"seconds": seconds_})

    async def hvac(self, climate: str, mode: str) -> None:
        await self.api.call("climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": mode})

    async def calls_at(self, climate: str, value: float) -> None:
        """A setpoint and what it means for ``calling``: CALLING calls, anything else does not."""
        await self.setpoints(**{climate: value})
        if value == CALLING:
            self.calling.add(climate)
        else:
            self.calling.discard(climate)

    async def west_calls(self) -> dict[str, float | None]:
        """West calls, its valve fully open. Starting from not calling, its pause (if due) comes once it
        has called with the boiler off for a calculation period, never before."""
        took = await self.change("West calls", lambda: self.calls_at(WEST, CALLING), own=WEST)
        paused = took.get("West paused")
        if paused is not None:
            self.check(paused >= PAUSE_CONFIRM_S, f"West's pause after a calculation period with the boiler off at least (after {paused:.1f} s)")
        return took

    async def west_stops(self) -> dict[str, float | None]:
        """West stops calling: a pause ends at once, for a learned West at its next calculation."""
        waits = {WEST: RESUME_ON_STOP_LEARNED} if self.learning == "learned" else None
        return await self.change("West stops calling", lambda: self.calls_at(WEST, STOPPED), own=WEST, waits=waits)

    async def east_calls(self) -> dict[str, float | None]:
        return await self.change("East calls", lambda: self.calls_at(EAST, CALLING), own=EAST)

    async def east_stops(self) -> dict[str, float | None]:
        closed = (lambda s: of(s, EAST, "valve") == 0.0, VALVE_CLOSES)
        return await self.change("East stops calling", lambda: self.calls_at(EAST, STOPPED), own=EAST, goals={"East's valve closed": closed})

    async def west_off(self) -> dict[str, float | None]:
        async def off() -> None:
            await self.hvac(WEST, "off")
            self.calling.discard(WEST)

        return await self.change("West turned off", off, own=WEST)

    async def west_on(self) -> dict[str, float | None]:
        async def on() -> None:
            await self.hvac(WEST, "heat")
            if (await self.snapshot()).get("west.setpoint") == CALLING:
                self.calling.add(WEST)

        return await self.change("West heating again", on, own=WEST)

    async def window(self, opened: bool) -> dict[str, float | None]:
        """West's window: opened, VTherm turns West off after its delay and the pause ends;
        closed, West heats again after the delay and pauses two calculations later."""
        was_calling = WEST in self.calling

        async def act() -> None:
            await self.api.call("input_boolean", "turn_on" if opened else "turn_off", {"entity_id": WEST_WINDOW})
            if opened:
                self.calling.discard(WEST)
            elif was_calling or (await self.snapshot()).get("west.setpoint") == CALLING:
                self.calling.add(WEST)

        mode = (lambda s: of(s, WEST, "hvac") == ("off" if opened else "heat"), Wait(WINDOW_DELAY_S, WINDOW_DELAY_S + MARGIN_S))
        return await self.change(
            f"West's window {'opened' if opened else 'closed'}",
            act,
            own=WEST,
            waits={WEST: WINDOW_OPENS if opened else WINDOW_CLOSES},
            goals={f"West {'off' if opened else 'heating'}": mode},
        )

    async def load(self, climate: str, state: str) -> None:
        """The thermostat given a learned state of the helper (``states/<state>.json``)."""
        await self.api.call(HELPER_DOMAIN, "load_learned_state", {"entity_id": climate, "state": state})
        self.reset_at[climate] = time.time()
        took, data = await self.until(lambda s: of(s, climate, "deadtime_reliable") is True and of(s, climate, "phase") not in (None, "Hysteresis"), MARGIN_S)
        self.check(took is not None, f"{NAMES[climate]} learned from {state}: phase {of(data, climate, 'phase')}, heating dead time {of(data, climate, 'deadtime')} s")

    async def reset(self, climate: str) -> None:
        """The thermostat's learning reset (SmartPI's service), and the moment of it."""
        await self.api.call(SMARTPI, "reset_smartpi_learning", {"entity_id": climate})
        self.reset_at[climate] = time.time()

    async def threshold_entity(self) -> tuple[str | None, float]:
        """The role's threshold: VTherm's count threshold, or its power threshold."""
        if self.role.count_threshold > 0:
            return self.ids.count_threshold, float(self.role.count_threshold)
        return self.ids.power_threshold, float(self.role.power_threshold_w)

    async def threshold_set(self, value: float) -> dict[str, float | None]:
        entity_id, _ = await self.threshold_entity()

        async def act() -> None:
            await self.api.call("number", "set_value", {"entity_id": entity_id, "value": value})
            if self.role.count_threshold > 0:
                self.count_threshold = value
            else:
                self.power_threshold = value

        return await self.change(f"{entity_id} = {value:g}", act)

    async def hold(self, n: int = 3) -> None:
        """West stays paused, calling, the boiler off: ``n`` readings a calculation apart."""
        for _ in range(n):
            snap = await self.snapshot()
            self.check(
                of(snap, WEST, "paused") is True and calls(snap, WEST) and snap["boiler"] == "off",
                f"the pause holds: West paused {of(snap, WEST, 'paused')}, valve opening {of(snap, WEST, 'opening')} %, boiler {snap['boiler']}",
            )
            await asyncio.sleep(RECALC_S)

    async def restart(self) -> float:
        """Home Assistant restarted; SmartPI's diagnostics back. The time of the restart."""
        self.note("— Home Assistant restarts")
        at = time.time()
        took = await self.api.restart()
        self.note(f"Home Assistant restarted in {took:.0f} s")
        took, snap = await self.until(lambda s: bool(of(s, WEST, "phase")) and bool(of(s, EAST, "phase")), AFTER_RESTART.maximum_s)
        self.check(took is not None, f"SmartPI's diagnostics back after {seconds(took)} s")
        return at

    # --- the start state

    async def base(self) -> None:
        """Every thermostat heating, West and East not calling, North not calling, the window
        closed, the role's threshold and the user's learning setting as prepared; then the
        settled state (``settled``)."""
        for climate in KEYS:
            state = await self.r.ha.state(climate)
            if state is not None and state["state"] != "heat":
                await self.hvac(climate, "heat")
        await self.api.call("input_boolean", "turn_off", {"entity_id": WEST_WINDOW})
        await self.valve_delay(CONTEXT.valve_delay_s)
        await self.setpoints(**{WEST: STOPPED, EAST: STOPPED, NORTH: STOPPED})
        self.calling.clear()
        entity_id, value = await self.threshold_entity()
        for other in (self.ids.count_threshold, self.ids.power_threshold):
            wanted = value if other == entity_id else 0.0
            current = _number(await self.r.ha.state(other)) if other else None
            if other and current != wanted:
                await self.api.call("number", "set_value", {"entity_id": other, "value": wanted})
                self.note(f"start state: {other} back to {wanted:g} (was {current})")
        for climate in COUNTED:
            if get(await self.live(climate), "learning.enabled") is False:
                await self.api.call(SMARTPI, "set_smartpi_learning", {"entity_id": climate, "learning_enabled": True})
                self.note(f"start state: {NAMES[climate]}'s learning setting back on")
        await self.settled()

    async def settled(self) -> dict[str, Any] | None:
        """West and East not calling with their valves closed, no thermostat paused, VTherm's
        boiler off for the role's delay plus ``SETTLED_S``."""
        hold_s = self.role.boiler_delay_s + SETTLED_S

        def quiet(s: dict[str, Any]) -> bool:
            off_since = published_at(s.get("boiler_since"))
            return (
                all(not calls(s, c) and of(s, c, "valve") == 0.0 for c in COUNTED)
                and all(of(s, c, "paused") is not True for c in KEYS)
                and s["boiler"] == "off"
                and s["switch"] == "off"
                and off_since is not None
                and s["t"] - off_since >= hold_s
            )

        took, snap = await self.until(quiet, START_STATE.maximum_s)
        self.check(
            took is not None,
            f"start state after {seconds(took)} s: valve openings {[of(snap, c, 'opening') for c in COUNTED]} %, valves "
            f"{[of(snap, c, 'valve') for c in COUNTED]}, paused {[of(snap, c, 'paused') for c in KEYS]}, VTherm's boiler "
            f"{snap.get('boiler')} since {snap.get('boiler_since')} ({hold_s:.0f} s needed), switch {snap.get('switch')}",
        )
        return snap if took is not None else None

    async def start(self, start: str, north: float) -> bool:
        """West's and East's learning as ``start`` declares, North at ``north``, then the settled
        start state; whether it was reached."""
        self.learning = start
        for climate in COUNTED:
            if start == "learned":
                await self.api.call(HELPER_DOMAIN, "load_learned_state", {"entity_id": climate, "state": KEYS[climate]})
                self.reset_at[climate] = time.time()
                took, data = await self.until(lambda s, c=climate: of(s, c, "phase") not in (None, "Hysteresis"), MARGIN_S)
                self.check(took is not None, f"start: {NAMES[climate]} learned, phase {of(data, climate, 'phase')}, heating dead time {of(data, climate, 'deadtime')} s")
            else:
                await self.reset(climate)
                took, data = await self.until(
                    lambda s, c=climate: of(s, c, "deadtime_reliable") is False and of(s, c, "accepted_a") == 0 and of(s, c, "heat_outcome") is None,
                    MARGIN_S,
                )
                self.check(took is not None, f"start: {NAMES[climate]} fresh, phase {of(data, climate, 'phase')}, accepted updates of a {of(data, climate, 'accepted_a')}")
        await self.setpoints(**{NORTH: north})
        return await self.settled() is not None


# --- the trace --------------------------------------------------------------------------------


TIMELINE = ("boiler", "switch", "count", "power", "west.paused", "east.paused", "west.opening", "east.opening", "east.valve")
TIMELINE_LINES = 40


class Trace:
    """Every ``TRACE_S`` one reading of the instance, in the background; the changes are written
    to ``<results dir>/traces/<date>-<scenario>-<role>-instance<N>-<time>.json``."""

    def __init__(self, bench: Bench, sid: str) -> None:
        self.bench = bench
        self.sid = sid
        self.samples: list[dict[str, Any]] = []
        self.missed = 0
        self.task: asyncio.Task[None] | None = None
        self.started = datetime.now(UTC)

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            try:
                self.samples.append(await self.bench.snapshot())
            except Exception:  # noqa: BLE001 — Home Assistant restarting, or a dropped connection
                self.missed += 1
            await asyncio.sleep(TRACE_S)

    def changes(self) -> list[dict[str, Any]]:
        """The first sample in full, then each sample's changed keys."""
        out: list[dict[str, Any]] = []
        last: dict[str, Any] = {}
        for s in self.samples:
            changed = {k: v for k, v in s.items() if k != "t" and last.get(k, object()) != v}
            if changed or not out:
                out.append({"at": datetime.fromtimestamp(s["t"], UTC).isoformat(timespec="seconds")} | changed)
            last = s
        return out

    def file(self) -> pathlib.Path:
        from ha_client import N  # noqa: PLC0415 — the bench's client
        from harness import results_file  # noqa: PLC0415 — the bench's harness

        name = f"{self.started:%Y-%m-%d}-{self.sid}-{self.bench.role.name}-qas-instance{N}-{self.started:%H%M%S}.json"
        return results_file(self.bench.r.pack).parent / "traces" / name

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        b = self.bench
        try:
            out = self.file()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"scenario": self.sid, "role": b.role.name, "trace_s": TRACE_S, "samples": self.changes()}, indent=1), encoding="utf-8")
            b.note(f"trace: {out.name}, {len(self.samples)} readings, {self.missed} missed")
        except Exception as err:  # noqa: BLE001 — the trace must never hide the result
            b.note(f"trace: not written ({err})")
        lines = []
        for key in TIMELINE:
            for t, value in transitions(self.samples, key)[1:]:
                lines.append((t, f"{datetime.fromtimestamp(t, UTC):%H:%M:%S} {key} {value}"))
        lines.sort()
        for _, line in lines[:TIMELINE_LINES]:
            b.note(f"timeline: {line}")
        if len(lines) > TIMELINE_LINES:
            b.note(f"timeline: {len(lines) - TIMELINE_LINES} more changes in the trace")
        for key in ("west", "east"):
            n, problems = learned_in_pauses(self.samples, key)
            if n:
                b.check(not problems, f"nothing learned in {key}'s {n} pause(s)" + (f": {'; '.join(problems[:3])}" if problems else ""))
