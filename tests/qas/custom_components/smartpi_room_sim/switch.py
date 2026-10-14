"""Heaters on/off, and the boiler: water emitters heat only while the boiler is on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    spec = hub.sim.spec
    entities: list[SwitchEntity] = [Heater(hub, h.name) for h in spec.heaters if h.kind == "switch"]
    if spec.boiler is not None:
        entities.append(Boiler(hub))
    add(entities)


class Heater(SimEntity, SwitchEntity):
    """A heater's relay. A device that stops responding keeps its state (``ignore``) or also
    goes unavailable (``unavailable``)."""

    def __init__(self, hub: SimHub, name: str) -> None:
        super().__init__(hub, "switch", name, f"Sim {name}")
        self._key = name
        self._on: bool | None = None
        self._up = True

    @property
    def is_on(self) -> bool | None:
        return self._on

    @property
    def available(self) -> bool:
        return self._up

    def refresh(self) -> bool:
        sim = self.hub.sim
        on = sim.applied[self._key] > 0.0
        up = sim.modes[self._key] != "unavailable"
        if (on, up) == (self._on, self._up):
            return False
        self._on, self._up = on, up
        return True

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self.hub.changed()
        self.hub.sim.set_command(self._key, 1.0)
        self.hub.changed()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self.hub.changed()
        self.hub.sim.set_command(self._key, 0.0)
        self.hub.changed()


class Boiler(SimEntity, SwitchEntity):
    def __init__(self, hub: SimHub) -> None:
        super().__init__(hub, "switch", "boiler", "Sim boiler")
        self._on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        return self._on

    def refresh(self) -> bool:
        on = self.hub.sim.boiler_on
        if on == self._on:
            return False
        self._on = on
        return True

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self.hub.changed()
        self.hub.sim.set_boiler(True)
        self.hub.changed()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self.hub.changed()
        self.hub.sim.set_boiler(False)
        self.hub.changed()
