"""SmartPI test rooms: simulated rooms for the QAS test instances only, never part of a release.

Set up from YAML::

    smartpi_test_rooms:
      outdoor: 3.0
      rooms:
        west: {heater: radiator, temperature: 19.5, loss: 0.001, gain: 0.04, delay_min: 3, lag_min: 10}

Entities, all ``*.smartpi_test_rooms_*``: each room's temperature sensor and its heater (a valve,
``number``, for a water radiator; a ``switch`` for an electric heater), the outdoor temperature
sensor and, with a radiator, the boiler's switch that Versatile Thermostat's central boiler
drives. The physics is ``model.py``; it runs in real time, one tick every 7 s.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .model import STEP_S, RoomModel, Simulation

DOMAIN = "smartpi_test_rooms"
UPDATED = f"{DOMAIN}_updated"

_ROOM = vol.Schema(
    {
        vol.Required("heater"): vol.In(["radiator", "electric"]),
        vol.Optional("temperature", default=19.5): vol.Coerce(float),
        vol.Optional("loss", default=0.001): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("gain", default=0.04): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("delay_min", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("lag_min", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
    }
)
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Optional("outdoor", default=3.0): vol.Coerce(float), vol.Required("rooms"): {cv.slug: _ROOM}})},
    extra=vol.ALLOW_EXTRA,
)


class Hub:
    """The simulation on Home Assistant's clock."""

    def __init__(self, hass: HomeAssistant, sim: Simulation, heaters: dict[str, str]) -> None:
        self.hass = hass
        self.sim = sim
        self.heaters = heaters  # room -> "radiator" or "electric"
        self._start = time.monotonic()

    @callback
    def update(self, _now: datetime | None = None) -> None:
        """Catch up to now and refresh the entities (each tick, and before any input changes)."""
        self.sim.advance(time.monotonic() - self._start)
        async_dispatcher_send(self.hass, UPDATED)


class SimEntity(Entity):
    """An entity of the test rooms, refreshed after each update."""

    _attr_should_poll = False

    def __init__(self, hub: Hub, platform: str, key: str, name: str) -> None:
        self.hub = hub
        self.entity_id = f"{platform}.{DOMAIN}_{key}"
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name

    async def async_added_to_hass(self) -> None:
        self.refresh()
        self.async_on_remove(async_dispatcher_connect(self.hass, UPDATED, self._updated))

    @callback
    def _updated(self) -> None:
        if self.refresh():
            self.async_write_ha_state()

    def refresh(self) -> bool:
        """Read the simulation; True when the state changed."""
        raise NotImplementedError


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    rooms = {
        key: RoomModel(
            temperature=values["temperature"],
            loss=values["loss"],
            gain=values["gain"],
            delay_s=values["delay_min"] * 60.0,
            lag_min=values["lag_min"],
            water=values["heater"] == "radiator",
        )
        for key, values in conf["rooms"].items()
    }
    hub = Hub(hass, Simulation(rooms, conf["outdoor"]), {key: values["heater"] for key, values in conf["rooms"].items()})
    hass.data[DOMAIN] = hub
    for platform in ("sensor", "switch", "number"):
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))
    stop = async_track_time_interval(hass, hub.update, timedelta(seconds=STEP_S))

    @callback
    def _stop(_event: Event) -> None:
        stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
    return True
