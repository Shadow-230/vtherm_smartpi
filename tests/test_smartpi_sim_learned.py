"""SmartPI started from a learned model: the in-process twins of the QAS scenarios QL0-QL5.

The learned states are the QAS fixtures (tests/qas/build_fixtures.py), loaded into a thermostat
that is already running and still learning, the way the helpers' ``seed_state`` loads them on the
bench (documentation/en/testing.md, the QAS section). From there each test checks what the QAS
scenario of the same number checks on a real Home Assistant.
"""

from __future__ import annotations

import json
from dataclasses import replace
import pathlib
import sys

import pytest

from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase

QAS = pathlib.Path(__file__).resolve().parent / "qas"
if str(QAS) not in sys.path:
    sys.path.insert(0, str(QAS))

import build_fixtures  # noqa: E402
from custom_components.smartpi_qas_helpers import FIXTURES, prepared_state  # noqa: E402

from sim.runner import Simulation  # noqa: E402

LIVING, STUDIO, LOUNGE = build_fixtures.FIXTURE_SET


def _payload(fixture: build_fixtures.Fixture) -> dict:
    return json.loads((FIXTURES / f"{fixture.name}.json").read_text(encoding="utf-8"))


def _seed(sim: Simulation, payload: dict) -> None:
    """What ``seed_state`` does to a running thermostat (smartpi_qas_helpers ``_load``)."""
    items = list(sim.handler._dependency_items)  # noqa: SLF001
    source = next((item.item_id for item in items if item.is_source), None)
    sim.algo.load_state(prepared_state(payload, sim.algo.applied_reset_token, source))
    sim.algo.forget_dependencies_except(item.item_id for item in items)


async def _seeded(monkeypatch, fixture: build_fixtures.Fixture) -> Simulation:
    """The fixture's room, half an hour into its learning, then its learned state loaded."""
    spec, weather = build_fixtures._spec(fixture)  # noqa: SLF001
    sim = Simulation(monkeypatch, spec, weather)
    await sim.run_for(0.5)
    assert sim.algo.phase == SmartPIPhase.HYSTERESIS
    _seed(sim, _payload(fixture))
    await sim.run_for(0.1)
    return sim


def _zone_fit(sim: Simulation) -> dict:
    return sim.algo.dependency_diagnostics()["second_source"]["zone_fit"]


@pytest.mark.parametrize("fixture", build_fixtures.FIXTURE_SET, ids=lambda f: f.name)
def test_each_fixture_is_a_learned_model_of_its_room(fixture) -> None:
    """QL0's fixtures: SmartPI loads each as a learned model (the zone's with its published fit),
    close to the true room the bench simulates."""
    payload = _payload(fixture)
    assert build_fixtures.learned(fixture, payload) == []
    heater = build_fixtures.ROLES[fixture.role].sim["heaters"][fixture.heater]
    room = build_fixtures.ROLES[fixture.role].sim["rooms"][fixture.room]
    est = payload["est_state"]
    assert 0.8 < est["a"] / heater["a"] < 1.2
    assert 0.75 < est["b"] / room["b"] < 1.25


def test_the_fixture_is_made_ready_for_the_thermostat() -> None:
    """The applied reset token stays the thermostat's own, and the zone's item takes the id the
    zone form gave (or goes, without a zone)."""
    payload = _payload(LOUNGE) | {"dependency_reset_token": "fixture-token"}
    (key,) = payload["dependencies"]["items"]

    ready = prepared_state(payload, "entry-token", "form-id")
    assert ready["dependency_reset_token"] == "entry-token"
    assert list(ready["dependencies"]["items"]) == ["form-id"]
    assert ready["dependencies"]["items"]["form-id"] == payload["dependencies"]["items"][key]
    assert payload["dependency_reset_token"] == "fixture-token"  # the fixture is not changed

    alone = prepared_state(payload, None, None)
    assert "dependency_reset_token" not in alone
    assert alone["dependencies"]["items"] == {}


