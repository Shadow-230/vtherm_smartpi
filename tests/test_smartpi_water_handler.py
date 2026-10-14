"""The handler reads the water data from the chosen source and publishes the factor it applies."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_MODEL_KEPT_FOR_WATER,
    CONF_SMART_PI_SUPPLY_ENTITY,
    CONF_SMART_PI_WATER_SOURCE,
    DOMAIN,
)
from custom_components.vtherm_smartpi.handler import WATER_FACTOR_KEY, WATER_ISSUE_PREFIX, SmartPIHandler
from custom_components.vtherm_smartpi.heating_inputs import PROVIDER_MISSING_WARNING_S, PROVIDERS_KEY
from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase
from custom_components.vtherm_smartpi.smartpi.guards import GuardAction
from custom_components.vtherm_smartpi.smartpi.water_input import AVAILABILITY_USED

PROVIDER = "provider:test"
SUPPLY = "sensor.radiator_supply"
HEAT = "binary_sensor.boiler_heat"
DHW = "binary_sensor.boiler_dhw"


def _water(supply=45.0, supply_reason=None, heat_available=True, heat_reason=None):
    return {
        "supply_temperature": supply,
        "supply_reason": supply_reason,
        "heat_available": heat_available,
        "heat_reason": heat_reason,
    }


class _Provider:
    """A provider of SmartPI's contract (v1): answers from ``data`` by entity id."""

    name = "test"
    version = 1

    def __init__(self, data=None, error=None):
        self.data = data or {}
        self.error = error
        self.calls = []

    def heating_inputs(self, entity_id):
        self.calls.append(entity_id)
        if self.error:
            raise self.error
        return self.data.get(entity_id)


class _States:
    """Home Assistant's states, as the handler reads them."""

    def __init__(self, states=None):
        self.states = dict(states or {})

    def get(self, entity_id):
        value = self.states.get(entity_id)
        if value is None:
            return None
        state, attributes = value if isinstance(value, tuple) else (value, {})
        return SimpleNamespace(state=state, attributes=attributes)


def _provide(runtime, data=None, **kwargs) -> _Provider:
    """Register a test provider and choose it as the water source."""
    provider = _Provider(data, **kwargs)
    runtime.hass.data.setdefault(PROVIDERS_KEY, {})["test"] = provider
    runtime.entry_infos[CONF_SMART_PI_WATER_SOURCE] = PROVIDER
    return provider


def _entities(runtime, states, own=True) -> _States:
    """Choose entities as the water source, the boiler's signals the thermostat's own."""
    runtime.hass.states = _States(states)
    runtime.entry_infos.update({CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_SUPPLY_ENTITY: SUPPLY})
    if own:
        runtime.entry_infos.update(
            {CONF_SMART_PI_BOILER_SIGNALS: "own", CONF_SMART_PI_HEAT_AVAILABLE_ENTITY: HEAT, CONF_SMART_PI_DHW_ENTITY: DHW}
        )
    return runtime.hass.states


def _handler(runtime, heating_type="radiator"):
    runtime.entry_infos[CONF_SMART_PI_HEATING_TYPE] = heating_type
    handler = SmartPIHandler(runtime)
    handler.init_algorithm()
    handler._async_save = AsyncMock()
    return handler


@pytest.fixture
def no_registry(monkeypatch):
    monkeypatch.setattr(
        "custom_components.vtherm_smartpi.handler.er.async_get",
        lambda hass: MagicMock(async_get_entity_id=MagicMock(return_value=None)),
    )


def test_reads_the_chosen_provider_by_entity_id(fake_handler_runtime):
    provider = _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(52.5)})
    reading, inputs = _handler(fake_handler_runtime)._read_water()
    assert reading.supply_c == 52.5
    assert reading.heat_available is True
    assert provider.calls == [fake_handler_runtime.entity_id]
    assert inputs["supply"] == {"source": PROVIDER, "available": True, "data": True}


