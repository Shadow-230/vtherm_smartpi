"""Doors and windows: open by their daily schedule, or as a scenario sets them. With a boiler, its
signals: it can heat the circuits, domestic hot water has priority, it is in a fault."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
    entities: list[BinarySensorEntity] = [
        Opening(hub, opening.name, opening.device_class) for opening in hub.sim.spec.openings
    ]
    if hub.sim.spec.boiler is not None:
        entities += [
            BoilerSignal(hub, "boiler_heat_available", lambda sim: sim.heat_available()),
            BoilerSignal(hub, "boiler_dhw", lambda sim: sim.dhw_active()),
            BoilerSignal(hub, "boiler_fault", lambda sim: sim.boiler_state == "fault", BinarySensorDeviceClass.PROBLEM),
        ]
    add(entities)


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


class BoilerSignal(SimEntity, BinarySensorEntity):
    """One of the boiler's signals, read from the simulation."""

    def __init__(
        self,
        hub: SimHub,
        key: str,
        read: Callable[[Any], bool],
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        super().__init__(hub, "binary_sensor", key, f"Sim {key.replace('_', ' ')}")
        self._read = read
        self._attr_device_class = device_class
        self._on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        return self._on

    def refresh(self) -> bool:
        on = bool(self._read(self.hub.sim))
        if on == self._on:
            return False
        self._on = on
        return True
