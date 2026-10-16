"""A zone's recovery after a setback: the setback seen from the day's setpoint, and the fast
recovery, where the additional source carries the zone's load at the lower setpoint."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_COOL, VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import (
    SOURCE_SETBACK_MEMORY_S,
    ZONE_RECOVERY_DEFAULTS,
    ZONE_RECOVERY_ECONOMY,
    ZONE_RECOVERY_FAST,
)
from custom_components.vtherm_smartpi.smartpi.dependencies import (
    KEY_ZONE_RECOVERY,
    KIND_SOURCE,
    DependencyItem,
    DependencyState,
)
from custom_components.vtherm_smartpi.smartpi.dependency_inputs import DependencyReadings, SourceObservation
from custom_components.vtherm_smartpi.smartpi.dependency_model import DependencyModel, ModelContext
from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings
from custom_components.vtherm_smartpi.smartpi.zone_fit import ZoneFitResult

SPLIT = {
    "split_rule": "lead_band",
    "lead_band_percent": 20,
    "lead_emitter": "radiator",
    "source_emitter": "floor_water",
}
HOUR = 3600.0


def _item(recovery: str | None = None) -> DependencyItem:
    extra = dict(SPLIT) if recovery is None else {**SPLIT, KEY_ZONE_RECOVERY: recovery}
    return DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg-src", extra=extra)


def _model(recovery: str = ZONE_RECOVERY_FAST, state: DependencyState | None = None, fit: bool = True) -> DependencyModel:
    model = DependencyModel(DependencyState() if state is None else state)
    model.configure_source(SourceSettings.from_item(_item(recovery)))
    if fit:
        model._source.fits["heat"].result = ZoneFitResult(
            a=0.03, b=0.001, a_f=0.03, a_f_se=0.001, candidate=4, windows=50.0, k=(), lead_delay_s=600.0
        )
    return model


def _ctx(now_s: float, target: float, zone: float | None = None, hvac=VThermHvacMode_HEAT) -> ModelContext:
    return ModelContext(
        now_s=now_s,
        zone_temp=target if zone is None else zone,
        ext_temp=5.0,
        power=0.1,
        a=0.03,
        b=0.001,
        deadtime_s=600.0,
        model_reliable=True,
        hvac_mode=hvac,
        learning_phase=False,
        lead_demand=0.1,
        lead_output=0.1,
        target_temp=target,
    )


def _readings() -> DependencyReadings:
    return DependencyReadings(source=SourceObservation("s1", "climate.floor", True, (), 1.0))


def _setback(model: DependencyModel, now_s: float, target: float, hvac=VThermHvacMode_HEAT) -> bool:
    return model.update(_readings(), _ctx(now_s, target, hvac=hvac)).source["setback"]


# The setback


def test_a_setpoint_half_a_degree_below_the_day_s_is_a_setback() -> None:
    model = _model()
    assert not _setback(model, 0.0, 20.0)
    assert not _setback(model, HOUR, 19.6)
    assert _setback(model, 2 * HOUR, 19.5)
    assert _setback(model, 3 * HOUR, 17.0)
    assert not _setback(model, 10 * HOUR, 20.0)


def test_a_setback_lasting_longer_than_the_memory_becomes_the_day_s_setpoint() -> None:
    model = _model()
    _setback(model, 0.0, 20.0)
    assert _setback(model, HOUR, 17.0)
    assert _setback(model, SOURCE_SETBACK_MEMORY_S, 17.0)
    assert not _setback(model, SOURCE_SETBACK_MEMORY_S + 2 * HOUR, 17.0)


def test_in_cooling_a_higher_setpoint_is_the_setback() -> None:
    model = _model()
    assert not _setback(model, 0.0, 24.0, VThermHvacMode_COOL)
    assert not _setback(model, HOUR, 23.0, VThermHvacMode_COOL)
    assert _setback(model, 2 * HOUR, 23.6, VThermHvacMode_COOL)


def test_a_change_of_hvac_mode_starts_a_new_reference() -> None:
    model = _model()
    _setback(model, 0.0, 20.0)
    assert not _setback(model, HOUR, 17.0, VThermHvacMode_COOL)


def test_the_reference_survives_a_restart_with_its_age() -> None:
    """The monotonic clock starts again after a restart: the reference keeps its age."""
    state = DependencyState()
    model = _model(state=state)
    _setback(model, 1000.0, 20.0)
    _setback(model, 1000.0 + 2 * HOUR, 17.0)
    assert state.for_item("s1")["setback_ref"] == {"mode": "heat", "setpoint": 20.0, "age_s": 2 * HOUR}

    restarted = _model(state=state)
    assert _setback(restarted, 50.0, 17.0)
    assert restarted._source.setback_ref[2] == pytest.approx(50.0 - 2 * HOUR)

    expired = DependencyState()
    expired.for_item("s1")["setback_ref"] = {"mode": "heat", "setpoint": 20.0, "age_s": SOURCE_SETBACK_MEMORY_S + 1.0}
    assert not _setback(_model(state=expired), 50.0, 17.0)


@pytest.mark.parametrize("saved", [None, "20", {"mode": "heat", "setpoint": "x", "age_s": 0.0}, {"mode": 1}])
def test_an_unreadable_saved_reference_is_dropped(saved) -> None:
    state = DependencyState()
    state.for_item("s1")["setback_ref"] = saved
    model = _model(state=state)
    assert not _setback(model, 50.0, 17.0)
    assert model._source.setback_ref == ("heat", 17.0, 50.0)


# The fast recovery


def _command(recovery: str, fit: bool = True, braked: float | None = None, monkeypatch=None) -> float:
    model = _model(recovery, fit=fit)
    if braked is not None:
        monkeypatch.setattr(model, "_predictive_command", lambda *_args: braked)
    model.update(_readings(), _ctx(0.0, 20.0))
    return model.update(_readings(), _ctx(HOUR, 17.0)).source_command


def test_in_a_fast_recovery_the_source_carries_the_load_at_the_setback() -> None:
    """b * (17 - 5) / a_f = 0.001 * 12 / 0.03 = 0.4, above what the loop asks at the setback."""
    assert _command(ZONE_RECOVERY_FAST) == pytest.approx(0.4)
    assert _command(ZONE_RECOVERY_ECONOMY) < 0.4


def test_the_brake_still_caps_the_fast_recovery(monkeypatch) -> None:
    assert _command(ZONE_RECOVERY_FAST, braked=0.1, monkeypatch=monkeypatch) == pytest.approx(0.1)


def test_without_a_fit_the_recovery_changes_nothing() -> None:
    assert _command(ZONE_RECOVERY_FAST, fit=False) == pytest.approx(_command(ZONE_RECOVERY_ECONOMY, fit=False))


def test_an_economical_recovery_is_the_allocation_alone() -> None:
    """Same command with and without a setback: today's behaviour."""
    economy = _model(ZONE_RECOVERY_ECONOMY)
    economy.update(_readings(), _ctx(0.0, 20.0))
    in_setback = economy.update(_readings(), _ctx(HOUR, 17.0))
    plain = _model(ZONE_RECOVERY_ECONOMY)
    plain.update(_readings(), _ctx(0.0, 17.0))
    no_setback = plain.update(_readings(), _ctx(HOUR, 17.0))
    assert in_setback.source["setback"] and not no_setback.source["setback"]
    assert in_setback.source_command == pytest.approx(no_setback.source_command)
    assert in_setback.source["recovery"] == ZONE_RECOVERY_ECONOMY


