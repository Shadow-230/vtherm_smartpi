"""The QAS pack's files (tests/qas) agree with each other, and its learned state loads into
SmartPI as the room's learned model."""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

QAS = pathlib.Path(__file__).resolve().parent / "qas"
if str(QAS) not in sys.path:
    sys.path.insert(0, str(QAS))

import configuration  # noqa: E402
import learned_state  # noqa: E402
import run  # noqa: E402
from pack import BOILER_SWITCH, ROLES, WEST_WINDOW, room  # noqa: E402
from scenarios import (  # noqa: E402
    SCENARIOS,
    boiler_back,
    west_leaves,
)
from steps import (  # noqa: E402
    EAST,
    LIVE,
    MARGIN_S,
    NORTH,
    PAUSE_CONFIRM_S,
    PAUSE_ON_SETPOINT,
    RECALC_S,
    RESUME_ON_SETPOINT,
    SETTLED_S,
    STARTS,
    WAITING_HEAT,
    WEST,
    Bench,
    Ids,
    demand_agrees,
    learned_in_pauses,
    logged_pauses,
    reached,
    snapshot_of,
    spans,
    transitions,
    waited_for_the_boiler,
)

from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: E402
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT  # noqa: E402
from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase  # noqa: E402


@pytest.mark.parametrize("name", sorted(ROLES))
def test_each_role_s_configuration_is_up_to_date(name: str) -> None:
    path = configuration.CONFIG / f"{name}.yaml"
    assert path.read_text(encoding="utf-8") == configuration.text(ROLES[name]), "run python tests/qas/configuration.py"


def test_no_other_role_configuration() -> None:
    assert {p.stem for p in configuration.CONFIG.glob("*.yaml")} == set(ROLES)


@pytest.mark.parametrize("room", learned_state.ROOMS, ids=lambda r: r.key)
def test_each_learned_state_is_up_to_date(room) -> None:
    assert learned_state.file(room).read_text(encoding="utf-8") == learned_state.text(room), "run python tests/qas/learned_state.py"


def test_no_other_learned_state() -> None:
    assert {p.name for p in learned_state.STATES.glob("*.json")} == {path.name for path, _ in learned_state.files()}