def test_a_registered_provider_is_not_read_unless_chosen(fake_handler_runtime):
    provider = _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(52.5)})
    fake_handler_runtime.entry_infos.pop(CONF_SMART_PI_WATER_SOURCE)
    reading, inputs = _handler(fake_handler_runtime)._read_water()
    assert reading is None
    assert provider.calls == []
    assert inputs["supply"] == {"source": "none"}


@pytest.mark.parametrize(
    "setup, reason",
    [
        ("not_registered", "provider_missing"),
        ("not_served", "not_served"),
        ("raises", "provider_error"),
        ("without_method", "provider_error"),
    ],
)
def test_any_failure_means_no_data(fake_handler_runtime, setup, reason):
    fake_handler_runtime.entry_infos[CONF_SMART_PI_WATER_SOURCE] = PROVIDER
    if setup == "not_served":
        _provide(fake_handler_runtime, {})
    elif setup == "raises":
        _provide(fake_handler_runtime, error=RuntimeError("boom"))
    elif setup == "without_method":
        fake_handler_runtime.hass.data[PROVIDERS_KEY] = {"test": SimpleNamespace(name="test", version=1)}
    reading, _ = _handler(fake_handler_runtime)._read_water()
    assert reading.supply_c is None
    assert reading.supply_reason == reason


def test_badly_typed_fields_are_no_data(fake_handler_runtime):
    _provide(
        fake_handler_runtime,
        {fake_handler_runtime.entity_id: {"supply_temperature": "45", "supply_reason": 3, "heat_available": "yes", "heat_reason": ""}},
    )
    reading, _ = _handler(fake_handler_runtime)._read_water()
    assert reading.supply_c is None
    assert reading.supply_reason is None
    assert reading.heat_available is None
    assert reading.heat_reason is None


def test_reads_the_supply_sensor_and_the_thermostat_s_own_signals(fake_handler_runtime):
    _entities(fake_handler_runtime, {SUPPLY: ("48.5", {"unit_of_measurement": "°C"}), HEAT: "on", DHW: "on"})
    reading, inputs = _handler(fake_handler_runtime)._read_water()
    assert reading.supply_c == 48.5
    assert (reading.heat_available, reading.heat_reason) == (False, "dhw_active")
    assert inputs["dhw"] == {"source": "entity", "entity": DHW, "data": True}
    assert inputs["fault"] == {"source": "none"}


def test_the_boiler_s_signals_from_the_defaults_entry(fake_handler_runtime):
    _entities(fake_handler_runtime, {SUPPLY: "48.5", "binary_sensor.shared_dhw": "on"}, own=False)
    defaults = SimpleNamespace(
        unique_id=DOMAIN, data={}, options={CONF_SMART_PI_DHW_ENTITY: "binary_sensor.shared_dhw"}
    )
    fake_handler_runtime.hass.config_entries._entries.append(defaults)
    # The thermostat's own values count only when it chose its own.
    fake_handler_runtime.entry_infos[CONF_SMART_PI_DHW_ENTITY] = DHW
    reading, inputs = _handler(fake_handler_runtime)._read_water()
    assert reading.heat_reason == "dhw_active"
    assert inputs["dhw"]["entity"] == "binary_sensor.shared_dhw"


async def _control(handler, phase=SmartPIPhase.STABLE):
    algo = handler._thermostat.prop_algorithm
    algo.guards.check_guard_cut = MagicMock(return_value=GuardAction.NONE)
    algo.guards.check_guard_kick = MagicMock(return_value=GuardAction.NONE)
    with (
        patch.object(type(algo), "phase", new_callable=PropertyMock, return_value=phase),
        patch.object(algo, "calculate", MagicMock()),
    ):
        await handler.control_heating(timestamp=None)