@pytest.mark.parametrize("fixture", build_fixtures.FIXTURE_SET, ids=lambda f: f.name)
async def test_ql0_a_learned_state_loaded_into_a_running_thermostat_shows_at_once(monkeypatch, fixture) -> None:
    """QL0: loaded into a thermostat that is still learning, the model is in use at the next
    calculation: the phase Stable, a and b the fixture's, the zone's model published."""
    sim = await _seeded(monkeypatch, fixture)
    payload = _payload(fixture)
    assert sim.algo.phase == SmartPIPhase.STABLE
    # Within 1 %, as the helper waits for: a zone's a and b come from its fit, which takes the
    # windows since the start in too.
    assert sim.algo.est.a == pytest.approx(payload["est_state"]["a"], rel=0.01)
    assert sim.algo.est.b == pytest.approx(payload["est_state"]["b"], rel=0.01)
    if fixture.zone:
        assert _zone_fit(sim)["published"]
        assert sim.source_handler.is_executive_source


async def test_ql1_the_floor_applies_the_main_power_over_its_water_factor_below_full(monkeypatch) -> None:
    """QL1: in a learned zone on 40 degC water the main source asks for part of the floor's power;
    the floor's command is that power over its water factor, below 100 %."""
    sim = await _seeded(monkeypatch, LOUNGE)
    await sim.run_for(1.5)  # the zone back near its setpoint after the learning phase's swings
    samples = []
    for _ in range(24):
        await sim.run_for(0.1)
        command = sim.source_algo.dependency_diagnostics()["command"]
        factor = sim.source_algo.water_factor
        samples.append((command, factor, sim.source_algo.on_percent))
    assert all(0.5 < factor < 1.0 for _c, factor, _p in samples)
    assert all(on == pytest.approx(min(1.0, command / factor), abs=0.015) for command, factor, on in samples)
    assert sum(0.0 < on < 0.99 for _c, _f, on in samples) >= 10
    assert _zone_fit(sim)["published"] and sim.algo.phase == SmartPIPhase.STABLE


def _services(monkeypatch, sim: Simulation) -> None:
    """The handler's services as Home Assistant calls them; the entity's attributes are not
    written (the simulator has no entity registry), and the state is saved by the test itself."""
    monkeypatch.setattr(sim.handler, "update_attributes", lambda: None)
    monkeypatch.setattr(sim.handler, "_store", None)


async def test_ql2_a_forced_calibration_and_a_reset_on_a_learned_thermostat(monkeypatch) -> None:
    """QL2: from Stable, a forced calibration runs (phase Calibration) and ends back in Stable with
    a and b close to what they were; a reset then starts the learning again, and the saved state
    keeps it reset; the kept state put back is learned again."""
    sim = await _seeded(monkeypatch, LIVING)
    _services(monkeypatch, sim)
    kept = json.loads(json.dumps(sim.algo.save_state()))
    a, b = sim.algo.est.a, sim.algo.est.b

    await sim.handler.service_force_smartpi_calibration()
    assert sim.algo.phase == SmartPIPhase.CALIBRATION
    start_h = sim.clock.elapsed_s / 3600.0
    while sim.algo.phase == SmartPIPhase.CALIBRATION and sim.clock.elapsed_s / 3600.0 < start_h + 4.0:
        await sim.run_for(0.1)
    took_h = sim.clock.elapsed_s / 3600.0 - start_h
    assert sim.algo.phase == SmartPIPhase.STABLE, f"still calibrating after {took_h:.1f} h"
    assert sim.algo.est.a == pytest.approx(a, rel=0.15)
    assert sim.algo.est.b == pytest.approx(b, rel=0.15)

    await sim.handler.service_reset_smart_pi_learning()
    assert sim.algo.phase == SmartPIPhase.HYSTERESIS
    assert sim.algo.est.learn_ok_count_a == 0
    saved = json.loads(json.dumps(sim.algo.save_state()))
    sim.handler.init_algorithm()  # a restart: a new SmartPI from the saved state
    sim.handler._store = None  # noqa: SLF001
    sim.algo.load_state(saved)
    assert sim.algo.phase == SmartPIPhase.HYSTERESIS

    _seed(sim, kept)
    await sim.run_for(0.1)
    assert sim.algo.phase == SmartPIPhase.STABLE
    assert sim.algo.est.a == pytest.approx(a, rel=1e-6)


