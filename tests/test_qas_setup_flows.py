"""The QAS setup scripts against the real forms, in-process (local only).

Each role's setup stages (tests/qas/setup/stages.py) run against Home Assistant in the test, with
the role's simulator, Versatile Thermostat from the test path (10.5.1 in the local environment,
the version the bench uses) and SmartPI: every form must accept the scripts' answers, and the
role's entries, areas and base options must come out as the role says. The bench runs the same
scripts through the REST API.

Local only (``SMARTPI_QAS_LOCAL=1``): CI uses Versatile Thermostat's master, whose forms may
change before its next release.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

import pytest
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from homeassistant.setup import async_setup_component

QAS = pathlib.Path(__file__).resolve().parent / "qas"
for path in (QAS, QAS / "setup"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from roles import MAX_POWER_INPUT, ROLES, Role  # noqa: E402
from stages import SMARTPI, VT, run_stages  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTPI_QAS_LOCAL") != "1",
    reason="local only: set SMARTPI_QAS_LOCAL=1 (Versatile Thermostat 10.5.1 on the path)",
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
                "area_id": e.area_id or self._device_area(e.device_id),
                "device_id": e.device_id,
                "config_entry_id": e.config_entry_id,
            }
            for e in er.async_get(self.hass).entities.values()
        ]

    async def areas(self) -> list[dict[str, Any]]:
        return [{"area_id": a.id, "name": a.name} for a in ar.async_get(self.hass).async_list_areas()]

    async def create_area(self, name: str) -> str:
        return ar.async_get(self.hass).async_create(name).id

    def _device_area(self, device_id: str | None) -> str | None:
        device = dr.async_get(self.hass).async_get(device_id) if device_id else None
        return device.area_id if device is not None else None

    async def set_area(self, entity_id: str, area_id: str, device_id: str | None) -> None:
        if device_id:
            dr.async_get(self.hass).async_update_device(device_id, area_id=area_id)
        else:
            er.async_get(self.hass).async_update_entity(entity_id, area_id=area_id)

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


async def _start(hass: HomeAssistant, role: Role) -> _Api:
    from config.build import configuration  # noqa: PLC0415

    config = configuration(role)
    assert await async_setup_component(hass, "smartpi_room_sim", {"smartpi_room_sim": config["smartpi_room_sim"]})
    if "input_number" in config:
        assert await async_setup_component(hass, "input_number", {"input_number": config["input_number"]})
    assert await async_setup_component(hass, VT, {})
    assert await async_setup_component(hass, SMARTPI, {})
    assert await async_setup_component(hass, "smartpi_qas_helpers", {"smartpi_qas_helpers": config["smartpi_qas_helpers"]})
    await hass.async_block_till_done()
    return _Api(hass)


def _stored(hass: HomeAssistant):
    async def stored(title: str) -> Mapping[str, Any]:
        entry = next(
            e for e in hass.config_entries.async_entries(SMARTPI) if e.title == title
        )
        return dict(entry.options)

    return stored


# Unloading every entry at the end of the test reloads VTherm's thermostats once more, after the
# SmartPI factory is gone: one of their control timers is left to the test's teardown.
@pytest.mark.parametrize("expected_lingering_timers", [True])
@pytest.mark.parametrize("name", sorted(ROLES))
async def test_role_sets_up_through_the_forms(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    """Every stage of the role's setup goes through the real forms without an error logged, and
    again changes nothing."""
    caplog.set_level(logging.WARNING)
    role = ROLES[name]
    if role.plugin != "qas":
        # The in-process plugin is this branch's: the 0.4.0 steps run on the bench only.
        role = dataclasses.replace(role, plugin="qas")
    api = await _start(hass, role)
    await run_stages(api, role, _stored(hass))

    titles = {(e.domain, e.title) for e in hass.config_entries.async_entries()}
    for thermostat in role.thermostats:
        assert (VT, thermostat.name) in titles
        assert hass.states.get(thermostat.climate) is not None, thermostat.climate
    for title in role.smartpi_titles:
        assert (SMARTPI, title) in titles
    for title, neighbours in role.neighbours.items():
        items = (await _stored(hass)(title)).get("smart_pi_dependencies") or []
        assert len([i for i in items if i.get("kind") == "neighbour"]) == len(neighbours)
    if role.zone_entry is not None:
        items = (await _stored(hass)(role.zone_entry)).get("smart_pi_dependencies") or []
        assert [i for i in items if i.get("kind") == "source"]
    if role.max_power_w is not None:
        assert float(hass.states.get(MAX_POWER_INPUT).state) == role.max_power_w
    for thermostat in role.heating_inputs:
        stored = await _stored(hass)(thermostat.name)
        assert stored.get("smart_pi_water_source") == thermostat.water_source, thermostat.name
        if thermostat.call_source is not None and (role.zone is None or role.zone.additional != thermostat.climate):
            assert stored.get("smart_pi_boiler_call_source") == thermostat.call_source, thermostat.name
        # The setup kept the model: these inputs' data starting ask nothing again.
        assert stored.get("smart_pi_model_kept_for_water"), thermostat.name
    if role.boiler_signals:
        defaults = await _stored(hass)(role.smartpi_titles[0])
        assert defaults.get("smart_pi_dhw_entity") == er.async_get(hass).async_get("binary_sensor.smartpi_room_sim_boiler_dhw").id

    errors = [
        f"{r.name}: {r.getMessage()[:200]}"
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ]
    assert not errors, errors

    before = len(api.lines)
    await run_stages(api, role, _stored(hass))
    assert api.lines[before:] == [
        line for line in api.lines[before:] if line.startswith(("area ", "number.", MAX_POWER_INPUT))
    ], api.lines[before:]


MASTER_WARNING = "no longer has exactly this thermostat"


@pytest.mark.parametrize("expected_lingering_timers", [True])
@pytest.mark.parametrize("name", ["zone", "zone-ref", "zone-valve"])
async def test_a_zone_reloaded_gives_no_false_master_warning(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    """The zone's forms and a reload of each SmartPI entry rebuild the thermostats as VTherm does
    (the algorithm before the entity id): the zone's master, with exactly its two devices, is not
    reported."""
    caplog.set_level(logging.WARNING)
    role = ROLES[name]
    api = await _start(hass, role)
    await run_stages(api, role, _stored(hass))
    for title in role.smartpi_titles:
        assert await hass.config_entries.async_reload(_entry_id(hass, SMARTPI, title))
        await hass.async_block_till_done()

    warnings = [r.getMessage() for r in caplog.records if MASTER_WARNING in r.getMessage()]
    assert not warnings, warnings


DUPLICATE_ID = "does not generate unique IDs"


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_thermostat_s_own_entry_takes_over_its_diagnostic_sensor(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The defaults entry shows the diagnostic sensor of each SmartPI thermostat without an entry of
    its own (here after it is reloaded, as after a restart). The thermostat's own entry then takes
    the sensor over: no duplicate unique ID is refused when that entry is created, reloaded, its
    options saved, or the zone form creates the other thermostat's entry, and each sensor stays
    live after the defaults entry is reloaded. Before, the defaults entry kept the sensor until it
    was reloaded or Home Assistant restarted (the same in 0.4.0)."""
    from flows import run_flow  # noqa: PLC0415
    from stages import settings_save_steps, smartpi_thermostat_steps, zone_steps  # noqa: PLC0415

    caplog.set_level(logging.ERROR)
    role = ROLES["zone-valve"]
    api = await _start(hass, role)
    await run_stages(api, role, _stored(hass), only="vtherm")
    defaults = _entry_id(hass, SMARTPI, role.smartpi_titles[0])
    registry = er.async_get(hass)
    radiator, floor = role.thermostats[0], role.thermostats[1]

    def owner(thermostat) -> str | None:
        unique_id = registry.async_get(thermostat.climate).unique_id
        entity_id = registry.async_get_entity_id("sensor", SMARTPI, f"smartpi_diag_{unique_id}")
        state = hass.states.get(entity_id) if entity_id else None
        if state is None or state.attributes.get("restored"):
            return None
        return registry.async_get(entity_id).config_entry_id

    assert await hass.config_entries.async_reload(defaults)
    await hass.async_block_till_done()
    assert owner(radiator) == owner(floor) == defaults

    await run_flow(api.transport, "config", SMARTPI, smartpi_thermostat_steps(role, radiator))
    main = _entry_id(hass, SMARTPI, radiator.name)
    assert owner(radiator) == main
    assert await hass.config_entries.async_reload(main)
    await hass.async_block_till_done()
    await run_flow(api.transport, "options", main, settings_save_steps(role))
    await run_flow(api.transport, "options", main, zone_steps(role.zone, role))
    assert owner(floor) == _entry_id(hass, SMARTPI, floor.name)
    assert await hass.config_entries.async_reload(defaults)
    await hass.async_block_till_done()

    duplicates = [r.getMessage() for r in caplog.records if DUPLICATE_ID in r.getMessage()]
    assert not duplicates, duplicates
    assert owner(radiator) == main
    assert owner(floor) == _entry_id(hass, SMARTPI, floor.name)