@pytest.mark.parametrize("source", ["provider", "entities"])
async def test_control_applies_the_water_and_publishes_the_factor(fake_handler_runtime, monkeypatch, no_registry, source):
    if source == "provider":
        _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(40.0)})
    else:
        _entities(fake_handler_runtime, {SUPPLY: "40.0", HEAT: "on", DHW: "off"})
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {}}
    handler = _handler(fake_handler_runtime)
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", MagicMock())
    await _control(handler)
    algo = fake_handler_runtime.prop_algorithm
    assert isinstance(algo, SmartPI)
    assert 0.0 < algo.water_factor < 1.0
    handler.update_attributes()
    assert fake_handler_runtime._attr_extra_state_attributes["specific_states"][WATER_FACTOR_KEY] == pytest.approx(algo.water_factor, abs=1e-3)
    # The diagnostics show where each signal comes from.
    inputs = algo.water_diagnostics()["inputs"]
    assert inputs["supply"]["data"] is True
    assert inputs["supply"]["source"] == (PROVIDER if source == "provider" else "entity")


@pytest.mark.parametrize("data", [None, _water(supply=None, supply_reason="circuit_not_measured", heat_available=None, heat_reason="circuit_not_measured")])
async def test_without_water_data_in_use_the_key_is_absent(fake_handler_runtime, no_registry, data):
    # Present means "uses the water data": a provider may then skip its own pause on water swings.
    if data is not None:
        _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: data})
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {WATER_FACTOR_KEY: 0.4}}
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    handler.update_attributes()
    assert WATER_FACTOR_KEY not in fake_handler_runtime._attr_extra_state_attributes["specific_states"]
    assert fake_handler_runtime.prop_algorithm.water_factor == 1.0


async def test_a_momentary_gap_keeps_publishing_the_average(fake_handler_runtime, monkeypatch, no_registry):
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", MagicMock())
    provider = _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(40.0)})
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {}}
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    provider.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="flow_stale", heat_available=None, heat_reason="flow_stale")
    await _control(handler)
    handler.update_attributes()
    assert fake_handler_runtime._attr_extra_state_attributes["specific_states"][WATER_FACTOR_KEY] == pytest.approx(
        fake_handler_runtime.prop_algorithm.water_factor, abs=1e-3
    )


async def test_an_electric_type_neither_reads_nor_publishes(fake_handler_runtime, no_registry):
    provider = _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(40.0)})
    fake_handler_runtime._attr_extra_state_attributes = {"specific_states": {WATER_FACTOR_KEY: 0.5}}
    handler = _handler(fake_handler_runtime, heating_type="electric_fast")
    await _control(handler)
    handler.update_attributes()
    assert provider.calls == []
    assert WATER_FACTOR_KEY not in fake_handler_runtime._attr_extra_state_attributes["specific_states"]


@pytest.mark.parametrize("phase, offered", [(SmartPIPhase.STABLE, True), (SmartPIPhase.HYSTERESIS, False)])
async def test_water_appearing_offers_the_model_reset_once_learned(fake_handler_runtime, monkeypatch, phase, offered):
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    provider = _provide(
        fake_handler_runtime,
        {fake_handler_runtime.entity_id: _water(supply=None, supply_reason="not_mapped", heat_available=None, heat_reason="not_mapped")},
    )
    handler = _handler(fake_handler_runtime)
    await _control(handler, phase)
    assert not create_issue.called
    provider.data[fake_handler_runtime.entity_id] = _water(45.0)
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


@pytest.mark.parametrize("kept, offered", [(PROVIDER, False), ("entities|sensor.other", True), (None, True)])
async def test_the_options_settle_the_first_data_of_the_inputs_they_kept_the_model_for(
    fake_handler_runtime, monkeypatch, kept, offered
):
    """The user kept the model when the options asked about these inputs: their data
    starting is not asked about again; other inputs, or no answer, are."""
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    provider = _provide(fake_handler_runtime, {})
    if kept is not None:
        fake_handler_runtime.entry_infos[CONF_SMART_PI_MODEL_KEPT_FOR_WATER] = kept
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    provider.data[fake_handler_runtime.entity_id] = _water(45.0)
    await _control(handler)
    assert fake_handler_runtime.prop_algorithm.water.availability == AVAILABILITY_USED
    assert create_issue.called is offered


