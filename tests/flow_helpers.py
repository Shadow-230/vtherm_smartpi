"""Helpers for the options and config flows of SmartPI: a menu of sections, written at "Save and finish".

Each section of a window ends back in its menu; nothing is written until ``save`` is chosen.
"""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType


async def options(hass, entry):
    """Open an entry's options: its menu."""
    return await hass.config_entries.options.async_init(entry.entry_id)


async def configure(hass, result, user_input=None):
    """Answer the step the flow shows (options or config flow)."""
    manager = hass.config_entries.options if _is_options(hass, result) else hass.config_entries.flow
    return await manager.async_configure(result["flow_id"], user_input)


async def choose(hass, result, step: str):
    """Choose a menu item."""
    assert result["type"] == FlowResultType.MENU, result
    assert step in result["menu_options"], (step, result["menu_options"])
    return await configure(hass, result, {"next_step_id": step})


async def save(hass, result, reset: bool | None = None):
    """Back in the menu: "Save and finish". ``reset``: the answer to the reset question, which
    must then be asked (None: it must not be asked)."""
    result = await choose(hass, result, "save")
    if reset is None:
        assert not (result["type"] == FlowResultType.FORM and result["step_id"] == "model_reset"), result
        return result
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "model_reset", result
    return await configure(hass, result, {"reset_model": reset})


def fields(result) -> list[str]:
    return [getattr(key, "schema", key) for key in result["data_schema"].schema]


def default(result, name):
    for key in result["data_schema"].schema:
        if getattr(key, "schema", key) == name:
            return key.default()
    raise KeyError(name)


def _is_options(hass, result) -> bool:
    flow_id = result["flow_id"]
    return any(flow["flow_id"] == flow_id for flow in hass.config_entries.options.async_progress())
