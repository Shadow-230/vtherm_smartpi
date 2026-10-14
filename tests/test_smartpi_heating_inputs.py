"""Inputs from the heating system: the sources a user chooses and how each is read.

Entities (any integration, or a plain sensor), a provider of SmartPI's contract
(v1) and VT's central boiler (its live entities). Anything wrong means no data.
"""

from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.vtherm_smartpi import heating_inputs as hi
from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_REASON_ENTITY,
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_FAULT_ENTITY,
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_SUPPLY_ENTITY,
    CONF_SMART_PI_WATER_SOURCE,
)
from custom_components.vtherm_smartpi.smartpi.boiler_call import REASON_NOT_CALLED, SOURCE_VTHERM, BoilerReading
from custom_components.vtherm_smartpi.smartpi.water_input import WaterReading

SUPPLY = "sensor.radiator_supply"
HEAT = "binary_sensor.boiler_heat"
DHW = "binary_sensor.boiler_dhw"
FAULT = "binary_sensor.boiler_fault"
CALLED = "binary_sensor.boiler_called"
REASON = "sensor.boiler_reason"
SIGNALS = hi.BoilerSignals(heat_available=HEAT, dhw=DHW, fault=FAULT, called=CALLED, reason=REASON)
ENTITIES = hi.HeatingInputsConfig(hi.SOURCE_ENTITIES, hi.SOURCE_ENTITIES, SUPPLY, SIGNALS)


class _Provider:
    """A provider of the contract: answers from ``data`` by entity id."""

    def __init__(self, data=None, name="test", version=1, error=None):
        self.name, self.version, self.data, self.error, self.calls = name, version, data or {}, error, []

    def heating_inputs(self, entity_id):
        self.calls.append(entity_id)
        if self.error:
            raise self.error
        return self.data.get(entity_id)


def _register(hass, provider):
    hass.data.setdefault(hi.PROVIDERS_KEY, {})[provider.name] = provider


# --- configuration -----------------------------------------------------------------


def test_an_entry_without_the_keys_keeps_the_behaviour_from_before():
    assert hi.config_from_options({}, None, used_by_central_boiler=False) == hi.HeatingInputsConfig()
    config = hi.config_from_options({}, None, used_by_central_boiler=True)
    assert (config.water_source, config.call_source) == (hi.SOURCE_NONE, SOURCE_VTHERM)


@pytest.mark.parametrize("water, expected", [("provider:", "none"), ("auto", "none"), (3, "none"), ("provider:sb", "provider:sb")])
def test_an_unknown_source_is_none(water, expected):
    config = hi.config_from_options({CONF_SMART_PI_WATER_SOURCE: water, CONF_SMART_PI_BOILER_CALL_SOURCE: "x"}, None, False)
    assert config.water_source == expected
    assert config.call_source == hi.SOURCE_NONE


def test_water_from_entities_needs_the_supply_sensor():
    config = hi.config_from_options({CONF_SMART_PI_WATER_SOURCE: hi.SOURCE_ENTITIES}, None, False)
    assert config.water_source == hi.SOURCE_NONE
    config = hi.config_from_options(
        {CONF_SMART_PI_WATER_SOURCE: hi.SOURCE_ENTITIES, CONF_SMART_PI_SUPPLY_ENTITY: SUPPLY}, None, False
    )
    assert (config.water_source, config.supply_entity) == (hi.SOURCE_ENTITIES, SUPPLY)
    assert config.water_inputs == f"entities|{SUPPLY}"
    # The supply sensor counts only with entities.
    config = hi.config_from_options({CONF_SMART_PI_WATER_SOURCE: "provider:sb", CONF_SMART_PI_SUPPLY_ENTITY: SUPPLY}, None, False)
    assert config.supply_entity is None
    assert config.water_inputs == "provider:sb"


