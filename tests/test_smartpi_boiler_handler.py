"""The handler reads whether the boiler is called, from the source chosen in the options."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.vtherm_smartpi import heating_inputs
from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_REASON_ENTITY,
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_FAULT_ENTITY,
    CONF_SMART_PI_HEATING_TYPE,
    DOMAIN,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.heating_inputs import PROVIDERS_KEY
from custom_components.vtherm_smartpi.smartpi.boiler_call import REASON_NOT_CALLED, SOURCE_VTHERM, BoilerReading
from tests.test_smartpi_water_handler import PROVIDER, _control, _Provider, _States, _water

CALLED = "binary_sensor.boiler_called"
REASON = "sensor.boiler_reason"
FAULT = "binary_sensor.boiler_fault"
VT_ENTITIES = {
    heating_inputs.VT_CENTRAL_BOILER_UNIQUE_ID: "binary_sensor.central_boiler",
    heating_inputs.VT_ACTIVE_COUNT_UNIQUE_ID: "sensor.nb_device_active_boiler",
    heating_inputs.VT_COUNT_THRESHOLD_UNIQUE_ID: "number.boiler_activation_threshold",
    heating_inputs.VT_ACTIVE_POWER_UNIQUE_ID: "sensor.total_power_active_boiler",
    heating_inputs.VT_POWER_THRESHOLD_UNIQUE_ID: "number.boiler_power_activation_threshold",
}


@pytest.fixture
def no_registry(monkeypatch):
    monkeypatch.setattr(
        "custom_components.vtherm_smartpi.handler.er.async_get",
        lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)),
    )


def _handler(runtime, heating_type="radiator"):
    if heating_type is None:
        runtime.smartpi_options.pop(CONF_SMART_PI_HEATING_TYPE, None)
    else:
        runtime.smartpi_options[CONF_SMART_PI_HEATING_TYPE] = heating_type
    handler = SmartPIHandler(runtime)
    handler.init_algorithm()
    handler._async_save = AsyncMock()
    return handler


def _provider_call(runtime, fields) -> _Provider:
    provider = _Provider({runtime.entity_id: fields})
    runtime.hass.data.setdefault(PROVIDERS_KEY, {})["test"] = provider
    runtime.smartpi_options[CONF_SMART_PI_BOILER_CALL_SOURCE] = PROVIDER
    return provider


def _vt_boiler(monkeypatch, runtime, on, power=None, threshold=None, used=True):
    """VT's central boiler: its binary sensor and, if given, its power against the threshold (W)."""
    monkeypatch.setattr(heating_inputs, "_vt_entity_id", lambda hass, domain, unique_id: VT_ENTITIES.get(unique_id))
    runtime.is_used_by_central_boiler = used
    states = {VT_ENTITIES[heating_inputs.VT_CENTRAL_BOILER_UNIQUE_ID]: on}
    if power is not None:
        states[VT_ENTITIES[heating_inputs.VT_ACTIVE_POWER_UNIQUE_ID]] = (str(power), {"unit_of_measurement": "W"})
        states[VT_ENTITIES[heating_inputs.VT_POWER_THRESHOLD_UNIQUE_ID]] = (str(threshold), {"unit_of_measurement": "W"})
    runtime.hass.states = _States({k: v for k, v in states.items() if v is not None})


@pytest.mark.parametrize("reason", ["below_threshold", "no_demand", "activation_delay", "boiler_fault"])
def test_a_provider_says_why_it_does_not_call_the_boiler(fake_handler_runtime, reason):
    _provider_call(fake_handler_runtime, {**_water(), "boiler_called": False, "call_reason": reason})
    reading, inputs = _handler(fake_handler_runtime)._read_boiler()
    assert reading == BoilerReading(False, reason, PROVIDER)
    assert inputs["called"] == {"source": PROVIDER, "available": True, "data": True}


def test_a_provider_s_demand_is_kept_for_the_diagnostics(fake_handler_runtime):
    demand = {"demand_count": 1, "count_threshold": 2, "count_by": "devices", "demand_power_kw": 0.4, "power_threshold_kw": 0.6}
    _provider_call(fake_handler_runtime, {"boiler_called": False, "call_reason": "below_threshold", "demand": demand})
    assert _handler(fake_handler_runtime)._read_boiler()[0].demand == demand


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"boiler_called": None, "call_reason": "not_controlling"},
        {"boiler_called": False, "call_reason": "a_reason_smartpi_does_not_know"},
        {"boiler_called": "no", "call_reason": "no_demand"},
    ],
)
def test_a_provider_s_call_not_given_is_not_known_even_with_vt_s_boiler(fake_handler_runtime, monkeypatch, fields):
    """No fallback: the source is chosen explicitly."""
    _provider_call(fake_handler_runtime, fields)
    _vt_boiler(monkeypatch, fake_handler_runtime, "off")
    fake_handler_runtime.smartpi_options[CONF_SMART_PI_BOILER_CALL_SOURCE] = PROVIDER
    assert _handler(fake_handler_runtime)._read_boiler()[0] is None


