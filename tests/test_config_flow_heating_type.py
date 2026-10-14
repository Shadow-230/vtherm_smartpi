"""The heating type in the regulator settings, and the model reset offered as a repair."""

from __future__ import annotations

from homeassistant.core import ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.config_flow import build_main_options_schema, build_options_schema
from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_HEATING_TYPE,
    DEFAULT_OPTIONS,
    DOMAIN,
    SERVICE_RESET_SMARTPI_LEARNING,
)
from custom_components.vtherm_smartpi.repairs import CONF_RESET_MODEL, WaterModelResetFlow, async_create_fix_flow
from custom_components.vtherm_smartpi.smartpi.const import HEATING_TYPES


def _field(schema, name):
    for key, value in schema.schema.items():
        if getattr(key, "schema", key) == name:
            return key, value
    raise AssertionError(f"{name} not in schema")


def test_heating_type_defaults_to_water_radiator():
    assert DEFAULT_OPTIONS[CONF_SMART_PI_HEATING_TYPE] == "radiator"
    for schema in (build_options_schema(DEFAULT_OPTIONS), build_main_options_schema(DEFAULT_OPTIONS, include_valve_linearization=True)):
        key, value = _field(schema, CONF_SMART_PI_HEATING_TYPE)
        assert key.default() == "radiator"
        assert isinstance(value, selector.SelectSelector)
        assert value.config["options"] == list(HEATING_TYPES)
        assert value.config["translation_key"] == "heating_type"


def test_an_entry_without_the_key_shows_the_default():
    defaults = {k: v for k, v in DEFAULT_OPTIONS.items() if k != CONF_SMART_PI_HEATING_TYPE}
    key, _ = _field(build_main_options_schema(defaults, include_valve_linearization=False), CONF_SMART_PI_HEATING_TYPE)
    assert key.default() == "radiator"


@pytest.mark.asyncio
async def test_global_options_store_the_heating_type(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="SmartPI defaults", unique_id=DOMAIN, data=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    assert result["type"] == FlowResultType.FORM
    shown = {getattr(key, "schema", key) for key in result["data_schema"].schema}
    user_input = {key: value for key, value in DEFAULT_OPTIONS.items() if key in shown}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**user_input, CONF_SMART_PI_HEATING_TYPE: "floor_water"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    stored = {**entry.data, **entry.options}
    assert stored[CONF_SMART_PI_HEATING_TYPE] == "floor_water"


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", [True, False])
async def test_the_repair_resets_the_model_only_when_checked(hass, reset) -> None:
    calls: list[ServiceCall] = []

    async def handle(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register(DOMAIN, SERVICE_RESET_SMARTPI_LEARNING, handle)
    flow = await async_create_fix_flow(hass, "water_data_changed_x", {"entity_id": "climate.room"})
    assert isinstance(flow, WaterModelResetFlow)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    schema_key, _ = _field(result["data_schema"], CONF_RESET_MODEL)
    assert schema_key.default() is True
    result = await flow.async_step_confirm({CONF_RESET_MODEL: reset})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert [call.data["entity_id"] for call in calls] == (["climate.room"] if reset else [])


def test_english_texts_cover_the_heating_type_and_the_reset_offer():
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / "custom_components" / "vtherm_smartpi" / "translations" / "en.json"
    texts = json.loads(path.read_text(encoding="utf-8"))
    assert set(texts["selector"]["heating_type"]["options"]) == set(HEATING_TYPES)
    for section, step in (("config", "global"), ("config", "thermostat_settings"), ("options", "init"), ("options", "settings")):
        assert CONF_SMART_PI_HEATING_TYPE in texts[section]["step"][step]["data"]
    for issue in ("water_data_appeared", "water_data_gone"):
        confirm = texts["issues"][issue]["fix_flow"]["step"]["confirm"]
        assert CONF_RESET_MODEL in confirm["data"]
        assert "{name}" in texts["issues"][issue]["title"]
