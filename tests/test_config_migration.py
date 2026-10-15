"""The entries' migration to minor version 2: the settings sorted into shared and "thermostat only".

A thermostat's entry keeps its full copy and does not use the defaults. The defaults entry gets
every shared setting and loses the others; those it set are given, after Home Assistant has
started, to an entry created for each thermostat on SmartPI that has none, with a repair notice.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.vtherm_smartpi as smartpi_init
from custom_components.vtherm_smartpi.config_flow import SmartPIConfigFlow
from custom_components.vtherm_smartpi.const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEBUG,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
    CONF_SMART_PI_MIN_VALVE,
    CONF_SMART_PI_SETTINGS_TO_MOVE,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    ISSUE_THERMOSTAT_ENTRIES_CREATED,
    PROP_FUNCTION_SMART_PI,
    SHARED_SETTINGS,
    THERMOSTAT_SETTINGS,
)

VT_DOMAIN = "versatile_thermostat"
# The real setup of an entry (the autouse fixture stands in for the entries the migration creates).
REAL_SETUP_ENTRY = smartpi_init.async_setup_entry
# The defaults entry as the fork's dev branch created it: every setting, the heating type too.
DEV_DEFAULTS = {**DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: "radiator"}
# The defaults entry of the release 0.4.0: no heating type.
RELEASE_DEFAULTS = dict(DEFAULT_OPTIONS)


@pytest.fixture(autouse=True)
def _entries_are_not_set_up(hass):
    """An entry created by the migration would start Versatile Thermostat when set up; Versatile
    Thermostat itself counts as loaded (SmartPI depends on it)."""
    hass.config.components.add(VT_DOMAIN)
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        yield


def _defaults(hass, data, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="SmartPI defaults", unique_id=DOMAIN, data=data, options=options or {}, version=1, minor_version=1
    )
    entry.add_to_hass(hass)
    return entry


def _vtherm(hass, unique_id: str, name: str, algorithm: str = PROP_FUNCTION_SMART_PI) -> er.RegistryEntry:
    """A Versatile Thermostat on ``algorithm``: its entry (not set up) and its climate entity."""
    # Its configuration in an entry of another domain: under its own it would start Versatile Thermostat.
    vt_entry = MockConfigEntry(domain="test_vtherm", title=name, unique_id=f"vt-entry-{unique_id}", data={CONF_PROP_FUNCTION: algorithm})
    vt_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, unique_id, suggested_object_id=unique_id.replace("-", "_"), config_entry=vt_entry
    )


def _own_entries(hass) -> dict[str, MockConfigEntry]:
    return {
        entry.data[CONF_TARGET_VTHERM]: entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_TARGET_VTHERM)
    }


def test_the_major_version_stays_1_so_the_release_still_loads_the_entries() -> None:
    assert (SmartPIConfigFlow.VERSION, SmartPIConfigFlow.MINOR_VERSION) == (1, 2)


async def test_a_thermostat_s_entry_keeps_its_values_and_does_not_use_the_defaults(hass) -> None:
    options = {CONF_TARGET_VTHERM: "vt-a", **DEV_DEFAULTS, CONF_SMART_PI_DEADBAND: 0.2, CONF_SMART_PI_DEPENDENCIES: []}
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=f"{DOMAIN}-vt-a", data={CONF_TARGET_VTHERM: "vt-a", **DEV_DEFAULTS}, options=options, minor_version=1
    )
    entry.add_to_hass(hass)

    assert await smartpi_init.async_migrate_entry(hass, entry)

    assert entry.minor_version == 2
    assert entry.version == 1
    assert dict(entry.options) == {**options, CONF_SMART_PI_USE_DEFAULTS: False}


async def test_an_entry_never_saved_keeps_its_data_as_its_options(hass) -> None:
    """Created by the config flow and never saved: its values are in its data."""
    data = {CONF_TARGET_VTHERM: "vt-a", **RELEASE_DEFAULTS}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=f"{DOMAIN}-vt-a", data=data, minor_version=1)
    entry.add_to_hass(hass)

    assert await smartpi_init.async_migrate_entry(hass, entry)

    assert dict(entry.options) == {**data, CONF_SMART_PI_USE_DEFAULTS: False}


async def test_the_defaults_keep_the_shared_settings_only(hass) -> None:
    """Every shared setting is written (its stored value, else the form's); the "thermostat only"
    ones go, and the ones that are set wait under a temporary key."""
    options = {**DEV_DEFAULTS, CONF_SMART_PI_DEBUG: True, CONF_SMART_PI_DHW_ENTITY: "binary_sensor.dhw"}
    del options[CONF_SMART_PI_DEADBAND]
    entry = _defaults(hass, dict(DEV_DEFAULTS), options)

    assert await smartpi_init.async_migrate_entry(hass, entry)

    assert entry.minor_version == 2
    stored = dict(entry.options)
    assert set(SHARED_SETTINGS) <= set(stored)
    assert stored[CONF_SMART_PI_DEBUG] is True
    assert stored[CONF_SMART_PI_DEADBAND] == DEFAULT_OPTIONS[CONF_SMART_PI_DEADBAND]
    assert stored[CONF_SMART_PI_DHW_ENTITY] == "binary_sensor.dhw"
    assert not set(THERMOSTAT_SETTINGS) & set(stored)
    assert not set(THERMOSTAT_SETTINGS) & set(entry.data)
    assert stored[CONF_SMART_PI_SETTINGS_TO_MOVE] == {CONF_SMART_PI_HEATING_TYPE: "radiator"}


@pytest.mark.parametrize(
    "extra, moved",
    [
        ({}, None),
        ({CONF_MINIMAL_ACTIVATION_DELAY: 30}, {CONF_MINIMAL_ACTIVATION_DELAY: 30}),
        ({CONF_MINIMAL_DEACTIVATION_DELAY: 10.5}, {CONF_MINIMAL_DEACTIVATION_DELAY: 10.5}),
        ({CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE: True}, {CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE: True}),
        (
            {CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: True},
            {
                CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: True,
                CONF_SMART_PI_MIN_VALVE: DEFAULT_OPTIONS[CONF_SMART_PI_MIN_VALVE],
                CONF_SMART_PI_KNEE_DEMAND: DEFAULT_OPTIONS[CONF_SMART_PI_KNEE_DEMAND],
                CONF_SMART_PI_KNEE_VALVE: DEFAULT_OPTIONS[CONF_SMART_PI_KNEE_VALVE],
                CONF_SMART_PI_MAX_VALVE: DEFAULT_OPTIONS[CONF_SMART_PI_MAX_VALVE],
            },
        ),
        ({CONF_SMART_PI_HEATING_TYPE: "unknown"}, None),
    ],
)
async def test_only_the_settings_that_are_set_move(hass, extra, moved) -> None:
    """The release's defaults: no heating type; a delay of 0, the PWM restart and the valve curve
    off are "not set"."""
    entry = _defaults(hass, {**RELEASE_DEFAULTS, **extra})

    assert await smartpi_init.async_migrate_entry(hass, entry)

    assert entry.options.get(CONF_SMART_PI_SETTINGS_TO_MOVE) == moved


async def test_a_newer_major_version_is_not_migrated(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    assert not await smartpi_init.async_migrate_entry(hass, entry)


async def test_after_the_start_each_thermostat_on_smartpi_without_an_entry_gets_one(hass) -> None:
    """With the moved settings, a notice lists the entries; a thermostat with its own entry and a
    thermostat on another algorithm get nothing."""
    _vtherm(hass, "vt-bedroom", "Bedroom")
    _vtherm(hass, "vt-kitchen", "Kitchen")
    _vtherm(hass, "vt-hall", "Hall", algorithm="tpi")
    kept = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-vt-kitchen",
        data={CONF_TARGET_VTHERM: "vt-kitchen"},
        options={CONF_TARGET_VTHERM: "vt-kitchen"},
        minor_version=2,
    )
    kept.add_to_hass(hass)
    defaults = _defaults(hass, {**DEV_DEFAULTS, CONF_MINIMAL_ACTIVATION_DELAY: 20})
    assert await smartpi_init.async_migrate_entry(hass, defaults)
    reload = AsyncMock()

    with patch.object(smartpi_init, "_async_update_options", reload):
        await smartpi_init._async_move_settings(hass, defaults.entry_id)
        await hass.async_block_till_done()

    entries = _own_entries(hass)
    assert set(entries) == {"vt-bedroom", "vt-kitchen"}
    created = dict(entries["vt-bedroom"].data)
    assert created == {
        CONF_TARGET_VTHERM: "vt-bedroom",
        CONF_SMART_PI_USE_DEFAULTS: True,
        CONF_SMART_PI_HEATING_TYPE: "radiator",
        CONF_MINIMAL_ACTIVATION_DELAY: 20,
    }
    assert entries["vt-kitchen"] is kept and dict(kept.options) == {CONF_TARGET_VTHERM: "vt-kitchen"}
    assert CONF_SMART_PI_SETTINGS_TO_MOVE not in defaults.options
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_THERMOSTAT_ENTRIES_CREATED)
    assert issue is not None and not issue.is_fixable
    assert issue.translation_placeholders == {"entries": entries["vt-bedroom"].title}


async def test_nothing_set_creates_nothing_and_shows_no_notice(hass) -> None:
    _vtherm(hass, "vt-bedroom", "Bedroom")
    defaults = _defaults(hass, dict(RELEASE_DEFAULTS))
    assert await smartpi_init.async_migrate_entry(hass, defaults)

    await smartpi_init._async_move_settings(hass, defaults.entry_id)

    assert _own_entries(hass) == {}
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_THERMOSTAT_ENTRIES_CREATED) is None


async def test_removing_the_temporary_key_does_not_reload_the_defaults(hass) -> None:
    """SmartPI removes its own temporary key: the defaults entry's update listener does nothing."""
    entry = _defaults(hass, dict(RELEASE_DEFAULTS))
    smartpi_init._ensure_domain_data(hass)[f"{smartpi_init.DATA_SKIP_UPDATE}_{entry.entry_id}"] = True
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await smartpi_init._async_update_options(hass, entry)
    reload.assert_not_called()


async def test_the_defaults_entry_moves_its_settings_once_home_assistant_has_started(hass) -> None:
    """Set up after the migration (Home Assistant runs in the test): the entries are created."""
    _vtherm(hass, "vt-bedroom", "Bedroom")
    defaults = _defaults(hass, dict(DEV_DEFAULTS))
    assert await smartpi_init.async_migrate_entry(hass, defaults)
    with (
        patch.object(smartpi_init, "_reload_smartpi_vtherms", AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
    ):
        assert await REAL_SETUP_ENTRY(hass, defaults)
        await hass.async_block_till_done()

    assert set(_own_entries(hass)) == {"vt-bedroom"}
    assert CONF_SMART_PI_SETTINGS_TO_MOVE not in defaults.options


async def test_an_entry_already_migrated_is_left_as_it_is(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, data={}, options=dict(RELEASE_DEFAULTS), version=1, minor_version=2
    )
    entry.add_to_hass(hass)

    assert await smartpi_init.async_migrate_entry(hass, entry)
    assert dict(entry.options) == RELEASE_DEFAULTS and entry.minor_version == 2


async def test_a_thermostat_whose_entry_is_not_created_is_not_named_in_the_notice(hass) -> None:
    """Another integration's climate entity is not a thermostat on SmartPI; an import that
    creates no entry (aborted) leaves the thermostat out of the notice."""
    _vtherm(hass, "vt-bedroom", "Bedroom")
    er.async_get(hass).async_get_or_create("climate", "generic_thermostat", "gt-office")
    defaults = _defaults(hass, {**DEV_DEFAULTS, CONF_MINIMAL_ACTIVATION_DELAY: 20})
    assert await smartpi_init.async_migrate_entry(hass, defaults)
    init = AsyncMock(return_value={"type": "abort", "reason": "already_configured"})

    with patch.object(hass.config_entries.flow, "async_init", init):
        await smartpi_init._async_move_settings(hass, defaults.entry_id)

    assert [call.kwargs["data"][CONF_TARGET_VTHERM] for call in init.await_args_list] == ["vt-bedroom"]
    assert CONF_SMART_PI_SETTINGS_TO_MOVE not in defaults.options
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_THERMOSTAT_ENTRIES_CREATED) is None


async def test_moving_the_settings_of_a_removed_entry_does_nothing(hass) -> None:
    """The defaults entry removed before Home Assistant has started: nothing is created."""
    _vtherm(hass, "vt-bedroom", "Bedroom")

    await smartpi_init._async_move_settings(hass, "removed-entry")

    assert _own_entries(hass) == {}
