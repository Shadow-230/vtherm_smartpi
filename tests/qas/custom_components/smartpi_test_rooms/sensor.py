"""The rooms' and the outdoor temperature sensors, 0.1 degC, written only when they change."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, Hub, SimEntity
from .model import reading


async def async_setup_platform(
    hass: HomeAssistant, _config: ConfigType, add: AddEntitiesCallback, _info: DiscoveryInfoType | None = None
) -> None:
    hub: Hub = hass.data[DOMAIN]
    add([Temperature(hub, room) for room in hub.sim.rooms] + [Temperature(hub, None)])


class Temperature(SimEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, hub: Hub, room: str | None) -> None:
        key = f"{room}_temperature" if room else "outdoor_temperature"
        super().__init__(hub, "sensor", key, f"Test {room or 'outdoor'} temperature")
        self._room = room
        self._attr_native_value = None

    def refresh(self) -> bool:
        sim = self.hub.sim
        value = reading(sim.rooms[self._room].temperature if self._room else sim.outdoor_c(sim.t_s))
        if value == self._attr_native_value:
            return False
        self._attr_native_value = value
        return True
