"""The author's COOL changes with the fork's inputs.

In COOL the braking and the reference governor predict the room after the
cooling stops with the passive dead time (the rise delay), and they still get
the effective model (b_eff, T_ref) of the known inputs. A neighbour's window
preset changes the setpoint like the user does: within a cycle it sets the
in-cycle flag, so the feed-forward trim refuses a bumpless transfer then.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_COOL, VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import GovernanceRegime
from custom_components.vtherm_smartpi.smartpi.effective_model import ExchangeInput, KnownInputs, compose
from tests.helpers import force_smartpi_stable_mode

K = 0.001
NEIGHBOUR_C = 30.0
STATUS = ({"id": "n1", "kind": "neighbour", "entity": "climate.hall"},)


def _algo(mode) -> SmartPI:
    algo = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Cool"
    )
    algo.est.ensure_hvac_mode(mode)
    algo.dt_est.ensure_hvac_mode(mode)
    force_smartpi_stable_mode(algo)
    a = -0.02 if mode == VThermHvacMode_COOL else 0.02
    history_size = len(algo.est.a_meas_hist)
    algo.est.a_meas_hist.clear()
    algo.est.a_meas_hist.extend([a] * history_size)
    algo.est.a = a
    algo._last_hvac_mode = mode
    # Distinct dead times: the active cooling delay and the passive rise delay.
    algo.dt_est.deadtime_heat_s, algo.dt_est.deadtime_heat_reliable = 300.0, True
    algo.dt_est.deadtime_cool_s, algo.dt_est.deadtime_cool_reliable = 900.0, True
    algo._read_known_inputs = lambda *args, **kwargs: None
    return algo


def test_cool_braking_uses_the_passive_deadtime_with_the_effective_model(monkeypatch) -> None:
    algo = _algo(VThermHvacMode_COOL)
    algo._known_inputs = KnownInputs(exchanges=(ExchangeInput("n1", K, NEIGHBOUR_C),), status=STATUS)
    seen = []
    filter_setpoint = algo.sp_mgr.filter_setpoint

    def recording(**kwargs):
        seen.append(kwargs)
        return filter_setpoint(**kwargs)

    monkeypatch.setattr(algo.sp_mgr, "filter_setpoint", recording)
    clock = [10_000.0]
    with patch("custom_components.vtherm_smartpi.algo.time.monotonic", side_effect=lambda: clock[0]):
        for _ in range(2):
            clock[0] += 60.0
            algo.calculate(target_temp=24.0, current_temp=25.0, ext_current_temp=32.0, hvac_mode=VThermHvacMode_COOL)

    assert seen
    model = compose(algo.est.b, 32.0, algo._known_inputs)
    for kwargs in seen:
        # The rise after the cooling stops: the heating dead time, not the cooling one.
        assert kwargs["stop_deadtime_s"] == 300.0
        assert kwargs["stop_deadtime_reliable"] is True
        assert kwargs["b"] == pytest.approx(model.b)
        assert kwargs["ext_current_temp"] == pytest.approx(model.ext_temp)
    assert model.b == pytest.approx(algo.est.b + K)


def test_a_neighbour_preset_change_in_cycle_blocks_the_bumpless_transfer() -> None:
    """The preset a neighbour's open window imposes, within a cycle, flags the cycle."""
    algo = _algo(VThermHvacMode_HEAT)
    clock = [10_000.0]
    with patch("custom_components.vtherm_smartpi.algo.time.monotonic", side_effect=lambda: clock[0]):
        clock[0] += 60.0
        algo.calculate(target_temp=20.0, current_temp=19.9, ext_current_temp=5.0, hvac_mode=VThermHvacMode_HEAT)
        assert algo._setpoint_changed_in_cycle is False

        algo._known_inputs = KnownInputs(control_action="eco", action_setpoint=17.0, status=STATUS)
        clock[0] += 60.0
        algo.calculate(target_temp=20.0, current_temp=19.9, ext_current_temp=5.0, hvac_mode=VThermHvacMode_HEAT)

    assert algo._setpoint_changed_in_cycle is True
    assert "setpoint_changed" in algo._build_ownership_constraint_flags(0.0)
    # Everything else as in a deadband cycle that would allow the transfer.
    algo.gov.update_regime(GovernanceRegime.DEAD_BAND)
    algo.ctl.last_i_mode = "I:FREEZE(deadband)"
    algo.ctl.last_sat = "NO_SAT"
    algo.sp_mgr.reset()
    assert algo._current_bumpless_context_rejection() == "application_setpoint_changed"
