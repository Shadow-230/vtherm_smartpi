"""The water radiators' valves, 0 to 100 %, as VTherm's over_valve drives them. A valve takes and
reports a new opening at once, or after the hub's ``valve_report_delay_s``."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
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
        self._pending: CALLBACK_TYPE | None = None

    def refresh(self) -> bool:
        value = round(self.hub.sim.rooms[self._room].command * 100.0, 1)
        if value == self._attr_native_value:
            return False
        self._attr_native_value = value
        return True

    async def async_set_native_value(self, value: float) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None
        delay_s = self.hub.valve_report_delay_s
        if delay_s <= 0.0:
            self._apply(value)
            return

        @callback
        def later(_now) -> None:
            self._pending = None
            self._apply(value)

        self._pending = async_call_later(self.hass, timedelta(seconds=delay_s), later)

    async def async_will_remove_from_hass(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    @callback
    def _apply(self, value: float) -> None:
        self.hub.update()
        self.hub.sim.set_command(self._room, value / 100.0)
        self.hub.update()