def test_the_boiler_signals_come_from_the_defaults_unless_the_thermostat_sets_its_own():
    defaults = {CONF_SMART_PI_HEAT_AVAILABLE_ENTITY: HEAT, CONF_SMART_PI_DHW_ENTITY: DHW}
    own = {CONF_SMART_PI_FAULT_ENTITY: FAULT, CONF_SMART_PI_BOILER_CALLED_ENTITY: CALLED}
    assert hi.config_from_options(own, defaults, False).signals == hi.BoilerSignals(heat_available=HEAT, dhw=DHW)
    assert hi.config_from_options({**own, CONF_SMART_PI_BOILER_SIGNALS: "defaults"}, defaults, False).signals == (
        hi.BoilerSignals(heat_available=HEAT, dhw=DHW)
    )
    assert hi.config_from_options({**own, CONF_SMART_PI_BOILER_SIGNALS: "own"}, defaults, False).signals == (
        hi.BoilerSignals(fault=FAULT, called=CALLED)
    )
    # A thermostat without its own entry: its options are the defaults.
    assert hi.config_from_options(defaults, None, False).signals == hi.BoilerSignals(heat_available=HEAT, dhw=DHW)


# --- entities ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state, attributes, supply, reason",
    [
        ("45.2", {"unit_of_measurement": "°C"}, 45.2, None),
        ("45", {}, 45.0, None),
        ("113", {"unit_of_measurement": "°F"}, 45.0, None),
        ("318.15", {"unit_of_measurement": "K"}, 45.0, None),
        ("unavailable", {}, None, hi.SUPPLY_UNAVAILABLE),
        ("unknown", {}, None, hi.SUPPLY_UNAVAILABLE),
        ("hot", {}, None, hi.SUPPLY_UNAVAILABLE),
        ("nan", {}, None, hi.SUPPLY_UNAVAILABLE),
    ],
)
async def test_the_supply_sensor(hass, state, attributes, supply, reason):
    hass.states.async_set(SUPPLY, state, attributes)
    reading, inputs = hi.water_from_entities(hass, ENTITIES)
    assert reading.supply_c == (pytest.approx(supply) if supply is not None else None)
    assert reading.supply_reason == reason
    assert inputs["supply"] == {"source": "entity", "entity": SUPPLY, "data": supply is not None}


async def test_a_missing_supply_entity_gives_no_data(hass):
    reading, inputs = hi.water_from_entities(hass, ENTITIES)
    assert reading == WaterReading(supply_reason=hi.SUPPLY_UNAVAILABLE)
    assert inputs["heat"] == {"source": "entity", "entity": HEAT, "data": False}


@pytest.mark.parametrize(
    "heat, dhw, fault, available, reason",
    [
        ("on", "off", "off", True, None),
        ("off", "off", "off", False, "no_heat"),
        ("on", "on", "off", False, "dhw_active"),
        ("on", "off", "on", False, "fault"),
        # Hot water first: the circuit's water goes to the tank.
        ("off", "on", "on", False, "dhw_active"),
        ("unavailable", "off", "off", None, None),
        (None, None, None, None, None),
    ],
)
async def test_the_boiler_s_heat_signals(hass, heat, dhw, fault, available, reason):
    hass.states.async_set(SUPPLY, "50")
    for entity_id, state in ((HEAT, heat), (DHW, dhw), (FAULT, fault)):
        if state is not None:
            hass.states.async_set(entity_id, state)
    reading, _ = hi.water_from_entities(hass, ENTITIES)
    assert (reading.heat_available, reading.heat_reason) == (available, reason)


async def test_signals_not_set_are_none_in_the_diagnostics(hass):
    hass.states.async_set(SUPPLY, "50")
    config = hi.HeatingInputsConfig(hi.SOURCE_ENTITIES, hi.SOURCE_NONE, SUPPLY, hi.BoilerSignals())
    reading, inputs = hi.water_from_entities(hass, config)
    assert reading == WaterReading(supply_c=50.0)
    assert inputs == {
        "supply": {"source": "entity", "entity": SUPPLY, "data": True},
        "heat": {"source": "none"},
        "dhw": {"source": "none"},
        "fault": {"source": "none"},
    }