def test_the_settled_variant_is_up_to_date_and_publishes_west_s_response_lag() -> None:
    """``west_settled``: West's learned model with its response lag published (780 s, the
    radiator's delay plus its lag) and heating periods of two hours, so that SmartPI's settling
    time of a heating is three lags, 2340 s (BP29)."""
    from scenarios import SETTLED_HEAT_S, SETTLED_STATE  # noqa: PLC0415

    west = room("West")
    path = learned_state.settled_file()
    assert SETTLED_STATE == learned_state.SETTLED == path.stem
    assert path.read_text(encoding="utf-8") == learned_state.text(west, settled=True), "run python tests/qas/learned_state.py"
    smartpi = SmartPI(hass=MagicMock(), cycle_min=5, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS")
    smartpi.load_state(json.loads(path.read_text(encoding="utf-8")))
    lag = smartpi.learn_win.response_lag
    assert lag.lag_s == learned_state.lag_s(west) == 780.0
    assert lag.active_settle_s == pytest.approx(3 * 780.0)
    assert smartpi.dt_est.deadtime_heat_s == pytest.approx(780.0) and smartpi.dt_est.deadtime_heat_reliable
    assert SETTLED_HEAT_S >= lag.active_settle_s + 5 * RECALC_S


@pytest.mark.parametrize("room", learned_state.ROOMS, ids=lambda r: r.key)
def test_each_learned_state_loads_as_its_room_s_model(room) -> None:
    """Loaded into SmartPI, the state gives the room's a and b, its dead times reliable, and SmartPI
    past its first phase at its first calculation."""
    model = learned_state.model(room)
    smartpi = SmartPI(hass=MagicMock(), cycle_min=5, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS")
    smartpi.load_state(json.loads(learned_state.file(room).read_text(encoding="utf-8")))

    smartpi.calculate(target_temp=20.0, current_temp=19.5, ext_current_temp=3.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)

    assert (smartpi.est.a, smartpi.est.b) == pytest.approx((model["a"], model["b"]))
    assert smartpi.dt_est.deadtime_heat_reliable and smartpi.dt_est.deadtime_cool_reliable
    assert smartpi.dt_est.deadtime_heat_s == pytest.approx(model["deadtime_s"])
    assert smartpi.phase != SmartPIPhase.HYSTERESIS


def test_the_catalogue_is_the_scenarios() -> None:
    """README.md's catalogue lists the registered scenarios as run.py writes them."""
    text = (QAS / "README.md").read_text(encoding="utf-8")
    block = text[text.index("<!-- catalogue:") : text.index("<!-- end of catalogue -->")]
    assert "\n".join(line for line in block.splitlines() if line.startswith(("| BP", "| BR"))) == run.catalogue(), (
        "run python tests/qas/run.py --list --markdown"
    )


def test_each_scenario_has_its_checks_roles_and_counterpart() -> None:
    tests = pathlib.Path(__file__).resolve().parent
    for scenario in SCENARIOS.values():
        assert scenario.checks, scenario.id
        assert set(scenario.roles) <= set(ROLES), scenario.id
        assert scenario.start in STARTS, scenario.id
        for name in scenario.counterpart.split(", "):
            assert (tests / name).is_file(), f"{scenario.id}: no {name}"


def test_each_scenario_says_its_start_state_event_and_expected_result() -> None:
    """One format for the catalogue: the start state → the event → the expected result."""
    for scenario in SCENARIOS.values():
        assert len(scenario.parts) == 3 and all(scenario.parts), scenario.id


def test_every_role_has_scenarios_beyond_bp0() -> None:
    for name in ROLES:
        assert [s.id for s in SCENARIOS.values() if name in s.roles and s.id != "BP0"], name


def test_the_steps_of_the_pause_run_on_the_three_roles() -> None:
    """The role matrix: the pause's steps on the device count, the power and the activation delay."""
    three = {"boiler-count", "boiler-power", "boiler-delay"}
    for sid in ("BP1", "BP2", "BP3", "BP5", "BP6", "BP7", "BP8", "BP9", "BP10", "BP11", "BP18", "BP20"):
        assert three <= set(SCENARIOS[sid].roles), sid
    assert set(SCENARIOS["BP13"].roles) == {"boiler-levels", "boiler-levels-delay"}


@pytest.mark.parametrize(
    ("name", "west", "east", "both"),
    [
        ("boiler-count", False, False, True),
        ("boiler-power", False, False, True),
        ("boiler-delay", False, False, True),
        ("boiler-levels", False, True, True),
        ("boiler-levels-delay", False, True, True),
    ],
)
def test_what_each_role_makes_of_the_calls(name: str, west: bool, east: bool, both: bool) -> None:
    """West alone is below every role's threshold; East alone reaches only the 600 W of the levels
    (900 W); both reach every threshold. A thermostat calling below it is due to pause."""
    bench = Bench(MagicMock(), Ids({}, None, None, None, None, None), ROLES[name])
    for calling, reaches in (({WEST}, west), ({EAST}, east), ({WEST, EAST}, both)):
        bench.calling = set(calling)
        assert bench.reaches() is reaches, calling
        assert all(bench.due_paused(c) is (not reaches) for c in calling)
        assert bench.due_paused(NORTH) is False


def test_nothing_in_the_pack_is_collected_by_pytest() -> None:
    assert not list(QAS.rglob("test_*.py"))


def test_each_scenario_s_typical_time_is_within_its_maximum() -> None:
    for scenario in SCENARIOS.values():
        for name in scenario.roles:
            wait = scenario.time_for(ROLES[name])
            assert 0 <= wait.typical_s <= wait.maximum_s, (scenario.id, name)


# --- the steps' readings and the trace's checks (steps.py) -------------------------------------


def _ids() -> Ids:
    return Ids(
        diag={WEST: "sensor.west_diag", EAST: "sensor.east_diag", NORTH: "sensor.north_diag"},
        boiler="binary_sensor.boiler",
        count="sensor.count",
        power="sensor.power",
        count_threshold="number.count_threshold",
        power_threshold="number.power_threshold",
    )


def test_a_snapshot_reads_every_entity_once() -> None:
    """One reading of all states gives VTherm's boiler, count, power and thresholds, the boiler's
    switch, the window, and each thermostat's SmartPI diagnostics (the valve opening among them),
    mode, setpoint, and its valve's opening or its heater's state."""
    live = {"learning": {"boiler_paused": True, "accepted_updates_a": 31}, "power": {"current_cycle_percent": 100.0, "next_cycle_percent": 80.0}}
    states = {
        "binary_sensor.boiler": {"state": "off", "last_changed": "2026-11-02T10:00:00+00:00"},
        "sensor.count": {"state": "1"},
        "sensor.power": {"state": "1600.0"},
        "number.count_threshold": {"state": "0.0"},
        "number.power_threshold": {"state": "2500.0"},
        BOILER_SWITCH: {"state": "off"},
        WEST_WINDOW: {"state": "off"},
        "sensor.west_diag": {"state": "ok", "attributes": {"live": live}},
        WEST: {"state": "heat", "attributes": {"temperature": 23.0}},
        room("West").device: {"state": "80.0"},
        room("North").device: {"state": "on"},
    }
    snap = snapshot_of(states, _ids(), 1.0)
    assert (snap["boiler"], snap["count"], snap["power"], snap["power_threshold"], snap["switch"]) == ("off", 1.0, 1600.0, 2500.0, "off")
    assert (snap["west.paused"], snap["west.opening"], snap["west.next_opening"], snap["west.accepted_a"]) == (True, 100.0, 80.0, 31)
    assert (snap["west.hvac"], snap["west.setpoint"], snap["west.valve"], snap["north.heater"]) == ("heat", 23.0, 80.0, "on")
    assert snap["east.paused"] is None and snap["east.valve"] is None
    assert reached(snap) is False
    assert demand_agrees(snap, ROLES["boiler-power"])


def test_the_threshold_as_smartpi_reads_it() -> None:
    assert reached({"count": 2.0, "count_threshold": 2.0, "power": None, "power_threshold": None}) is True
    assert reached({"count": 1.0, "count_threshold": 2.0, "power": 0.0, "power_threshold": 0.0}) is False
    assert reached({"count": None, "count_threshold": None, "power": 600.0, "power_threshold": 600.0}) is True
    assert reached({"count": None, "count_threshold": 2.0, "power": None, "power_threshold": 600.0}) is None


def test_vtherm_s_count_and_power_against_the_valve_openings() -> None:
    """VTherm's count is the open valves; its power the device powers times the valve opening
    SmartPI asks for the next cycle (300 W and 900 W in boiler-levels)."""
    snap = {"count": 2.0, "power": 750.0, "west.valve": 100.0, "east.valve": 50.0, "west.next_opening": 100.0, "east.next_opening": 50.0}
    assert demand_agrees(snap, ROLES["boiler-levels"])
    assert not demand_agrees(snap | {"count": 1.0}, ROLES["boiler-levels"])
    assert not demand_agrees(snap | {"power": 1200.0}, ROLES["boiler-levels"])


def _trace(rows: list[dict]) -> list[dict]:
    """Samples every 5 s from rows of the keys that change."""
    out, last = [], {}
    for i, row in enumerate(rows):
        last = last | row
        out.append({"t": 1000.0 + 5.0 * i} | last)
    return out


def test_the_trace_finds_its_changes_and_runs() -> None:
    samples = _trace([{"boiler": "off"}, {}, {"boiler": "on"}, {}, {"boiler": "off"}])
    assert transitions(samples, "boiler") == [(1000.0, "off"), (1010.0, "on"), (1020.0, "off")]
    assert spans(samples, "boiler", "on") == [(2, 3)]


def test_nothing_learned_in_a_pause() -> None:
    """A pause with its counters and dead time unchanged and no detection armed is clean."""
    samples = _trace(
        [
            {"west.paused": False, "west.accepted_a": 30, "west.deadtime": 780.0},
            {"west.paused": True, "west.accepted_a": 31},
            {},
            {"west.paused": False},
        ]
    )
    assert learned_in_pauses(samples, "west") == (1, [])


def test_what_a_pause_must_not_learn() -> None:
    samples = _trace(
        [
            {"west.paused": True, "west.accepted_a": 31, "west.dt_state": "OFF"},
            {"west.accepted_a": 32},
            {"west.dt_state": WAITING_HEAT},
            {"west.paused": False, "west.dt_state": "HEATING"},
        ]
    )
    n, problems = learned_in_pauses(samples, "west")
    assert n == 1
    assert any("accepted_a changed" in p for p in problems)
    assert any("detection armed" in p for p in problems)


def test_bp20_reads_the_boiler_s_return_from_its_watch() -> None:
    """The boiler's return after the restart, from the readings of the watch: at the time VTherm
    gives for it, 0 s when it never went off, None when it did not come back."""
    since = 1000.0
    seen = [
        {"t": 1004.0, "boiler": None},
        {"t": 1009.0, "boiler": "off", "boiler_since": datetime.fromtimestamp(998.0, UTC).isoformat()},
        {"t": 1129.0, "boiler": "on", "boiler_since": datetime.fromtimestamp(1120.2, UTC).isoformat()},
    ]
    assert boiler_back(seen, since) == pytest.approx(120.2)
    assert boiler_back([{"t": 1005.0, "boiler": "on", "boiler_since": datetime.fromtimestamp(900.0, UTC).isoformat()}], since) == 0.0
    assert boiler_back([{"t": 1005.0, "boiler": "on"}], since) == 5.0
    assert boiler_back(seen[:2], since) is None


def test_the_typical_times_of_the_own_setpoint_and_the_closing_start_state() -> None:
    """A thermostat's own start pauses a calculation period after its setpoint's own calculation,
    at the next check: 60-120 s, 90 s on average (mean 84 s over 117 pauses on the bench), at most
    two periods with a margin (East's 116 s in BP6 on the bench), also
    while an activation delay keeps the boiler off; a learned West that stops resumes a calculation
    later, a fresh one at once; after a restart the pause returns a calculation period after SmartPI
    runs again; the start state after a scenario that ends with the boiler on waits for it off the
    role's delay plus 30 s, and after one that ends with a learned West paused for its resume."""
    count, delay = ROLES["boiler-count"], ROLES["boiler-delay"]
    assert PAUSE_ON_SETPOINT.typical_s == 1.5 * RECALC_S
    assert PAUSE_ON_SETPOINT.maximum_s >= 2 * RECALC_S + MARGIN_S
    assert delay.boiler_delay_s + MARGIN_S >= 2 * RECALC_S + MARGIN_S  # steps.Bench.waited
    assert west_leaves(count).typical_s == RESUME_ON_SETPOINT.typical_s == 0.0
    assert west_leaves(count, learned=True).typical_s == RECALC_S
    assert SCENARIOS["BP3"].time_for(count).typical_s == 1.5 * RECALC_S
    assert SCENARIOS["BP9"].time(count).typical_s == 1.5 * RECALC_S + 20.0 + RECALC_S
    for sid in ("BP0", "BP2", "BP5", "BP7", "BP8", "BP18", "BP20", "BP25"):
        s = SCENARIOS[sid]
        for name in s.roles:
            assert s.closing(ROLES[name]).typical_s == ROLES[name].boiler_delay_s + SETTLED_S, (sid, name)
    assert SCENARIOS["BP2"].closing(delay).typical_s == 150.0
    assert SCENARIOS["BP9"].closing(count).typical_s == RECALC_S
    assert SCENARIOS["BP9"].closing(delay).typical_s == delay.boiler_delay_s + SETTLED_S  # the restart's boiler-off time
    assert SCENARIOS["BP1"].closing(delay).typical_s == 0.0




# SmartPI's info lines as the bench's log gives them (2026-11-10, BP6 on boiler-delay): East's pause
# of 3.9 s, shorter than a reading.
_LOG = [
    "2026-11-10 08:08:45.066 INFO (MainThread) [custom_components.vtherm_smartpi.algo] West - SmartPI learning paused (central boiler off)",
    "2026-11-10 08:10:45.071 INFO (MainThread) [custom_components.vtherm_smartpi.algo] East - SmartPI learning paused (central boiler off)",
    "2026-11-10 08:10:48.931 INFO (MainThread) [custom_components.vtherm_smartpi.algo] West - SmartPI learning resumed (central boiler off)",
    "2026-11-10 08:10:48.931 INFO (MainThread) [custom_components.vtherm_smartpi.algo] East - SmartPI learning resumed (central boiler off)",
    "2026-11-10 08:14:45.070 INFO (MainThread) [custom_components.vtherm_smartpi.algo] West - SmartPI learning paused (central boiler off)",
]


def _at(clock: str) -> float:
    return datetime.fromisoformat(f"2026-11-10T{clock}+00:00").timestamp()


def test_the_pauses_read_from_smartpi_s_log() -> None:
    """Each thermostat's pauses and resumes from SmartPI's info lines, in UTC, to the millisecond."""
    east = logged_pauses(_LOG, "East")
    assert east == [(pytest.approx(_at("08:10:45.071")), True), (pytest.approx(_at("08:10:48.931")), False)]
    assert [p for _, p in logged_pauses(_LOG, "West")] == [True, False, True]
    assert logged_pauses(_LOG, "North") == []


def test_a_pause_while_the_boiler_waits_and_its_resume_at_its_start() -> None:
    """From the step's start up to the boiler's start: the last pause, then the first resume
    after it; a thermostat already paused has only its resume; no boiler start, nothing."""
    on_at = _at("08:10:48.931")
    paused, resumed = waited_for_the_boiler(logged_pauses(_LOG, "East"), _at("08:08:48.9"), on_at)
    assert (paused, resumed) == (pytest.approx(_at("08:10:45.071")), pytest.approx(on_at))
    paused, resumed = waited_for_the_boiler(logged_pauses(_LOG, "West"), _at("08:10:00"), on_at)
    assert (paused, resumed) == (None, pytest.approx(on_at))
    assert waited_for_the_boiler(logged_pauses(_LOG, "West"), _at("08:08:00"), None) == (None, None)
    # A pause after the boiler's start is not one while it waited.
    assert waited_for_the_boiler(logged_pauses(_LOG, "West"), _at("08:11:00"), _at("08:12:00")) == (None, None)


def test_a_room_settles_600_s_after_its_valve_closed_and_its_reset_and_below_its_peak() -> None:
    """BP26-BP28 start a measured start once the room's valve has been closed for SmartPI's 600 s,
    its learning was reset or loaded 600 s before, and its room is a sensor step below its highest
    reading since."""
    from scenarios import MIN_OFF_S, a_settled_room  # noqa: PLC0415

    from custom_components.vtherm_smartpi.smartpi.deadtime_estimator import DeadTimeEstimator  # noqa: PLC0415

    assert DeadTimeEstimator().min_off_time_seconds == MIN_OFF_S
    closed = datetime.fromtimestamp(1000.0, UTC).isoformat()
    holds = a_settled_room(WEST, {}, 900.0)
    readings = [(1100.0, 19.4), (1300.0, 19.5), (1500.0, 19.5), (1610.0, 19.5), (1700.0, 19.4)]
    assert [holds({"t": t, "west.indoor": c, "west.valve": 0.0, "west.valve_since": closed}) for t, c in readings] == [False, False, False, False, True]
    assert not a_settled_room(WEST, {"c": 19.6}, 900.0)({"t": 1700.0, "west.indoor": 19.5, "west.valve": 20.0, "west.valve_since": closed})
    # Reset 500 s before: not yet, though the valve and the room are ready.
    assert not a_settled_room(WEST, {"c": 19.6}, 1200.0)({"t": 1700.0, "west.indoor": 19.5, "west.valve": 0.0, "west.valve_since": closed})
    assert a_settled_room(EAST, {"c": 19.6}, 1100.0)({"t": 1700.0, "east.indoor": 19.5, "east.valve": 0.0, "east.valve_since": closed})


def test_bp27_calls_again_within_smartpi_s_600_s_and_watches_15_minutes() -> None:
    """BP27, on the activation delay: West's second call and the boiler's wait stay within the
    600 s SmartPI's estimator asks without heat, with a calculation's margin; West's diagnostics
    are watched for 15 minutes from the boiler's start."""
    from scenarios import MIN_OFF_S, NO_DETECTION_WATCH_S, REST_S  # noqa: PLC0415

    delay = ROLES["boiler-delay"]
    s = SCENARIOS["BP27"]
    assert (s.roles, s.start, s.boiler_at_end) == (("boiler-delay",), "fresh", True)
    assert REST_S + delay.boiler_delay_s + RECALC_S <= MIN_OFF_S - RECALC_S
    assert NO_DETECTION_WATCH_S >= 15 * 60.0


def test_bp26_to_bp28_ask_a_measured_dead_time_of_at_least_a_calculation() -> None:
    """A dead time below one of SmartPI's calculations (60 s) is a rise already under way."""
    from scenarios import SHORTEST_DEADTIME_S, at_least_a_calculation  # noqa: PLC0415

    assert SHORTEST_DEADTIME_S == RECALC_S == 60.0
    assert [at_least_a_calculation(v) for v in (None, 0.0035, 59.9, 60.0, 780.0)] == [False, False, False, True, True]


def test_bp28_calls_within_600_s_of_the_reset() -> None:
    """BP28, on the activation delay: West's call after the reset and the boiler's wait stay within
    the 600 s SmartPI's estimator asks after a reset, with a calculation's margin."""
    from scenarios import CALL_AFTER_RESET_S, MIN_OFF_S  # noqa: PLC0415

    delay = ROLES["boiler-delay"]
    s = SCENARIOS["BP28"]
    assert (s.roles, s.start, s.boiler_at_end) == (("boiler-delay",), "fresh", True)
    assert CALL_AFTER_RESET_S + delay.boiler_delay_s + RECALC_S <= MIN_OFF_S - RECALC_S


def test_each_value_the_pack_reads_is_in_smartpi_s_diagnostics() -> None:
    """Every value a snapshot keeps of SmartPI's diagnostics is published, West's passive dead time
    and its detection's outcome among them (BP6)."""
    smartpi = SmartPI(hass=MagicMock(), cycle_min=5, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS")
    live = smartpi.get_published_diagnostics()
    for name, dotted in LIVE.items():
        data = live
        for key in dotted.split("."):
            assert isinstance(data, dict) and key in data, (name, dotted)
            data = data[key]


_STOPS_LOG = [
    "2026-11-10 21:53:32.104 INFO (MainThread) [custom_components.vtherm_smartpi.algo] East - SmartPI heat stopped with the central boiler (heating dead-time detection aborted)",
    "2026-11-10 22:40:31.000 INFO (MainThread) [custom_components.vtherm_smartpi.algo] East - SmartPI heat stopped with the central boiler",
    "2026-11-10 22:41:00.000 INFO (MainThread) [custom_components.vtherm_smartpi.algo] West - SmartPI heat stopped with the central boiler",
    "2026-11-10 21:54:25.337 INFO (MainThread) [custom_components.vtherm_smartpi.smartpi.deadtime_estimator] SmartPI: Cool Deadtime detected = 845.5s (drop delayed by 57.8s)",
]


def test_the_heat_stops_and_the_passive_dead_times_read_from_smartpi_s_log() -> None:
    """Each thermostat's stops of heat with the boiler, and whether a heating detection was
    dropped; the passive dead times, which name no thermostat."""
    from steps import logged_cool_dead_times, logged_heat_stops  # noqa: PLC0415

    assert logged_heat_stops(_STOPS_LOG, "East") == [(pytest.approx(_at("21:53:32.104")), True), (pytest.approx(_at("22:40:31.000")), False)]
    assert [aborted for _, aborted in logged_heat_stops(_STOPS_LOG, "West")] == [False]
    assert logged_cool_dead_times(_STOPS_LOG) == [(pytest.approx(_at("21:54:25.337")), 845.5)]


def test_bp29_and_bp30_run_on_the_device_count_with_north_off() -> None:
    """BP29 starts learned (West then given its settled state), BP30 fresh; both on the device
    count, North off so that only West stops; BP30's West calls within the 600 s after its reset."""
    from scenarios import LONG_HEAT_S, MIN_OFF_S  # noqa: PLC0415
    from steps import STOPPED  # noqa: PLC0415

    for sid, start in (("BP29", "learned"), ("BP30", "fresh")):
        s = SCENARIOS[sid]
        assert (s.roles, s.start, s.north, s.boiler_at_end) == (("boiler-count",), start, STOPPED, True), sid
    assert LONG_HEAT_S >= 30 * 60.0 > MIN_OFF_S


def test_the_settling_time_the_pack_expects_is_smartpi_s() -> None:
    """The pack's settling time is the estimator's: the heating dead time, and with a published
    lag at least min(3 lags, half the median heating period)."""
    from scenarios import settling_s  # noqa: PLC0415

    from custom_components.vtherm_smartpi.smartpi.deadtime_estimator import DeadTimeEstimator  # noqa: PLC0415

    for lag, median in ((None, None), (780.0, 7200.0), (780.0, 2400.0), (780.0, 1200.0), (780.0, None)):
        est = DeadTimeEstimator()
        est.deadtime_heat_s, est.deadtime_heat_reliable = 780.0, True
        est.active_settle_s = None if lag is None else (3 * lag if median is None else min(3 * lag, median / 2))
        assert settling_s(780.0, lag, median) == est._active_settling_s(VThermHvacMode_HEAT), (lag, median)
    assert settling_s(None, 780.0, 7200.0) is None


def test_bp31_and_bp32_keep_a_cycle_s_margin_around_their_thresholds() -> None:
    """SmartPI counts a heating up to a cycle late: BP31's short heating stays below West's dead time
    (780 s) and its long one above it; BP32's stays above the dead time and below the settling time
    of three lags (2340 s), counted a cycle early or late. Both on the device count, learned,
    North off."""
    from scenarios import BETWEEN_HEAT_S, COUNTED_LATE_S, LEARNED_HEAT_S, SHORT_HEAT_S  # noqa: PLC0415
    from steps import CYCLE_S, STOPPED  # noqa: PLC0415

    dead, settling = 780.0, 3 * 780.0
    assert COUNTED_LATE_S == CYCLE_S
    assert SHORT_HEAT_S + COUNTED_LATE_S <= dead < LEARNED_HEAT_S - COUNTED_LATE_S
    assert dead < BETWEEN_HEAT_S - COUNTED_LATE_S and BETWEEN_HEAT_S + COUNTED_LATE_S < settling
    for sid in ("BP31", "BP32"):
        s = SCENARIOS[sid]
        assert (s.roles, s.start, s.north, s.boiler_at_end) == (("boiler-count",), "learned", STOPPED, True), sid


def test_the_pack_s_pause_confirmation_is_smartpi_s() -> None:
    from custom_components.vtherm_smartpi.boiler_call import BOILER_PAUSE_CONFIRM_S  # noqa: PLC0415

    assert PAUSE_CONFIRM_S == BOILER_PAUSE_CONFIRM_S


def test_bp25_runs_twenty_own_starts_with_a_valve_delay() -> None:
    """BP25: West's own start completes the threshold, 20 times, with the valves reporting after
    15 s, on the device count; it passes on SmartPI's info line of a pause, which must not appear."""
    from scenarios import OWN_START_VALVE_DELAY_S, OWN_STARTS, PAUSED_LINE, own_start  # noqa: PLC0415

    s = SCENARIOS["BP25"]
    count = ROLES["boiler-count"]
    assert (s.roles, s.start, OWN_STARTS, OWN_START_VALVE_DELAY_S) == (("boiler-count",), "fresh", 20, 15.0)
    assert PAUSED_LINE == "West - SmartPI learning paused"  # algo.py, set_boiler_learning_paused
    assert s.time(count).typical_s == pytest.approx(OWN_STARTS * own_start(count).typical_s)
    assert 50 * 60 <= s.time_for(count).typical_s <= 80 * 60
