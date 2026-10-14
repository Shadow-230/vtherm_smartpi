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
  calls;
- a heating provider of SmartPI's contract (v1), fed by the room simulator, for the thermostats
  of its ``circuits`` (climate entity → simulated heater): ``set_provider`` registers or
  unregisters it, forces a reason for its supply water or for a boiler not called, and
  ``provider_status`` tells how often SmartPI asked it, by thermostat, and which providers are
  registered.

Set up from YAML (``smartpi_qas_helpers:``, with ``provider: {name, circuits}`` for the provider).
Each other service names the VTherm climate entity.
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
SIM = "smartpi_room_sim"
# SmartPI's registry of heating providers (its heating_inputs.py): nothing of SmartPI is imported.
PROVIDERS_KEY = "vtherm_smartpi_heating_providers"
_PROVIDER = vol.Schema({vol.Required("name"): cv.string, vol.Required("circuits"): {cv.entity_id: cv.slug}})
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Any(None, vol.Schema({vol.Optional("provider"): _PROVIDER}))}, extra=vol.ALLOW_EXTRA
)
RECORDED = (
    "smartpi_autocalib_snapshot_taken",
    "smartpi_autocalib_triggered",
    "smartpi_autocalib_success",
    "smartpi_autocalib_retry",
    "smartpi_autocalib_degraded",
    "vtherm_smartpi_event",
)
MAX_RECORDED = 200


class SimProvider:
    """A heating provider of SmartPI's contract (v1): the simulated boiler's water for each of its
    circuits, and whether the boiler is called.

    ``supply_reason``: forced for every circuit (no supply temperature then); ``call_reason``: the
    boiler not called for this reason (the simulated boiler then does not fire either).
    """

    version = 1

    def __init__(self, hass: HomeAssistant, name: str, circuits: dict[str, str]) -> None:
        self.hass = hass
        self.name = name
        self.circuits = dict(circuits)
        self.calls: dict[str, int] = {}
        self.supply_reason: str | None = None
        self.call_reason: str | None = None

    def heating_inputs(self, entity_id: str) -> dict[str, Any] | None:
        self.calls[entity_id] = self.calls.get(entity_id, 0) + 1
        heater = self.circuits.get(entity_id)
        hub = self.hass.data.get(SIM)
        if heater is None or hub is None or hub.sim.spec.boiler is None:
            return None
        sim = hub.sim
        data: dict[str, Any] = {}
        if self.supply_reason:
            data.update(supply_temperature=None, supply_reason=self.supply_reason, heat_available=None, heat_reason=None)
        else:
            if sim.dhw_active():
                heat = (False, "dhw_active")
            elif sim.boiler_state == "fault":
                heat = (False, "fault")
            elif sim.boiler_state == "off":
                heat = (False, "boiler_off")
            elif not sim.firing:
                heat = (False, "flame_off")
            else:
                heat = (True, None)
            supply = sim.supply_c()
            data.update(
                supply_temperature=round(supply, 1) if supply is not None else None,
                supply_reason=None,
                heat_available=heat[0],
                heat_reason=heat[1],
            )
        calling = sum(1 for name in self.circuits.values() if sim.applied.get(name, 0.0) > 0.0)
        if self.call_reason:
            data.update(boiler_called=False, call_reason=self.call_reason)
        elif sim.boiler_state == "fault":
            data.update(boiler_called=False, call_reason="boiler_fault")
        elif sim.firing:
            data.update(boiler_called=True, call_reason=None)
        else:
            data.update(boiler_called=False, call_reason="no_demand")
        data["demand"] = {"demand_count": calling, "count_threshold": 2, "count_by": "zones"}
        return data


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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    silenced: set[str] = set()
    provider_config = (config.get(DOMAIN) or {}).get("provider")
    provider = (
        SimProvider(hass, provider_config["name"], provider_config["circuits"]) if provider_config else None
    )
    switched_off: dict[str, bool] = {}

    def _register(registered: bool) -> None:
        registry = hass.data.setdefault(PROVIDERS_KEY, {})
        if registered:
            registry[provider.name] = provider
        else:
            registry.pop(provider.name, None)

    if provider is not None:
        _register(True)

    async def set_provider(call: ServiceCall) -> None:
        if provider is None:
            raise ServiceValidationError("no provider configured (smartpi_qas_helpers: provider:)")
        if "registered" in call.data:
            _register(call.data["registered"])
        if "supply_reason" in call.data:
            provider.supply_reason = call.data["supply_reason"] or None
        if "call_reason" in call.data:
            provider.call_reason = call.data["call_reason"] or None
            hub = hass.data.get(SIM)
            if hub is not None and hub.sim.spec.boiler is not None:
                # Not called, the boiler does not fire; called again, it goes back to its switch.
                hub.changed()
                if provider.call_reason and "boiler_on" not in switched_off:
                    switched_off["boiler_on"] = hub.sim.boiler_on
                    hub.sim.set_boiler(False)
                elif not provider.call_reason and "boiler_on" in switched_off:
                    hub.sim.set_boiler(switched_off.pop("boiler_on"))
                hub.changed()
        _LOGGER.warning(
            "smartpi_qas_helpers: provider %s %s, supply reason %s, call reason %s",
            provider.name,
            "registered" if hass.data.get(PROVIDERS_KEY, {}).get(provider.name) is provider else "not registered",
            provider.supply_reason,
            provider.call_reason,
        )

    async def provider_status(call: ServiceCall) -> ServiceResponse:
        del call
        providers = sorted(name for name in (hass.data.get(PROVIDERS_KEY) or {}) if isinstance(name, str))
        if provider is None:
            return {"configured": False, "providers": providers}
        return {
            "configured": True,
            "providers": providers,
            "registered": hass.data.get(PROVIDERS_KEY, {}).get(provider.name) is provider,
            "calls": dict(provider.calls),
            "supply_reason": provider.supply_reason,
            "call_reason": provider.call_reason,
        }
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
        "set_provider",
        set_provider,
        vol.Schema(
            {
                vol.Optional("registered"): cv.boolean,
                vol.Optional("supply_reason"): vol.Any(None, cv.string),
                vol.Optional("call_reason"): vol.Any(None, cv.string),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, "provider_status", provider_status, vol.Schema({}), supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN,
        "seed_state",
        seed_state,
        vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("fixture"): cv.slug}),
    )
    return True