@pytest.mark.parametrize(
    "called, fault, reason_state, expected",
    [
        ("on", "off", None, BoilerReading(True, None, hi.SOURCE_ENTITIES)),
        ("off", "off", "below_threshold", BoilerReading(False, "below_threshold", hi.SOURCE_ENTITIES)),
        ("off", "off", "activation_delay", BoilerReading(False, "activation_delay", hi.SOURCE_ENTITIES)),
        ("off", "off", "a reason SmartPI does not know", BoilerReading(False, REASON_NOT_CALLED, hi.SOURCE_ENTITIES)),
        ("off", "off", None, BoilerReading(False, REASON_NOT_CALLED, hi.SOURCE_ENTITIES)),
        # The fault first.
        ("off", "on", "no_demand", BoilerReading(False, "boiler_fault", hi.SOURCE_ENTITIES)),
        ("on", "on", None, BoilerReading(True, None, hi.SOURCE_ENTITIES)),
        ("unavailable", "off", "no_demand", None),
        (None, None, None, None),
    ],
)
async def test_the_boiler_s_call_from_entities(hass, called, fault, reason_state, expected):
    for entity_id, state in ((CALLED, called), (FAULT, fault), (REASON, reason_state)):
        if state is not None:
            hass.states.async_set(entity_id, state)
    reading, inputs = hi.boiler_from_entities(hass, ENTITIES)
    assert reading == expected
    assert inputs["called"] == {"source": "entity", "entity": CALLED, "data": expected is not None}
    assert inputs["reason"]["data"] is (reason_state is not None)


async def test_an_entity_is_kept_by_its_registry_id_and_survives_a_rename(hass):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "supply-1", suggested_object_id="radiator_supply")
    stored = hi.stored_entity(hass, entry.entity_id)
    assert stored == entry.id
    registry.async_update_entity(entry.entity_id, new_entity_id="sensor.renamed_supply")
    hass.states.async_set("sensor.renamed_supply", "41.5")
    config = hi.HeatingInputsConfig(hi.SOURCE_ENTITIES, hi.SOURCE_NONE, stored)
    reading, inputs = hi.water_from_entities(hass, config)
    assert reading.supply_c == 41.5
    assert inputs["supply"]["entity"] == "sensor.renamed_supply"
    # An entity without a registry entry is kept by its entity id.
    assert hi.stored_entity(hass, "sensor.yaml_only") == "sensor.yaml_only"
    # A registry id that no longer exists: no state, shown as stored.
    assert hi.resolve_entity(hass, "0123456789abcdef0123456789abcdef") == "0123456789abcdef0123456789abcdef"
    assert hi.resolve_entity(hass, None) is None


# --- providers -----------------------------------------------------------------------


def test_providers_register_and_unregister_without_imports():
    hass = SimpleNamespace(data={})
    assert hi.provider_names(hass) == []
    first, second = _Provider(name="zeta"), _Provider(name="alpha")
    _register(hass, first)
    _register(hass, second)
    assert hi.provider_names(hass) == ["alpha", "zeta"]
    del hass.data[hi.PROVIDERS_KEY]["zeta"]
    assert hi.provider_names(hass) == ["alpha"]
    # A registry that is not a mapping, or odd names, are ignored.
    hass.data[hi.PROVIDERS_KEY] = ["alpha"]
    assert hi.registered_providers(hass) == {}
    hass.data[hi.PROVIDERS_KEY] = {"": first, 3: first, "ok": first}
    assert hi.provider_names(hass) == ["ok"]


def test_the_provider_source_value():
    assert hi.provider_source("sb") == "provider:sb"
    assert hi.provider_name("provider:sb") == "sb"
    assert hi.provider_name("provider:") is None
    assert hi.provider_name("entities") is None
    assert hi.provider_name(None) is None


