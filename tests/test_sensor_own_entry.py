"""A thermostat's own SmartPI entry takes its diagnostic sensor over from the defaults entry."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_DEBUG,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
)

VT_DOMAIN = "versatile_thermostat"


DUPLICATE_ID = "does not generate unique IDs"


async def _set_up(hass, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_a_thermostat_s_own_entry_takes_over_its_diagnostic_sensor(hass, caplog, monkeypatch) -> None:
    """The defaults entry shows the diagnostics of a SmartPI thermostat without an entry of its own.

    When the thermostat gets its own entry, that entry takes the sensor over: Home Assistant's real
    entity platform refuses no duplicate unique ID when the own entry is created, reloaded or its
    options saved, and the sensor stays live when the defaults entry is reloaded. Before, the
    defaults entry kept the sensor until it was reloaded or Home Assistant restarted (also in 0.4.0).
    """
    import custom_components.vtherm_smartpi as smartpi_init
    from homeassistant.setup import async_setup_component

    # Versatile Thermostat itself is not needed: its thermostats are not reloaded here.
    hass.config.components.add(VT_DOMAIN)
    monkeypatch.setattr(smartpi_init, "_reload_smartpi_vtherms", AsyncMock())
    monkeypatch.setattr(smartpi_init, "_reload_smartpi_vtherms_for_target", AsyncMock())
    monkeypatch.setattr(smartpi_init, "_reload_smartpi_vtherms_using_defaults", AsyncMock())
    assert await async_setup_component(hass, "sensor", {})

    vt_entry = MockConfigEntry(domain=VT_DOMAIN, unique_id="vt-x", data={CONF_PROP_FUNCTION: PROP_FUNCTION_SMART_PI})
    vt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("climate", VT_DOMAIN, "vt-x", suggested_object_id="vt_x", config_entry=vt_entry)
    defaults = MockConfigEntry(domain=DOMAIN, title="SmartPI defaults", unique_id=DOMAIN, data=dict(DEFAULT_OPTIONS))
    defaults.add_to_hass(hass)
    await _set_up(hass, defaults)
    diagnostics = registry.async_get_entity_id("sensor", DOMAIN, "smartpi_diag_vt-x")
    assert registry.async_get(diagnostics).config_entry_id == defaults.entry_id

    own = MockConfigEntry(
        domain=DOMAIN, title="X", unique_id=f"{DOMAIN}-vt-x", data={CONF_TARGET_VTHERM: "vt-x", **DEFAULT_OPTIONS}
    )
    own.add_to_hass(hass)
    await _set_up(hass, own)
    assert registry.async_get(diagnostics).config_entry_id == own.entry_id
    for _ in range(2):
        assert await hass.config_entries.async_reload(own.entry_id)
        await hass.async_block_till_done()
    hass.config_entries.async_update_entry(own, options={**DEFAULT_OPTIONS, CONF_SMART_PI_DEBUG: True})
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(defaults.entry_id)
    await hass.async_block_till_done()

    assert DUPLICATE_ID not in caplog.text
    assert registry.async_get(diagnostics).config_entry_id == own.entry_id
    state = hass.states.get(diagnostics)
    assert state is not None and not state.attributes.get("restored"), state
