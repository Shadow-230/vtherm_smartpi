"""The reference governor and the fork's zone features.

A thermostat working as another thermostat's additional source (the source
role) applies the main source's command and regulates nothing, so a governor
episode it had before must not resume when it leaves the role. A neighbour's
open window imposes a preset temperature: for the governor this is an ordinary
setpoint change.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.effective_model import KnownInputs
from custom_components.vtherm_smartpi.smartpi.setpoint import SmartPISetpointManager
from tests.helpers import force_smartpi_stable_mode

STATUS = ({"id": "n1", "kind": "neighbour", "entity": "climate.hall"},)


def _filter_step(manager: SmartPISetpointManager, *, target: float, current: float, now: float) -> float:
    """One model-ready setpoint calculation, as in the author's governor wiring tests."""
    return manager.filter_setpoint(
        target_temp=target,
        current_temp=current,
        hvac_mode=VThermHvacMode_HEAT,
        a=0.4,
        b=0.02,
        ext_current_temp=20.0,
        u_ref=1.0,
        stop_deadtime_s=240.0,
        stop_deadtime_reliable=True,
        tau_reliable=True,
        deadband_c=0.05,
        kp=1.0,
        next_cycle_u_ref=0.9,
        cycle_min=10.0,
        remaining_cycle_min=0.0,
        now_monotonic=now,
        temperature_slope_h=0.01,
    )


def _algo() -> SmartPI:
    algo = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Governor"
    )
    force_smartpi_stable_mode(algo)
    return algo


def test_leaving_the_source_role_clears_the_governor_episode() -> None:
    """An episode armed before the source role does not resume after it: the setpoint starts afresh."""
    algo = _algo()
    manager = algo.sp_mgr
    _filter_step(manager, target=21.0, current=21.0, now=0.0)
    _filter_step(manager, target=22.0, current=21.0, now=60.0)
    _filter_step(manager, target=22.0, current=21.7, now=120.0)
    assert manager.trajectory_active
    assert manager.reference_governor_authority_selected

    algo.enter_source_role("climate.main")
    algo.leave_source_role()

    assert not manager.trajectory_active
    assert not manager.reference_governor_authority_selected
    assert not manager.reference_governor_authority_pending
    assert manager.filtered_setpoint is None


def test_a_neighbour_window_preset_is_a_setpoint_change_for_the_governor() -> None:
    """The governor follows the imposed preset, and the window's close is a setpoint increase."""
    algo = _algo()
    manager = algo.sp_mgr
    clock = [10_000.0]

    def tick(room: float) -> None:
        clock[0] += 60.0
        algo.calculate(target_temp=20.0, current_temp=room, ext_current_temp=5.0, hvac_mode=VThermHvacMode_HEAT)

    with patch("custom_components.vtherm_smartpi.algo.time.monotonic", side_effect=lambda: clock[0]):
        tick(19.9)
        tick(19.9)
        assert manager._last_user_target_temp == 20.0
        algo._read_known_inputs = lambda *args, **kwargs: None
        algo._known_inputs = KnownInputs(control_action="eco", action_setpoint=17.0, status=STATUS)
        tick(19.9)
        assert manager._last_user_target_temp == 17.0
        assert manager.filtered_setpoint == 17.0
        assert not manager._pending_target_change_braking
        tick(18.5)
        algo._known_inputs = None
        tick(18.5)

    assert manager._last_user_target_temp == 20.0
    # The return to 20 degC is a setpoint increase: braking near the target is armed.
    assert manager._pending_target_change_braking
