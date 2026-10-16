"""The entry lifecycle with the fork's parts: zones, entries that use the defaults, removal.

The thermostats' reloads are recorded (the VT reload helpers are replaced); an entry's reload
runs its real unload and setup, as Home Assistant does, so each test counts what one event
reloads.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.vtherm_smartpi as smartpi_init
from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
)

VT_DOMAIN = "versatile_thermostat"


@pytest.fixture
def reloads(hass, monkeypatch) -> list[str]:
    """What each event reloads: ``vt:<unique id>``, ``defaults-users`` and ``entry:<title>``."""
    done: list[str] = []

    async def for_target(hass, unique_id):
        done.append(f"vt:{unique_id}")

    async def using_defaults(hass):
        done.append("defaults-users")

    async def reload_entry(entry_id):
        plugin_entry = hass.config_entries.async_get_entry(entry_id)
        done.append(f"entry:{plugin_entry.title}")
        await smartpi_init.async_unload_entry(hass, plugin_entry)
        return await smartpi_init.async_setup_entry(hass, plugin_entry)

    monkeypatch.setattr(smartpi_init, "_reload_smartpi_vtherms_for_target", for_target)
    monkeypatch.setattr(smartpi_init, "_reload_smartpi_vtherms_using_defaults", using_defaults)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))
    hass.set_state(CoreState.running)
    return done


def _entry(hass, title: str, target: str | None = None, **options) -> MockConfigEntry:
    data = {CONF_TARGET_VTHERM: target} if target else dict(DEFAULT_OPTIONS)
    entry = MockConfigEntry(
        domain=DOMAIN, title=title, unique_id=f"{DOMAIN}-{target}" if target else DOMAIN, data=data, options=options
    )
    entry.add_to_hass(hass)
    return entry


def _zone(hass) -> tuple[MockConfigEntry, MockConfigEntry]:
    """A radiator's entry holding the zone, and the floor's (its additional source)."""
    floor = er.async_get(hass).async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    source = {
        "id": "s1",
        "kind": "source",
        "entity": floor.id,
        "split_rule": "lead_band",
        "lead_band_percent": 20,
        "lead_emitter": "radiator",
        "source_emitter": "floor_water",
    }
    main = _entry(hass, "Radiator", "vt-radiator", **{CONF_SMART_PI_DEPENDENCIES: [source]})
    return main, _entry(hass, "Floor", "vt-floor")


async def test_a_thermostat_save_reloads_it_and_the_zone_mains_once(hass, reloads) -> None:
    """The floor's entry saved alone: the floor, then the zone's main source (it reads the floor's
    heating type at start)."""
    _main, floor = _zone(hass)

    await smartpi_init._async_update_options(hass, floor)

    assert reloads == ["entry:Floor", "vt:vt-floor", "vt:vt-radiator"]


async def test_a_defaults_save_reloads_entries_using_defaults_and_thermostats_without_entry_once(
    hass, reloads
) -> None:
    """Each own entry that uses the defaults is reloaded as an entry (its setup reloads its
    thermostat once); a thermostat with its own values is not reloaded."""
    defaults = _entry(hass, "SmartPI defaults")
    _entry(hass, "Kitchen", "vt-kitchen", **{CONF_SMART_PI_USE_DEFAULTS: True})
    _entry(hass, "Office", "vt-office", **{CONF_SMART_PI_USE_DEFAULTS: False})

    await smartpi_init._async_update_options(hass, defaults)

    assert reloads == ["entry:SmartPI defaults", "entry:Kitchen", "vt:vt-kitchen", "defaults-users"]


async def test_creating_a_thermostat_entry_reloads_only_that_thermostat(hass, reloads) -> None:
    """As the options form, the zone form or the migration create it."""
    _entry(hass, "SmartPI defaults")
    created = _entry(hass, "Hall", "vt-hall")

    assert await smartpi_init.async_setup_entry(hass, created)

    assert reloads == ["vt:vt-hall"]


async def test_removing_a_thermostat_entry_reloads_it_and_its_zone_mains(hass, reloads) -> None:
    """After the entry has left the registry: the floor now uses the defaults, and the zone's
    main source reads its heating type again."""
    _main, floor = _zone(hass)
    assert await smartpi_init.async_unload_entry(hass, floor)
    del hass.config_entries._entries[floor.entry_id]

    await smartpi_init.async_remove_entry(hass, floor)

    assert reloads == ["vt:vt-floor", "vt:vt-radiator"]


async def test_removing_the_defaults_entry_reloads_its_users(hass, reloads) -> None:
    defaults = _entry(hass, "SmartPI defaults")
    _entry(hass, "Kitchen", "vt-kitchen", **{CONF_SMART_PI_USE_DEFAULTS: True})
    del hass.config_entries._entries[defaults.entry_id]

    await smartpi_init.async_remove_entry(hass, defaults)

    assert reloads == ["entry:Kitchen", "vt:vt-kitchen", "defaults-users"]


async def test_unloading_reloads_nothing(hass, reloads) -> None:
    """Home Assistant stops, or an entry is disabled: no thermostat is reloaded."""
    _main, floor = _zone(hass)

    assert await smartpi_init.async_unload_entry(hass, floor)

    assert reloads == []
