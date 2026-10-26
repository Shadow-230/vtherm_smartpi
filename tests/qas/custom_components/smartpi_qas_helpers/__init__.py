"""Test-only helper for the QAS bench, reaching into the running SmartPI — never released.

- ``seed_state``: a thermostat's SmartPI state replaced by a learned one (``fixtures/<name>.json``,
  written by ``tests/qas/build_fixtures.py``) and saved by SmartPI itself. A scenario that needs a
  learned SmartPI starts from it: a calibration, for example, does not start while SmartPI still
  learns from scratch.

Set up from YAML (``smartpi_qas_helpers:``). The service names the VTherm climate entity.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartpi_qas_helpers"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Any(None, {})}, extra=vol.ALLOW_EXTRA)


def _thermostat(hass: HomeAssistant, entity_id: str) -> Any:
    component = hass.data.get("climate")
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None or getattr(entity, "_algo_handler", None) is None:
        raise ServiceValidationError(f"{entity_id} is not a VTherm thermostat with SmartPI")
    return entity


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def seed_state(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        path = FIXTURES / f"{call.data['fixture']}.json"
        if not path.is_file():
            raise ServiceValidationError(f"no fixture {path.name}")
        payload = await hass.async_add_executor_job(path.read_text, "utf-8")
        thermostat.prop_algorithm.load_state(json.loads(payload))
        await thermostat._algo_handler._async_save()  # noqa: SLF001 — a test helper reaches in
        _LOGGER.warning(
            "smartpi_qas_helpers: SmartPI state of %s replaced by the fixture %s",
            thermostat.entity_id,
            path.name,
        )

    hass.services.async_register(
        DOMAIN,
        "seed_state",
        seed_state,
        vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("fixture"): cv.slug}),
    )
    return True
