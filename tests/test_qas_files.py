"""The QAS pack's files (tests/qas) agree with each other, and its learned state loads into
SmartPI as the room's learned model."""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

QAS = pathlib.Path(__file__).resolve().parent / "qas"
if str(QAS) not in sys.path:
    sys.path.insert(0, str(QAS))

import configuration  # noqa: E402
import learned_state  # noqa: E402
import run  # noqa: E402
from pack import ROLES  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: E402
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT  # noqa: E402
from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase  # noqa: E402


@pytest.mark.parametrize("name", sorted(ROLES))
def test_each_role_s_configuration_is_up_to_date(name: str) -> None:
    path = configuration.CONFIG / f"{name}.yaml"
    assert path.read_text(encoding="utf-8") == configuration.text(ROLES[name]), "run python tests/qas/configuration.py"


def test_no_other_role_configuration() -> None:
    assert {p.stem for p in configuration.CONFIG.glob("*.yaml")} == set(ROLES)


def test_the_learned_state_is_up_to_date() -> None:
    assert learned_state.FILE.read_text(encoding="utf-8") == learned_state.text(), "run python tests/qas/learned_state.py"


def test_the_learned_state_loads_as_the_room_s_model() -> None:
    """Loaded into SmartPI, the state gives the room's a and b, its dead times reliable, and SmartPI
    past its first phase at its first calculation."""
    model = learned_state.model()
    smartpi = SmartPI(hass=MagicMock(), cycle_min=5, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS")
    smartpi.load_state(json.loads(learned_state.FILE.read_text(encoding="utf-8")))

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
        for name in scenario.counterpart.split(", "):
            assert (tests / name).is_file(), f"{scenario.id}: no {name}"


def test_nothing_in_the_pack_is_collected_by_pytest() -> None:
    assert not list(QAS.rglob("test_*.py"))


def test_the_author_s_release_runs_only_its_scenarios() -> None:
    """With --author-release, the minimum set is BP0 only, and a scenario of the pause is refused."""
    assert run.selected(["M"], "boiler-count", author_release=True) == ["BP0"]
    assert run.selected(["BP0", "BR1"], "boiler-count", author_release=True) == ["BP0", "BR1"]
    with pytest.raises(SystemExit):
        run.selected(["BP1"], "boiler-count", author_release=True)


def test_each_scenario_s_typical_time_is_within_its_maximum() -> None:
    for scenario in SCENARIOS.values():
        assert 0 <= scenario.time.typical_s <= scenario.time.maximum_s, scenario.id


def test_the_comparison_of_the_reference_runs(tmp_path: pathlib.Path) -> None:
    """run.py --compare: a, b and the dead times of both runs at the end, with their ratio to the true values."""
    from scenarios import reference_truth  # noqa: PLC0415

    def kept(name: str, a: float) -> str:
        samples = [{"a": a, "b": 0.001, "deadtime_heat_s": 780.0, "deadtime_cool_s": 600.0}, {"a": a / 2, "b": 0.0011, "deadtime_heat_s": 2400.0, "deadtime_cool_s": 600.0}]
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"version": name, "truth": reference_truth(), "samples": samples}), encoding="utf-8")
        return str(path)

    table = run.compare(kept("qas", 0.04), kept("author", 0.03))
    assert "| a | 0.04 | 0.02 (0.50) | 0.02–0.04 | 0.015 (0.38) | 0.015–0.03 |" in table
    assert "| heating dead time, s | 780.0 | 2400.0 (3.08) |" in table
