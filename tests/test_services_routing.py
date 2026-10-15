"""SmartPI's four services reach the thermostats they name, on VTherm's climate entities.

The author's ``test_services.py`` checks the learning switch's schema; these check every
service, the ways a call names its thermostats, and the thermostats a service skips.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.vtherm_smartpi as smartpi_init
from custom_components.vtherm_smartpi.const import (
    DATA_SERVICES_REGISTERED,
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


async def test_a_call_names_thermostats_by_target_and_data_and_reaches_the_handler(monkeypatch) -> None:
    """Entity ids from the target (a string or a list) and from the data; a method only on the
    algorithm handler is found there; each thermostat once."""
    living, living_methods = _entity("climate.living", on_handler=True)
    hall, hall_methods = _entity("climate.hall")
    den, den_methods = _entity("climate.den")
    _hass, handlers = _services(monkeypatch, [living, hall, den])

    await handlers[SERVICE_RESET_SMARTPI_INTEGRAL](
        _call({"entity_id": ["climate.den", 7]}, target={"entity_id": ["climate.living", "climate.hall", None]})
    )
    await handlers[SERVICE_RESET_SMARTPI_INTEGRAL](_call({"entity_id": "climate.hall"}, target={"entity_id": "climate.den"}))

    living_methods[METHODS[SERVICE_RESET_SMARTPI_INTEGRAL]].assert_awaited_once()
    assert hall_methods[METHODS[SERVICE_RESET_SMARTPI_INTEGRAL]].await_count == 2
    assert den_methods[METHODS[SERVICE_RESET_SMARTPI_INTEGRAL]].await_count == 2


async def test_thermostats_that_cannot_take_the_service_are_skipped(monkeypatch, caplog) -> None:
    """Another algorithm, an entity that is not there, a SmartPI thermostat without the method
    (warned about): nothing is called."""
    tpi, tpi_methods = _entity("climate.tpi", algorithm="tpi")
    bare = SimpleNamespace(entity_id="climate.bare", proportional_function=PROP_FUNCTION_SMART_PI)
    _hass, handlers = _services(monkeypatch, [tpi, bare], extracted={"climate.tpi", "climate.gone", "climate.bare"})

    await handlers[SERVICE_FORCE_SMARTPI_CALIBRATION](_call())

    tpi_methods[METHODS[SERVICE_FORCE_SMARTPI_CALIBRATION]].assert_not_awaited()
    assert "not available on climate.bare" in caplog.text


async def test_without_the_climate_component_or_a_target_nothing_is_called(monkeypatch) -> None:
    living, methods = _entity("climate.living")
    hass, handlers = _services(monkeypatch, [living])
    await handlers[SERVICE_RESET_SMARTPI_LEARNING](_call())
    hass.data = {}
    await handlers[SERVICE_RESET_SMARTPI_LEARNING](_call({"entity_id": "climate.living"}))
    methods[METHODS[SERVICE_RESET_SMARTPI_LEARNING]].assert_not_awaited()


def test_services_are_registered_once_and_removed() -> None:
    hass = MagicMock()
    hass.data = {}
    smartpi_init._register_services(hass)
    smartpi_init._register_services(hass)
    assert hass.services.async_register.call_count == 4
    assert hass.data[DOMAIN][DATA_SERVICES_REGISTERED] is True

    smartpi_init._unregister_services(hass)
    assert {call.args[1] for call in hass.services.async_remove.call_args_list} == set(METHODS)
    assert hass.data[DOMAIN][DATA_SERVICES_REGISTERED] is False