async def test_kept_inputs_still_offer_the_reset_when_their_data_go_and_come_back(fake_handler_runtime, monkeypatch):
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    provider = _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(45.0)})
    fake_handler_runtime.entry_infos[CONF_SMART_PI_MODEL_KEPT_FOR_WATER] = PROVIDER
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    assert not create_issue.called
    provider.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="not_mapped", heat_available=None)
    await _control(handler)
    assert create_issue.call_args.kwargs["translation_key"] == "water_data_gone"
    provider.data[fake_handler_runtime.entity_id] = _water(45.0)
    await _control(handler)
    assert create_issue.call_args.kwargs["translation_key"] == "water_data_appeared"


async def test_water_going_for_good_offers_the_model_reset(fake_handler_runtime, monkeypatch):
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    provider = _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(45.0)})
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    create_issue.reset_mock()
    provider.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="circuit_not_measured", heat_available=None, heat_reason="circuit_not_measured")
    await _control(handler)
    assert create_issue.call_args.kwargs["translation_key"] == "water_data_gone"
    assert fake_handler_runtime.prop_algorithm.water_factor == 1.0
    # A momentary gap is no change for good.
    create_issue.reset_mock()
    provider.data[fake_handler_runtime.entity_id] = _water(45.0)
    await _control(handler)
    create_issue.reset_mock()
    provider.data[fake_handler_runtime.entity_id] = _water(supply=None, supply_reason="flow_stale", heat_available=None, heat_reason="flow_stale")
    await _control(handler)
    assert not create_issue.called


async def test_no_source_chosen_drops_the_water_at_once_without_a_repair(fake_handler_runtime, monkeypatch, no_registry):
    """Water data were in use; the options now choose no source: the factor is 1 at once,
    nothing waits for 24 h, and the repair is not offered (the options asked)."""
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    _provide(fake_handler_runtime, {fake_handler_runtime.entity_id: _water(40.0)})
    handler = _handler(fake_handler_runtime)
    await _control(handler)
    algo = fake_handler_runtime.prop_algorithm
    assert algo.water.availability == AVAILABILITY_USED
    create_issue.reset_mock()
    fake_handler_runtime.entry_infos[CONF_SMART_PI_WATER_SOURCE] = "none"
    await _control(handler)
    assert algo.water_factor == 1.0
    assert algo.water.state.source == "none"
    assert algo.water.save_state()["long_average"] is None
    assert not create_issue.called
    assert algo.water_diagnostics()["inputs"]["supply"] == {"source": "none"}


async def test_a_chosen_provider_missing_for_long_is_reported_once(fake_handler_runtime, monkeypatch, no_registry, caplog):
    fake_handler_runtime.entry_infos[CONF_SMART_PI_WATER_SOURCE] = PROVIDER
    clock = [1000.0]
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.time.monotonic", lambda: clock[0])
    handler = _handler(fake_handler_runtime)
    with caplog.at_level(logging.WARNING, logger="custom_components.vtherm_smartpi.handler"):
        await _control(handler)
        clock[0] += PROVIDER_MISSING_WARNING_S
        await _control(handler)
        clock[0] += PROVIDER_MISSING_WARNING_S
        await _control(handler)
    warnings = [r for r in caplog.records if "is not registered" in r.getMessage()]
    assert len(warnings) == 1
    assert "'test'" in warnings[0].getMessage()
    inputs = fake_handler_runtime.prop_algorithm.water_diagnostics()["inputs"]
    assert inputs["supply"] == {"source": PROVIDER, "available": False, "data": False}


