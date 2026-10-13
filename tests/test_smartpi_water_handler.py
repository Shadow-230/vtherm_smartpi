"""The handler reads Smart Boiler's water data and publishes the factor it applies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE, DOMAIN
from custom_components.vtherm_smartpi.handler import (
    SMART_BOILER_API_KEY,
    SMART_BOILER_MANAGER,
    WATER_FACTOR_KEY,
    WATER_ISSUE_PREFIX,
    SmartPIHandler,
)
from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase
from custom_components.vtherm_smartpi.smartpi.guards import GuardAction


def _water(supply=45.0, supply_reason=None, heat_available=True, heat_reason=None):
    return {
        "supply_temperature": supply,
        "supply_reason": supply_reason,
        "heat_available": heat_available,
        "heat_reason": heat_reason,
    }


class _Api:
    def __init__(self, data=None, error=None):
        self.data = data or {}
        self.error = error
        self.calls = []

    def zone_water(self, entity_id):
        self.calls.append(entity_id)
        if self.error:
            raise self.error
        return self.data.get(entity_id)


def _handler(runtime, heating_type="radiator"):
    runtime.entry_infos[CONF_SMART_PI_HEATING_TYPE] = heating_type
    handler = SmartPIHandler(runtime)
    handler.init_algorithm()
    handler._async_save = AsyncMock()
    return handler


def test_reads_the_public_api_by_entity_id(fake_handler_runtime):
    api = _Api({fake_handler_runtime.entity_id: _water(52.5)})
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = api
    reading = _handler(fake_handler_runtime)._read_water()
    assert reading.supply_c == 52.5
    assert reading.heat_available is True
    assert api.calls == [fake_handler_runtime.entity_id]


def test_prefers_the_feature_manager(fake_handler_runtime):
    manager = SimpleNamespace(supply_temperature=48.0, supply_reason=None, heat_available=False, heat_reason="dhw_active")
    names = []

    def get_feature_manager(name):
        names.append(name)
        return manager

    fake_handler_runtime.get_feature_manager = get_feature_manager
    api = _Api({fake_handler_runtime.entity_id: _water(30.0)})
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = api
    reading = _handler(fake_handler_runtime)._read_water()
    assert names == [SMART_BOILER_MANAGER]
    assert reading.supply_c == 48.0
    assert reading.heat_reason == "dhw_active"
    assert api.calls == []


def test_falls_back_to_the_api_when_the_thermostat_has_no_manager(fake_handler_runtime):
    fake_handler_runtime.get_feature_manager = lambda name: None
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: _water(41.0)})
    assert _handler(fake_handler_runtime)._read_water().supply_c == 41.0


@pytest.mark.parametrize("setup", ["no_api", "not_a_zone", "api_raises", "manager_raises", "api_without_function"])
def test_any_failure_means_no_data(fake_handler_runtime, setup):
    if setup == "not_a_zone":
        fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({})
    elif setup == "api_raises":
        fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api(error=RuntimeError("boom"))
    elif setup == "manager_raises":
        def broken(_name):
            raise KeyError("no manager")

        fake_handler_runtime.get_feature_manager = broken
    elif setup == "api_without_function":
        fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = object()
    assert _handler(fake_handler_runtime)._read_water() is None


def test_badly_typed_fields_are_no_data(fake_handler_runtime):
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api(
        {fake_handler_runtime.entity_id: {"supply_temperature": "45", "supply_reason": 3, "heat_available": "yes", "heat_reason": ""}}
    )
    reading = _handler(fake_handler_runtime)._read_water()
    assert reading.supply_c is None
    assert reading.supply_reason is None
    assert reading.heat_available is None
    assert reading.heat_reason is None


async def _control(handler, phase=SmartPIPhase.STABLE):
    algo = handler._thermostat.prop_algorithm
    algo.guards.check_guard_cut = MagicMock(return_value=GuardAction.NONE)
    algo.guards.check_guard_kick = MagicMock(return_value=GuardAction.NONE)
    with (
        patch.object(type(algo), "phase", new_callable=PropertyMock, return_value=phase),
        patch.object(algo, "calculate", MagicMock()),
    ):
        await handler.control_heating(timestamp=None)


async def test_control_applies_the_water_and_publishes_the_factor(fake_handler_runtime, monkeypatch):
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.er.async_get", lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)))
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: _water(40.0)})
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {}}
    handler = _handler(fake_handler_runtime)
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", MagicMock())
    await _control(handler)
    algo = fake_handler_runtime.prop_algorithm
    assert isinstance(algo, SmartPI)
    assert 0.0 < algo.water_factor < 1.0
    handler.update_attributes()
    assert fake_handler_runtime._attr_extra_state_attributes["specific_states"][WATER_FACTOR_KEY] == pytest.approx(algo.water_factor, abs=1e-3)


@pytest.mark.parametrize("data", [None, _water(supply=None, supply_reason="circuit_not_measured", heat_available=None, heat_reason="circuit_not_measured")])
async def test_without_water_data_in_use_the_key_is_absent(fake_handler_runtime, monkeypatch, data):
    # Present means "uses the water data": Smart Boiler then skips its pause on water swings.
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.er.async_get", lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)))
    if data is not None:
        fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = _Api({fake_handler_runtime.entity_id: data})
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {WATER_FACTOR_KEY: 0.4}}
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    handler.update_attributes()
    assert WATER_FACTOR_KEY not in fake_handler_runtime._attr_extra_state_attributes["specific_states"]
    assert fake_handler_runtime.prop_algorithm.water_factor == 1.0


async def test_a_momentary_gap_keeps_publishing_the_average(fake_handler_runtime, monkeypatch):
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.er.async_get", lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)))
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", MagicMock())
    api = _Api({fake_handler_runtime.entity_id: _water(40.0)})
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = api
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {}}
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    api.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="flow_stale", heat_available=None, heat_reason="flow_stale")
    await _control(handler)
    handler.update_attributes()
    assert fake_handler_runtime._attr_extra_state_attributes["specific_states"][WATER_FACTOR_KEY] == pytest.approx(
        fake_handler_runtime.prop_algorithm.water_factor, abs=1e-3
    )


async def test_an_electric_type_neither_reads_nor_publishes(fake_handler_runtime, monkeypatch):
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.er.async_get", lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)))
    api = _Api({fake_handler_runtime.entity_id: _water(40.0)})
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = api
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {WATER_FACTOR_KEY: 0.5}}
    handler = _handler(fake_handler_runtime, heating_type="electric_fast")
    await _control(handler)
    handler.update_attributes()
    assert api.calls == []
    assert WATER_FACTOR_KEY not in fake_handler_runtime._attr_extra_state_attributes["specific_states"]


@pytest.mark.parametrize("phase, offered", [(SmartPIPhase.STABLE, True), (SmartPIPhase.HYSTERESIS, False)])
async def test_water_appearing_offers_the_model_reset_once_learned(fake_handler_runtime, monkeypatch, phase, offered):
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    api = _Api({fake_handler_runtime.entity_id: _water(supply=None, supply_reason="not_mapped", heat_available=None, heat_reason="not_mapped")})
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = api
    handler = _handler(fake_handler_runtime)
    await _control(handler, phase)
    assert not create_issue.called
    api.data[fake_handler_runtime.entity_id] = _water(45.0)
    await _control(handler, phase)
    assert create_issue.called is offered
    if offered:
        args, kwargs = create_issue.call_args
        assert args[1] == DOMAIN
        assert args[2] == f"{WATER_ISSUE_PREFIX}{fake_handler_runtime.unique_id}"
        assert kwargs["translation_key"] == "water_data_appeared"
        assert kwargs["is_fixable"] is True
        assert kwargs["data"] == {"entity_id": fake_handler_runtime.entity_id, "name": fake_handler_runtime.name}
        assert kwargs["translation_placeholders"] == {"name": fake_handler_runtime.name}


async def test_water_going_for_good_offers_the_model_reset(fake_handler_runtime, monkeypatch):
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    api = _Api({fake_handler_runtime.entity_id: _water(45.0)})
    fake_handler_runtime.hass.data[SMART_BOILER_API_KEY] = api
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    create_issue.reset_mock()
    api.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="circuit_not_measured", heat_available=None, heat_reason="circuit_not_measured")
    await _control(handler)
    assert create_issue.call_args.kwargs["translation_key"] == "water_data_gone"
    assert fake_handler_runtime.prop_algorithm.water_factor == 1.0
    # A momentary gap is no change for good.
    create_issue.reset_mock()
    api.data[fake_handler_runtime.entity_id] = _water(45.0)
    await _control(handler)
    create_issue.reset_mock()
    api.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="flow_stale", heat_available=None, heat_reason="flow_stale")
    await _control(handler)
    assert not create_issue.called
