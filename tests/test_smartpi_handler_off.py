"""Test SmartPIHandler.control_heating behavior when the thermostat is OFF."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_OFF
from fakes.fake_thermostat_runtime import FakeThermostatRuntime


async def _run_off_control_heating(
    *, is_device_active: bool, is_cycle_running: bool
) -> FakeThermostatRuntime:
    """Drive control_heating in OFF mode and return the thermostat fake."""
    thermostat = FakeThermostatRuntime()
    thermostat.vtherm_hvac_mode = VThermHvacMode_OFF
    thermostat.cycle_min = 5
    thermostat.is_device_active = is_device_active
    thermostat.cycle_scheduler.is_cycle_running = is_cycle_running

    handler = SmartPIHandler(thermostat)

    with (
        patch.object(handler, "_async_save", new_callable=AsyncMock),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"),
    ):
        await handler.control_heating(force=False)

    return thermostat


@pytest.mark.asyncio
async def test_off_cancels_running_cycle_when_device_already_inactive():
    """HVAC OFF must cancel the master cycle even during an already-off PWM phase."""
    thermostat = await _run_off_control_heating(
        is_device_active=False, is_cycle_running=True
    )

    assert thermostat._on_time_sec == 0
    assert thermostat._off_time_sec == 300
    thermostat.cycle_scheduler.cancel_cycle.assert_awaited_once()
    thermostat.async_underlying_entity_turn_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_off_turns_off_active_device_without_cancelling_cycle():
    """HVAC OFF with an active device turns the underlying off."""
    thermostat = await _run_off_control_heating(
        is_device_active=True, is_cycle_running=True
    )

    thermostat.async_underlying_entity_turn_off.assert_awaited_once()
    thermostat.cycle_scheduler.cancel_cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_off_does_nothing_when_device_inactive_and_no_cycle_running():
    """HVAC OFF with an inactive device and no running cycle has nothing to stop."""
    thermostat = await _run_off_control_heating(
        is_device_active=False, is_cycle_running=False
    )

    thermostat.cycle_scheduler.cancel_cycle.assert_not_awaited()
    thermostat.async_underlying_entity_turn_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_off_cancels_a_calibration_in_progress():
    """Switched off during a calibration, SmartPI ends it as cancelled and saves its state."""
    thermostat = FakeThermostatRuntime()
    thermostat.vtherm_hvac_mode = VThermHvacMode_OFF
    thermostat.prop_algorithm = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="calibration"
    )
    calibration = thermostat.prop_algorithm.calibration_mgr
    calibration.start_calibration(time.monotonic(), is_manual=True)
    assert calibration.is_calibrating
    handler = SmartPIHandler(thermostat)

    with (
        patch.object(handler, "_async_save", new_callable=AsyncMock) as save,
        patch.object(handler, "update_attributes"),
    ):
        await handler.on_state_changed(True)

    assert not calibration.is_calibrating
    save.assert_awaited_once()
