"""The QAS test pack (tests/qas) as files: what CI can check without a Home Assistant to test.

- nothing under tests/qas is collected by pytest;
- each role's configuration.yaml is what roles.py gives, and the simulator accepts it;
- the runner, the setup and the scenarios compile, and import without the bench's lib/;
- every scenario of the plan is registered, with its catalogue entry and a one-sentence
  expectation, and the catalogue in documentation/en/testing.md lists them all;
- the runner's base compares the stored neighbours and zone by what they mean;
- compare.py judges QB1 and QE5 as the plan says, from the two results files it is given;
- the seeded state is one SmartPI loads;
- each scenario says what it does to SmartPI's learned state and the phase it needs; the runner
  lists both, runs the learned set (L) in its order, and reports a scenario NOT RUN when SmartPI is
  not in its phase, without calling the harness.
"""

from __future__ import annotations

import json
import pathlib
import py_compile
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
QAS = ROOT / "tests" / "qas"
for path in (QAS, QAS / "setup"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import compare  # noqa: E402
import run  # noqa: E402
from scenarios import ql  # noqa: E402
from scenarios.registry import ANY, FRESH, LEARNED, SEED, Phase, State, changes  # noqa: E402
from config.build import configuration, render  # noqa: E402
from roles import ROLES  # noqa: E402
from scenarios import PLANNED, SCENARIOS  # noqa: E402

from custom_components.smartpi_room_sim import CONFIG_SCHEMA  # noqa: E402
from custom_components.smartpi_room_sim.engine import spec_from_config  # noqa: E402

PLAN_IDS = (
    "QA1 QA2 QA3 QA4 QA5 QA6 QB1 QB2 QB3 QC1 QC2 QC3 QC4 "
    "QD1 QD2 QD3 QD4 QD5 QD6 QE1 QE2 QE3 QE4 QE5 QE6 QE7 "
    "QW1 QW2 QW3 QW4 QW5 QW6 QW7 QW8 QW9 QW10 "
    "QL0 QL1 QL2 QL3 QL4 QL5"
).split()
# The learned set ("L"): QL0 loads the models, the others need them; QL2 and QL4 run detached and
# their short variants are in the set.
PLAN_LEARNED = "QL0 QL1 QL2s QL3 QL4s QL5".split()
# The plan's minimum set (§5, "M"); QC2's minimum is its short variant QC2s, QB2's needs QB2a,
# QW1's is QW1s.
PLAN_MINIMUM = (
    "QA1 QA2 QA4 QA5 QA6 QB2a QB2 QC1 QC2s QC3 QD1 QD2 QD3 QD4 QE1 QE2 QE3 QE4 "
    "QW1s QW2 QW3 QW4 QW5 QW6 QW7 QW8 QW9 QW10"
).split()
TESTING_DOC = ROOT / "documentation" / "en" / "testing.md"


def test_nothing_in_the_pack_is_collected_by_pytest() -> None:
    names = [p.name for p in QAS.rglob("*.py")]
    assert not [n for n in names if n.startswith("test_") or n.endswith("_test.py") or n == "conftest.py"]


def test_the_role_files_are_up_to_date_and_valid() -> None:
    for role in ROLES.values():
        path = QAS / "config" / f"{role.name}.yaml"
        assert path.read_text(encoding="utf-8") == render(role), f"{path.name}: run tests/qas/config/build.py"
        config = CONFIG_SCHEMA({"smartpi_room_sim": configuration(role)["smartpi_room_sim"]})
        spec_from_config(config["smartpi_room_sim"])
        assert "initial" not in json.dumps(configuration(role).get("input_number", {}))
    # A role names no instance: the testing session chooses where it runs.
    assert not any(hasattr(role, "instance") for role in ROLES.values())
    assert {"water", "electric"} <= set(ROLES)


def test_the_pack_compiles() -> None:
    for path in QAS.rglob("*.py"):
        py_compile.compile(str(path), doraise=True)


def test_every_scenario_of_the_plan_is_registered() -> None:
    assert set(PLAN_IDS) <= set(SCENARIOS), sorted(set(PLAN_IDS) - set(SCENARIOS))
    for scenario in SCENARIOS.values():
        assert set(scenario.roles) <= set(ROLES), scenario.id
        doc = scenario.doc
        assert doc.endswith(".") and "\n" not in doc and not re.search(r"\.\s+[A-Z]", doc), (scenario.id, doc)
        for long_id in scenario.short_of:
            assert SCENARIOS[long_id].minutes > scenario.minutes, scenario.id
    minimum = {s.id for s in SCENARIOS.values() if s.minimum}
    assert set(PLAN_MINIMUM) <= minimum, sorted(set(PLAN_MINIMUM) - minimum)
    for scenario in SCENARIOS.values():
        if scenario.detached:
            assert any(scenario.id in s.short_of for s in SCENARIOS.values()), f"{scenario.id} has no short variant"


def test_the_catalogue_lists_every_scenario() -> None:
    text = TESTING_DOC.read_text(encoding="utf-8")
    section = text[text.index("## 6. QAS on a real Home Assistant") :]
    for sid in [*SCENARIOS, *(p.id for p in PLANNED)]:
        assert re.search(rf"^\| {re.escape(sid)} ", section, re.M), f"{sid} is not in the catalogue"
    assert run.catalogue() in section, "the catalogue's rows differ from the registry: paste `run.py --list --markdown`"


def test_the_minimum_set_of_each_role() -> None:
    for role in ROLES.values():
        ids = run.expand(["M"], role)
        assert all(SCENARIOS[i].minimum and role.name in SCENARIOS[i].roles for i in ids)
    assert "QZ1" in run.expand(["M"], ROLES["base"])
    with pytest.raises(SystemExit):
        run.expand(["QC1"], ROLES["base"])


def test_the_learned_set_of_each_role() -> None:
    """L: QL0 first (it loads the models), then what keeps the state, then what changes it."""
    learned = {s.id for s in SCENARIOS.values() if s.learned_set}
    assert learned == set(PLAN_LEARNED)
    assert not learned & {s.id for s in SCENARIOS.values() if s.minimum}
    assert run.expand(["L"], ROLES["base"]) == ["QL0", "QL3", "QL2s"]
    water = run.expand(["L"], ROLES["water"])
    assert water[0] == "QL0" and set(water) == {"QL0", "QL1", "QL3", "QL4s", "QL5"}
    assert run.expand(["L"], ROLES["zone"]) == []
    # Every scenario needing a learned SmartPI runs where QL0 loads the models.
    for scenario in SCENARIOS.values():
        if scenario.phase.need == LEARNED:
            assert set(scenario.roles) <= set(SCENARIOS["QL0"].roles) == set(ql.LEARNED_MODELS), scenario.id
    for models in ql.LEARNED_MODELS.values():
        for _climate, fixture, _zone in models:
            assert (QAS / "custom_components/smartpi_qas_helpers/fixtures" / f"{fixture}.json").is_file()


def test_each_scenario_says_what_it_does_to_the_learned_state_and_the_phase_it_needs() -> None:
    for scenario in SCENARIOS.values():
        assert isinstance(scenario.state, State) and isinstance(scenario.phase, Phase), scenario.id
        assert scenario.state.label in run.listing(None)
    # A scenario that loads a learned state says so; a long learning run starts fresh.
    for sid in ("QB2a", "QD2", "QW3", "QW4", "QL0"):
        assert SEED in SCENARIOS[sid].state.changes, sid
    for sid in ("QB1", "QB1s", "QC4", "QD5", "QD6", "QE5", "QE5s", "QW1"):
        assert SCENARIOS[sid].phase.need == FRESH, sid
    assert changes(SEED).label == "changes: seed" and State().label == "keeps"
    assert changes("calibration", "reset", restored=True).label == "changes: calibration, reset (put back)"
    with pytest.raises(ValueError):
        changes("something else")
    with pytest.raises(ValueError):
        Phase(FRESH, zone_model=True)


def _live(phase: str | None, a: float | None = 0.03, published: bool | None = None) -> dict:
    data: dict = {"control": {"phase": phase}, "model": {"a": a, "b": 0.001}}
    if published is not None:
        data["dependencies"] = {"second_source": {"zone_fit": {"published": published}}}
    return data


def test_the_phase_a_scenario_needs() -> None:
    """Fresh: still learning (Hysteresis); learned: Stable with a and b (and the zone's model
    published where asked); a calibration or missing diagnostics meet neither."""
    assert Phase(ANY).problem({"Living": {}}) is None
    assert Phase(FRESH).problem({"Living": _live("Hysteresis")}) is None
    assert "fresh" in Phase(FRESH).problem({"Living": _live("Stable")})
    assert Phase(LEARNED).problem({"Living": _live("Stable")}) is None
    assert "QL0" in Phase(LEARNED).problem({"Living": _live("Hysteresis")})
    assert "QL0" in Phase(LEARNED).problem({"Living": _live("Stable", a=None)})
    assert "calibration" in Phase(LEARNED).problem({"Living": _live("Calibration")})
    assert "no SmartPI diagnostics" in Phase(FRESH).problem({"Living": {}})
    zone = Phase(LEARNED, zone_model=True)
    lives = {"Studio": _live("Stable"), "Lounge radiator": _live("Stable", published=False)}
    assert "zone's model" in zone.problem(lives, zone_main="Lounge radiator")
    lives["Lounge radiator"] = _live("Stable", published=True)
    assert zone.problem(lives, zone_main="Lounge radiator") is None
    two = Phase(FRESH).problem({"Studio": _live("Stable"), "Nursery": _live("Hysteresis")})
    assert two.startswith("Studio:") and "Nursery" not in two


def test_the_base_compares_the_neighbours_by_what_they_mean() -> None:
    names = {"r1": "climate.hall", "r2": "binary_sensor.smartpi_room_sim_kitchen_door", "r3": "climate.dining"}
    expected = run.Dependencies(run.expected_dependencies(ROLES["neighbours"], "Kitchen"), names)
    stored = [
        {"id": "x1", "kind": "neighbour", "entity": "r3", "mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"},
        {"id": "x2", "kind": "neighbour", "entity": "r1", "mode": "door", "door_sensors": ["r2"], "neighbour_window_action": "nothing"},
    ]
    assert not stored != expected
    stored[1]["neighbour_window_action"] = "eco"
    assert stored != expected
    assert run.Dependencies([], names) == []


def test_compare_reads_the_two_results_files_it_is_given(tmp_path, capsys) -> None:
    new, ref = {"mean_error_second_half": 0.03}, {"mean_error_second_half": 0.04}
    (tmp_path / "qb1s-instance3.json").write_text(json.dumps(new))
    (tmp_path / "other.json").write_text(json.dumps(ref))
    assert compare.main(["qb1s", "3", str(tmp_path / "other.json"), str(tmp_path)]) == 0
    assert compare.main(["qb1s", "3", "9", str(tmp_path)]) == 2
    assert "qb1s-instance9.json" in capsys.readouterr().out
    assert compare.main(["qb1"]) == 2


def test_compare_judges_the_pairs() -> None:
    good = compare.judge("qb1", {"a": 0.035, "b": 0.0012, "mean_error_second_half": 0.05}, {"a": 0.034, "b": 0.00115, "mean_error_second_half": 0.03})
    assert all(ok for ok, _ in good)
    bad = compare.judge("qb1", {"a": 0.040, "b": 0.0012, "mean_error_second_half": 0.10}, {"a": 0.034, "b": 0.0012, "mean_error_second_half": 0.03})
    assert [ok for ok, _ in bad] == [False, True, False]
    zone = compare.judge(
        "qe5",
        {"mean_error_second_half": 0.03, "floor_share": 0.4, "fit_published_h": 20.0, "fit_published_after_restart": True},
        {"mean_error_second_half": 0.06},
    )
    assert all(ok for ok, _ in zone)


def test_the_seeded_state_is_one_smartpi_loads() -> None:
    from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: PLC0415

    payload = json.loads((QAS / "custom_components/smartpi_qas_helpers/fixtures/learned_room.json").read_text())
    algo = SmartPI(None, cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Living")
    algo.load_state(payload)
    assert algo.est.a == pytest.approx(0.035, rel=0.05)
    assert algo.est.b == pytest.approx(0.0012, rel=0.05)


STUB_HARNESS = '''
from dataclasses import dataclass, field
CALLS = []

@dataclass
class Pack:
    domain: str
    base: dict = field(default_factory=dict)
    role_key: str = ""
    multi_entry: bool = False

async def main(pack, scenarios, ids, reset=None, role=None):
    CALLS.append((pack, dict(scenarios), list(ids), reset, role))

def results_file(pack):
    import os, pathlib
    return pathlib.Path(os.environ["TESTBED_RESULTS"])

def tested_commit():
    return "vtherm_smartpi abc1234"
'''
STUB_CLIENT = '''
class HA:
    def __init__(self, domain):
        self.domain = domain
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return None
    async def ws_cmd(self, payload):
        if payload["type"] == "config/device_registry/list":
            return []
        return [
            {"id": "r-hall", "entity_id": "climate.hall"},
            {"id": "r-dining", "entity_id": "climate.dining"},
            {"id": "r-living", "entity_id": "climate.living", "unique_id": "u-living", "platform": "versatile_thermostat"},
            {"id": "r-diag", "entity_id": "sensor.living_smartpi", "unique_id": "smartpi_diag_u-living", "platform": "vtherm_smartpi"},
        ]
    async def state(self, entity_id):
        import os
        if entity_id != "sensor.living_smartpi":
            return None
        return {"state": "ok", "attributes": {"live": {"control": {"phase": os.environ["QAS_STUB_PHASE"]}, "model": {"a": 0.035, "b": 0.0012}}}}
'''


@pytest.fixture
def stub_bench(tmp_path, monkeypatch):
    """A stand-in for the bench's lib/ (its harness and its client); nothing connects anywhere."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "harness.py").write_text(STUB_HARNESS)
    (lib / "ha_client.py").write_text(STUB_CLIENT)
    monkeypatch.setenv("TESTBED_ROOT", str(tmp_path))
    monkeypatch.setenv("TESTBED_RESULTS", str(tmp_path / "results.md"))
    monkeypatch.setenv("HA_INSTANCE", "2")
    for name in ("harness", "ha_client"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield tmp_path
    for name in ("harness", "ha_client"):
        sys.modules.pop(name, None)
    if str(lib) in sys.path:
        sys.path.remove(str(lib))


def test_the_runner_hands_the_harness_its_scenarios_and_base(stub_bench) -> None:
    """run.main builds the named role's base and gives the harness the role's minimum set, one
    scenario at a time."""
    with pytest.raises(SystemExit):
        run.main(["M"])  # the role is named, never taken from the instance
    assert run.main(["--role", "neighbours", "M"]) == 0
    import harness  # noqa: PLC0415 — the stand-in

    ids = run.expand(["M"], ROLES["neighbours"])
    assert [call[2] for call in harness.CALLS] == [[sid] for sid in ids]
    pack, scenarios, _ids, reset, role = harness.CALLS[-1]
    assert role == "neighbours" and pack.multi_entry and pack.domain == "vtherm_smartpi"
    assert set(scenarios) == set(_ids)
    base = pack.base["neighbours"]
    assert set(base) == {"SmartPI defaults", "Kitchen"}
    assert base["Kitchen"]["smart_pi_heating_type"] == "radiator"
    assert base["Kitchen"]["smart_pi_dependencies"].expected[0][1] in ("climate.dining", "climate.hall")


def test_a_scenario_whose_phase_is_not_met_is_not_run(stub_bench, monkeypatch) -> None:
    """QL3 needs the living room learned: in Hysteresis it is NOT RUN, with the reason, in the
    results file as the harness writes a result, and the harness is not called; in Stable it runs."""
    monkeypatch.setenv("QAS_STUB_PHASE", "Hysteresis")
    assert run.main(["--role", "base", "QL3"]) == 0
    import harness  # noqa: PLC0415 — the stand-in

    assert harness.CALLS == []
    text = (stub_bench / "results.md").read_text(encoding="utf-8")
    assert "### QL3 — NOT RUN (" in text and SCENARIOS["QL3"].doc in text
    assert "-      commit vtherm_smartpi abc1234" in text
    assert "Living: phase Hysteresis, the scenario needs a learned SmartPI (run QL0 first)" in text

    monkeypatch.setenv("QAS_STUB_PHASE", "Stable")
    assert run.main(["--role", "base", "QL3"]) == 0
    assert [call[2] for call in harness.CALLS] == [["QL3"]]
    assert text == (stub_bench / "results.md").read_text(encoding="utf-8")


def test_the_water_role_s_base_holds_its_sources() -> None:
    """The role water's base: the boiler's signals in the defaults, each thermostat's sources and
    the studio's supply sensor, compared by entity id (a form stores a registry id)."""
    import asyncio  # noqa: PLC0415

    names = {"r-supply": "sensor.smartpi_room_sim_studio_radiator_supply", "r-heat": "binary_sensor.smartpi_room_sim_boiler_heat_available"}

    class _HA:
        def __init__(self, _domain):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def ws_cmd(self, _payload):
            return [{"id": key, "entity_id": value} for key, value in names.items()]

    module = type(sys)("ha_client")
    module.HA = _HA
    sys.modules["ha_client"] = module
    try:
        base = asyncio.run(run.build_base(ROLES["water"]))
    finally:
        sys.modules.pop("ha_client", None)
    assert base["SmartPI defaults"]["smart_pi_heat_available_entity"] == "r-heat"
    studio = base["Studio"]
    assert studio["smart_pi_water_source"] == "entities"
    assert studio["smart_pi_supply_entity"] == "r-supply"
    assert not studio["smart_pi_supply_entity"] != "sensor.smartpi_room_sim_studio_radiator_supply"
    assert studio["smart_pi_boiler_call_source"] == "provider:qas"
    assert base["Nursery"]["smart_pi_water_source"] == "provider:qas"
    # The zone's additional source chooses its water only.
    assert "smart_pi_boiler_call_source" not in base["Lounge floor"]