@pytest.mark.parametrize(
    "provider, status",
    [
        (None, hi.PROVIDER_MISSING),
        (_Provider({"climate.room": {}}), hi.PROVIDER_OK),
        (_Provider({}), hi.PROVIDER_NOT_SERVED),
        (_Provider({"climate.room": {}}, version=2), hi.PROVIDER_ERROR),
        (_Provider({"climate.room": {}}, version=True), hi.PROVIDER_ERROR),
        (_Provider({"climate.room": {}}, version="1"), hi.PROVIDER_ERROR),
        (_Provider(error=RuntimeError("boom")), hi.PROVIDER_ERROR),
        (_Provider({"climate.room": ["not", "a", "mapping"]}), hi.PROVIDER_ERROR),
        (SimpleNamespace(name="test", version=1), hi.PROVIDER_ERROR),
    ],
)
def test_a_provider_is_read_defensively(provider, status):
    hass = SimpleNamespace(data={})
    if provider is not None:
        hass.data[hi.PROVIDERS_KEY] = {"test": provider}
    assert hi.ask_provider(hass, "test", "climate.room").status == status


def test_a_thermostat_without_its_entity_id_is_not_asked_for():
    hass = SimpleNamespace(data={})
    provider = _Provider({"climate.room": {}})
    _register(hass, provider)
    assert hi.ask_provider(hass, "test", None).status == hi.PROVIDER_NOT_SERVED
    assert provider.calls == []


def test_a_provider_s_water_fields():
    fields = {"supply_temperature": 52.5, "supply_reason": None, "heat_available": False, "heat_reason": "dhw_active"}
    reading, inputs = hi.water_from_provider(hi.ProviderAnswer(hi.PROVIDER_OK, fields), "provider:test")
    assert reading == WaterReading(supply_c=52.5, heat_available=False, heat_reason="dhw_active")
    assert inputs["supply"] == {"source": "provider:test", "available": True, "data": True}
    assert inputs["dhw"] == {"source": "provider:test", "available": True, "data": True}


def test_badly_typed_water_fields_are_no_data():
    fields = {"supply_temperature": "45", "supply_reason": 3, "heat_available": "yes", "heat_reason": ""}
    reading, inputs = hi.water_from_provider(hi.ProviderAnswer(hi.PROVIDER_OK, fields), "provider:test")
    assert reading == WaterReading()
    assert inputs["supply"]["data"] is False
    for value in (True, float("nan"), float("inf"), 10**400):
        assert hi.water_reading_of({"supply_temperature": value}).supply_c is None


class _BrokenMapping(Mapping):
    """A mapping whose reads fail."""

    def __getitem__(self, key):
        raise RuntimeError("broken")

    def __iter__(self):
        raise RuntimeError("broken")

    def __len__(self):
        return 1


def test_a_mapping_that_fails_is_no_data():
    hass = SimpleNamespace(data={})
    _register(hass, _Provider({"climate.room": _BrokenMapping()}))
    assert hi.ask_provider(hass, "test", "climate.room").status == hi.PROVIDER_ERROR
    assert hi.demand_of(_BrokenMapping()) is None
    assert hi.demand_of({"demand_power_kw": 10**400, "count_threshold": 10**400}) == {"count_threshold": 10**400}


@pytest.mark.parametrize(
    "status, reason, available",
    [
        (hi.PROVIDER_MISSING, hi.SUPPLY_PROVIDER_MISSING, False),
        (hi.PROVIDER_NOT_SERVED, hi.SUPPLY_NOT_SERVED, True),
        (hi.PROVIDER_ERROR, hi.SUPPLY_PROVIDER_ERROR, True),
    ],
)
def test_no_answer_is_a_gap_with_its_reason(status, reason, available):
    reading, inputs = hi.water_from_provider(hi.ProviderAnswer(status), "provider:test")
    assert reading == WaterReading(supply_reason=reason)
    assert inputs["supply"] == {"source": "provider:test", "available": available, "data": False}
    call, call_inputs = hi.boiler_from_provider(hi.ProviderAnswer(status), "provider:test")
    assert call is None
    assert call_inputs["called"] == {"source": "provider:test", "available": available, "data": False}


