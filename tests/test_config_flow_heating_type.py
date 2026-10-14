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

from flow_helpers import save


def _field(schema, name):
    for key, value in schema.schema.items():
        if getattr(key, "schema", key) == name:
            return key, value
    raise AssertionError(f"{name} not in schema")


def test_the_heating_type_comes_first_and_starts_not_set():
    """Set for one thermostat only: not in the defaults; not set = no water data, no boiler's call."""
    assert CONF_SMART_PI_HEATING_TYPE not in DEFAULT_OPTIONS
    assert CONF_SMART_PI_HEATING_TYPE not in {getattr(k, "schema", k) for k in build_options_schema(DEFAULT_OPTIONS).schema}
    schema = build_main_options_schema(DEFAULT_OPTIONS, include_valve_linearization=True)
    assert getattr(next(iter(schema.schema)), "schema", None) == CONF_SMART_PI_HEATING_TYPE
    key, value = _field(schema, CONF_SMART_PI_HEATING_TYPE)
    assert key.default() == "not_set"
    assert isinstance(value, selector.SelectSelector)
    assert value.config["options"] == ["not_set", *HEATING_TYPES]
    assert value.config["translation_key"] == "heating_type"


def test_an_entry_with_a_heating_type_shows_it():
    key, _ = _field(
        build_main_options_schema({**DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: "floor_water"}, include_valve_linearization=False),
        CONF_SMART_PI_HEATING_TYPE,
    )
    assert key.default() == "floor_water"


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen, stored", [("floor_water", "floor_water"), ("not_set", None)])
async def test_a_thermostat_s_settings_store_the_heating_type(hass, chosen, stored) -> None:
    """"Not set" is stored as no key."""
    from unittest.mock import patch

    from custom_components.vtherm_smartpi.const import CONF_TARGET_VTHERM

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-vt-x",
        data={CONF_TARGET_VTHERM: "vt-x", **DEFAULT_OPTIONS},
        options={CONF_TARGET_VTHERM: "vt-x", **DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: "radiator"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.vtherm_smartpi.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SMART_PI_HEATING_TYPE: chosen, "smart_pi_use_defaults": True}
        )
        # Another heating type changes what was learned: the reset question at save.
        result = await save(hass, result, reset=False)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options.get(CONF_SMART_PI_HEATING_TYPE) == stored


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
    assert set(texts["selector"]["heating_type"]["options"]) == {"not_set", *HEATING_TYPES}
    for section, step in (("config", "settings"), ("options", "settings"), ("options", "source_role")):
        assert CONF_SMART_PI_HEATING_TYPE in texts[section]["step"][step]["data"]
    for section, step in (("config", "global"), ("options", "regulator")):
        assert CONF_SMART_PI_HEATING_TYPE not in texts[section]["step"][step]["data"]
    for issue in ("water_data_appeared", "water_data_gone"):
        confirm = texts["issues"][issue]["fix_flow"]["step"]["confirm"]
        assert CONF_RESET_MODEL in confirm["data"]
        assert "{name}" in texts["issues"][issue]["title"]