async def _restart(sim: Simulation, monkeypatch) -> None:
    """Restart Home Assistant: new algorithms (and link), each loaded from its saved state."""
    from custom_components.vtherm_smartpi.const import DATA_SOURCE_LINK, DOMAIN  # noqa: PLC0415
    from custom_components.vtherm_smartpi.smartpi.source_link import SourceLink  # noqa: PLC0415

    handlers = [sim.handler] + ([sim.source_handler] if sim.zone.source is not None else [])
    states = [json.loads(json.dumps(h._thermostat.prop_algorithm.save_state())) for h in handlers]  # noqa: SLF001
    if sim.zone.source is not None:
        sim.thermostat.hass.data[DOMAIN][DATA_SOURCE_LINK] = SourceLink()
    for handler, state in reversed(list(zip(handlers, states))):
        handler.init_algorithm()
        handler._store = None  # noqa: SLF001
        handler._legacy_store = None  # noqa: SLF001
        handler._thermostat.prop_algorithm.load_state(state)  # noqa: SLF001
    if sim.zone.source is not None:
        sim._drive_source(monkeypatch, sim.zone.source.split)  # noqa: SLF001
    for handler in reversed(handlers):
        await handler._thermostat.async_control_heating(force=True)  # noqa: SLF001


@pytest.mark.parametrize("fixture", [LIVING, LOUNGE], ids=lambda f: f.name)
async def test_ql3_restarts_keep_the_learned_model_and_the_zone_s(monkeypatch, fixture) -> None:
    """QL3: two restarts keep a, b, their counts, the dead time and the phase, and a zone's
    published model (its a, b and a_f).

    The restarts come once the room regulates near its setpoint: restarted while the zone still
    heats at full power after the seeding, the dead-time estimator may take one more, shorter
    sample (a measurement after the restart, not a value the restart lost).
    """
    sim = await _seeded(monkeypatch, fixture)
    await sim.run_for(1.5)
    before = (sim.algo.est.a, sim.algo.est.b, sim.algo.est.learn_ok_count_a, sim.algo.est.learn_ok_count_b, sim.algo.dt_est.deadtime_heat_s)
    fit = dict(_zone_fit(sim)) if fixture.zone else None
    for _ in range(2):
        await _restart(sim, monkeypatch)
        assert sim.algo.dt_est.deadtime_heat_s == before[4]
        await sim.run_for(0.1)
        assert sim.algo.phase == SmartPIPhase.STABLE
        assert sim.algo.est.a == pytest.approx(before[0], rel=1e-3)
        assert sim.algo.est.b == pytest.approx(before[1], rel=1e-3)
        assert (sim.algo.est.learn_ok_count_a, sim.algo.est.learn_ok_count_b) >= before[2:4]
        assert sim.algo.dt_est.deadtime_heat_s == before[4]
        if fit is not None:
            after = _zone_fit(sim)
            assert after["published"]
            for key in ("a", "b", "a_f"):
                assert after[key] == pytest.approx(fit[key], rel=1e-3)


class _CallProvider:
    """The boiler's call from a provider of SmartPI's contract: not called in a window."""

    name = "qas"
    version = 1

    def __init__(self, sim: Simulation, window_h: tuple[float, float]) -> None:
        self.sim, self.window_h = sim, window_h

    def called(self, t_s: float) -> bool:
        return not self.window_h[0] <= t_s / 3600.0 < self.window_h[1]

    def heating_inputs(self, entity_id):  # noqa: ARG002
        called = self.called(self.sim.clock.elapsed_s)
        return {"boiler_called": called, "call_reason": None if called else "below_threshold"}


