"""The QAS test pack (tests/qas) as files: what CI can check without a Home Assistant to test.

- nothing under tests/qas is collected by pytest;
- each role's configuration.yaml is what roles.py gives, and the simulator accepts it;
- the runner, the setup and the scenarios compile, and import without the bench's lib/;
- every scenario of the plan is registered, with its catalogue entry and a one-sentence
  expectation, and the catalogue in documentation/en/testing.md lists them all;
- the runner's base compares the stored neighbours and zone by what they mean;
- compare.py judges QB1 and QE5 as the plan says;
- the seeded state is one SmartPI loads.
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
from config.build import configuration, render  # noqa: E402
from roles import INSTANCE_ROLES, ROLES  # noqa: E402
from scenarios import PLANNED, SCENARIOS  # noqa: E402

from custom_components.smartpi_room_sim import CONFIG_SCHEMA  # noqa: E402
from custom_components.smartpi_room_sim.engine import spec_from_config  # noqa: E402

PLAN_IDS = (
    "QA1 QA2 QA3 QA4 QA5 QA6 QB1 QB2 QB3 QC1 QC2 QC3 QC4 "
    "QD1 QD2 QD3 QD4 QD5 QD6 QE1 QE2 QE3 QE4 QE5 QE6 QE7"
).split()
# The plan's minimum set (§5, "M"); QC2's minimum is its short variant QC2s, QB2's needs QB2a.
PLAN_MINIMUM = "QA1 QA2 QA4 QA5 QA6 QB2a QB2 QC1 QC2s QC3 QD1 QD2 QD3 QD4 QE1 QE2 QE3 QE4".split()
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
    assert sorted(INSTANCE_ROLES) == list(range(1, 9))


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
        return [{"id": "r-hall", "entity_id": "climate.hall"}, {"id": "r-dining", "entity_id": "climate.dining"}]
'''


def test_the_runner_hands_the_harness_its_scenarios_and_base(tmp_path, monkeypatch) -> None:
    """With a stand-in for the bench's lib/, run.main builds the role's base and gives the
    harness the role's minimum set; nothing connects anywhere."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "harness.py").write_text(STUB_HARNESS)
    (lib / "ha_client.py").write_text(STUB_CLIENT)
    monkeypatch.setenv("TESTBED_ROOT", str(tmp_path))
    monkeypatch.setenv("HA_INSTANCE", "2")
    for name in ("harness", "ha_client"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    assert run.main(["M"]) == 0
    import harness  # noqa: PLC0415 — the stand-in

    pack, scenarios, ids, reset, role = harness.CALLS[-1]
    assert role == "neighbours" and pack.multi_entry and pack.domain == "vtherm_smartpi"
    assert ids == run.expand(["M"], ROLES["neighbours"]) and set(scenarios) == set(ids)
    base = pack.base["neighbours"]
    assert set(base) == {"SmartPI defaults", "Kitchen"}
    assert base["Kitchen"]["smart_pi_heating_type"] == "radiator"
    assert base["Kitchen"]["smart_pi_dependencies"].expected[0][1] in ("climate.dining", "climate.hall")
    for name in ("harness", "ha_client"):
        sys.modules.pop(name, None)
    sys.path.remove(str(lib))
