"""The QAS setup scripts against the real forms, in-process (local only).

Each role's setup stages (tests/qas/setup/stages.py) run against Home Assistant in the test, with
the role's simulator, Versatile Thermostat from the test path and SmartPI: every form must accept
the scripts' answers, and the role's entries and thresholds must come out as the role says. The
bench runs the same scripts through the REST API.

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
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from homeassistant.setup import async_setup_component

QAS = pathlib.Path(__file__).resolve().parent / "qas"
for path in (QAS, QAS / "setup"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from roles import HOUSE_POWER_INPUT, MAX_POWER_INPUT, ROLES, Role  # noqa: E402
from stages import SMARTPI, THRESHOLD_COUNT_UID, THRESHOLD_POWER_UID, VT, run_stages  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTPI_QAS_LOCAL") != "1",
    reason="local only: set SMARTPI_QAS_LOCAL=1 (Versatile Thermostat on the path)",
)


class _Transport:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _mgr(self, kind: str) -> Any:
        return self.hass.config_entries.flow if kind == "config" else self.hass.config_entries.options

    def _json(self, kind: str, result: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(result)
        result.pop("result", None)
        return _BaseFlowManagerView(self._mgr(kind))._prepare_result_json(result)

    async def start(self, kind: str, handler: str) -> dict[str, Any]:
        if kind == "config":
            result = await self._mgr(kind).async_init(
                handler, context={"source": "user", "show_advanced_options": True}
            )
        else:
            result = await self._mgr(kind).async_init(handler)
        await self.hass.async_block_till_done()
        return self._json(kind, result)

    async def submit(self, kind: str, flow_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        mgr = self._mgr(kind)
        try:
            result = await mgr.async_configure(flow_id, dict(data))
        except data_entry_flow.InvalidData as err:
            step = mgr.async_get(flow_id)["step_id"]
            return {"type": "form", "flow_id": flow_id, "step_id": step, "errors": err.schema_errors}
        await self.hass.async_block_till_done()
        return self._json(kind, result)

    async def abort(self, kind: str, flow_id: str) -> None:
        self._mgr(kind).async_abort(flow_id)


class _Api:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.transport = _Transport(hass)
        self.lines: list[str] = []

    async def entries(self) -> list[dict[str, Any]]:
        return [
            {"entry_id": e.entry_id, "domain": e.domain, "title": e.title}
            for e in self.hass.config_entries.async_entries()
        ]

    async def entity_registry(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": e.entity_id,
                "id": e.id,
                "unique_id": e.unique_id,
                "platform": e.platform,
                "device_id": e.device_id,
                "config_entry_id": e.config_entry_id,
            }
            for e in er.async_get(self.hass).entities.values()
        ]

    async def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        await self.hass.services.async_call(domain, service, dict(data), blocking=True)
        await self.hass.async_block_till_done()

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        state = self.hass.states.get(entity_id)
        return None if state is None else {"state": state.state, "attributes": dict(state.attributes)}

    async def settle(self, entity_ids: list[str], within_s: float) -> list[str]:
        await self.hass.async_block_till_done()
        return [e for e in entity_ids if self.hass.states.get(e) is None]

    def log(self, text: str) -> None:
        self.lines.append(text)


@pytest.fixture(autouse=True)
def _vt_release_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Versatile Thermostat's checkout on the test path says the version of its previous release
    in its manifest; the bench stamps the release's tag into it, as a release does (the bench's
    docs/lessons.md). SmartPI's version check is left to its own tests."""
    monkeypatch.setattr(
        "custom_components.vtherm_smartpi.async_check_vtherm_version", AsyncMock(return_value=True)
    )


async def _start(hass: HomeAssistant, role: Role) -> _Api:
    from config.build import configuration  # noqa: PLC0415

    config = configuration(role)
    assert await async_setup_component(hass, "smartpi_room_sim", {"smartpi_room_sim": config["smartpi_room_sim"]})
    if "input_number" in config:
        assert await async_setup_component(hass, "input_number", {"input_number": config["input_number"]})
    assert await async_setup_component(hass, VT, {})
    assert await async_setup_component(hass, SMARTPI, {})
    assert await async_setup_component(hass, "smartpi_qas_helpers", {"smartpi_qas_helpers": None})
    await hass.async_block_till_done()
    return _Api(hass)