def test_the_call_from_entities(fake_handler_runtime):
    fake_handler_runtime.hass.states = _States({CALLED: "off", REASON: "activation_delay", FAULT: "off"})
    fake_handler_runtime.smartpi_options.update(
        {
            CONF_SMART_PI_BOILER_CALL_SOURCE: "entities",
            CONF_SMART_PI_BOILER_SIGNALS: "own",
            CONF_SMART_PI_BOILER_CALLED_ENTITY: CALLED,
            CONF_SMART_PI_BOILER_REASON_ENTITY: REASON,
            CONF_SMART_PI_FAULT_ENTITY: FAULT,
        }
    )
    reading, inputs = _handler(fake_handler_runtime)._read_boiler()
    assert reading == BoilerReading(False, "activation_delay", "entities")
    assert inputs["reason"] == {"source": "entity", "entity": REASON, "data": True}


def test_the_call_from_the_defaults_signals(fake_handler_runtime):
    fake_handler_runtime.hass.states = _States({"binary_sensor.shared_call": "on"})
    fake_handler_runtime.hass.config_entries._entries.append(
        SimpleNamespace(unique_id=DOMAIN, data={CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor.shared_call"}, options={})
    )
    fake_handler_runtime.smartpi_options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "entities"
    assert _handler(fake_handler_runtime)._read_boiler()[0] == BoilerReading(True, None, "entities")


def test_an_entry_without_a_choice_reads_vt_s_central_boiler_where_it_serves_the_thermostat(fake_handler_runtime, monkeypatch):
    _vt_boiler(monkeypatch, fake_handler_runtime, "off", power=400, threshold=600)
    reading, inputs = _handler(fake_handler_runtime)._read_boiler()
    assert reading.reason == "below_threshold"
    assert reading.source == SOURCE_VTHERM
    assert reading.demand == {"count_by": "devices", "demand_power_kw": 0.4, "power_threshold_kw": 0.6}
    assert inputs["called"]["entity"] == "binary_sensor.central_boiler"
    _vt_boiler(monkeypatch, fake_handler_runtime, "off", used=False)
    assert _handler(fake_handler_runtime)._read_boiler() == (None, None)


def test_vt_s_central_boiler_counts_only_for_a_thermostat_it_serves(fake_handler_runtime, monkeypatch):
    fake_handler_runtime.smartpi_options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "vtherm_central_boiler"
    _vt_boiler(monkeypatch, fake_handler_runtime, "off", used=False)
    reading, inputs = _handler(fake_handler_runtime)._read_boiler()
    assert reading is None
    assert inputs["called"] == {"source": SOURCE_VTHERM, "data": False}
    _vt_boiler(monkeypatch, fake_handler_runtime, None)
    assert _handler(fake_handler_runtime)._read_boiler()[0] is None
    _vt_boiler(monkeypatch, fake_handler_runtime, "on")
    assert _handler(fake_handler_runtime)._read_boiler()[0] == BoilerReading(True, None, SOURCE_VTHERM)
    _vt_boiler(monkeypatch, fake_handler_runtime, "off")
    assert _handler(fake_handler_runtime)._read_boiler()[0] == BoilerReading(False, REASON_NOT_CALLED, SOURCE_VTHERM)


def test_no_call_source_reads_nothing(fake_handler_runtime, monkeypatch):
    _vt_boiler(monkeypatch, fake_handler_runtime, "off")
    fake_handler_runtime.smartpi_options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "none"
    assert _handler(fake_handler_runtime)._read_boiler() == (None, None)


async def test_control_hands_the_call_to_the_algorithm(fake_handler_runtime, monkeypatch, no_registry):
    _vt_boiler(monkeypatch, fake_handler_runtime, "off")
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    algo = fake_handler_runtime.prop_algorithm
    assert algo.boiler.reading == BoilerReading(False, REASON_NOT_CALLED, SOURCE_VTHERM)
    block = algo.boiler_diagnostics()
    assert block["source"] == SOURCE_VTHERM
    assert block["inputs"]["called"]["data"] is True


async def test_a_chosen_source_without_data_still_shows_in_the_diagnostics(fake_handler_runtime, no_registry):
    fake_handler_runtime.hass.states = _States({})
    fake_handler_runtime.smartpi_options.update(
        {CONF_SMART_PI_BOILER_CALL_SOURCE: "entities", CONF_SMART_PI_BOILER_SIGNALS: "own", CONF_SMART_PI_BOILER_CALLED_ENTITY: CALLED}
    )
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    block = fake_handler_runtime.prop_algorithm.boiler_diagnostics()
    assert block["source"] == "entities"
    assert block["called"] is None
    assert block["inputs"]["called"] == {"source": "entity", "entity": CALLED, "data": False}


@pytest.mark.parametrize("heating_type", ["electric_fast", None])
async def test_an_electric_or_no_heating_type_does_not_read_the_boiler(
    fake_handler_runtime, monkeypatch, no_registry, heating_type
):
    """Not set is not used: no boiler's call, as in the release."""
    _vt_boiler(monkeypatch, fake_handler_runtime, "off")
    handler = _handler(fake_handler_runtime, heating_type=heating_type)
    await _control(handler)
    assert fake_handler_runtime.prop_algorithm.boiler.reading is None
    assert fake_handler_runtime.prop_algorithm.boiler_diagnostics() is None


@pytest.mark.parametrize("humidity", [62.0, None])
async def test_control_hands_the_room_s_humidity_to_the_algorithm(fake_handler_runtime, no_registry, humidity):
    """Versatile Thermostat's humidity feature; a thermostat without a sensor gives None."""
    fake_handler_runtime.current_humidity = humidity
    handler = _handler(fake_handler_runtime, heating_type="electric_fast")
    await _control(handler)
    assert fake_handler_runtime.prop_algorithm._humidity == humidity