@pytest.mark.parametrize("reason", hi.CALL_REASONS)
def test_a_provider_says_why_it_does_not_call_the_boiler(reason):
    answer = hi.ProviderAnswer(hi.PROVIDER_OK, {"boiler_called": False, "call_reason": reason})
    reading, inputs = hi.boiler_from_provider(answer, "provider:test")
    assert reading == BoilerReading(False, reason, "provider:test")
    assert inputs["reason"]["data"] is True


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"boiler_called": None, "call_reason": "not_controlling"},
        {"boiler_called": None, "call_reason": "waiting_data"},
        {"boiler_called": False, "call_reason": "a_reason_smartpi_does_not_know"},
        {"boiler_called": False},
        {"boiler_called": "no", "call_reason": "no_demand"},
    ],
)
def test_a_call_the_provider_does_not_give_is_not_known(fields):
    assert hi.boiler_from_provider(hi.ProviderAnswer(hi.PROVIDER_OK, fields), "provider:test")[0] is None


def test_the_demand_is_kept_for_the_diagnostics():
    demand = {
        "demand_count": 1,
        "count_threshold": 2.0,  # a whole number given as a float counts
        "count_by": "devices",
        "demand_power_kw": 0.4,
        "power_threshold_kw": 0.6,
        "demand_opening": 0.2,
        "opening_threshold": None,  # the criterion is not set
        "something_else": 5,
    }
    answer = hi.ProviderAnswer(hi.PROVIDER_OK, {"boiler_called": True, "demand": demand})
    reading, _ = hi.boiler_from_provider(answer, "provider:test")
    assert reading.demand == {
        "demand_count": 1,
        "count_threshold": 2,
        "count_by": "devices",
        "demand_power_kw": 0.4,
        "power_threshold_kw": 0.6,
        "demand_opening": 0.2,
    }


def test_badly_typed_demand_fields_are_left_out():
    demand = {"demand_count": True, "count_threshold": "2", "count_by": "", "demand_power_kw": float("nan"), "power_threshold_kw": 0.6}
    assert hi.demand_of(demand) == {"power_threshold_kw": 0.6}
    assert hi.demand_of({"demand_count": 1.5}) is None
    assert hi.demand_of("not a mapping") is None


def test_a_missing_provider_is_reported_once_after_a_while():
    watch = hi.ProviderWatch()
    missing = hi.ProviderAnswer(hi.PROVIDER_MISSING)
    assert watch.seen("sb", missing, 0.0) is False
    assert watch.seen("sb", missing, hi.PROVIDER_MISSING_WARNING_S - 1.0) is False
    assert watch.seen("sb", missing, hi.PROVIDER_MISSING_WARNING_S) is True
    assert watch.seen("sb", missing, 2 * hi.PROVIDER_MISSING_WARNING_S) is False
    # Back, then missing again: a new episode.
    assert watch.seen("sb", hi.ProviderAnswer(hi.PROVIDER_OK, {}), 3 * hi.PROVIDER_MISSING_WARNING_S) is False
    assert watch.seen("sb", missing, 4 * hi.PROVIDER_MISSING_WARNING_S) is False
    assert watch.seen("sb", missing, 5 * hi.PROVIDER_MISSING_WARNING_S) is True


# --- VT's central boiler -------------------------------------------------------------


def _vt_entities(hass, on, count=None, count_threshold=None, power=None, power_threshold=None, unit="W"):
    registry = er.async_get(hass)
    for domain, unique_id, state, attributes in (
        ("binary_sensor", hi.VT_CENTRAL_BOILER_UNIQUE_ID, on, {}),
        ("sensor", hi.VT_ACTIVE_COUNT_UNIQUE_ID, count, {}),
        ("number", hi.VT_COUNT_THRESHOLD_UNIQUE_ID, count_threshold, {}),
        ("sensor", hi.VT_ACTIVE_POWER_UNIQUE_ID, power, {"unit_of_measurement": unit}),
        ("number", hi.VT_POWER_THRESHOLD_UNIQUE_ID, power_threshold, {"unit_of_measurement": unit}),
    ):
        if state is None:
            continue
        entry = registry.async_get_or_create(domain, hi.VT_DOMAIN, unique_id, suggested_object_id=unique_id)
        hass.states.async_set(entry.entity_id, state, attributes)