# VTherm's warning when a thermostat it has just created starts: it has no saved target yet.
FIRST_START = "No previously saved temperature"


def _vt_number(hass: HomeAssistant, unique_id: str) -> float:
    entity_id = er.async_get(hass).async_get_entity_id("number", VT, unique_id)
    return float(hass.states.get(entity_id).state)


# Unloading every entry at the end of the test reloads VTherm's thermostats once more, after the
# SmartPI factory is gone: one of their control timers is left to the test's teardown.
@pytest.mark.parametrize("expected_lingering_timers", [True])
@pytest.mark.parametrize("name", sorted(ROLES))
async def test_role_sets_up_through_the_forms(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    """Every stage of the role's setup goes through the real forms without a warning or an error
    of the integrations logged, the thermostats run SmartPI with its diagnostics after SmartPI's entry is set up
    again, and a second run changes nothing."""
    caplog.set_level(logging.WARNING)
    role = ROLES[name]
    api = await _start(hass, role)
    await run_stages(api, role)
    # The setup ends with a restart (setup_role.py): SmartPI's defaults entry, set up again, then
    # shows the diagnostics of the thermostats created after it.
    for entry in hass.config_entries.async_entries(SMARTPI):
        assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    titles = {(e.domain, e.title) for e in hass.config_entries.async_entries()}
    assert {title for domain, title in titles if domain == SMARTPI} == set(role.smartpi_titles)
    registry = er.async_get(hass)
    for thermostat in role.thermostats:
        assert (VT, thermostat.name) in titles
        state = hass.states.get(thermostat.climate)
        assert state is not None, thermostat.climate
        vt_entry = hass.config_entries.async_get_entry(registry.async_get(thermostat.climate).config_entry_id)
        assert vt_entry.data.get("proportional_function") == "smartpi", thermostat.name
        assert bool(vt_entry.data.get("used_by_controls_central_boiler")) is thermostat.central_boiler
        diagnostic = registry.async_get_entity_id(
            "sensor", SMARTPI, f"smartpi_diag_{registry.async_get(thermostat.climate).unique_id}"
        )
        assert diagnostic is not None and hass.states.get(diagnostic) is not None, thermostat.name
    if role.central_boiler:
        assert _vt_number(hass, THRESHOLD_COUNT_UID) == role.boiler_count_threshold
        assert _vt_number(hass, THRESHOLD_POWER_UID) == role.boiler_power_threshold
    if role.power_manager:
        assert float(hass.states.get(HOUSE_POWER_INPUT).state) == role.house_power
        assert float(hass.states.get(MAX_POWER_INPUT).state) == role.max_power
    if role.central_boiler:
        # The power threshold's unit is the power manager's: kW in the role boiler-power.
        threshold = er.async_get(hass).async_get_entity_id("number", VT, THRESHOLD_POWER_UID)
        unit = hass.states.get(threshold).attributes.get("unit_of_measurement")
        assert unit == ("kW" if role.power_unit == "kw" else "W"), unit

    problems = [
        f"{r.levelname} {r.name}: {r.getMessage()[:200]}"
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.name.startswith("custom_components.")
        and FIRST_START not in r.getMessage()
    ]
    assert not problems, problems

    before = len(api.lines)
    await run_stages(api, role)
    assert all(line.startswith(("number.", "input_number.")) for line in api.lines[before:]), api.lines[before:]


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_helper_loads_a_learned_state(hass: HomeAssistant) -> None:
    """The helper's ``seed_state`` gives a set-up thermostat its room's learned model, saved by
    SmartPI itself."""
    from build_fixtures import rooms  # noqa: PLC0415

    role = ROLES["boiler-count"]
    api = await _start(hass, role)
    await run_stages(api, role)
    thermostat = role.thermostat("Lounge")
    entity = hass.data["climate"].get_entity(thermostat.climate)
    saved = AsyncMock()
    entity._algo_handler._async_save = saved  # noqa: SLF001

    await hass.services.async_call(
        "smartpi_qas_helpers",
        "seed_state",
        {"entity_id": thermostat.climate, "fixture": f"learned_{thermostat.room}"},
        blocking=True,
    )

    model = rooms()[thermostat.room]
    assert entity.prop_algorithm.est.a == pytest.approx(model["a"])
    assert entity.prop_algorithm.est.b == pytest.approx(model["b"])
    saved.assert_awaited_once()