def _saved_water(handler, availability, long_average=None, inputs=None):
    """The water state a previous run saved (as loaded at start)."""
    water = handler._thermostat.prop_algorithm.water
    state = {"heating_type": "radiator", "availability": availability, "long_average": long_average, "no_data_s": None}
    if inputs is not None:
        state["inputs"] = inputs
    water.load_state(state)
    return water


async def test_other_inputs_kept_in_the_options_ask_nothing_after_a_lasting_state(fake_handler_runtime, monkeypatch, no_registry):
    """The provider said the circuit is not measured (saved: lasting); the options switch the
    water to a supply sensor and keep the model: its data start without a repair."""
    create_issue, delete_issue = MagicMock(), MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_delete_issue", delete_issue)
    _entities(fake_handler_runtime, {SUPPLY: "45.0", HEAT: "on", DHW: "off"})
    fake_handler_runtime.entry_infos[CONF_SMART_PI_MODEL_KEPT_FOR_WATER] = f"entities|{SUPPLY}"
    handler = _handler(fake_handler_runtime)
    _saved_water(handler, "lasting", inputs=PROVIDER)
    await _control(handler)
    water = fake_handler_runtime.prop_algorithm.water
    assert water.availability == AVAILABILITY_USED and water.inputs == f"entities|{SUPPLY}"
    assert not create_issue.called
    # The open repair about the old inputs goes.
    assert delete_issue.call_args.args[2] == f"{WATER_ISSUE_PREFIX}{fake_handler_runtime.unique_id}"


async def test_other_inputs_do_not_use_the_old_inputs_average(fake_handler_runtime, monkeypatch, no_registry):
    """The old source's long-term average is not applied to a new source that has no data yet."""
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_delete_issue", MagicMock())
    fake_handler_runtime.entry_infos[CONF_SMART_PI_WATER_SOURCE] = "provider:other"
    handler = _handler(fake_handler_runtime)
    _saved_water(handler, "used", long_average=0.9, inputs=PROVIDER)
    await _control(handler)
    algo = fake_handler_runtime.prop_algorithm
    assert algo.water.state.source == "none"
    assert algo.water_factor == 1.0
    assert algo.water.state.supply_reason == "provider_missing"


async def test_the_same_inputs_keep_their_state_across_a_restart(fake_handler_runtime, monkeypatch, no_registry):
    delete_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_delete_issue", delete_issue)
    fake_handler_runtime.entry_infos[CONF_SMART_PI_WATER_SOURCE] = PROVIDER
    handler = _handler(fake_handler_runtime)
    _saved_water(handler, "used", long_average=0.6, inputs=PROVIDER)
    await _control(handler)  # the provider is not registered yet: a momentary gap
    algo = fake_handler_runtime.prop_algorithm
    assert algo.water.state.source == "average"
    assert algo.water_factor == pytest.approx(0.6)
    assert not delete_issue.called


async def test_an_entry_from_before_the_sources_keeps_today_s_behaviour_without_data(fake_handler_runtime, monkeypatch, no_registry):
    """No key in the options and a state saved without inputs: no water data now is a gap, as
    before (the long-term average, lasting after 24 h), not a choice made in the options."""
    create_issue = MagicMock()
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_create_issue", create_issue)
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.ir.async_delete_issue", MagicMock())
    clock = [1000.0]
    monkeypatch.setattr("custom_components.vtherm_smartpi.handler.time.monotonic", lambda: clock[0])
    handler = _handler(fake_handler_runtime)
    _saved_water(handler, "used", long_average=0.7)
    await _control(handler)
    algo = fake_handler_runtime.prop_algorithm
    assert algo.water.state.source == "average"
    assert algo.water_factor == pytest.approx(0.7)
    clock[0] += 24 * 3600.0
    await _control(handler)
    assert algo.water.state.source == "lasting"
    assert create_issue.call_args.kwargs["translation_key"] == "water_data_gone"
    # Once the options choose a source, the state is the chosen inputs'.
    assert algo.water.inputs == "none"
