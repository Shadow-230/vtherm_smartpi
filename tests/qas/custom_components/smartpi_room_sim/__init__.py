"""SmartPI room simulator for the QAS test instances only — never part of a release.

Rooms, heaters, valves, a boiler, doors, windows and the outdoors as Home Assistant entities, for
Versatile Thermostat and SmartPI to regulate (tests/qas). The physics is ``physics.py``, the same
file the in-process simulator uses; ``engine.py`` drives it in real time. Set up from YAML
(``smartpi_room_sim:``); every entity id starts with ``smartpi_room_sim_``.

Services (``services.yaml``): set or ramp a room's temperature, move the outdoor mean, open or
close a door or a window (or give it back to its schedule), make a device stop responding, and
reset everything to the configured state.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .engine import DEVICE_MODES, ELECTRIC, Simulation, spec_from_config

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartpi_room_sim"
SIGNAL_UPDATE = f"{DOMAIN}_update"
PLATFORMS = ("sensor", "binary_sensor", "switch", "number")

_ROOM = vol.Schema(
    {
        vol.Optional("temperature", default=19.0): vol.Coerce(float),
        vol.Optional("b", default=0.001): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("capacity", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
        vol.Optional("resolution", default=0.1): vol.In([0.1, 0.01]),
    }
)
_HEATER = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Optional("kind", default="switch"): vol.In(["switch", "valve"]),
        vol.Optional("a", default=0.03): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("delay_min", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("lag_min", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("stages", default=1): vol.In([1, 2]),
        vol.Optional("emitter", default=ELECTRIC): vol.In([ELECTRIC, "radiator", "floor_water"]),
        vol.Optional("power_w", default=1000.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
    }
)
_LINK = vol.Schema(
    {
        vol.Required("rooms"): vol.All([cv.string], vol.Length(min=2, max=2)),
        vol.Required("g"): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("door"): cv.string,
    }
)
_OPENING = vol.Schema(
    {
        vol.Optional("device_class", default="door"): vol.In(["door", "window"]),
        vol.Optional("room"): cv.string,
        vol.Optional("g", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional("schedule", default=[]): [
            vol.All([vol.All(vol.Coerce(float), vol.Range(min=0.0, max=24.0))], vol.Length(min=2, max=2))
        ],
    }
)
_BOILER = vol.Schema(
    {
        vol.Optional("fire_c", default=60.0): vol.Coerce(float),
        vol.Optional("idle_c", default=12.0): vol.Coerce(float),
        vol.Optional("tau_up_min", default=4.0): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
        vol.Optional("tau_down_min", default=20.0): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
    }
)
_OUTDOOR = vol.Schema(
    {
        vol.Optional("mean", default=3.0): vol.Coerce(float),
        vol.Optional("swing", default=3.0): vol.Coerce(float),
        vol.Optional("peak_hour", default=15.0): vol.Coerce(float),
        vol.Optional("wiggle", default=0.06): vol.Coerce(float),
        vol.Optional("resolution", default=0.1): vol.In([0.1, 0.01]),
    }
)
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("rooms"): {cv.slug: vol.Any(None, _ROOM)},
                vol.Optional("heaters", default={}): {cv.slug: _HEATER},
                vol.Optional("links", default=[]): [_LINK],
                vol.Optional("openings", default={}): {cv.slug: _OPENING},
                vol.Optional("boiler"): vol.Any(None, _BOILER),
                vol.Optional("outdoor", default={}): _OUTDOOR,
                vol.Optional("base_power_w", default=250.0): vol.Coerce(float),
                vol.Optional("step_s", default=7.0): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=59.0)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


class SimHub:
    """The simulation and its clock in Home Assistant."""

    def __init__(self, hass: HomeAssistant, sim: Simulation) -> None:
        self.hass = hass
        self.sim = sim
        self._start = time.monotonic()

    def now_s(self) -> float:
        return time.monotonic() - self._start

    @callback
    def tick(self, _now: datetime | None = None) -> None:
        self.sim.advance(self.now_s())
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def changed(self) -> None:
        """An input changed between ticks: the simulation catches up first."""
        self.sim.advance(self.now_s())
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)


def _check(names: list[str], name: str, what: str) -> str:
    if name not in names:
        raise ServiceValidationError(f"no {what} {name!r}; known: {', '.join(names)}")
    return name


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    spec = spec_from_config(config[DOMAIN])
    now = dt_util.now()
    start_hour = now.hour + now.minute / 60.0 + now.second / 3600.0
    hub = SimHub(hass, Simulation(spec, start_hour=start_hour))
    hass.data[DOMAIN] = hub

    rooms = [r.name for r in spec.rooms]
    heaters = [h.name for h in spec.heaters]
    openings = [o.name for o in spec.openings]

    async def set_room(call: ServiceCall) -> None:
        hub.changed()
        hub.sim.set_room(_check(rooms, call.data["room"], "room"), call.data["temperature"])
        hub.changed()

    async def ramp_room(call: ServiceCall) -> None:
        hub.changed()
        hub.sim.ramp_room(
            _check(rooms, call.data["room"], "room"), call.data["temperature"], call.data["minutes"]
        )

    async def set_outdoor(call: ServiceCall) -> None:
        hub.changed()
        hub.sim.set_outdoor(call.data["mean"], call.data.get("swing"), call.data.get("minutes", 0.0))
        hub.changed()

    async def set_opening(call: ServiceCall) -> None:
        hub.changed()
        state = call.data["state"]
        schedule = call.data.get("schedule")
        hub.sim.set_opening(
            _check(openings, call.data["opening"], "opening"),
            None if state == "schedule" else state == "open",
            None if schedule is None else tuple(tuple(interval) for interval in schedule),
        )
        hub.changed()

    async def set_device(call: ServiceCall) -> None:
        hub.changed()
        hub.sim.set_mode(_check(heaters, call.data["heater"], "heater"), call.data["mode"])
        hub.changed()

    async def reset(call: ServiceCall) -> None:
        hub.changed()
        hub.sim.reset(rooms=call.data["rooms"])
        hub.changed()
        _LOGGER.info("smartpi_room_sim: reset to the configured state")

    hass.services.async_register(
        DOMAIN,
        "set_room",
        set_room,
        vol.Schema({vol.Required("room"): cv.string, vol.Required("temperature"): vol.Coerce(float)}),
    )
    hass.services.async_register(
        DOMAIN,
        "ramp_room",
        ramp_room,
        vol.Schema(
            {
                vol.Required("room"): cv.string,
                vol.Required("temperature"): vol.Coerce(float),
                vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_outdoor",
        set_outdoor,
        vol.Schema(
            {
                vol.Required("mean"): vol.Coerce(float),
                vol.Optional("swing"): vol.Coerce(float),
                vol.Optional("minutes", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_opening",
        set_opening,
        vol.Schema(
            {
                vol.Required("opening"): cv.string,
                vol.Required("state"): vol.In(["open", "closed", "schedule"]),
                vol.Optional("schedule"): [
                    vol.All(
                        [vol.All(vol.Coerce(float), vol.Range(min=0.0, max=24.0))],
                        vol.Length(min=2, max=2),
                    )
                ],
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_device",
        set_device,
        vol.Schema({vol.Required("heater"): cv.string, vol.Required("mode"): vol.In(DEVICE_MODES)}),
    )
    hass.services.async_register(
        DOMAIN, "reset", reset, vol.Schema({vol.Optional("rooms", default=True): cv.boolean})
    )

    for platform in PLATFORMS:
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))

    stop_ticking = async_track_time_interval(hass, hub.tick, timedelta(seconds=spec.step_s))

    @callback
    def _stop(_event: Event) -> None:
        stop_ticking()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
    _LOGGER.info(
        "smartpi_room_sim: %d rooms, %d heaters, %d openings, boiler %s, tick %.0f s",
        len(rooms),
        len(heaters),
        len(openings),
        "yes" if spec.boiler is not None else "no",
        spec.step_s,
    )
    return True


def hub_of(hass: HomeAssistant) -> SimHub:
    return hass.data[DOMAIN]


def attributes_of(sim: Simulation) -> dict[str, Any]:
    """The simulation's true parameters, for the scenarios' comparisons."""
    return sim.truth()