def test_the_load_needs_the_outdoor_temperature() -> None:
    model = _model()
    model.update(_readings(), _ctx(0.0, 20.0))
    ctx = _ctx(HOUR, 17.0)
    model.update(_readings(), ctx)
    fit = model.zone_fit(VThermHvacMode_HEAT)
    assert model._setback_load(ctx, fit, ()) == pytest.approx(0.4)
    assert model._setback_load(replace(ctx, ext_temp=None), fit, ()) is None


def test_in_cooling_the_fast_recovery_carries_the_cooling_load() -> None:
    """Outdoors at 30 degC, setback at 26 degC: 0.001 * (30 - 26) / 0.03 of cooling."""
    model = _model()
    model._source.fits["cool"].result = model._source.fits["heat"].result

    def hot(now_s: float, target: float) -> ModelContext:
        return replace(_ctx(now_s, target, hvac=VThermHvacMode_COOL), ext_temp=30.0)

    model.update(_readings(), hot(0.0, 24.0))
    ctx = hot(HOUR, 26.0)
    inputs = model.update(_readings(), ctx)
    assert inputs.source["setback"]
    assert model._setback_load(ctx, model.zone_fit(VThermHvacMode_COOL), ()) == pytest.approx(0.004 / 0.03)


# The setting


@pytest.mark.parametrize(
    "own, default, expected",
    [
        (None, ZONE_RECOVERY_ECONOMY, ZONE_RECOVERY_ECONOMY),
        (ZONE_RECOVERY_DEFAULTS, ZONE_RECOVERY_ECONOMY, ZONE_RECOVERY_ECONOMY),
        (ZONE_RECOVERY_FAST, ZONE_RECOVERY_ECONOMY, ZONE_RECOVERY_FAST),
        (ZONE_RECOVERY_ECONOMY, ZONE_RECOVERY_FAST, ZONE_RECOVERY_ECONOMY),
        ("unknown", "unknown", ZONE_RECOVERY_FAST),
    ],
)
def test_the_zone_s_own_recovery_wins_over_the_defaults(own, default, expected) -> None:
    assert SourceSettings.from_item(_item(own), default).recovery == expected


def test_the_recovery_defaults_to_fast() -> None:
    assert SourceSettings.from_item(_item()).recovery == ZONE_RECOVERY_FAST
