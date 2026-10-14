"""The options of the inputs from the heating system: "Water and boiler" and "Boiler signals".

The sources are chosen explicitly (no automatic choice), from a list built when
the form opens: none, entities, the registered providers and, for the boiler's
call, VT's central boiler where it serves the thermostat.
"""

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_PROP_FUNCTION,
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_REASON_ENTITY,
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_FAULT_ENTITY,
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_MODEL_KEPT_FOR_WATER,
    CONF_SMART_PI_RESET_TOKEN,
    CONF_SMART_PI_SUPPLY_ENTITY,
    CONF_SMART_PI_WATER_SOURCE,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
)
from custom_components.vtherm_smartpi.heating_inputs import PROVIDERS_KEY

VT_DOMAIN = "versatile_thermostat"


@pytest.fixture(autouse=True)
def _entries_are_not_set_up():
    """Saving options reloads the entry; setting it up would start Versatile Thermostat."""
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        yield


class _Provider:
    version = 1

    def __init__(self, name):
        self.name = name

    def heating_inputs(self, entity_id):  # noqa: ARG002
        return None


def _register(hass, *names):
    hass.data.setdefault(PROVIDERS_KEY, {}).update({name: _Provider(name) for name in names})


def _vtherm(hass, unique_id="vt-living", object_id="living", **config):
    """A Versatile Thermostat climate entity; its configuration in an entry of another domain."""
    vt_entry = MockConfigEntry(domain="test_vtherm", data={CONF_PROP_FUNCTION: PROP_FUNCTION_SMART_PI, **config})
    vt_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "climate", VT_DOMAIN, unique_id, suggested_object_id=object_id, config_entry=vt_entry
    )


def _thermostat_entry(hass, target="vt-living", heating_type="radiator", **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-{target}",
        title=target,
        data={CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS},
        options={CONF_TARGET_VTHERM: target, **DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: heating_type, **options},
    )
    entry.add_to_hass(hass)
    return entry


def _defaults_entry(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="SmartPI defaults", unique_id=DOMAIN, data=dict(DEFAULT_OPTIONS), options={**DEFAULT_OPTIONS, **options}
    )
    entry.add_to_hass(hass)
    return entry


async def _configure(hass, result, user_input):
    return await hass.config_entries.options.async_configure(result["flow_id"], user_input)


