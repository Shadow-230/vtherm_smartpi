"""The QAS pack's files (tests/qas) agree with each other, and its learned state loads into
SmartPI as the room's learned model."""

from __future__ import annotations

import itertools
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
from pack import AUTHOR_RELEASE, BOILER_SWITCH, ROLES, ROOMS_DOMAIN, WEST_WINDOW, room  # noqa: E402
from scenarios import (  # noqa: E402
    NATURAL_SWITCHES,
    NATURAL_THRESHOLD_W,
    SCENARIOS,
    boiler_back,
    cycle_metrics,
    is_control,
    race_start,
    race_summary,
    took_place,
    west_leaves,
)
from steps import (  # noqa: E402
    EAST,
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
    parse_evaluation,
    pause_mismatches,
    reached,
    snapshot_of,
    spans,
    transitions,
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
    assert {p.name for p in learned_state.STATES.glob("*.json")} == {learned_state.file(r).name for r in learned_state.ROOMS}


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
    """One format for the catalogue: the start state → the event → the expected result; a scenario
    that passes on a measurement says what the measurement is."""
    for scenario in SCENARIOS.values():
        assert len(scenario.parts) == 3 and all(scenario.parts), scenario.id
        assert ("passes on the measurement" in scenario.checks) is scenario.measured, scenario.id


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


def test_the_author_s_release_runs_only_its_scenarios() -> None:
    """With --author-release, the minimum set is BP0 only, and a scenario of the pause is refused."""
    assert run.selected(["M"], "boiler-count", author_release=True) == ["BP0"]
    assert run.selected(["BP0", "BR1", "BR2"], "boiler-count", author_release=True) == ["BP0", "BR1", "BR2"]
    assert run.selected(["BP14"], "boiler-power", author_release=True) == ["BP14"]
    with pytest.raises(SystemExit):
        run.selected(["BP1"], "boiler-count", author_release=True)


def test_each_scenario_s_typical_time_is_within_its_maximum() -> None:
    for scenario in SCENARIOS.values():
        for name in scenario.roles:
            wait = scenario.time_for(ROLES[name])
            assert 0 <= wait.typical_s <= wait.maximum_s, (scenario.id, name)


def test_the_reference_inputs_are_maximum_length_sequences() -> None:
    """West's setpoint and East's call follow pseudo-random binary sequences of maximum length (IEA
    EBC Annex 58, appendix E): each repeats only after 2**n - 1 steps, with 2**(n-1) high steps,
    holds a level for at most n steps, and neither repeats after 24 hours."""
    from scenarios import EAST_SEQUENCE, WEST_SEQUENCE  # noqa: PLC0415

    for sequence in (WEST_SEQUENCE, EAST_SEQUENCE):
        bits = sequence.bits()
        n = sequence.order
        assert len(bits) == 2**n - 1
        assert sum(bits) == 2 ** (n - 1)
        assert all(bits[k:] + bits[:k] != bits for k in range(1, len(bits)))
        assert max(len(list(run)) for _, run in itertools.groupby(bits + bits)) == n
        assert len(bits) * sequence.step_s != 24 * 3600.0


def _reference_run(tmp_path: pathlib.Path, name: str, sid: str, dead_times: tuple[float, float], reliable: bool = True, starts_off: int = 20) -> str:
    samples = [
        {"hours": hours, "deadtime_heat_s": dead_time, "deadtime_heat_reliable": reliable, "a": 0.036, "b": 0.00098, "accepted_updates_a": 1, "accepted_updates_b": 4}
        for hours, dead_time in zip((48.0, 96.0), dead_times, strict=True)
    ]
    path = tmp_path / f"{sid}-{name}.json"
    path.write_text(json.dumps({"scenario": sid, "version": name, "starts": {"boiler_off": starts_off, "boiler_on": 30}, "samples": samples}), encoding="utf-8")
    return str(path)


def test_the_comparison_of_the_reference_runs(tmp_path: pathlib.Path) -> None:
    """run.py --compare: West's heating dead time of both versions at 48 and 96 hours against the
    true 780 s, a and b as auxiliary readings, and the decision rule's two questions."""
    table = run.compare(_reference_run(tmp_path, "qas", "BR1", (880.0, 858.0)), _reference_run(tmp_path, "author", "BR1", (2860.0, 3315.0)))
    assert "| heating dead time, s, at 48 h | 880 (+13%) | 2860 (+267%) |" in table
    assert "| a at the end (96.0 h) (auxiliary) | 0.036 (-10%), 1 updates |" in table
    assert "Valid:" in table
    assert f"At the end (96.0 h): qas's error +10%, {AUTHOR_RELEASE}'s +325%; qas's error not larger: yes; qas within 20%: yes." in table


def test_a_reference_comparison_without_a_reliable_dead_time_is_not_valid(tmp_path: pathlib.Path) -> None:
    table = run.compare(
        _reference_run(tmp_path, "qas", "BR1", (880.0, 880.0), reliable=False),
        _reference_run(tmp_path, "author", "BR1", (3315.0, 3315.0), starts_off=2),
    )
    assert f"Not valid: qas: no reliable heating dead time at the end; author {AUTHOR_RELEASE}: fewer than 6 heating starts with the boiler off." in table
    assert "At 48 h" not in table


def test_the_limit_run_is_shown_not_judged(tmp_path: pathlib.Path) -> None:
    table = run.compare(_reference_run(tmp_path, "qas", "BR2", (880.0, 880.0), reliable=False), _reference_run(tmp_path, "author", "BR2", (6245.0, 6245.0)))
    assert "| heating dead time, s, at the end (96.0 h) | 880 (+13%), not reliable | 6245 (+701%) |" in table
    assert "BR2 measures a limit of the pause: shown, not judged by the rule." in table
    with pytest.raises(SystemExit):
        run.compare(_reference_run(tmp_path, "qas", "BR1", (880.0, 880.0)), _reference_run(tmp_path, "author", "BR2", (880.0, 880.0)))


def test_bp12_takes_the_outcome_of_the_detection_west_s_start_armed() -> None:
    """BP12 (b): an outcome published before West's start armed its detection is not taken; the
    outcome is read once the armed detection has ended."""
    from scenarios import heat_detection  # noqa: PLC0415

    def reading(state: str, outcome: str | None) -> dict:
        return {"west.dt_state": state, "west.heat_outcome": outcome}

    stale = reading("OFF", "aborted")
    waiting = reading("WAITING_HEAT_RESPONSE", "aborted")
    detected = reading("HEATING", "detected")
    assert heat_detection([stale]) == (False, None)
    assert heat_detection([stale, waiting, waiting]) == (True, None)
    assert heat_detection([stale, waiting, waiting, detected]) == (True, "detected")


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
    """A pause with its counters and dead time unchanged and no detection armed, followed by an
    episode that starts after the resume, is clean."""
    episode = datetime.fromtimestamp(1030.0, UTC).isoformat()
    samples = _trace(
        [
            {"west.paused": False, "west.accepted_a": 30, "west.deadtime": 780.0, "west.episode": None},
            {"west.paused": True, "west.accepted_a": 31},
            {},
            {"west.paused": False},
            {"west.episode": episode},
        ]
    )
    assert learned_in_pauses(samples, "west") == (1, [])


def test_what_a_pause_must_not_learn() -> None:
    before = datetime.fromtimestamp(900.0, UTC).isoformat()
    samples = _trace(
        [
            {"west.paused": True, "west.accepted_a": 31, "west.dt_state": "OFF"},
            {"west.accepted_a": 32},
            {"west.dt_state": WAITING_HEAT},
            {"west.paused": False, "west.dt_state": "HEATING"},
            {"west.episode": before},
        ]
    )
    n, problems = learned_in_pauses(samples, "west")
    assert n == 1
    assert any("accepted_a changed" in p for p in problems)
    assert any("detection armed" in p for p in problems)
    assert any("before the resume" in p for p in problems)


def _calls(paused: list[bool], below: bool, opening: float = 100.0) -> list[dict]:
    threshold = {"count": 1.0 if below else 2.0, "count_threshold": 2.0, "power": None, "power_threshold": None}
    return [{"t": 1000.0 + 10.0 * i, "west.paused": p, "west.opening": opening, "west.hvac": "heat"} | threshold for i, p in enumerate(paused)]


def test_the_pause_agrees_with_the_call_and_the_threshold() -> None:
    """Calling below the threshold, SmartPI pauses within two calculations; with the threshold
    reached, or no call, it resumes within one; none of this is due before that."""
    late = 2 * 60 + 20
    assert not pause_mismatches(_calls([False] * 10 + [True] * 10, below=True), "west")
    assert pause_mismatches(_calls([False] * (late // 10 + 3), below=True), "west")
    assert not pause_mismatches(_calls([True] * 5 + [False] * 5, below=False), "west")
    assert pause_mismatches(_calls([True] * 12, below=False), "west")
    assert pause_mismatches(_calls([True] * 12, below=True, opening=0.0), "west")
    assert not pause_mismatches([s | {"west.hvac": "off"} for s in _calls([False] * 30, below=True)], "west")


def test_the_natural_cycle_s_measurement() -> None:
    """BP14: switches and pauses per hour, the shortest whole times on and off, the swing and the
    largest error after the first hour, the overshoot after a boiler start, and what was learned."""
    rows = []
    for i in range(int(7200 / 5)):
        t = 5.0 * i
        on = (t // 600) % 2 == 1  # 10 minutes off, 10 on
        rows.append({"t": t, "boiler": "on" if on else "off", "west.paused": not on, "west.indoor": 20.1 if on else 19.9, "west.accepted_a": 31, "west.a": 0.04})
    m = cycle_metrics(rows, 20.0, settle_s=3600.0)
    assert m["hours"] == pytest.approx(2.0, abs=0.01)
    assert m["boiler_switches_per_hour"] == pytest.approx(5.5, abs=0.1)
    assert m["pauses_per_hour"] == pytest.approx(2.5, abs=0.1)
    assert (m["shortest_on_s"], m["shortest_off_s"]) == (600, 600)
    assert m["swing_c"] == pytest.approx(0.2)
    assert m["overshoot_c"] == pytest.approx(0.1)
    assert m["accepted_a"] == (31, 31)


def test_a_measurement_takes_place_only_when_read_throughout() -> None:
    full = [{"t": 5.0 * i, "west.opening": 40.0} for i in range(721)]
    assert took_place(full, 3600.0)[0]
    assert not took_place(full[:300], 3600.0)[0]
    assert not took_place([s | {"west.opening": None} for s in full], 3600.0)[0]


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


def test_bp14_s_threshold_is_above_west_s_steady_opening() -> None:
    """BP14 needs the valve to cross the power threshold again and again: the threshold is above
    West's steady opening at any setpoint BP14 sets (its room plus 0.3 degC, the room between 19
    and 21 degC), with the outdoor temperature at its lowest; and its measurement needs at least
    4 switches of the boiler."""
    west = room("West")
    outdoor = configuration.configuration(ROLES["boiler-power"])[ROOMS_DOMAIN]["outdoor"] - 0.2  # its lowest: the largest need
    power = ROLES["boiler-power"].device_power_w("West")
    for setpoint in (19.3, 20.3, 21.3):
        steady = west.loss * (setpoint - outdoor) / west.gain  # gain x opening = loss x (indoor - outdoor)
        assert steady * power < NATURAL_THRESHOLD_W < power, (setpoint, steady)
    assert NATURAL_SWITCHES >= 4
    assert "at least 4 times" in SCENARIOS["BP14"].checks


def test_the_typical_times_of_the_own_setpoint_and_the_closing_start_state() -> None:
    """A thermostat's own start pauses a calculation period after its setpoint's own calculation,
    at the next periodic calculation, 1.5 calculations later on average; a learned West that stops resumes a calculation later, a fresh one at once; the start
    state after a scenario that ends with the boiler on waits for it off the role's delay plus
    30 s, and after one that ends with a learned West paused for its resume."""
    assert PAUSE_ON_SETPOINT.typical_s == 1.5 * RECALC_S
    count, delay = ROLES["boiler-count"], ROLES["boiler-delay"]
    assert west_leaves(count).typical_s == RESUME_ON_SETPOINT.typical_s == 0.0
    assert west_leaves(count, learned=True).typical_s == RECALC_S
    assert SCENARIOS["BP3"].time_for(count).typical_s == 1.5 * RECALC_S
    for sid in ("BP0", "BP2", "BP5", "BP7", "BP8", "BP12", "BP18", "BP20", "BP21"):
        s = SCENARIOS[sid]
        for name in s.roles:
            assert s.closing(ROLES[name]).typical_s == ROLES[name].boiler_delay_s + SETTLED_S, (sid, name)
    assert SCENARIOS["BP2"].closing(delay).typical_s == 150.0
    assert SCENARIOS["BP9"].closing(count).typical_s == RECALC_S
    assert SCENARIOS["BP1"].closing(delay).typical_s == 0.0


# --- BP22-BP24: the race at a thermostat's own start --------------------------------------------


def _line(at: str, source: str, below: str, decision: str, name: str = "West") -> str:
    return (
        f"2026-11-04 09:00:00.000 DEBUG (MainThread) [custom_components.vtherm_smartpi.handler] {name} - SmartPI boiler pause "
        f"evaluation: at={at} mono=12.000 source={source} phase=Hysteresis committed=1.000 counted=True count=1.0 "
        f"count_threshold=2.0 power_w=2000.0 power_threshold_w=0.0 reached=False below={below} below_for=0.0 paused=False->False decision={decision}"
    )


def test_a_check_of_the_pause_is_read_from_its_log_line() -> None:
    """The debug line of handler.py gives the thermostat, the time and every key=value."""
    check = parse_evaluation(_line("2026-11-04T09:00:00.250+00:00", "force", "True", "stay_running"))
    assert (check["name"], check["source"], check["below"], check["decision"], check["count"]) == ("West", "force", "True", "stay_running", "1.0")
    assert check["at_s"] == pytest.approx(datetime(2026, 11, 4, 9, 0, 0, 250000, tzinfo=UTC).timestamp())
    assert parse_evaluation("2026-11-04 09:00:00.000 INFO [x] West - SmartPI learning paused (central boiler threshold)") is None


def test_a_check_is_read_without_the_log_s_colours() -> None:
    """``docker logs`` gives Home Assistant's coloured log: the colour codes around the line are
    not part of its last field."""
    line = "\x1b[36m" + _line("2026-11-04T09:00:00.250+00:00", "periodic", "True", "pause") + "\x1b[0m"
    check = parse_evaluation(line)
    assert (check["name"], check["source"], check["decision"]) == ("West", "periodic", "pause")
    record = race_start([check], datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC).timestamp(), [])
    assert record["false_pause"] is True


def test_the_pack_s_pause_confirmation_is_smartpi_s() -> None:
    from custom_components.vtherm_smartpi.boiler_call import BOILER_PAUSE_CONFIRM_S  # noqa: PLC0415

    assert PAUSE_CONFIRM_S == BOILER_PAUSE_CONFIRM_S


def _at(seconds: float) -> str:
    return datetime.fromtimestamp(1000.0 + seconds, UTC).isoformat()


def test_a_start_s_record() -> None:
    """From the checks and the snapshots: a false pause at the second check below the threshold,
    held until the resume, VTherm counting West and starting the boiler after the valve reported."""
    checks = [
        parse_evaluation(_line(_at(-30.0), "periodic", "False", "stay_running")),
        parse_evaluation(_line(_at(0.002), "force", "True", "stay_running")),
        parse_evaluation(_line(_at(1.0), "other", "True", "pause")),
        parse_evaluation(_line(_at(60.0), "periodic", "False", "resume")),
    ]
    snapshots = [
        {"count": 1.0, "count_threshold": 2.0, "power": None, "power_threshold": None, "boiler": "off", "west.valve": 0.0, "west.dt_state": "OFF"},
        {"count": 2.0, "count_threshold": 2.0, "power": None, "power_threshold": None, "count_since": _at(2.004), "boiler": "on", "boiler_since": _at(2.01), "west.valve": 100.0, "west.valve_since": _at(2.0), "west.dt_state": "OFF"},
    ]
    record = race_start(checks, 1000.0, snapshots)
    assert record["setpoint_at"] == "1970-01-01T00:16:40.000+00:00"
    assert [c["source"] for c in record["checks"]] == ["force", "other", "periodic"]
    assert (record["valve_reported_s"], record["counted_s"], record["boiler_on_s"]) == (2.0, 2.004, 2.01)
    assert (record["checks_in_window"], record["extra_in_window"]) == (2, None)
    assert (record["false_pause"], record["pause_at_s"], record["pause_s"], record["pause_still_on"], record["detection_armed"]) == (True, 1.0, 59.0, False, False)
    # BP24: the extra check counts only before VTherm counted West, and by its own source
    assert race_start(checks, 1000.0, snapshots, "sensor")["extra_in_window"] is True
    assert race_start(checks, 1000.0, snapshots, "periodic")["extra_in_window"] is False


def test_the_race_s_summary_for_each_combination() -> None:
    def rec(learning: str, delay: float, pause_s: float | None, extra: str = "none", hit: bool | None = None) -> dict:
        return {
            "learning": learning, "valve_delay_s": delay, "extra": extra, "checks_in_window": 2 if pause_s or hit else 1, "extra_in_window": hit,
            "false_pause": pause_s is not None, "pause_s": pause_s, "pause_still_on": False, "detection_armed": pause_s is None, "counted_s": delay + 0.003,
        }  # fmt: skip

    lines = race_summary([rec("fresh", 0.0, None), rec("fresh", 0.0, None), rec("fresh", 2.0, 59.0), rec("fresh", 2.0, None)])
    assert lines[0] == (
        "West fresh, valve delay 0 s, extra check none: 2 starts, a second check before VTherm counted West in 0, false pause in 0; "
        "detection armed in 2; VTherm counted West 0.003–0.003 s after the setpoint"
    )
    assert lines[1].startswith("West fresh, valve delay 2 s, extra check none: 2 starts, a second check before VTherm counted West in 1, false pause in 1 (held 59.0–59.0 s, median 59.0 s)")
    lines = race_summary([rec("learned", 0.5, None, "periodic", True), rec("learned", 0.5, None, "periodic", False)])
    assert lines == [
        (
            "West learned, valve delay 0.5 s, extra check periodic: 2 starts, a second check before VTherm counted West in 1 (the extra check in 1), "
            "false pause in 0; detection armed in 2; VTherm counted West 0.503–0.503 s after the setpoint"
        )
    ]


def test_the_race_s_roles_and_delays() -> None:
    """BP22-BP24 run on the three roles, `boiler-power` as the control run; BP24 measures only
    the valve delays above 0 s; `run.py --list` gives the time of each role and each delay; each
    ends with the boiler on, so the start state after it waits for the boiler off."""
    for sid in ("BP22", "BP23", "BP24"):
        assert SCENARIOS[sid].roles == ("boiler-count", "boiler-power", "boiler-delay")
        assert SCENARIOS[sid].closing(ROLES["boiler-delay"]).typical_s == 150.0
        assert "control run on `boiler-power`" in SCENARIOS[sid].checks or "On `boiler-power` it is a control run" in SCENARIOS[sid].checks
    assert [name for name in ROLES if is_control(ROLES[name]) and name in SCENARIOS["BP22"].roles] == ["boiler-power"]
    assert list(SCENARIOS["BP22"].by_delay(ROLES["boiler-count"])) == [0.0, 0.5, 2.0, 5.0, 15.0]
    assert list(SCENARIOS["BP24"].by_delay(ROLES["boiler-count"])) == [0.5, 2.0, 5.0, 15.0]
    for sid in ("BP22", "BP23", "BP24"):
        for name in SCENARIOS[sid].roles:
            parts = SCENARIOS[sid].by_delay(ROLES[name]).values()
            whole = SCENARIOS[sid].time(ROLES[name])
            assert sum(w.typical_s for w in parts) == pytest.approx(whole.typical_s)
            assert sum(w.maximum_s for w in parts) == pytest.approx(whole.maximum_s)
    listing = run.listing().splitlines()
    at = listing.index(next(line for line in listing if line.startswith("BP24")))
    assert [line.split()[2] for line in listing[at + 2 : at + 6]] == ["0.5", "2", "5", "15"]
