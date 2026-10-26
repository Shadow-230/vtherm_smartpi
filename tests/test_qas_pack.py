"""The QAS test pack (tests/qas): its files agree with each other, and its learned states load into
SmartPI as learned models of their rooms."""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

QAS = pathlib.Path(__file__).resolve().parent / "qas"
for path in (QAS, QAS / "setup"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_fixtures  # noqa: E402
import run  # noqa: E402
from config.build import render  # noqa: E402
from roles import ROLES  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: E402
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT  # noqa: E402
from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase  # noqa: E402

CATALOGUE_START = "<!-- catalogue:"
CATALOGUE_END = "<!-- end of catalogue -->"


@pytest.mark.parametrize("name", sorted(ROLES))
def test_each_role_s_configuration_is_up_to_date(name: str) -> None:
    """config/<role>.yaml is what config/build.py writes from roles.py."""
    path = QAS / "config" / f"{name}.yaml"
    assert path.read_text(encoding="utf-8") == render(ROLES[name]), f"run python tests/qas/config/build.py ({path.name})"


def test_no_stale_role_configuration() -> None:
    assert {p.stem for p in (QAS / "config").glob("*.yaml")} == set(ROLES)


def test_the_catalogue_is_the_registry_s() -> None:
    """The catalogue in tests/qas/README.md lists the registered scenarios, as run.py writes it."""
    text = (QAS / "README.md").read_text(encoding="utf-8")
    block = text[text.index(CATALOGUE_START) : text.index(CATALOGUE_END)]
    rows = [line for line in block.splitlines() if line.startswith("| Q")]
    assert "\n".join(rows) == run.catalogue(), "run python tests/qas/run.py --list --markdown"


def test_each_scenario_names_known_roles_and_an_existing_counterpart() -> None:
    tests = pathlib.Path(__file__).resolve().parent
    for scenario in SCENARIOS.values():
        assert set(scenario.roles) <= set(ROLES), scenario.id
        assert scenario.doc, f"{scenario.id}: no docstring"
        for twin in scenario.twin.split(", "):
            assert (tests / twin).is_file(), f"{scenario.id}: no {twin}"


def test_nothing_in_the_pack_is_collected_by_pytest() -> None:
    assert not list(QAS.rglob("test_*.py"))


@pytest.mark.parametrize("room", sorted(build_fixtures.rooms()))
def test_each_learned_state_loads_as_its_room_s_model(room: str) -> None:
    """A fixture loaded into SmartPI gives a learned model: the room's a and b, its dead times
    reliable, and SmartPI out of its first phase (hysteresis) at its first calculation."""
    model = build_fixtures.rooms()[room]
    path = build_fixtures.FIXTURES / f"learned_{room}.json"
    smartpi = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS"
    )
    smartpi.load_state(json.loads(path.read_text(encoding="utf-8")))

    smartpi.calculate(target_temp=20.0, current_temp=19.5, ext_current_temp=3.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)

    assert smartpi.est.a == pytest.approx(model["a"])
    assert smartpi.est.b == pytest.approx(model["b"])
    assert smartpi.dt_est.deadtime_heat_reliable and smartpi.dt_est.deadtime_cool_reliable
    assert smartpi.dt_est.deadtime_heat_s == pytest.approx(model["deadtime_s"])
    assert smartpi.phase != SmartPIPhase.HYSTERESIS


def test_every_room_has_its_learned_state() -> None:
    names = {p.stem for p in build_fixtures.FIXTURES.glob("*.json")}
    assert names == {f"learned_{room}" for room in build_fixtures.rooms()}