async def _open_inputs(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert "heating_inputs" in result["menu_options"]
    result = await _configure(hass, result, {"next_step_id": "heating_inputs"})
    assert result["step_id"] == "heating_inputs"
    return result


def _field(result, name):
    for key in result["data_schema"].schema:
        if getattr(key, "schema", key) == name:
            return key, result["data_schema"].schema[key]
    raise KeyError(name)


def _choices(result, name) -> list[tuple[str, str]]:
    _, field = _field(result, name)
    return [(option["value"], option["label"]) for option in field.config["options"]]


def _default(result, name):
    key, _ = _field(result, name)
    return key.default()


def _suggested(result, name):
    key, _ = _field(result, name)
    return (key.description or {}).get("suggested_value")


def _fields(result) -> list[str]:
    return [getattr(key, "schema", key) for key in result["data_schema"].schema]


# --- "SmartPI defaults" ----------------------------------------------------------------


async def test_the_defaults_keep_the_boiler_s_signals(hass) -> None:
    registry = er.async_get(hass)
    heat = registry.async_get_or_create("binary_sensor", "test", "heat", suggested_object_id="boiler_heat")
    entry = _defaults_entry(hass, **{CONF_SMART_PI_FAULT_ENTITY: "binary_sensor.old_fault"})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["settings", "boiler_signals"]
    result = await _configure(hass, result, {"next_step_id": "boiler_signals"})
    assert result["step_id"] == "boiler_signals"
    assert _fields(result) == [
        CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
        CONF_SMART_PI_DHW_ENTITY,
        CONF_SMART_PI_FAULT_ENTITY,
        CONF_SMART_PI_BOILER_CALLED_ENTITY,
        CONF_SMART_PI_BOILER_REASON_ENTITY,
    ]
    assert _suggested(result, CONF_SMART_PI_FAULT_ENTITY) == "binary_sensor.old_fault"
    result = await _configure(
        hass, result, {CONF_SMART_PI_HEAT_AVAILABLE_ENTITY: heat.entity_id, CONF_SMART_PI_DHW_ENTITY: "binary_sensor.yaml_dhw"}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # A registered entity is kept by its registry id; the field left empty goes.
    assert entry.options[CONF_SMART_PI_HEAT_AVAILABLE_ENTITY] == heat.id
    assert entry.options[CONF_SMART_PI_DHW_ENTITY] == "binary_sensor.yaml_dhw"
    assert CONF_SMART_PI_FAULT_ENTITY not in entry.options
    # The regulator settings are kept.
    assert entry.options[CONF_SMART_PI_HEATING_TYPE] == DEFAULT_OPTIONS[CONF_SMART_PI_HEATING_TYPE]


# --- a thermostat's "Water and boiler" --------------------------------------------------


async def test_an_entry_without_the_keys_shows_today_s_behaviour(hass) -> None:
    _vtherm(hass)
    entry = _thermostat_entry(hass)
    result = await _open_inputs(hass, entry)
    assert _default(result, CONF_SMART_PI_WATER_SOURCE) == "none"
    assert _default(result, CONF_SMART_PI_BOILER_CALL_SOURCE) == "none"
    # VT's central boiler is offered only where it serves the thermostat.
    assert [value for value, _ in _choices(result, CONF_SMART_PI_BOILER_CALL_SOURCE)] == ["none", "entities"]


async def test_vt_s_central_boiler_is_offered_and_the_default_where_it_serves_the_thermostat(hass) -> None:
    _vtherm(hass, used_by_controls_central_boiler=True)
    entry = _thermostat_entry(hass)
    result = await _open_inputs(hass, entry)
    assert _default(result, CONF_SMART_PI_BOILER_CALL_SOURCE) == "vtherm_central_boiler"
    assert [value for value, _ in _choices(result, CONF_SMART_PI_BOILER_CALL_SOURCE)] == [
        "none",
        "vtherm_central_boiler",
        "entities",
    ]


async def test_the_lists_show_the_registered_providers(hass) -> None:
    _vtherm(hass)
    _register(hass, "zeta", "alpha")
    entry = _thermostat_entry(hass)
    result = await _open_inputs(hass, entry)
    assert _choices(result, CONF_SMART_PI_WATER_SOURCE) == [
        ("none", "none"),
        ("entities", "entities"),
        ("provider:alpha", "alpha"),
        ("provider:zeta", "zeta"),
    ]
    assert ("provider:alpha", "alpha") in _choices(result, CONF_SMART_PI_BOILER_CALL_SOURCE)
    # Choosing a provider for both: nothing to read from entities, a change of the water.
    result = await _configure(
        hass, result, {CONF_SMART_PI_WATER_SOURCE: "provider:alpha", CONF_SMART_PI_BOILER_CALL_SOURCE: "provider:alpha"}
    )
    assert result["step_id"] == "heating_reset"
    result = await _configure(hass, result, {"reset_model": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_WATER_SOURCE] == "provider:alpha"
    assert entry.options[CONF_SMART_PI_BOILER_CALL_SOURCE] == "provider:alpha"
    assert entry.options[CONF_SMART_PI_RESET_TOKEN]
    assert CONF_SMART_PI_MODEL_KEPT_FOR_WATER not in entry.options


async def test_a_provider_no_longer_registered_stays_chosen_and_is_marked(hass) -> None:
    _vtherm(hass)
    _register(hass, "alpha")
    entry = _thermostat_entry(hass, **{CONF_SMART_PI_WATER_SOURCE: "provider:gone", CONF_SMART_PI_BOILER_CALL_SOURCE: "none"})
    result = await _open_inputs(hass, entry)
    assert _default(result, CONF_SMART_PI_WATER_SOURCE) == "provider:gone"
    assert _choices(result, CONF_SMART_PI_WATER_SOURCE)[-1] == ("provider:gone", "gone (not available)")
    # Saved unchanged: no question about the model.
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "provider:gone", CONF_SMART_PI_BOILER_CALL_SOURCE: "none"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options


async def test_water_from_entities_with_the_default_signals(hass) -> None:
    _vtherm(hass)
    supply = er.async_get(hass).async_get_or_create("sensor", "test", "supply", suggested_object_id="radiator_supply")
    _defaults_entry(hass, **{CONF_SMART_PI_DHW_ENTITY: "binary_sensor.boiler_dhw"})
    entry = _thermostat_entry(hass, **{CONF_SMART_PI_HEAT_AVAILABLE_ENTITY: "binary_sensor.stale"})
    result = await _open_inputs(hass, entry)
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_BOILER_CALL_SOURCE: "none"})

    assert result["step_id"] == "heating_entities"
    assert _fields(result) == [CONF_SMART_PI_SUPPLY_ENTITY, CONF_SMART_PI_BOILER_SIGNALS]
    assert _default(result, CONF_SMART_PI_BOILER_SIGNALS) == "defaults"
    # The defaults' signals the water reads are shown.
    assert "- dhw: binary_sensor.boiler_dhw" in result["description_placeholders"]["defaults"]
    assert "- boiler_called" not in result["description_placeholders"]["defaults"]
    result = await _configure(hass, result, {CONF_SMART_PI_SUPPLY_ENTITY: supply.entity_id, CONF_SMART_PI_BOILER_SIGNALS: "defaults"})

    assert result["step_id"] == "heating_reset"
    result = await _configure(hass, result, {"reset_model": False})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_WATER_SOURCE] == "entities"
    assert entry.options[CONF_SMART_PI_SUPPLY_ENTITY] == supply.id
    assert entry.options[CONF_SMART_PI_BOILER_SIGNALS] == "defaults"
    # The thermostat's own signals go with "defaults".
    assert CONF_SMART_PI_HEAT_AVAILABLE_ENTITY not in entry.options
    # The model kept: the data of these inputs starting are not asked about again.
    assert entry.options[CONF_SMART_PI_MODEL_KEPT_FOR_WATER] == f"entities|{supply.id}"
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options


