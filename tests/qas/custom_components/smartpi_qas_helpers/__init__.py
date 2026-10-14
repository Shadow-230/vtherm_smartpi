"""Test-only helpers for the QAS bench, reaching into the running SmartPI — never released.

What the in-process tests do with patches, for scenarios on a real Home Assistant:

- ``fire_autocalib_event``: an AutoCalib event fired through SmartPI's own handler
  (``_autocalib_event_data``), with the payload AutoCalib builds (the thermostat's name), without
  changing AutoCalib's state — for QA4 when no event comes on its own in time;
- ``silence_lead``: the main source of a zone stops publishing its commands to its additional
  source, while the link stays attached — for QE3, which checks the 15-minute fallback;
- ``seed_state``: SmartPI's learned state replaced by a fixture (``fixtures/<name>.json``) and
  saved by SmartPI itself, so a later version starts from what this one stored — for QB2;
- ``recorded_events``: the AutoCalib and SmartPI events fired since the start (or since
  ``clear``), with their data, as the service's response — the bench's client hears only service
  calls.

Set up from YAML (``smartpi_qas_helpers:``). Each service names the VTherm climate entity.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

import voluptuous as vol
from homeassistant.core import Event, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartpi_qas_helpers"
SMARTPI = "vtherm_smartpi"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Any(None, {})}, extra=vol.ALLOW_EXTRA)
RECORDED = (
    "smartpi_autocalib_snapshot_taken",
    "smartpi_autocalib_triggered",
    "smartpi_autocalib_success",
    "smartpi_autocalib_retry",
    "smartpi_autocalib_degraded",
    "vtherm_smartpi_event",
)
MAX_RECORDED = 200


def _thermostat(hass: HomeAssistant, entity_id: str) -> Any:
    component = hass.data.get("climate")
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None or getattr(entity, "_algo_handler", None) is None:
        raise ServiceValidationError(f"{entity_id} is not a VTherm thermostat with SmartPI")
    return entity


def _source_link(hass: HomeAssistant) -> Any:
    link = hass.data.get(SMARTPI, {}).get("source_link")
    if link is None:
        raise ServiceValidationError("no SmartPI source link: no zone with two sources is running")
    return link


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    silenced: set[str] = set()
    recorded: list[dict[str, Any]] = []

    @callback
    def _record(event: Event) -> None:
        recorded.append(
            {"event_type": event.event_type, "time": event.time_fired.isoformat(), "data": dict(event.data)}
        )
        del recorded[:-MAX_RECORDED]

    for event_type in RECORDED:
        hass.bus.async_listen(event_type, _record)

    async def recorded_events(call: ServiceCall) -> ServiceResponse:
        events = list(recorded)
        if call.data.get("clear"):
            recorded.clear()
        return {"events": events}

    async def fire_autocalib_event(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        handler = thermostat._algo_handler  # noqa: SLF001 — a test helper reaches in
        algo = thermostat.prop_algorithm
        from custom_components.vtherm_smartpi.smartpi.autocalib import (  # noqa: PLC0415
            AutoCalibEvent,
        )

        event = AutoCalibEvent(
            event_type="smartpi_autocalib_snapshot_taken",
            payload={"name": algo.autocalib._name, "reason": "qas_helper", "dt_cool_unavailable": False},  # noqa: SLF001
        )
        hass.bus.async_fire(event.event_type, handler._autocalib_event_data(event))  # noqa: SLF001
        _LOGGER.info("smartpi_qas_helpers: AutoCalib event fired for %s", thermostat.entity_id)

    async def silence_lead(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        link = _source_link(hass)
        lead = thermostat.unique_id
        if not hasattr(link, "_qas_publish"):
            link._qas_publish = link.publish_command  # noqa: SLF001

            def publish(lead_id: str, *args: Any, **kwargs: Any) -> None:
                if lead_id in silenced:
                    return
                link._qas_publish(lead_id, *args, **kwargs)  # noqa: SLF001

            link.publish_command = publish
        if call.data["silent"]:
            silenced.add(lead)
        else:
            silenced.discard(lead)
        _LOGGER.warning(
            "smartpi_qas_helpers: main source %s %s",
            thermostat.entity_id,
            "silenced" if call.data["silent"] else "publishing again",
        )

    async def seed_state(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        path = FIXTURES / f"{call.data['fixture']}.json"
        if not path.is_file():
            raise ServiceValidationError(f"no fixture {path.name}")
        payload = await hass.async_add_executor_job(path.read_text, "utf-8")
        thermostat.prop_algorithm.load_state(json.loads(payload))
        await thermostat._algo_handler._async_save()  # noqa: SLF001
        _LOGGER.warning(
            "smartpi_qas_helpers: SmartPI state of %s replaced by the fixture %s",
            thermostat.entity_id,
            path.name,
        )

    hass.services.async_register(
        DOMAIN,
        "recorded_events",
        recorded_events,
        vol.Schema({vol.Optional("clear", default=False): cv.boolean}),
        supports_response=SupportsResponse.ONLY,
    )
    entity = vol.Schema({vol.Required("entity_id"): cv.entity_id})
    hass.services.async_register(DOMAIN, "fire_autocalib_event", fire_autocalib_event, entity)
    hass.services.async_register(
        DOMAIN,
        "silence_lead",
        silence_lead,
        vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("silent"): cv.boolean}),
    )
    hass.services.async_register(
        DOMAIN,
        "seed_state",
        seed_state,
        vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("fixture"): cv.slug}),
    )
    return True
