"""How a thermostat's settings are read at run time: per key (settings.py).

Versatile Thermostat's entry without the "thermostat only" settings, then the shared settings
from the defaults entry (or the thermostat's own values when it does not use the defaults),
then the thermostat's own entry. A "thermostat only" setting that is not set is not used.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.vtherm_smartpi import settings as settings_module
from custom_components.vtherm_smartpi.const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEBUG,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_HYSTERESIS_ON,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
    CONF_SMART_PI_MIN_VALVE,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    SHARED_SETTINGS,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.settings import (
    effective_settings,
    entry_options,
    heating_type_of,
    own_heating_type,
    uses_defaults,
)

from fakes.fake_thermostat_runtime import FakeHass

SHARED = {key: DEFAULT_OPTIONS[key] for key in SHARED_SETTINGS}


def _entry(entry_id: str, data: dict, options: dict | None = None, unique_id: str | None = None):
    return SimpleNamespace(entry_id=entry_id, unique_id=unique_id, data=data, options=options or {})


def _hass(*entries) -> FakeHass:
    hass = FakeHass()
    hass.config_entries._entries.extend(entries)
    return hass


def _defaults(**options):
    return _entry("defaults", {}, {**SHARED, **options}, unique_id=DOMAIN)


def _own(options: dict, target: str = "vt-a"):
    return _entry(f"own-{target}", {CONF_TARGET_VTHERM: target}, {CONF_TARGET_VTHERM: target, **options}, f"{DOMAIN}-{target}")


@pytest.fixture(autouse=True)
def _report_again():
    """Each test sees its own "missing setting" errors."""
    settings_module._REPORTED.clear()
    yield
    settings_module._REPORTED.clear()


def test_without_an_entry_of_its_own_a_thermostat_takes_the_shared_settings_from_the_defaults():
    """A "thermostat only" setting left in the defaults (or in VTherm's entry) is not read."""
    hass = _hass(_defaults(**{CONF_SMART_PI_DEADBAND: 0.2, CONF_SMART_PI_HEATING_TYPE: "radiator"}))
    vtherm = {CONF_MINIMAL_ACTIVATION_DELAY: 60, "cycle_min": 10}

    config, own = effective_settings(hass, "vt-a", vtherm)

    assert own is None
    assert config[CONF_SMART_PI_DEADBAND] == 0.2
    assert config["cycle_min"] == 10
    assert CONF_SMART_PI_HEATING_TYPE not in config
    assert CONF_MINIMAL_ACTIVATION_DELAY not in config


def test_an_entry_that_uses_the_defaults_adds_its_thermostat_only_settings():
    own = _own(
        {
            CONF_SMART_PI_USE_DEFAULTS: True,
            CONF_SMART_PI_DEADBAND: 0.9,
            CONF_SMART_PI_HEATING_TYPE: "floor_water",
            CONF_MINIMAL_ACTIVATION_DELAY: 30,
            CONF_SMART_PI_DEPENDENCIES: [],
        }
    )
    hass = _hass(_defaults(**{CONF_SMART_PI_DEADBAND: 0.2}), own)

    config, found = effective_settings(hass, "vt-a", {CONF_MINIMAL_ACTIVATION_DELAY: 60})

    assert found is own
    assert config[CONF_SMART_PI_DEADBAND] == 0.2
    assert config[CONF_SMART_PI_HEATING_TYPE] == "floor_water"
    assert config[CONF_MINIMAL_ACTIVATION_DELAY] == 30
    assert config[CONF_SMART_PI_DEPENDENCIES] == []


def test_an_entry_with_its_own_values_wins_over_the_defaults():
    own = _own({**SHARED, CONF_SMART_PI_USE_DEFAULTS: False, CONF_SMART_PI_DEADBAND: 0.9})
    hass = _hass(_defaults(**{CONF_SMART_PI_DEADBAND: 0.2}), own)

    config, _ = effective_settings(hass, "vt-a")

    assert config[CONF_SMART_PI_DEADBAND] == 0.9


def test_an_entry_from_the_release_holds_a_full_copy():
    """0.4.0: no switch and no heating type; its values are its own, no water data."""
    own = _own(dict(DEFAULT_OPTIONS) | {CONF_SMART_PI_DEADBAND: 0.4})
    hass = _hass(_defaults(**{CONF_SMART_PI_DEADBAND: 0.2}), own)

    config, _ = effective_settings(hass, "vt-a")

    assert not uses_defaults(own.options)
    assert config[CONF_SMART_PI_DEADBAND] == 0.4
    assert heating_type_of(config) is None


def test_a_shared_setting_missing_from_an_entry_is_logged_once(caplog):
    """The defaults entry lacks one (edited by hand): the form's value; a thermostat with its own
    values lacks one: the defaults' value."""
    defaults = _defaults()
    del defaults.options[CONF_SMART_PI_HYSTERESIS_ON]
    defaults.options[CONF_SMART_PI_DEADBAND] = 0.2
    own = _own({**SHARED, CONF_SMART_PI_USE_DEFAULTS: False}, target="vt-b")
    del own.options[CONF_SMART_PI_DEADBAND]
    hass = _hass(defaults, own)

    with caplog.at_level(logging.ERROR):
        for _ in range(2):
            config_a, _ = effective_settings(hass, "vt-a")
            config_b, _ = effective_settings(hass, "vt-b")

    assert config_a[CONF_SMART_PI_HYSTERESIS_ON] == DEFAULT_OPTIONS[CONF_SMART_PI_HYSTERESIS_ON]
    assert config_b[CONF_SMART_PI_DEADBAND] == 0.2
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 2
    assert {CONF_SMART_PI_HYSTERESIS_ON, CONF_SMART_PI_DEADBAND} == {
        key for key in SHARED_SETTINGS for record in errors if key in record.getMessage()
    }


def test_without_a_defaults_entry_the_form_values_are_used(caplog):
    with caplog.at_level(logging.ERROR):
        config, _ = effective_settings(_hass(), "vt-a")
    assert all(config[key] == DEFAULT_OPTIONS[key] for key in SHARED_SETTINGS)
    assert "missing" in caplog.text


def test_saved_options_replace_the_data_as_a_whole():
    """A heating type set at creation and then "Not set" in the options is not taken back."""
    entry = _entry("e", {CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_TARGET_VTHERM: "vt-a"}, {CONF_TARGET_VTHERM: "vt-a"})
    assert entry_options(entry) == {CONF_TARGET_VTHERM: "vt-a"}
    assert entry_options(_entry("e", {"a": 1})) == {"a": 1}
    assert entry_options(None) == {}


def test_the_heating_type_is_the_thermostat_s_own():
    hass = _hass(_defaults(**{CONF_SMART_PI_HEATING_TYPE: "radiator"}), _own({CONF_SMART_PI_HEATING_TYPE: "unknown"}))
    assert own_heating_type(hass, "vt-a") is None
    assert own_heating_type(hass, "vt-other") is None
    assert own_heating_type(hass, None) is None


# --- the handler: not set = not used ------------------------------------------------------


def _handler(runtime) -> SmartPIHandler:
    handler = SmartPIHandler(runtime)
    handler.init_algorithm()
    handler._async_save = AsyncMock()
    return handler


def test_a_thermostat_without_its_own_entry_runs_as_in_the_release(fake_handler_runtime):
    """No heating type (no water data, no boiler's call), no delays, no valve curve, no PWM
    restart; VTherm's own TPI delay is not used."""
    runtime = fake_handler_runtime
    runtime.cycle_scheduler.is_valve_mode = True
    runtime.entry_infos = {CONF_MINIMAL_ACTIVATION_DELAY: 90, CONF_MINIMAL_DEACTIVATION_DELAY: 45}
    runtime.hass.config_entries._entries.append(
        _defaults(**{CONF_SMART_PI_HEATING_TYPE: "radiator", CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE: True})
    )

    handler = _handler(runtime)
    algo = runtime.prop_algorithm

    assert (runtime.minimal_activation_delay, runtime.minimal_deactivation_delay) == (0, 0)
    assert not algo.water.uses_water
    assert handler._allow_pwm_cycle_force is False
    assert handler._valve_linearization_configured is False
    assert handler._valve_curve_params is None


def test_the_thermostat_only_settings_of_its_own_entry_are_used(fake_handler_runtime):
    runtime = fake_handler_runtime
    runtime.cycle_scheduler.is_valve_mode = True
    runtime.smartpi_options.update(
        {
            CONF_SMART_PI_HEATING_TYPE: "radiator",
            CONF_MINIMAL_ACTIVATION_DELAY: 30,
            CONF_MINIMAL_DEACTIVATION_DELAY: 20,
            CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE: True,
            CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: True,
            CONF_SMART_PI_MIN_VALVE: 5.0,
            CONF_SMART_PI_KNEE_DEMAND: 70.0,
            CONF_SMART_PI_KNEE_VALVE: 20.0,
            CONF_SMART_PI_MAX_VALVE: 90.0,
        }
    )

    handler = _handler(runtime)

    assert (runtime.minimal_activation_delay, runtime.minimal_deactivation_delay) == (30, 20)
    assert runtime.prop_algorithm.water.uses_water
    assert handler._allow_pwm_cycle_force is True
    assert handler._valve_linearization_configured is True
    assert handler._valve_curve_params.knee_valve == 20.0


def test_a_valve_curve_on_without_its_points_is_reported_and_off(fake_handler_runtime, caplog):
    runtime = fake_handler_runtime
    runtime.cycle_scheduler.is_valve_mode = True
    runtime.smartpi_options[CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION] = True

    handler = _handler(runtime)

    assert handler._valve_linearization_configured is False
    assert handler._valve_curve_params is None
    assert "Invalid SmartPI valve curve parameters" in caplog.text


def test_record_full_diagnostics_follows_the_defaults_for_an_entry_that_uses_them(fake_handler_runtime):
    runtime = fake_handler_runtime
    runtime.hass.config_entries._entries.append(_defaults(**{CONF_SMART_PI_DEBUG: True}))
    runtime.smartpi_options.update({CONF_SMART_PI_USE_DEFAULTS: True, CONF_SMART_PI_DEBUG: False})

    _handler(runtime)

    assert runtime.prop_algorithm._debug_mode is True


async def test_a_save_of_the_defaults_reloads_the_thermostats_that_use_them(hass, monkeypatch):
    """Without an entry of their own directly, with one that uses the defaults through that entry
    (its setup reloads the thermostat once); not one with its own values."""
    from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    import custom_components.vtherm_smartpi as smartpi_init

    registry = er.async_get(hass)
    climates = []
    vt_entries = {}
    for unique_id in ("vt-none", "vt-defaults", "vt-own"):
        vt_entry = MockConfigEntry(domain="test_vtherm", unique_id=unique_id)
        vt_entry.add_to_hass(hass)
        vt_entries[unique_id] = vt_entry.entry_id
        reg = registry.async_get_or_create("climate", "versatile_thermostat", unique_id, config_entry=vt_entry)
        climates.append(SimpleNamespace(entity_id=reg.entity_id, unique_id=unique_id, proportional_function="smartpi"))
    hass.data[CLIMATE_DOMAIN] = SimpleNamespace(entities=climates)
    own_entries = {}
    for unique_id, use in (("vt-defaults", True), ("vt-own", False)):
        own = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}-{unique_id}",
            data={CONF_TARGET_VTHERM: unique_id, CONF_SMART_PI_USE_DEFAULTS: use},
        )
        own.add_to_hass(hass)
        own_entries[unique_id] = own.entry_id
    defaults = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={})
    defaults.add_to_hass(hass)
    reloaded = []

    async def reload(entry_id):
        reloaded.append(entry_id)
        return True

    monkeypatch.setattr(hass.config_entries, "async_reload", reload)

    await smartpi_init._reload_affected_vtherms(hass, defaults)

    assert reloaded == [own_entries["vt-defaults"], vt_entries["vt-none"]]