async def test_the_thermostat_s_own_signals_are_prefilled_from_the_defaults(hass) -> None:
    _vtherm(hass)
    _defaults_entry(hass, **{CONF_SMART_PI_DHW_ENTITY: "binary_sensor.boiler_dhw", CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor.call"})
    entry = _thermostat_entry(hass, **{CONF_SMART_PI_WATER_SOURCE: "provider:x"})
    result = await _open_inputs(hass, entry)
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "provider:x", CONF_SMART_PI_BOILER_CALL_SOURCE: "entities"})
    # The water does not come from entities: no supply sensor.
    assert _fields(result) == [CONF_SMART_PI_BOILER_SIGNALS]
    result = await _configure(hass, result, {CONF_SMART_PI_BOILER_SIGNALS: "own"})

    assert result["step_id"] == "heating_signals"
    # Only the signals the call reads.
    assert _fields(result) == [CONF_SMART_PI_FAULT_ENTITY, CONF_SMART_PI_BOILER_CALLED_ENTITY, CONF_SMART_PI_BOILER_REASON_ENTITY]
    assert _suggested(result, CONF_SMART_PI_BOILER_CALLED_ENTITY) == "binary_sensor.call"
    result = await _configure(
        hass, result, {CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor.own_call", CONF_SMART_PI_BOILER_REASON_ENTITY: "sensor.why"}
    )

    # The water source did not change: no question about the model.
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SMART_PI_BOILER_CALL_SOURCE] == "entities"
    assert entry.options[CONF_SMART_PI_BOILER_SIGNALS] == "own"
    assert entry.options[CONF_SMART_PI_BOILER_CALLED_ENTITY] == "binary_sensor.own_call"
    assert entry.options[CONF_SMART_PI_BOILER_REASON_ENTITY] == "sensor.why"
    assert CONF_SMART_PI_FAULT_ENTITY not in entry.options
    assert CONF_SMART_PI_RESET_TOKEN not in entry.options


