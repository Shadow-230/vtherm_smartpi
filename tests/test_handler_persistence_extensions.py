"""The fork's state goes through the author's state store, and a zone reset is saved at once.

The author's ``SmartPIStateStore`` writes the algorithm state at most every five
minutes from the control loop. The fork keeps its own parts in that state (the
dependencies' learned values, the zone's reset token, the water input) and saves
a zone reset immediately, as the reset service does.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vtherm_smartpi import state_store
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.const import CONF_SMART_PI_RESET_TOKEN
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.smartpi.const import HEATING_RADIATOR
from custom_components.vtherm_smartpi.state_store import SAVE_DELAY_SEC, SmartPIStateStore
from fakes.fake_thermostat_runtime import FakeThermostatRuntime

_MONOTONIC = "custom_components.vtherm_smartpi.state_store.time.monotonic"


def _algo(name: str) -> SmartPI:
    return SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name=name
    )


def _store() -> tuple[SmartPIStateStore, MagicMock]:
    """A state store over a mocked Home Assistant Store that keeps what it is given."""
    primary, legacy = MagicMock(), MagicMock()
    saved: dict = {}

    async def save(data):
        # Home Assistant writes JSON: a value that does not survive it would be lost.
        saved["data"] = json.loads(json.dumps(data))

    async def load():
        return saved.get("data")

    primary.async_save = AsyncMock(side_effect=save)
    primary.async_load = AsyncMock(side_effect=load)
    legacy.async_load = AsyncMock(return_value=None)
    with patch.object(state_store, "Store", side_effect=[primary, legacy]):
        store = SmartPIStateStore(MagicMock(), "zone")
    return store, primary


async def test_our_state_round_trips_through_the_state_store() -> None:
    """Dependencies, the zone's reset token and the water input survive a write and a load."""
    algo = _algo("Before")
    algo.configure_heating_type(HEATING_RADIATOR)
    algo.dependency_state.for_item("n1")["k"] = 0.0012
    algo.applied_reset_token = "zone-reset-1"
    algo.water.inputs = "entities:sensor.supply"
    store, _primary = _store()

    await store.save_now(algo)
    restored = _algo("After")
    restored.configure_heating_type(HEATING_RADIATOR)
    restored.load_state(await store.async_load())

    assert restored.dependency_state.items == {"n1": {"k": 0.0012}}
    assert restored.applied_reset_token == "zone-reset-1"
    assert restored.water.inputs == "entities:sensor.supply"


def test_control_passes_schedule_one_write_per_window() -> None:
    """Calls every minute arm one delayed write per five-minute window, not one each."""
    algo = _algo("Debounced")
    store, primary = _store()

    for minute in range(12):
        with patch(_MONOTONIC, return_value=1000.0 + 60.0 * minute):
            store.schedule_save(algo)

    windows = 1 + (11 * 60) // SAVE_DELAY_SEC
    assert primary.async_delay_save.call_count == windows
    primary.async_save.assert_not_called()


@pytest.mark.asyncio
async def test_a_zone_reset_is_saved_at_once() -> None:
    """A reset asked by the zone's form is written immediately, so a restart does not repeat it."""
    thermostat = FakeThermostatRuntime()
    thermostat.hass.async_create_task = MagicMock()
    thermostat.prop_algorithm = _algo("Reset")
    handler = SmartPIHandler(thermostat)
    store = MagicMock()
    store.save_now = MagicMock(side_effect=lambda _algo: AsyncMock()())
    handler._store = store

    with patch.object(handler, "_get_effective_config", return_value={CONF_SMART_PI_RESET_TOKEN: "zone-reset-2"}):
        handler._apply_reset_request()
        handler._apply_reset_request()

    assert thermostat.prop_algorithm.applied_reset_token == "zone-reset-2"
    store.save_now.assert_called_once_with(thermostat.prop_algorithm)
    thermostat.hass.async_create_task.assert_called_once()
    thermostat.hass.async_create_task.call_args.args[0].close()
    store.schedule_save.assert_not_called()
