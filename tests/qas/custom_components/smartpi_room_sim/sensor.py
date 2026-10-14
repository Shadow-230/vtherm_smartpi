"""Sensors: room and outdoor temperatures (rounded, written only on change), the house's power,
the boiler's water, the supply water of each water emitter and the heat each heater has sent."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import SimHub, hub_of
from .engine import ELECTRIC, quantize
from .entity import SimEntity


async def async_setup_platform(
    hass: HomeAssistant,
    _config: ConfigType,
    add: AddEntitiesCallback,
    _discovery: DiscoveryInfoType | None = None,
) -> None:
    hub = hub_of(hass)
    spec = hub.sim.spec
    entities: list[SensorEntity] = [RoomTemperature(hub, room.name, room.resolution) for room in spec.rooms]
    entities.append(OutdoorTemperature(hub))
    entities.append(HousePower(hub))
    if spec.boiler is not None:
        entities.append(BoilerWater(hub))
        entities += [SupplyWater(hub, heater.name) for heater in spec.heaters if heater.emitter != ELECTRIC]
    entities += [HeaterHeat(hub, heater.name) for heater in spec.heaters]
    add(entities)


class _Value(SimEntity, SensorEntity):
    """A sensor whose state is a number, written when it changes."""

    def __init__(self, hub: SimHub, key: str, name: str) -> None:
        super().__init__(hub, "sensor", key, name)
        self._value: float | None = None

    @property
    def native_value(self) -> float | None:
        return self._value

    def read(self) -> float:
        raise NotImplementedError

    def refresh(self) -> bool:
        value = self.read()
        if value == self._value:
            return False
        self._value = value
        return True


class RoomTemperature(_Value):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: SimHub, room: str, resolution: float) -> None:
        super().__init__(hub, f"{room}_temperature", f"Sim {room} temperature")
        self._room = room
        self._resolution = resolution
        self._attr_suggested_display_precision = 2 if resolution < 0.1 else 1

    def read(self) -> float:
        return quantize(self.hub.sim.temperature(self._room), self._resolution)


class OutdoorTemperature(_Value):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, hub: SimHub) -> None:
        super().__init__(hub, "outdoor_temperature", "Sim outdoor temperature")

    def read(self) -> float:
        sim = self.hub.sim
        return quantize(sim.outdoor_c(sim.t_s), sim.spec.outdoor.resolution)


class HousePower(_Value):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: SimHub) -> None:
        super().__init__(hub, "house_power", "Sim house power")

    def read(self) -> float:
        return round(self.hub.sim.house_power_w())


class BoilerWater(_Value):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: SimHub) -> None:
        super().__init__(hub, "boiler_water", "Sim boiler water")

    def read(self) -> float:
        sim = self.hub.sim
        return quantize(sim.water_at(sim.t_s), 0.5)


class SupplyWater(_Value):
    """The water on a water emitter's supply pipe, as a 0.1 degC sensor shows it."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, hub: SimHub, heater: str) -> None:
        super().__init__(hub, f"{heater}_supply", f"Sim {heater} supply")

    def read(self) -> float:
        return quantize(self.hub.sim.supply_c(), 0.1)


class HeaterHeat(_Value):
    """The heat a heater has sent into its emitter since the start (degC of its room)."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "°C"

    def __init__(self, hub: SimHub, heater: str) -> None:
        super().__init__(hub, f"{heater}_heat", f"Sim {heater} heat")
        self._heater = heater

    def read(self) -> float:
        return round(self.hub.sim.heat[self._heater], 1)