async def test_vt_s_central_boiler_called(hass):
    _vt_entities(hass, "on", "2", "2", "1500", "0")
    reading, inputs = hi.boiler_from_vtherm(hass)
    assert reading.called is True
    assert reading.reason is None
    assert reading.source == SOURCE_VTHERM
    assert reading.demand == {
        "demand_count": 2,
        "count_threshold": 2,
        "count_by": "devices",
        "demand_power_kw": 1.5,
        "power_threshold_kw": 0.0,
    }
    assert inputs["called"] == {"source": SOURCE_VTHERM, "entity": "binary_sensor.central_boiler_state", "data": True}


@pytest.mark.parametrize(
    "count, count_threshold, power, power_threshold, reason",
    [
        # The power criterion met while off: the activation delay runs.
        ("1", "0", "2.5", "2.5", "activation_delay"),
        ("2", "2", "0", "0", "activation_delay"),
        # Nothing active.
        ("0", "2", "0", "2.5", "no_demand"),
        # Active, under the thresholds.
        ("1", "2", "0.4", "0.6", "below_threshold"),
        # A threshold of 0 turns its criterion off.
        ("1", "0", "0.4", "0", "below_threshold"),
    ],
)
async def test_vt_s_reasons_follow_its_own_rule(hass, count, count_threshold, power, power_threshold, reason):
    _vt_entities(hass, "off", count, count_threshold, power, power_threshold, unit="kW")
    reading, _ = hi.boiler_from_vtherm(hass)
    assert reading == BoilerReading(False, reason, SOURCE_VTHERM, reading.demand)


async def test_vt_s_powers_are_compared_in_one_unit(hass):
    # 2500 W against 2.5 kW: VT's own conversion (anything but kW counts as W).
    _vt_entities(hass, "off", "1", "0", "2500", None)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("number", hi.VT_DOMAIN, hi.VT_POWER_THRESHOLD_UNIQUE_ID)
    hass.states.async_set(entry.entity_id, "2.5", {"unit_of_measurement": "kW"})
    reading, _ = hi.boiler_from_vtherm(hass)
    assert reading.reason == "activation_delay"
    assert reading.demand["demand_power_kw"] == 2.5
    assert reading.demand["power_threshold_kw"] == 2.5


async def test_vt_s_central_boiler_without_its_demand_entities(hass):
    _vt_entities(hass, "off")
    reading, inputs = hi.boiler_from_vtherm(hass)
    assert reading == BoilerReading(False, REASON_NOT_CALLED, SOURCE_VTHERM, None)
    assert inputs["reason"]["data"] is False


async def test_vt_s_central_boiler_unknown(hass):
    assert hi.boiler_from_vtherm(hass)[0] is None
    _vt_entities(hass, "unavailable", "1", "1")
    assert hi.boiler_from_vtherm(hass)[0] is None


def test_vt_s_central_boiler_counts_only_for_a_thermostat_it_serves():
    reading, inputs = hi.boiler_from_vtherm(SimpleNamespace(), counted=False)
    assert reading is None
    assert inputs["called"] == {"source": SOURCE_VTHERM, "data": False}


def test_vt_s_reason_without_values():
    assert hi.vtherm_reason(None) == REASON_NOT_CALLED
    assert hi.vtherm_reason({"count_by": "devices"}) == REASON_NOT_CALLED
    assert hi.vtherm_reason({"demand_count": 0}) == "no_demand"
    assert hi.vtherm_reason({"demand_power_kw": 0.3}) == "below_threshold"


def test_the_option_keys_of_the_boiler_signals_are_the_reason_entity_s_too():
    # The fifth signal reads a text sensor.
    options = {CONF_SMART_PI_BOILER_SIGNALS: "own", CONF_SMART_PI_BOILER_REASON_ENTITY: REASON}
    assert hi.config_from_options(options, None, False).signals.reason == REASON
