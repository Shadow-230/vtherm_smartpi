"""The handler reads whether the boiler is called: from Smart Boiler, else from VT's central boiler."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE
from custom_components.vtherm_smartpi.handler import (
    SMART_BOILER_API_KEY,
    VT_CENTRAL_BOILER_UNIQUE_ID,
    VT_DOMAIN,
    SmartPIHandler,
    vtherm_central_boiler_on,
)
from custom_components.vtherm_smartpi.smartpi.boiler_call import (
    REASON_NOT_CALLED,
    SOURCE_SMART_BOILER,
    SOURCE_VTHERM,
    BoilerReading,
)
from tests.test_smartpi_water_handler import _Api, _control, _water


def _handler(runtime, heating_type="radiator"):
    runtime.entry_infos[CONF_SMART_PI_HEATING_TYPE] = heating_type
    handler = SmartPIHandler(runtime)
    handler.init_algorithm()
    handler._async_save = AsyncMock()
    return handler


def _vt_boiler(monkeypatch, on):
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.vtherm_central_boiler_on", lambda hass: on)


@pytest.mark.parametrize("reason", ["below_threshold", "no_demand", "activation_delay", "boiler_fault"])
def test_smart_boiler_says_why_it_does_not_call_the_boiler(fake_handler_runtime, reason):
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api(
        {fake_handler_runtime.entity_id: {**_water(), "boiler_called": False, "call_reason": reason}}
    )
    assert _handler(fake_handler_runtime)._read_boiler() == BoilerReading(False, reason, SOURCE_SMART_BOILER)


def test_smart_boiler_s_demand_against_its_thresholds_is_kept_for_the_diagnostics(fake_handler_runtime):
    fields = {
        "boiler_called": False,
        "call_reason": "below_threshold",
        "demand_count": 1,
        "count_threshold": 2,
        "count_by": "devices",
        "demand_power_kw": 0.4,
        "power_threshold_kw": 0.6,
        "demand_opening": 0.2,
        "opening_threshold": None,  # the criterion is not set
    }
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: {**_water(), **fields}})
    reading = _handler(fake_handler_runtime)._read_boiler()
    assert reading.demand == {
        "demand_count": 1,
        "count_threshold": 2,
        "count_by": "devices",
        "demand_power_kw": 0.4,
        "power_threshold_kw": 0.6,
        "demand_opening": 0.2,
    }


def test_whole_numbers_given_as_floats_count(fake_handler_runtime):
    fields = {"boiler_called": True, "call_reason": None, "demand_count": 1.0, "count_threshold": 2.0}
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: {**_water(), **fields}})
    assert _handler(fake_handler_runtime)._read_boiler().demand == {"demand_count": 1, "count_threshold": 2}


def test_an_older_feature_manager_without_the_fields_falls_back_to_vt(fake_handler_runtime, monkeypatch):
    fake_handler_runtime.get_feature_manager = lambda name: SimpleNamespace(supply_temperature=45.0)
    fake_handler_runtime.is_used_by_central_boiler = True
    _vt_boiler(monkeypatch, True)
    assert _handler(fake_handler_runtime)._read_boiler() == BoilerReading(True, None, SOURCE_VTHERM)


def test_badly_typed_demand_fields_are_left_out(fake_handler_runtime):
    fields = {
        "boiler_called": True,
        "call_reason": None,
        "demand_count": True,
        "count_threshold": "2",
        "count_by": "",
        "demand_power_kw": float("nan"),
        "power_threshold_kw": 0.6,
    }
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: {**_water(), **fields}})
    assert _handler(fake_handler_runtime)._read_boiler().demand == {"power_threshold_kw": 0.6}


def test_the_feature_manager_gives_the_call_too(fake_handler_runtime):
    manager = SimpleNamespace(boiler_called=True, call_reason=None)
    fake_handler_runtime.get_feature_manager = lambda name: manager
    assert _handler(fake_handler_runtime)._read_boiler() == BoilerReading(True, None, SOURCE_SMART_BOILER)


@pytest.mark.parametrize(
    "fields",
    [
        {},  # an older Smart Boiler, without the fields
        {"boiler_called": None, "call_reason": "not_controlling"},
        {"boiler_called": None, "call_reason": "waiting_data"},
        {"boiler_called": False, "call_reason": "a_reason_smartpi_does_not_know"},
        {"boiler_called": "no", "call_reason": "no_demand"},
    ],
)
def test_without_smart_boiler_s_word_vt_s_central_boiler_is_read(fake_handler_runtime, monkeypatch, fields):
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: {**_water(), **fields}})
    fake_handler_runtime.is_used_by_central_boiler = True
    _vt_boiler(monkeypatch, False)
    assert _handler(fake_handler_runtime)._read_boiler() == BoilerReading(False, REASON_NOT_CALLED, SOURCE_VTHERM)


def test_vt_s_central_boiler_counts_only_for_a_thermostat_it_serves(fake_handler_runtime, monkeypatch):
    _vt_boiler(monkeypatch, False)
    fake_handler_runtime.is_used_by_central_boiler = False
    assert _handler(fake_handler_runtime)._read_boiler() is None
    fake_handler_runtime.is_used_by_central_boiler = True
    _vt_boiler(monkeypatch, None)
    assert _handler(fake_handler_runtime)._read_boiler() is None
    _vt_boiler(monkeypatch, True)
    assert _handler(fake_handler_runtime)._read_boiler() == BoilerReading(True, None, SOURCE_VTHERM)


async def test_control_hands_the_call_to_the_algorithm(fake_handler_runtime, monkeypatch):
    monkeypatch.setattr(
        "custom_components.vtherm_smartpi.handler.er.async_get",
        lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)),
    )
    fake_handler_runtime.is_used_by_central_boiler = True
    _vt_boiler(monkeypatch, False)
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    assert fake_handler_runtime.prop_algorithm.boiler.reading == BoilerReading(False, REASON_NOT_CALLED, SOURCE_VTHERM)


async def test_an_electric_heating_type_does_not_read_the_boiler(fake_handler_runtime, monkeypatch):
    monkeypatch.setattr(
        "custom_components.vtherm_smartpi.handler.er.async_get",
        lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)),
    )
    fake_handler_runtime.is_used_by_central_boiler = True
    _vt_boiler(monkeypatch, False)
    handler = _handler(fake_handler_runtime, heating_type="electric_fast")
    await _control(handler)
    assert fake_handler_runtime.prop_algorithm.boiler.reading is None


async def test_vt_s_central_boiler_is_read_from_its_binary_sensor(hass):
    assert vtherm_central_boiler_on(hass) is None
    entry = er.async_get(hass).async_get_or_create(
        "binary_sensor", VT_DOMAIN, VT_CENTRAL_BOILER_UNIQUE_ID, suggested_object_id="central_boiler"
    )
    hass.states.async_set(entry.entity_id, "on")
    assert vtherm_central_boiler_on(hass) is True
    hass.states.async_set(entry.entity_id, "off")
    assert vtherm_central_boiler_on(hass) is False
    hass.states.async_set(entry.entity_id, "unavailable")
    assert vtherm_central_boiler_on(hass) is None