# --- the scenarios' own flows ------------------------------------------------------------------


def _entry_id(hass: HomeAssistant, domain: str, title: str) -> str:
    return next(e.entry_id for e in hass.config_entries.async_entries(domain) if e.title == title)


async def _refused(api: _Api, entry_id: str, steps: list) -> dict[str, Any]:
    from flows import walk  # noqa: PLC0415

    result = await walk(api.transport, "options", entry_id, steps, refused_ok=True)
    if result.get("type") in ("form", "menu"):
        await api.transport.abort("options", result["flow_id"])
    return result


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_neighbour_scenarios_flows(hass: HomeAssistant) -> None:
    """QD1's form paths: each refusal, the door suggestion, the warning, the reset question at save,
    a removal with nothing chosen back in the menu, a neighbour that is gone refused on its form,
    and the save stopped by zone_changed, on the real forms."""
    from flows import Form, Menu, form_defaults, run_flow, walk  # noqa: PLC0415
    from scenarios import qd  # noqa: PLC0415
    from stages import save_steps  # noqa: PLC0415

    role = ROLES["neighbours"]
    api = await _start(hass, role)
    await run_stages(api, role, _stored(hass))
    kitchen = _entry_id(hass, SMARTPI, qd.TITLE)
    stored = _stored(hass)

    async def item_id(entity_id: str) -> str:
        registry_id = er.async_get(hass).async_get(entity_id).id
        items = (await stored(qd.TITLE)).get("smart_pi_dependencies") or []
        return next(i["id"] for i in items if i.get("entity") == registry_id)

    for entity in (qd.KITCHEN, qd.DINING):
        result = await _refused(api, kitchen, qd._add() + [Form("neighbour_add", {"entity": entity})])  # noqa: SLF001
        assert result.get("errors"), (entity, result)
    for doors in ([], [qd.HALL_WINDOW]):
        result = await _refused(api, kitchen, qd._add() + [  # noqa: SLF001
            Form("neighbour_add", {"entity": qd.HALL_SENSOR}),
            Form("neighbour_details", {"mode": "door", "door_sensors": doors}),
        ])  # fmt: skip
        assert result.get("errors"), (doors, result)

    await run_flow(api.transport, "options", kitchen, qd._remove([await item_id(qd.HALL)]))  # noqa: SLF001
    result = await walk(api.transport, "options", kitchen, qd._add() + [Form("neighbour_add", {"entity": qd.HALL})])  # noqa: SLF001
    assert qd.KITCHEN_DOOR in (result.get("description_placeholders") or {}).get("suggestions", ""), result
    assert not form_defaults(result).get("door_sensors"), result
    result = await walk(
        api.transport, "options", kitchen,
        [Form("neighbour_details", {"mode": "door", "door_sensors": [qd.KITCHEN_DOOR], "neighbour_window_action": "nothing"})],
        resume=result,
    )  # fmt: skip
    assert result.get("step_id") in ("neighbour_confirm", "thermostat_menu"), result
    result = await walk(
        api.transport, "options", kitchen,
        [Form("neighbour_confirm", if_shown=True), *save_steps()],
        resume=result,
    )  # fmt: skip
    assert result.get("type") == "create_entry", result

    dining = await item_id(qd.DINING)
    await run_flow(api.transport, "options", kitchen, qd._edit(dining, {"mode": "always", "door_sensors": [], "neighbour_window_action": "eco"}))  # noqa: SLF001
    result = await walk(api.transport, "options", kitchen, [Menu("dependencies"), Menu("dependency_remove"), Form("dependency_remove", {"remove": []})])
    assert result.get("step_id") == "thermostat_menu", result
    assert result["description_placeholders"]["note"].strip(), result
    await api.transport.abort("options", result["flow_id"])
    pending = await walk(api.transport, "options", kitchen, [Menu("dependencies"), Menu("dependency_remove"), Form("dependency_remove", {"remove": [dining]})])
    assert pending.get("step_id") == "thermostat_menu", pending
    await run_flow(api.transport, "options", kitchen, qd._edit(dining, {"mode": "always", "door_sensors": [], "neighbour_window_action": "nothing"}))  # noqa: SLF001
    result = await walk(api.transport, "options", kitchen, [Menu("save")], resume=pending)
    assert (result.get("type"), result.get("reason")) == ("abort", "zone_changed"), result

    await run_flow(api.transport, "options", kitchen, qd._add() + [  # noqa: SLF001
        Form("neighbour_add", {"entity": qd.HALL_SENSOR}),
        Form("neighbour_details", {"mode": "always", "door_sensors": []}),
        Form("neighbour_confirm", if_shown=True),
        *save_steps(),
    ])  # fmt: skip
    sensor_item = await item_id(qd.HALL_SENSOR)
    er.async_get(hass).async_remove(qd.HALL_SENSOR)
    await hass.async_block_till_done()
    result = await _refused(api, kitchen, [Menu("dependencies"), Menu("dependency_edit"), Form("dependency_edit", {"dependency": sensor_item})])
    assert result.get("errors") == {"dependency": "neighbour_missing"}, result
    await run_flow(api.transport, "options", kitchen, qd._remove([sensor_item]))  # noqa: SLF001


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_zone_scenarios_flows(hass: HomeAssistant) -> None:
    """QE1's, QE2's and QE4's form paths: each wrong master refused, the missing entry created at
    save, the zone moved to the main source, the save stopped by zone_changed, the floor's heating
    type and the master's devices changed, on the real forms."""
    from flows import Form, Menu, run_flow, walk  # noqa: PLC0415
    from scenarios import qe  # noqa: PLC0415
    from stages import save_steps, vt_thermostat_steps, zone_steps  # noqa: PLC0415

    role = ROLES["zone"]
    api = await _start(hass, role)
    await run_stages(api, role, _stored(hass))
    zone = role.zone
    main_title, other_title = "Lounge radiator", "Lounge floor"
    stored = _stored(hass)
    main = _entry_id(hass, SMARTPI, main_title)

    faulty = (
        qe._master("QAS self regulation", (zone.main, zone.additional), "auto_regulation_light"),  # noqa: SLF001
        qe._master("QAS one device", (zone.main,)),  # noqa: SLF001
        qe._master("QAS other sensors", (zone.main, "climate.spare")),  # noqa: SLF001
        qe._master("QAS no smartpi", (zone.main, "climate.lounge_dummy")),  # noqa: SLF001
        qe._master("QAS already zoned", (qe.EXTRA, zone.additional)),  # noqa: SLF001
    )
    for master in faulty:
        await run_flow(api.transport, "config", VT, vt_thermostat_steps(role, master))
        elsewhere = master.name == "QAS already zoned"
        source = _entry_id(hass, SMARTPI, qe.EXTRA_TITLE) if elsewhere else main
        result = await _refused(api, source, qe.zone_master_steps(not elsewhere, master.climate))
        assert result.get("errors"), (master.name, result)
        await hass.config_entries.async_remove(_entry_id(hass, VT, master.name))
        await hass.async_block_till_done()

    remove = [Menu("zone"), Menu("zone_remove"), *save_steps()]
    await run_flow(api.transport, "options", main, remove)
    assert not [i for i in (await stored(main_title)).get("smart_pi_dependencies") or [] if i.get("kind") == "source"]
    await hass.config_entries.async_remove(_entry_id(hass, SMARTPI, other_title))
    await hass.async_block_till_done()
    await run_flow(api.transport, "options", main, zone_steps(zone, role))
    assert any(e.title == other_title for e in hass.config_entries.async_entries(SMARTPI))

    await run_flow(api.transport, "options", main, remove)
    await run_flow(api.transport, "options", _entry_id(hass, SMARTPI, other_title), zone_steps(zone, role))
    assert [i for i in (await stored(main_title)).get("smart_pi_dependencies") or [] if i.get("kind") == "source"]

    pending = await walk(api.transport, "options", main, [
        Menu("zone"), Menu("zone_master"), Form("zone_master", {"master": zone.master}),
        Form("zone_sources", {"main_source": zone.main, "split_rule": zone.rule, "custom_response": False}),
        Form("zone_parameter"),
    ])  # fmt: skip
    await run_flow(api.transport, "options", main, remove)
    result = await walk(api.transport, "options", main, [Menu("save")], resume=pending)
    assert (result.get("type"), result.get("reason")) == ("abort", "zone_changed"), result
    await run_flow(api.transport, "options", main, zone_steps(zone, role))

    other = _entry_id(hass, SMARTPI, other_title)
    await run_flow(api.transport, "options", other, [Menu("source_role"), Form("source_role", {"smart_pi_heating_type": "floor_electric"}), *save_steps()])
    await run_flow(api.transport, "options", other, [Menu("source_role"), Form("source_role", {"smart_pi_heating_type": "floor_water"}), *save_steps()])

    master_entry = _entry_id(hass, VT, "Lounge")
    for devices in ([zone.main, "climate.lounge_dummy"], [zone.main, zone.additional]):
        await run_flow(api.transport, "options", master_entry, [Menu("type"), Form("type", {"underlying_entity_ids": devices}), Menu("finalize")])


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_water_scenarios_flows(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """QW9's form path: the studio's water source to none and back to entities, the model kept
    each time, on the real forms; the reset question is asked at each save and no error is logged."""
    from flows import run_flow, walk  # noqa: PLC0415
    from scenarios import qw  # noqa: PLC0415

    caplog.set_level(logging.ERROR)
    role = ROLES["water"]
    api = await _start(hass, role)
    await run_stages(api, role, _stored(hass))
    studio = _entry_id(hass, SMARTPI, "Studio")
    stored = _stored(hass)

    result = await walk(api.transport, "options", studio, qw._studio_water("none", keep=True)[:-1])  # noqa: SLF001
    assert result.get("step_id") == "model_reset", result
    await api.transport.abort("options", result["flow_id"])
    await run_flow(api.transport, "options", studio, qw._studio_water("none", keep=True))  # noqa: SLF001
    options = await stored("Studio")
    assert options["smart_pi_water_source"] == "none"
    assert "smart_pi_supply_entity" not in options
    assert options["smart_pi_model_kept_for_water"] == "none"
    await run_flow(api.transport, "options", studio, qw._studio_water("entities", keep=True))  # noqa: SLF001
    options = await stored("Studio")
    supply = er.async_get(hass).async_get("sensor.smartpi_room_sim_studio_radiator_supply")
    assert options["smart_pi_water_source"] == "entities"
    assert options["smart_pi_supply_entity"] == supply.id
    assert options["smart_pi_model_kept_for_water"] == f"entities|{supply.id}"
    assert "smart_pi_reset_token" not in options
    errors = [f"{r.name}: {r.getMessage()[:200]}" for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, errors
