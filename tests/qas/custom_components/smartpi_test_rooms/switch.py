"""The electric heaters' switches, and the boiler's switch that VTherm's central boiler drives."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, Hub, SimEntity


async def async_setup_platform(
    hass: HomeAssistant, _config: ConfigType, add: AddEntitiesCallback, _info: DiscoveryInfoType | None = None
) -> None:
    hub: Hub = hass.data[DOMAIN]
    entities: list[SwitchEntity] = [Heater(hub, room) for room, kind in hub.heaters.items() if kind == "electric"]
    if hub.sim.has_radiators:
        entities.append(Boiler(hub))
    add(entities)


class Heater(SimEntity, SwitchEntity):
    def __init__(self, hub: Hub, room: str) -> None:
        super().__init__(hub, "switch", f"{room}_heater", f"Test {room} heater")
        self._room = room
        self._attr_is_on = None

    def refresh(self) -> bool:
        on = self.hub.sim.rooms[self._room].command > 0.0
        if on == self._attr_is_on:
            return False
        self._attr_is_on = on
        return True

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self.hub.update()
        self.hub.sim.set_command(self._room, 1.0)
        self.hub.update()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self.hub.update()
        self.hub.sim.set_command(self._room, 0.0)
        self.hub.update()


class Boiler(SimEntity, SwitchEntity):
    def __init__(self, hub: Hub) -> None:
        super().__init__(hub, "switch", "boiler", "Test boiler")
        self._attr_is_on = None

    def refresh(self) -> bool:
        on = self.hub.sim.boiler_on
        if on == self._attr_is_on:
            return False
        self._attr_is_on = on
        return True

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self.hub.update()
        self.hub.sim.set_boiler(True)
        self.hub.update()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self.hub.update()
        self.hub.sim.set_boiler(False)
        self.hub.update()
