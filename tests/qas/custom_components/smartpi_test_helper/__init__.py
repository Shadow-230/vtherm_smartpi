"""SmartPI test helper for the QAS test instances only, never part of a release.

One service, ``load_learned_state``: a thermostat's SmartPI state replaced by a learned one
(``states/<name>.json``, written by ``tests/qas/learned_state.py``) and saved by SmartPI itself,
then one forced calculation, as after a change of the setpoint, so that SmartPI's diagnostics
show the learned model at once. A scenario that needs a learned SmartPI starts from it: a
calibration does not start while SmartPI still learns from scratch.

Set up from YAML (``smartpi_test_helper:``).
"""

from __future__ import annotations

import json
import logging
import pathlib

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartpi_test_helper"
STATES = pathlib.Path(__file__).resolve().parent / "states"
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Any(None, {})}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def load_learned_state(call: ServiceCall) -> None:
        component = hass.data.get("climate")
        thermostat = component.get_entity(call.data["entity_id"]) if component is not None else None
        handler = getattr(thermostat, "_algo_handler", None)
        if handler is None:
            raise ServiceValidationError(f"{call.data['entity_id']} is not a VTherm thermostat with SmartPI")
        path = STATES / f"{call.data['state']}.json"
        if not path.is_file():
            raise ServiceValidationError(f"no learned state {path.name}")
        state = json.loads(await hass.async_add_executor_job(path.read_text, "utf-8"))
        thermostat.prop_algorithm.load_state(state)
        await handler._async_save()  # noqa: SLF001 — a test helper reaches into SmartPI
        await thermostat.async_control_heating(force=True)
        _LOGGER.warning("smartpi_test_helper: %s now holds the learned state %s", thermostat.entity_id, path.name)

    hass.services.async_register(
        DOMAIN,
        "load_learned_state",
        load_learned_state,
        vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("state"): cv.slug}),
    )
    return True
