"""Versatile Thermostat's central boiler, read for SmartPI's learning.

A thermostat that Versatile Thermostat counts for its central boiler gets heat
only while the boiler is on. While SmartPI calls for heat and the boiler is
off, the room cools although the command says it heats: SmartPI does not learn
heating then (documentation/en/technical_doc.md, 3.8).
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import entity_registry as er

from .smartpi.const import SMARTPI_RECALC_INTERVAL_SEC

# Versatile Thermostat's central boiler state, a binary sensor, by unique ID.
VT_DOMAIN = "versatile_thermostat"
VT_CENTRAL_BOILER_UNIQUE_ID = "central_boiler_state"

# The pause starts once SmartPI has called with the boiler off, without a break, for one period
# of its periodic calculation. When a thermostat's own start turns the boiler on, Versatile
# Thermostat does so only once it counts the thermostat's device, after the device reports its
# new state: seconds with a real valve head. Checks in that time must not pause. One whole period
# is much longer, and two periodic calculations a period apart still confirm a pause: their gap
# differs from the period by milliseconds, hence the margin of 1 s. The value follows SmartPI's
# calculation period, not a publication.
BOILER_PAUSE_TIMER_MARGIN_S = 1.0
BOILER_PAUSE_CONFIRM_S = SMARTPI_RECALC_INTERVAL_SEC - BOILER_PAUSE_TIMER_MARGIN_S


def central_boiler_entity_id(hass: Any) -> str | None:
    """The entity ID of Versatile Thermostat's central boiler binary sensor, None without it."""
    try:
        registry = er.async_get(hass)
    except Exception:  # pylint: disable=broad-except
        return None
    return registry.async_get_entity_id("binary_sensor", VT_DOMAIN, VT_CENTRAL_BOILER_UNIQUE_ID)


def central_boiler_on(hass: Any) -> bool | None:
    """Whether Versatile Thermostat's central boiler is on; None when it cannot be read."""
    entity_id = central_boiler_entity_id(hass)
    states = getattr(hass, "states", None)
    state = states.get(entity_id) if entity_id is not None and states is not None else None
    if state is None or state.state not in ("on", "off"):
        return None
    return state.state == "on"
