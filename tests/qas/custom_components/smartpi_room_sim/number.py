"""Valves: an opening of 0 to 100 %, as Versatile Thermostat's over_valve drives it."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import SimHub, hub_of
from .entity import SimEntity


async def async_setup_platform(
    hass: HomeAssistant,
    _config: ConfigType,
    add: AddEntitiesCallback,
    _discovery: DiscoveryInfoType | None = None,
) -> None:
    hub = hub_of(hass)
    add(Valve(hub, h.name) for h in hub.sim.spec.heaters if h.kind == "valve")


class Valve(SimEntity, NumberEntity):
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    def __init__(self, hub: SimHub, name: str) -> None:
        super().__init__(hub, "number", name, f"Sim {name}")
        self._key = name
        self._value: float | None = None
        self._up = True

    @property
    def native_value(self) -> float | None:
        return self._value

    @property
    def available(self) -> bool:
        return self._up

    def refresh(self) -> bool:
        sim = self.hub.sim
        value = round(sim.applied[self._key] * 100.0, 1)
        up = sim.modes[self._key] != "unavailable"
        if (value, up) == (self._value, self._up):
            return False
        self._value, self._up = value, up
        return True

    async def async_set_native_value(self, value: float) -> None:
        self.hub.changed()
        self.hub.sim.set_command(self._key, value / 100.0)
        self.hub.changed()
