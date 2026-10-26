"""The QAS pack on Home Assistant in-process, with Versatile Thermostat and SmartPI (local only).

- Each role is prepared through the real forms (tests/qas/prepare.py), as the bench does it over
  the API: no warning or error of the integrations, no SmartPI repair, the thresholds set.
- The helper's ``load_learned_state`` gives a thermostat its learned model at once.
- The role boiler-count runs BP1, BP2 and BP8 end to end, the clock moved on by the test.

Local only (``SMARTPI_QAS_LOCAL=1``): CI uses Versatile Thermostat's main branch, whose forms may
change before its next release, while the bench uses a release.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

QAS = pathlib.Path(__file__).resolve().parent / "qas"
if str(QAS) not in sys.path:
    sys.path.insert(0, str(QAS))

from configuration import configuration  # noqa: E402
from pack import HELPER_DOMAIN, ROLES, ROOMS, ROOMS_DOMAIN, SMARTPI, VT, Role, room  # noqa: E402
from prepare import COUNT_THRESHOLD_UID, POWER_THRESHOLD_UID, set_up  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTPI_QAS_LOCAL") != "1",
    reason="local only: set SMARTPI_QAS_LOCAL=1 (Versatile Thermostat on the path)",
)

# VTherm's warning when a thermostat it has just created starts: it has no saved target yet.
FIRST_START = "No previously saved temperature"
WEST, EAST, NORTH = (r.climate for r in ROOMS)


class _Instance:
    """The methods prepare.py uses, over Home Assistant in the test."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.flows = hass.config_entries.flow

    def _json(self, result: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(result)
        result.pop("result", None)
        return _BaseFlowManagerView(self.flows)._prepare_result_json(result)

    async def flow_start(self, handler: str) -> dict[str, Any]:
        result = await self.flows.async_init(handler, context={"source": "user", "show_advanced_options": True})
        await self.hass.async_block_till_done()
        return self._json(result)

    async def flow_submit(self, flow_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        try:
            result = await self.flows.async_configure(flow_id, dict(data))
        except data_entry_flow.InvalidData as err:
            return {"type": "form", "flow_id": flow_id, "errors": err.schema_errors}
        await self.hass.async_block_till_done()
        return self._json(result)

    async def flow_abort(self, flow_id: str) -> None:
        self.flows.async_abort(flow_id)

    async def entries(self, domain: str) -> list[dict[str, Any]]:
        return [{"entry_id": e.entry_id, "title": e.title} for e in self.hass.config_entries.async_entries(domain)]

    async def entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(domain, platform, unique_id)

    async def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        await self.hass.services.async_call(domain, service, dict(data), blocking=True)
        await self.hass.async_block_till_done()

    async def wait_state(self, entity_id: str, within_s: float) -> bool:
        await self.hass.async_block_till_done()
        return self.hass.states.get(entity_id) is not None


@pytest.fixture(autouse=True)
def _vt_release_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Versatile Thermostat's checkout on the test path says the version of its previous release
    in its manifest; the bench writes the release's tag into it, as a release does. SmartPI's
    version check is left to its own tests."""
    monkeypatch.setattr("custom_components.vtherm_smartpi.async_check_vtherm_version", AsyncMock(return_value=True))


async def _prepare(hass: HomeAssistant, role: Role) -> list[str]:
    """The role's configuration and its preparation; SmartPI's entries then set up again, as after
    the preparation's restart. The lines it said."""
    config = configuration(role)
    assert await async_setup_component(hass, ROOMS_DOMAIN, {ROOMS_DOMAIN: config[ROOMS_DOMAIN]})
    assert await async_setup_component(hass, VT, {})
    assert await async_setup_component(hass, SMARTPI, {})
    assert await async_setup_component(hass, HELPER_DOMAIN, {HELPER_DOMAIN: None})
    await hass.async_block_till_done()
    said: list[str] = []
    await set_up(_Instance(hass), role, said.append)
    for entry in hass.config_entries.async_entries(SMARTPI):
        assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return said


def _live(hass: HomeAssistant, climate: str) -> dict[str, Any]:
    registry = er.async_get(hass)
    uid = registry.async_get(climate).unique_id
    entity_id = registry.async_get_entity_id("sensor", SMARTPI, f"smartpi_diag_{uid}")
    state = hass.states.get(entity_id) if entity_id else None
    return (state.attributes.get("live") or {}) if state else {}


# Unloading every entry at the end of the test reloads VTherm's thermostats once more, after the
# SmartPI factory is gone: one of their control timers is left to the test's teardown.
@pytest.mark.parametrize("expected_lingering_timers", [True])
@pytest.mark.parametrize("name", sorted(ROLES))
async def test_each_role_is_prepared_through_the_forms(hass: HomeAssistant, caplog: pytest.LogCaptureFixture, name: str) -> None:
    """Every form takes the answers, no integration logs a warning or an error, SmartPI reports no
    repair, each thermostat runs SmartPI with its diagnostics, the role's thresholds are set, and
    preparing again changes nothing."""
    caplog.set_level(logging.WARNING)
    role = ROLES[name]
    await _prepare(hass, role)

    registry = er.async_get(hass)
    assert [e.title for e in hass.config_entries.async_entries(SMARTPI)] == ["SmartPI defaults"]
    for r in ROOMS:
        vt_entry = hass.config_entries.async_get_entry(registry.async_get(r.climate).config_entry_id)
        assert vt_entry.data["proportional_function"] == "smartpi", r.name
        assert vt_entry.data["used_by_controls_central_boiler"] is r.counted, r.name
        assert _live(hass, r.climate).get("control", {}).get("phase"), r.name
    for uid, value in ((COUNT_THRESHOLD_UID, role.count_threshold), (POWER_THRESHOLD_UID, role.power_threshold_w)):
        assert float(hass.states.get(registry.async_get_entity_id("number", VT, uid)).state) == value
    problems = [
        f"{r.levelname} {r.name}: {r.getMessage()[:200]}"
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("custom_components.") and FIRST_START not in r.getMessage()
    ]
    assert not problems, problems
    repairs = [key for key in ir.async_get(hass).issues if key[0] == SMARTPI]
    assert not repairs, repairs

    said: list[str] = []
    await set_up(_Instance(hass), role, said.append)
    assert all(line.startswith("number.") for line in said), said


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_helper_loads_the_learned_state(hass: HomeAssistant) -> None:
    """``load_learned_state`` gives West its room's model, saved by SmartPI and shown at once."""
    from learned_state import model  # noqa: PLC0415

    await _prepare(hass, ROLES["boiler-count"])
    await hass.services.async_call(HELPER_DOMAIN, "load_learned_state", {"entity_id": WEST, "state": "west"}, blocking=True)
    await hass.async_block_till_done()

    live = _live(hass, WEST)
    assert live["control"]["phase"] == "Stable"
    assert live["model"]["a"] == pytest.approx(model()["a"])
    assert live["model"]["deadtime_heat_s"] == pytest.approx(model()["deadtime_s"])


async def _run_for(hass: HomeAssistant, freezer: Any, seconds: float, step_s: float = 7.0) -> None:
    elapsed = 0.0
    while elapsed < seconds:
        freezer.tick(step_s)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        elapsed += step_s


async def _targets(hass: HomeAssistant, **values: float) -> None:
    for climate, value in values.items():
        await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "heat"}, blocking=True)
        await hass.services.async_call("climate", "set_temperature", {"entity_id": climate, "temperature": value}, blocking=True)
    await hass.async_block_till_done()


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_boiler_pause_end_to_end(hass: HomeAssistant, freezer: Any) -> None:
    """As BP8, BP1 and BP2 run on the bench: West and East starting together never pause; West
    calling alone pauses with VTherm's boiler off while North (not counted) does not; East calling
    too resumes West."""
    await _prepare(hass, ROLES["boiler-count"])
    boiler = er.async_get(hass).async_get_entity_id("binary_sensor", VT, "central_boiler_state")

    def paused(climate: str) -> Any:
        return _live(hass, climate).get("learning", {}).get("boiler_paused")

    await _targets(hass, **{WEST: 7.0, EAST: 7.0, NORTH: 23.0})
    await _run_for(hass, freezer, 15 * 60)
    assert hass.states.get(boiler).state == "off"
    await _targets(hass, **{WEST: 23.0, EAST: 23.0})
    seen = []
    for _ in range(60):
        await _run_for(hass, freezer, 7.0)
        seen += [c for c in (WEST, EAST) if paused(c)]
    assert not seen, seen
    assert hass.states.get(boiler).state == "on"

    await _targets(hass, **{EAST: 7.0})
    await _run_for(hass, freezer, 6 * 60)
    assert paused(WEST) is True
    assert _live(hass, WEST)["power"]["current_cycle_percent"] > 0
    assert hass.states.get(boiler).state == "off"
    assert paused(EAST) is False and paused(NORTH) is False

    await _targets(hass, **{EAST: 23.0})
    await _run_for(hass, freezer, 6 * 60)
    assert paused(WEST) is False
    assert hass.states.get(boiler).state == "on"
    assert room("West").counted and not room("North").counted
