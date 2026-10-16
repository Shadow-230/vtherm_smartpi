"""SmartPI's four services reach the thermostats they name, on VTherm's climate entities, and
refuse a call that names no SmartPI thermostat.

The author's ``test_services.py`` checks the learning switch's schema; these check every
service, the ways a call names its thermostats, and the thermostats a service skips.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

import custom_components.vtherm_smartpi as smartpi_init
from custom_components.vtherm_smartpi.const import (
    DOMAIN,
    PROP_FUNCTION_SMART_PI,
    SERVICE_FORCE_SMARTPI_CALIBRATION,
    SERVICE_RESET_SMARTPI_INTEGRAL,
    SERVICE_RESET_SMARTPI_LEARNING,
    SERVICE_SET_SMARTPI_LEARNING,
)

METHODS = {
    SERVICE_RESET_SMARTPI_LEARNING: "service_reset_smart_pi_learning",
    SERVICE_FORCE_SMARTPI_CALIBRATION: "service_force_smartpi_calibration",
    SERVICE_RESET_SMARTPI_INTEGRAL: "service_reset_smartpi_integral",
    SERVICE_SET_SMARTPI_LEARNING: "service_set_smartpi_learning",
}


def _entity(entity_id: str, algorithm: str = PROP_FUNCTION_SMART_PI, on_handler: bool = False):
    """A climate entity: its service methods on itself, or on its algorithm handler."""
    entity = SimpleNamespace(entity_id=entity_id, proportional_function=algorithm)
    methods = {name: AsyncMock() for name in METHODS.values()}
    if on_handler:
        entity._algo_handler = SimpleNamespace(**methods)
    else:
        entity.__dict__.update(methods)
    return entity, methods


def _services(monkeypatch, entities, extracted=()):
    monkeypatch.setattr(
        "custom_components.vtherm_smartpi.service_helper.async_extract_entity_ids",
        AsyncMock(return_value=set(extracted)),
    )
    component = SimpleNamespace(entities=list(entities))
    hass = MagicMock()
    hass.data = {"climate": component}
    smartpi_init._register_services(hass)
    return hass, {call.args[1]: call.args[2] for call in hass.services.async_register.call_args_list}


def _call(data=None, target=None):
    return SimpleNamespace(data=data or {}, target=target)


@pytest.mark.parametrize("service", list(METHODS))
async def test_each_service_reaches_the_thermostat_s_method(monkeypatch, service) -> None:
    living, methods = _entity("climate.living")
    _hass, handlers = _services(monkeypatch, [living], extracted={"climate.living"})
    data = {"learning_enabled": True} if service == SERVICE_SET_SMARTPI_LEARNING else {}

    await handlers[service](_call(data))

    method = methods[METHODS[service]]
    if service == SERVICE_SET_SMARTPI_LEARNING:
        method.assert_awaited_once_with(True)
    else:
        method.assert_awaited_once_with()


async def test_each_named_thermostat_is_reached_once_also_through_its_handler(monkeypatch) -> None:
    """The thermostats Home Assistant extracts from the call (its target or the entity_id in its
    data); a method only on the algorithm handler is found there."""
    living, living_methods = _entity("climate.living", on_handler=True)
    hall, hall_methods = _entity("climate.hall")
    _hass, handlers = _services(monkeypatch, [living, hall], extracted={"climate.living", "climate.hall"})

    await handlers[SERVICE_RESET_SMARTPI_INTEGRAL](_call())

    living_methods[METHODS[SERVICE_RESET_SMARTPI_INTEGRAL]].assert_awaited_once()
    hall_methods[METHODS[SERVICE_RESET_SMARTPI_INTEGRAL]].assert_awaited_once()


async def test_a_mixed_target_reaches_only_smartpi_thermostats(monkeypatch) -> None:
    living, living_methods = _entity("climate.living")
    tpi, tpi_methods = _entity("climate.tpi", algorithm="tpi")
    _hass, handlers = _services(monkeypatch, [living, tpi], extracted={"climate.living", "climate.tpi"})

    await handlers[SERVICE_FORCE_SMARTPI_CALIBRATION](_call())

    living_methods[METHODS[SERVICE_FORCE_SMARTPI_CALIBRATION]].assert_awaited_once()
    tpi_methods[METHODS[SERVICE_FORCE_SMARTPI_CALIBRATION]].assert_not_awaited()


async def test_thermostats_that_cannot_take_the_service_are_skipped(monkeypatch, caplog) -> None:
    """Another algorithm, an entity that is not there, a SmartPI thermostat without the method
    (warned about): nothing is called."""
    tpi, tpi_methods = _entity("climate.tpi", algorithm="tpi")
    bare = SimpleNamespace(entity_id="climate.bare", proportional_function=PROP_FUNCTION_SMART_PI)
    _hass, handlers = _services(monkeypatch, [tpi, bare], extracted={"climate.tpi", "climate.gone", "climate.bare"})

    await handlers[SERVICE_FORCE_SMARTPI_CALIBRATION](_call())

    tpi_methods[METHODS[SERVICE_FORCE_SMARTPI_CALIBRATION]].assert_not_awaited()
    assert "not available on climate.bare" in caplog.text


async def test_a_call_without_a_smartpi_thermostat_is_refused(monkeypatch) -> None:
    """No target, only another algorithm's thermostat, or no climate component: the call shows
    an error instead of doing nothing."""
    living, methods = _entity("climate.living")
    tpi, _ = _entity("climate.tpi", algorithm="tpi")
    hass, handlers = _services(monkeypatch, [living, tpi])
    with pytest.raises(ServiceValidationError) as raised:
        await handlers[SERVICE_RESET_SMARTPI_LEARNING](_call())
    assert raised.value.translation_key == "no_smartpi_target"

    hass, handlers = _services(monkeypatch, [living, tpi], extracted={"climate.tpi"})
    with pytest.raises(ServiceValidationError):
        await handlers[SERVICE_RESET_SMARTPI_LEARNING](_call())

    hass, handlers = _services(monkeypatch, [living], extracted={"climate.living"})
    hass.data = {}
    with pytest.raises(ServiceValidationError):
        await handlers[SERVICE_RESET_SMARTPI_LEARNING](_call())
    methods[METHODS[SERVICE_RESET_SMARTPI_LEARNING]].assert_not_awaited()


async def test_a_qas_style_call_with_entity_id_in_data_still_works(hass) -> None:
    """The QAS test pack calls the services with {"entity_id": ...} in the data."""
    living, methods = _entity("climate.living")
    hass.data["climate"] = SimpleNamespace(entities=[living])
    smartpi_init._register_services(hass)

    await hass.services.async_call(DOMAIN, SERVICE_SET_SMARTPI_LEARNING, {"entity_id": "climate.living", "learning_enabled": False}, blocking=True)
    await hass.services.async_call(DOMAIN, SERVICE_RESET_SMARTPI_INTEGRAL, {"entity_id": ["climate.living"]}, blocking=True)

    methods[METHODS[SERVICE_SET_SMARTPI_LEARNING]].assert_awaited_once_with(False)
    methods[METHODS[SERVICE_RESET_SMARTPI_INTEGRAL]].assert_awaited_once_with()


def test_the_services_are_registered_with_the_integration_and_never_removed() -> None:
    """async_setup registers them once; an entry's unload or removal leaves them in place."""
    hass = MagicMock()
    hass.data = {}
    smartpi_init._register_services(hass)
    assert {call.args[1] for call in hass.services.async_register.call_args_list} == set(METHODS)
    assert not hasattr(smartpi_init, "_unregister_services")
