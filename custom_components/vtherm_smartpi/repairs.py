"""Repairs for SmartPI: the model reset offered when water data appear or go for good."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, SERVICE_RESET_SMARTPI_LEARNING

CONF_RESET_MODEL = "reset_model"


class WaterModelResetFlow(RepairsFlow):
    """One step: reset the thermostat's learned model (checked by default)."""

    def __init__(self, data: dict[str, Any] | None) -> None:
        self._entity_id = (data or {}).get("entity_id")
        self._name = (data or {}).get("name") or self._entity_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if user_input.get(CONF_RESET_MODEL, True) and self._entity_id:
                await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_RESET_SMARTPI_LEARNING,
                    {"entity_id": self._entity_id},
                    blocking=True,
                )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(CONF_RESET_MODEL, default=True): bool}),
            description_placeholders={"entity_id": str(self._entity_id), "name": str(self._name)},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow of a SmartPI repair issue."""
    return WaterModelResetFlow(data)
