"""Doors and windows: open by their daily schedule, or as a scenario sets them."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
    add(Opening(hub, opening.name, opening.device_class) for opening in hub.sim.spec.openings)


class Opening(SimEntity, BinarySensorEntity):
    def __init__(self, hub: SimHub, name: str, device_class: str) -> None:
        super().__init__(hub, "binary_sensor", name, f"Sim {name}")
        self._key = name
        self._attr_device_class = BinarySensorDeviceClass(device_class)
        self._open: bool | None = None

    @property
    def is_on(self) -> bool | None:
        return self._open

    def refresh(self) -> bool:
        is_open = self.hub.sim.is_open(self._key)
        if is_open == self._open:
            return False
        self._open = is_open
        return True