async def test_an_own_signal_left_empty_stays_empty(hass) -> None:
    """A thermostat with its own signals keeps an empty one empty: the defaults do not fill it."""
    _vtherm(hass)
    _defaults_entry(hass, **{CONF_SMART_PI_FAULT_ENTITY: "binary_sensor.shared_fault"})
    entry = _thermostat_entry(
        hass,
        **{
            CONF_SMART_PI_WATER_SOURCE: "none",
            CONF_SMART_PI_BOILER_CALL_SOURCE: "entities",
            CONF_SMART_PI_BOILER_SIGNALS: "own",
            CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor.own_call",
        },
    )
    result = await _open_inputs(hass, entry)
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "none", CONF_SMART_PI_BOILER_CALL_SOURCE: "entities"})
    result = await _configure(hass, result, {CONF_SMART_PI_BOILER_SIGNALS: "own"})
    assert _suggested(result, CONF_SMART_PI_FAULT_ENTITY) is None
    assert _suggested(result, CONF_SMART_PI_BOILER_CALLED_ENTITY) == "binary_sensor.own_call"
    result = await _configure(hass, result, {CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor.own_call"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SMART_PI_FAULT_ENTITY not in entry.options


@pytest.mark.parametrize(
    "before, after, asked",
    [
        ({}, {CONF_SMART_PI_WATER_SOURCE: "none"}, False),
        ({CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_SUPPLY_ENTITY: "sensor.a"}, {CONF_SMART_PI_SUPPLY_ENTITY: "sensor.a"}, False),
        ({CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_SUPPLY_ENTITY: "sensor.a"}, {CONF_SMART_PI_SUPPLY_ENTITY: "sensor.b"}, True),
        ({CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_SUPPLY_ENTITY: "sensor.a"}, {CONF_SMART_PI_WATER_SOURCE: "none"}, True),
    ],
)
async def test_the_reset_is_asked_on_a_change_of_the_water_source_or_its_sensor_only(hass, before, after, asked) -> None:
    _vtherm(hass)
    entry = _thermostat_entry(hass, **before)
    result = await _open_inputs(hass, entry)
    water = after.get(CONF_SMART_PI_WATER_SOURCE, before.get(CONF_SMART_PI_WATER_SOURCE, "none"))
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: water, CONF_SMART_PI_BOILER_CALL_SOURCE: "none"})
    if result.get("step_id") == "heating_entities":
        result = await _configure(
            hass, result, {CONF_SMART_PI_SUPPLY_ENTITY: after[CONF_SMART_PI_SUPPLY_ENTITY], CONF_SMART_PI_BOILER_SIGNALS: "defaults"}
        )
    assert (result.get("step_id") == "heating_reset") is asked
    if asked:
        result = await _configure(hass, result, {"reset_model": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert bool(entry.options.get(CONF_SMART_PI_RESET_TOKEN)) is asked
    if water == "none":
        assert CONF_SMART_PI_SUPPLY_ENTITY not in entry.options


async def test_the_supply_sensor_already_chosen_is_the_default(hass) -> None:
    _vtherm(hass)
    entry = _thermostat_entry(hass, **{CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_SUPPLY_ENTITY: "sensor.a"})
    result = await _open_inputs(hass, entry)
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "entities", CONF_SMART_PI_BOILER_CALL_SOURCE: "none"})
    assert _default(result, CONF_SMART_PI_SUPPLY_ENTITY) == "sensor.a"


async def test_an_electric_heating_type_has_no_water_and_boiler(hass) -> None:
    _vtherm(hass)
    entry = _thermostat_entry(hass, heating_type="electric_fast")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "heating_inputs" not in result["menu_options"]


async def test_a_zone_s_additional_source_chooses_its_water_data_only(hass) -> None:
    radiator = _vtherm(hass, "vt-radiator", "radiator")
    floor = _vtherm(hass, "vt-floor", "floor")
    _thermostat_entry(
        hass,
        "vt-radiator",
        **{
            CONF_SMART_PI_DEPENDENCIES: [
                {"id": "s1", "kind": "source", "entity": floor.id, "split_rule": "together", "split_ratio": 100,
                 "lead_emitter": "radiator", "source_emitter": "floor_water"}
            ]
        },
    )
    del radiator
    _register(hass, "alpha")
    floor_entry = _thermostat_entry(hass, "vt-floor", "floor_water", **{CONF_SMART_PI_BOILER_CALL_SOURCE: "entities"})
    result = await hass.config_entries.options.async_init(floor_entry.entry_id)
    assert result["step_id"] == "source_menu"
    result = await _configure(hass, result, {"next_step_id": "heating_inputs"})
    assert _fields(result) == [CONF_SMART_PI_WATER_SOURCE]
    result = await _configure(hass, result, {CONF_SMART_PI_WATER_SOURCE: "provider:alpha"})
    # Its call is not read: no entities to ask for, though the stored call source is entities.
    assert result["step_id"] == "heating_reset"
    result = await _configure(hass, result, {"reset_model": False})
    assert floor_entry.options[CONF_SMART_PI_WATER_SOURCE] == "provider:alpha"
    assert floor_entry.options[CONF_SMART_PI_MODEL_KEPT_FOR_WATER] == "provider:alpha"


async def test_a_zone_s_new_entry_takes_the_boiler_s_signals_from_the_defaults(hass) -> None:
    """The entry the zone form creates starts from the defaults, without copying their signals."""
    _vtherm(hass, "vt-floor", "floor")
    _defaults_entry(hass, **{CONF_SMART_PI_DHW_ENTITY: "binary_sensor.boiler_dhw", CONF_SMART_PI_BOILER_SIGNALS: "own"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "import"}, data={CONF_TARGET_VTHERM: "vt-floor"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["result"].data
    assert CONF_SMART_PI_DHW_ENTITY not in data
    assert CONF_SMART_PI_BOILER_SIGNALS not in data