async def test_ql4_a_boiler_not_called_keeps_the_learned_a(monkeypatch) -> None:
    """QL4: the studio calls while the provider says the boiler is not called (and it does not
    fire): SmartPI holds, learns no a, stays Stable, and its a and b stay where they were through
    the window and one settling time after it."""
    from custom_components.vtherm_smartpi.const import CONF_SMART_PI_BOILER_CALL_SOURCE  # noqa: PLC0415
    from custom_components.vtherm_smartpi.heating_inputs import PROVIDERS_KEY  # noqa: PLC0415
    from custom_components.vtherm_smartpi.smartpi.boiler_call import MODE_HELD  # noqa: PLC0415
    from sim.boiler import SimBoiler  # noqa: PLC0415

    spec, weather = build_fixtures._spec(STUDIO)  # noqa: SLF001
    window_h = (2.0, 4.0)
    provider: list[_CallProvider] = []
    boiler = SimBoiler(water_c=spec.boiler.water_c, on_demand=True, idle_c=12.0, demand=lambda t_s: provider[0].called(t_s))
    sim = Simulation(monkeypatch, replace(spec, boiler=boiler), weather)
    provider.append(_CallProvider(sim, window_h))
    sim.thermostat.hass.data.setdefault(PROVIDERS_KEY, {})["qas"] = provider[0]
    sim.thermostat.smartpi_options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "provider:qas"
    await sim.run_for(0.5)
    _seed(sim, _payload(STUDIO))
    await sim.run_for(window_h[0] - 0.5)
    a, b, count = sim.algo.est.a, sim.algo.est.b, sim.algo.est.learn_ok_count_a
    held = []
    while sim.clock.elapsed_s / 3600.0 < window_h[1] + 1.5:
        await sim.run_for(0.1)
        hour = sim.clock.elapsed_s / 3600.0
        assert sim.algo.phase == SmartPIPhase.STABLE, f"{sim.algo.phase} at {hour:.1f} h"
        if window_h[0] <= hour < window_h[1]:
            held.append(sim.algo.boiler_mode == MODE_HELD)
            assert sim.algo.est.learn_ok_count_a == count, f"a learned at {hour:.1f} h"
    assert any(held)
    assert sim.algo.est.a == pytest.approx(a, rel=0.05)
    assert sim.algo.est.b == pytest.approx(b, rel=0.05)


async def test_ql5_hot_water_holds_the_integral_while_smartpi_regulates(monkeypatch) -> None:
    """QL5: hot water with priority for 20 minutes while the learned studio regulates: every
    sample Stable, the integral and the command held by the water; afterwards the hold is gone."""
    from custom_components.vtherm_smartpi.algo import WATER_INTEGRAL_HOLD  # noqa: PLC0415
    from sim.boiler import SimBoiler  # noqa: PLC0415

    spec, weather = build_fixtures._spec(STUDIO)  # noqa: SLF001
    window_h = (2.5, 2.5 + 20.0 / 60.0)
    boiler = SimBoiler(water_c=spec.boiler.water_c, dhw_daily_h=(window_h,))
    sim = Simulation(monkeypatch, replace(spec, boiler=boiler), weather)
    await sim.run_for(0.5)
    _seed(sim, _payload(STUDIO))
    await sim.run_for(window_h[0] + 0.1 - 0.5)  # the hold starts at the next control call
    samples = []
    while sim.clock.elapsed_s / 3600.0 < window_h[1]:
        samples.append((sim.algo.phase, sim.algo.ctl.integral, sim.algo.on_percent, sim.algo.ctl.integral_hold_mode))
        await sim.run_for(1.0 / 60.0)
    assert len(samples) >= 10
    assert all(phase == SmartPIPhase.STABLE for phase, *_ in samples)
    assert all(mode == WATER_INTEGRAL_HOLD for *_, mode in samples)
    assert max(s[1] for s in samples) - min(s[1] for s in samples) < 1e-9
    assert max(s[2] for s in samples) - min(s[2] for s in samples) < 1e-9
    await sim.run_for(0.5)
    assert sim.algo.ctl.integral_hold_mode != WATER_INTEGRAL_HOLD
