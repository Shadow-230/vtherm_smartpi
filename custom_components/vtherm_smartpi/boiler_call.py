"""Versatile Thermostat's central boiler threshold, read for SmartPI's learning.

Versatile Thermostat starts its central boiler when enough devices (valve heads
or switches) of its thermostats are active, or when their total power reaches a
threshold. Below the threshold the
boiler does not heat: a thermostat that calls gets no heat, and the room keeps
cooling although SmartPI's command says it heats. SmartPI pauses its learning
then (documentation/en/technical_doc.md, 3.7).
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import entity_registry as er

# Versatile Thermostat's central boiler entities, by unique ID.
VT_DOMAIN = "versatile_thermostat"
VT_ACTIVE_COUNT_UNIQUE_ID = "nb_device_active_boiler"
VT_ACTIVE_POWER_UNIQUE_ID = "total_power_active_boiler"
VT_COUNT_THRESHOLD_UNIQUE_ID = "boiler_activation_threshold"
VT_POWER_THRESHOLD_UNIQUE_ID = "boiler_power_activation_threshold"
_KILO_WATT_UNITS = ("kW", "kw")


def _number(hass: Any, registry: Any, domain: str, unique_id: str) -> tuple[float | None, str | None]:
    """The numeric state of a Versatile Thermostat entity and its unit."""
    entity_id = registry.async_get_entity_id(domain, VT_DOMAIN, unique_id)
    states = getattr(hass, "states", None)
    state = states.get(entity_id) if entity_id is not None and states is not None else None
    if state is None:
        return None, None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None, None
    return value, state.attributes.get("unit_of_measurement")


def _watts(value: float, unit: str | None) -> float:
    """As Versatile Thermostat does: kW is converted, anything else counts as W."""
    return value * 1000.0 if unit in _KILO_WATT_UNITS else value


def central_boiler_threshold_reached(hass: Any) -> bool | None:
    """Whether the demand reaches Versatile Thermostat's central boiler threshold.

    Versatile Thermostat's rule: a threshold counts when it is above 0 and the
    number of active devices (valve heads or switches), or their total power,
    reaches it. None when the values cannot be read.
    """
    try:
        registry = er.async_get(hass)
    except Exception:  # pylint: disable=broad-except
        return None
    count, _ = _number(hass, registry, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID)
    count_threshold, _ = _number(hass, registry, "number", VT_COUNT_THRESHOLD_UNIQUE_ID)
    power, power_unit = _number(hass, registry, "sensor", VT_ACTIVE_POWER_UNIQUE_ID)
    power_threshold, threshold_unit = _number(hass, registry, "number", VT_POWER_THRESHOLD_UNIQUE_ID)
    known = False
    if count is not None and count_threshold is not None:
        known = True
        if count_threshold > 0 and count >= count_threshold:
            return True
    if power is not None and power_threshold is not None:
        known = True
        if power_threshold > 0 and _watts(power, power_unit) >= _watts(power_threshold, threshold_unit):
            return True
    return False if known else None
