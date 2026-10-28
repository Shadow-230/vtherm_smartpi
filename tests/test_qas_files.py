"""The QAS pack's files (tests/qas) agree with each other, and its learned state loads into
SmartPI as the room's learned model."""

from __future__ import annotations

import itertools
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
    assert run.selected(["BP0", "BR1", "BR2"], "boiler-count", author_release=True) == ["BP0", "BR1", "BR2"]
    with pytest.raises(SystemExit):
        run.selected(["BP1"], "boiler-count", author_release=True)


def test_each_scenario_s_typical_time_is_within_its_maximum() -> None:
    for scenario in SCENARIOS.values():
        assert 0 <= scenario.time.typical_s <= scenario.time.maximum_s, scenario.id


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
    assert "| a at 96 h (auxiliary) | 0.036 (-10%), 1 updates |" in table
    assert "Valid:" in table
    assert "At 96 h: qas's error +10%, 0.5.2's +325%; qas's error not larger: yes; qas within 20%: yes." in table


def test_a_reference_comparison_without_a_reliable_dead_time_is_not_valid(tmp_path: pathlib.Path) -> None:
    table = run.compare(
        _reference_run(tmp_path, "qas", "BR1", (880.0, 880.0), reliable=False),
        _reference_run(tmp_path, "author", "BR1", (3315.0, 3315.0), starts_off=2),
    )
    assert "Not valid: qas: no reliable heating dead time at the end; author 0.5.2: fewer than 6 heating starts with the boiler off." in table
    assert "At 48 h" not in table


def test_the_limit_run_is_shown_not_judged(tmp_path: pathlib.Path) -> None:
    table = run.compare(_reference_run(tmp_path, "qas", "BR2", (880.0, 880.0), reliable=False), _reference_run(tmp_path, "author", "BR2", (6245.0, 6245.0)))
    assert "| heating dead time, s, at 96 h | 880 (+13%), not reliable | 6245 (+701%) |" in table
    assert "BR2 measures a limit of the pause: shown, not judged by the rule." in table
    with pytest.raises(SystemExit):
        run.compare(_reference_run(tmp_path, "qas", "BR1", (880.0, 880.0)), _reference_run(tmp_path, "author", "BR2", (880.0, 880.0)))
