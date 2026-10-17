"""The settings of a thermostat's entry: the heating type first, the "thermostat only" settings and
"Use the SmartPI defaults"; the defaults entry's shared settings; creating an entry through the menu.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
    SHARED_SETTINGS,
    THERMOSTAT_SETTINGS,
)

from flow_helpers import choose, configure, default, fields, options, save

VT_DOMAIN = "versatile_thermostat"
SHARED = {key: DEFAULT_OPTIONS[key] for key in SHARED_SETTINGS}


@pytest.fixture(autouse=True)
def _entries_are_not_set_up():
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        yield


def _vtherm(hass, unique_id: str = "vt-living", object_id: str = "living", **config):
    vt_entry = MockConfigEntry(domain="test_vtherm", data={CONF_PROP_FUNCTION: PROP_FUNCTION_SMART_PI, **config})
    vt_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, unique_id, suggested_object_id=object_id, config_entry=vt_entry
    )


def _defaults(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="SmartPI defaults", unique_id=DOMAIN, data={**SHARED, **options})
    entry.add_to_hass(hass)
    return entry


def _entry(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-vt-living",
        title="Living SmartPI",
        data={CONF_TARGET_VTHERM: "vt-living"},
        options={CONF_TARGET_VTHERM: "vt-living", **options},
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_heating_type_comes_first_then_the_thermostat_only_settings_and_the_switch(hass) -> None:
    _vtherm(hass, thermostat_type="thermostat_over_valve")
    entry = _entry(hass, **{CONF_SMART_PI_USE_DEFAULTS: True})
    result = await choose(hass, await options(hass, entry), "settings")

    assert fields(result) == [
        CONF_SMART_PI_HEATING_TYPE,
        CONF_MINIMAL_ACTIVATION_DELAY,
        CONF_MINIMAL_DEACTIVATION_DELAY,
        CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
        CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
        CONF_SMART_PI_USE_DEFAULTS,
    ]
    assert default(result, CONF_SMART_PI_HEATING_TYPE) == "not_set"
    assert default(result, CONF_SMART_PI_USE_DEFAULTS) is True


async def test_not_set_is_stored_as_no_key(hass) -> None:
    _vtherm(hass)
    entry = _entry(hass, **{CONF_SMART_PI_USE_DEFAULTS: True, CONF_SMART_PI_HEATING_TYPE: "radiator"})
    result = await choose(hass, await options(hass, entry), "settings")
    assert default(result, CONF_SMART_PI_HEATING_TYPE) == "radiator"
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "not_set", CONF_SMART_PI_USE_DEFAULTS: True})
    result = await save(hass, result, reset=False)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SMART_PI_HEATING_TYPE not in entry.options


async def test_with_the_defaults_the_entry_holds_no_shared_setting(hass) -> None:
    """Switched on, a thermostat's own values of the shared settings go: the defaults' apply."""
    _vtherm(hass)
    entry = _entry(hass, **{**SHARED, CONF_SMART_PI_USE_DEFAULTS: False, CONF_SMART_PI_DEADBAND: 0.4})
    result = await choose(hass, await options(hass, entry), "settings")
    assert default(result, CONF_SMART_PI_USE_DEFAULTS) is False
    result = await configure(
        hass, result, {CONF_SMART_PI_USE_DEFAULTS: True, CONF_MINIMAL_ACTIVATION_DELAY: 30, CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE: True}
    )
    assert result["step_id"] == "thermostat_menu"
    result = await save(hass, result)

    assert entry.options[CONF_SMART_PI_USE_DEFAULTS] is True
    assert not set(SHARED_SETTINGS) & set(entry.options)
    assert entry.options[CONF_MINIMAL_ACTIVATION_DELAY] == 30
    assert entry.options[CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE] is True


async def test_without_the_defaults_the_own_values_start_as_the_defaults(hass) -> None:
    _vtherm(hass)
    _defaults(hass, **{CONF_SMART_PI_DEADBAND: 0.3})
    entry = _entry(hass, **{CONF_SMART_PI_USE_DEFAULTS: True})
    result = await choose(hass, await options(hass, entry), "settings")
    result = await configure(hass, result, {CONF_SMART_PI_USE_DEFAULTS: False})

    assert result["step_id"] == "settings_own"
    assert fields(result) == list(SHARED_SETTINGS)
    assert default(result, CONF_SMART_PI_DEADBAND) == 0.3
    result = await configure(hass, result, {CONF_SMART_PI_DEADBAND: 0.5})
    result = await save(hass, result)

    assert entry.options[CONF_SMART_PI_USE_DEFAULTS] is False
    assert entry.options[CONF_SMART_PI_DEADBAND] == 0.5
    assert set(SHARED_SETTINGS) <= set(entry.options)


async def test_own_values_already_held_are_shown(hass) -> None:
    _vtherm(hass)
    _defaults(hass, **{CONF_SMART_PI_DEADBAND: 0.3})
    entry = _entry(hass, **{**SHARED, CONF_SMART_PI_USE_DEFAULTS: False, CONF_SMART_PI_DEADBAND: 0.6})
    result = await choose(hass, await options(hass, entry), "settings")
    result = await configure(hass, result, {CONF_SMART_PI_USE_DEFAULTS: False})
    assert default(result, CONF_SMART_PI_DEADBAND) == 0.6


async def test_the_valve_curve_follows_its_switch_and_is_a_menu_item(hass) -> None:
    _vtherm(hass, thermostat_type="thermostat_over_valve")
    entry = _entry(hass, **{CONF_SMART_PI_USE_DEFAULTS: True})
    result = await choose(hass, await options(hass, entry), "settings")
    result = await configure(hass, result, {CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: True, CONF_SMART_PI_USE_DEFAULTS: True})
    assert result["step_id"] == "valve_curve"
    result = await configure(hass, result, {CONF_SMART_PI_KNEE_VALVE: 20})
    assert "valve_curve" in result["menu_options"]

    result = await choose(hass, result, "valve_curve")
    assert default(result, CONF_SMART_PI_KNEE_VALVE) == 20
    result = await configure(hass, result, {CONF_SMART_PI_KNEE_VALVE: 25})
    result = await save(hass, result)

    assert entry.options[CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION] is True
    assert entry.options[CONF_SMART_PI_KNEE_VALVE] == 25


async def test_the_defaults_regulator_item_holds_the_shared_settings_only(hass) -> None:
    entry = _defaults(hass)
    result = await options(hass, entry)
    assert result["menu_options"] == ["regulator", "boiler_signals", "save"]
    result = await choose(hass, result, "regulator")

    assert fields(result) == list(SHARED_SETTINGS)
    assert not set(THERMOSTAT_SETTINGS) & set(fields(result))
    result = await configure(hass, result, {**SHARED, CONF_SMART_PI_DEADBAND: 0.2})
    assert result["description_placeholders"]["changes"] == "Regulator settings"
    result = await save(hass, result)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_DEADBAND] == 0.2


async def test_a_new_entry_is_created_through_the_menu(hass) -> None:
    """It starts with "Use the SmartPI defaults" on and nothing of its own; it is created at save."""
    living = _vtherm(hass)
    hass.states.async_set(living.entity_id, "heat", {"friendly_name": "Living"})
    _defaults(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TARGET_VTHERM: living.entity_id})
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "thermostat_menu"
    assert result["menu_options"] == ["settings", "zone", "dependencies", "save"]

    result = await choose(hass, result, "settings")
    assert default(result, CONF_SMART_PI_USE_DEFAULTS) is True
    result = await configure(hass, result, {CONF_SMART_PI_HEATING_TYPE: "floor_water", CONF_SMART_PI_USE_DEFAULTS: True})
    assert "heating_inputs" in result["menu_options"]
    assert result["description_placeholders"]["changes"] == "Heating type and regulation"
    # The thermostat ran without a heating type: the reset question at save.
    result = await save(hass, result, reset=False)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living"
    assert result["data"][CONF_TARGET_VTHERM] == "vt-living"
    assert result["data"][CONF_SMART_PI_USE_DEFAULTS] is True
    assert result["data"][CONF_SMART_PI_HEATING_TYPE] == "floor_water"
    assert not set(SHARED_SETTINGS) & set(result["data"])
    assert CONF_SMART_PI_DEPENDENCIES not in result["data"]


async def test_a_new_entry_saved_at_once_holds_nothing_of_its_own(hass) -> None:
    living = _vtherm(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    # The first flow creates the defaults entry.
    assert result["type"] == FlowResultType.CREATE_ENTRY
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TARGET_VTHERM: living.entity_id})
    result = await save(hass, result)
    assert result["data"] == {CONF_TARGET_VTHERM: "vt-living", CONF_SMART_PI_USE_DEFAULTS: True}


async def test_a_thermostat_that_already_has_an_entry_is_refused(hass) -> None:
    living = _vtherm(hass)
    _defaults(hass)
    _entry(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TARGET_VTHERM: living.entity_id})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_an_entity_without_a_unique_id_is_refused(hass) -> None:
    _defaults(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TARGET_VTHERM: "climate.yaml_only"})
    assert result["errors"] == {CONF_TARGET_VTHERM: "invalid_entity"}
