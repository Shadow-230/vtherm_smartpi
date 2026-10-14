"""The base of the simulator's entities: fixed entity ids, refreshed on each tick."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import DOMAIN, SIGNAL_UPDATE, SimHub


class SimEntity(Entity):
    """An entity of the simulator; ``refresh`` returns whether its state changed."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, hub: SimHub, platform: str, key: str, name: str) -> None:
        self.hub = hub
        self.entity_id = f"{platform}.{DOMAIN}_{key}"
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name

    async def async_added_to_hass(self) -> None:
        self.refresh()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        if self.refresh():
            self.async_write_ha_state()

    def refresh(self) -> bool:
        """Read the simulation; True when the entity's state or availability changed."""
        raise NotImplementedError
