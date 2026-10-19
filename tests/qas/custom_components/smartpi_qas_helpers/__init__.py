"""Test-only helpers for the QAS bench, reaching into the running SmartPI — never released.

What the in-process tests do with patches, for scenarios on a real Home Assistant:

- ``fire_autocalib_event``: an AutoCalib event fired through SmartPI's own handler
  (``_autocalib_event_data``), with the payload AutoCalib builds (the thermostat's name), without
  changing AutoCalib's state — for QA4 when no event comes on its own in time;
- ``silence_lead``: the main source of a zone stops publishing its commands to its additional
  source, while the link stays attached — for QE3, which checks the 15-minute fallback;
- ``seed_state``: SmartPI's learned state replaced by a fixture (``fixtures/<name>.json``) and
  saved by SmartPI itself, so a later version starts from what this one stored — for QB2, and the
  learned model the QL scenarios start from. The fixture is made ready for the thermostat first
  (``prepared_state``): it keeps the reset token the thermostat already applied (a restart then
  does not reset the model again), and its zone's item takes the id the zone form gave here;
- ``keep_state`` / ``restore_state``: a thermostat's SmartPI state kept under a name (in the
  helpers' own storage, so it survives a restart) and put back later — for QL2, which resets the
  model and puts back the one it found;
- ``recorded_events``: the AutoCalib and SmartPI events fired since the start (or since
  ``clear``), with their data, as the service's response — the bench's client hears only service
  calls;
- a heating provider of SmartPI's contract (v1), fed by the room simulator, for the thermostats
  of its ``circuits`` (climate entity → simulated heater): ``set_provider`` registers or
  unregisters it, forces a reason for its supply water or for a boiler not called, and
  ``provider_status`` tells how often SmartPI asked it, by thermostat, and which providers are
  registered;
- ``entry_setups``: how many times a SmartPI entry was set up (loaded) since the start, from Home
  Assistant's config entry changes — for QA7, which checks that a save reloads the entry once and
  a closed window not at all.

Set up from YAML (``smartpi_qas_helpers:``, with ``provider: {name, circuits}`` for the provider).
Each other service names the VTherm climate entity.
"""

from __future__ import annotations

import copy
import json
import logging
import pathlib
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryChange, ConfigEntryState
from homeassistant.core import Event, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
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
KEPT_STORE_KEY = f"{DOMAIN}.kept_states"


def prepared_state(payload: dict[str, Any], applied_reset_token: str | None, source_item_id: str | None) -> dict[str, Any]:
    """A saved SmartPI state made ready for one thermostat.

    - The reset token it applied stays the thermostat's own: a fixture's token (or none) would
      make SmartPI apply the entry's reset again at the next start.
    - A zone's item (the item holding a ``zone_fit``) takes the id of the thermostat's configured
      zone item, which the form chose at random; without a zone here it is dropped, as SmartPI
      drops what is not configured when it starts.
    """
    state = copy.deepcopy(payload)
    if applied_reset_token is not None:
        state["dependency_reset_token"] = applied_reset_token
    else:
        state.pop("dependency_reset_token", None)
    dependencies = state.get("dependencies")
    items = dependencies.get("items") if isinstance(dependencies, dict) else None
    if isinstance(items, dict):
        zone_items = [key for key, value in items.items() if isinstance(value, dict) and "zone_fit" in value]
        for key in zone_items:
            value = items.pop(key)
            if source_item_id is not None and key == zone_items[0]:
                items[source_item_id] = value
    return state


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

    def _load(thermostat: Any, payload: dict[str, Any]) -> None:
        """Load a saved state into a running thermostat as SmartPI does when it starts (0.4.0,
        for QB2a, has no reset token and no dependencies: the state is loaded as it is)."""
        handler = thermostat._algo_handler  # noqa: SLF001 — a test helper reaches in
        algo = thermostat.prop_algorithm
        items = list(getattr(handler, "_dependency_items", ()))
        source = next((item.item_id for item in items if item.is_source), None)
        algo.load_state(prepared_state(payload, getattr(algo, "applied_reset_token", None), source))
        forget = getattr(algo, "forget_dependencies_except", None)
        if forget is not None:
            forget(item.item_id for item in items)

    async def seed_state(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        path = FIXTURES / f"{call.data['fixture']}.json"
        if not path.is_file():
            raise ServiceValidationError(f"no fixture {path.name}")
        payload = await hass.async_add_executor_job(path.read_text, "utf-8")
        _load(thermostat, json.loads(payload))
        await thermostat._algo_handler._async_save()  # noqa: SLF001
        _LOGGER.warning(
            "smartpi_qas_helpers: SmartPI state of %s replaced by the fixture %s",
            thermostat.entity_id,
            path.name,
        )

    kept_store: Store = Store(hass, 1, KEPT_STORE_KEY)

    async def keep_state(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        kept = await kept_store.async_load() or {}
        state = json.loads(json.dumps(thermostat.prop_algorithm.save_state()))
        kept.setdefault(thermostat.entity_id, {})[call.data["name"]] = state
        await kept_store.async_save(kept)
        _LOGGER.warning("smartpi_qas_helpers: SmartPI state of %s kept as %s", thermostat.entity_id, call.data["name"])

    async def restore_state(call: ServiceCall) -> None:
        thermostat = _thermostat(hass, call.data["entity_id"])
        kept = await kept_store.async_load() or {}
        state = (kept.get(thermostat.entity_id) or {}).get(call.data["name"])
        if state is None:
            raise ServiceValidationError(f"no state of {thermostat.entity_id} kept as {call.data['name']}")
        _load(thermostat, state)
        await thermostat._algo_handler._async_save()  # noqa: SLF001
        _LOGGER.warning("smartpi_qas_helpers: SmartPI state of %s put back from %s", thermostat.entity_id, call.data["name"])

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
    setups: dict[str, int] = {}
    loaded: set[str] = set()

    @callback
    def _entry_changed(change: ConfigEntryChange, config_entry: Any) -> None:
        if config_entry.domain != SMARTPI or change is not ConfigEntryChange.UPDATED:
            return
        if config_entry.state is ConfigEntryState.LOADED:
            if config_entry.entry_id not in loaded:
                loaded.add(config_entry.entry_id)
                setups[config_entry.entry_id] = setups.get(config_entry.entry_id, 0) + 1
        else:
            loaded.discard(config_entry.entry_id)

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed)

    async def entry_setups(call: ServiceCall) -> ServiceResponse:
        return {"count": setups.get(call.data["entry_id"], 0)}

    hass.services.async_register(
        DOMAIN,
        "entry_setups",
        entry_setups,
        vol.Schema({vol.Required("entry_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "seed_state",
        seed_state,
        vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("fixture"): cv.slug}),
    )
    kept_schema = vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Optional("name", default="kept"): cv.slug})
    hass.services.async_register(DOMAIN, "keep_state", keep_state, kept_schema)
    hass.services.async_register(DOMAIN, "restore_state", restore_state, kept_schema)
    return True
