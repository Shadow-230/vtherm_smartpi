"""The water radiators' valves, 0 to 100 %, as VTherm's over_valve drives them."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, Hub, SimEntity


async def async_setup_platform(
    hass: HomeAssistant, _config: ConfigType, add: AddEntitiesCallback, _info: DiscoveryInfoType | None = None
) -> None:
    hub: Hub = hass.data[DOMAIN]
    add(Valve(hub, room) for room, kind in hub.heaters.items() if kind == "radiator")


class Valve(SimEntity, NumberEntity):
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    def __init__(self, hub: Hub, room: str) -> None:
        super().__init__(hub, "number", f"{room}_valve", f"Test {room} valve")
        self._room = room
        self._attr_native_value = None

    def refresh(self) -> bool:
        value = round(self.hub.sim.rooms[self._room].command * 100.0, 1)
        if value == self._attr_native_value:
            return False
        self._attr_native_value = value
        return True

    async def async_set_native_value(self, value: float) -> None:
        self.hub.update()
        self.hub.sim.set_command(self._room, value / 100.0)
        self.hub.update()
