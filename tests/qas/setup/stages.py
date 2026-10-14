"""An instance set up through Home Assistant's API, stage by stage, from its role (roles.py).

Stages, in this order (``STAGES``); each one skips what already exists, so a stage can be run
again after a failure:

1. ``vtherm``  — Versatile Thermostat's central configuration (with the central boiler and the
   power manager where the role has them), SmartPI's defaults entry (so that SmartPI's algorithm
   is there for the thermostats), then the thermostats (over_switch, over_valve), then the
   over_climate masters with self-regulation off;
2. ``areas``   — the areas, with the simulator's entities and the thermostats in them (the door
   suggestions of the neighbour form read them);
3. ``inputs``  — VTherm's central boiler thresholds and the power manager's maximum power;
4. ``smartpi`` — one SmartPI entry per SmartPI thermostat, with its heating type;
5. ``options`` — each thermostat entry's options saved once, then the role's neighbours and zone,
   the boiler's signals in the defaults entry and each thermostat's "Water and boiler" (the water
   data and the boiler's call sources).

``Api`` is where it runs: the bench's client (``setup/setup_role.py``) or Home Assistant in a test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from flows import Form, Menu, Transport, run_flow
from roles import (
    BOILER_DHW,
    BOILER_FAULT,
    BOILER_HEAT_AVAILABLE,
    BOILER_SWITCH,
    HOUSE_POWER_SENSOR,
    MAX_POWER_INPUT,
    OUTDOOR_SENSOR,
    SAFETY_DELAY_MIN,
    SMARTPI_DEFAULTS_TITLE,
    CYCLE_MIN,
    Neighbour,
    Role,
    Thermostat,
    Zone,
    supply_sensor,
    temperature_sensor,
)

VT = "versatile_thermostat"
SMARTPI = "vtherm_smartpi"
VT_CENTRAL_TITLE = "Central configuration"
THRESHOLD_COUNT_UID = "boiler_activation_threshold"
THRESHOLD_POWER_UID = "boiler_power_activation_threshold"
# SmartPI's option keys of the inputs from the heating system (its const.py).
WATER_SOURCE = "smart_pi_water_source"
CALL_SOURCE = "smart_pi_boiler_call_source"
SUPPLY_ENTITY = "smart_pi_supply_entity"
BOILER_SIGNALS = {
    "smart_pi_heat_available_entity": BOILER_HEAT_AVAILABLE,
    "smart_pi_dhw_entity": BOILER_DHW,
    "smart_pi_fault_entity": BOILER_FAULT,
}


class Api(Protocol):
    transport: Transport

    async def entries(self) -> list[dict[str, Any]]:
        """Config entries: entry_id, domain, title."""

    async def entity_registry(self) -> list[dict[str, Any]]:
        """Entity registry: entity_id, id, unique_id, platform, area_id, device_id,
        config_entry_id."""

    async def areas(self) -> list[dict[str, Any]]:
        """Areas: area_id, name."""

    async def create_area(self, name: str) -> str:
        """A new area; its id."""

    async def set_area(self, entity_id: str, area_id: str, device_id: str | None) -> None:
        """Put an entity in an area: through its device where it has one (an entity without a
        name of its own takes its device's area)."""

    async def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        """Call a service and wait for it."""

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        """An entity's state, or None."""

    async def settle(self, entity_ids: list[str], within_s: float) -> list[str]:
        """Wait until these entities have a state; the ones still missing."""

    def log(self, text: str) -> None:
        """Say what the stage did."""


# --- Versatile Thermostat ---------------------------------------------------------------------


def vt_central_steps(role: Role) -> list[Form | Menu]:
    features = {
        "use_window_feature": True,
        "use_power_feature": role.power_manager,
        "use_central_boiler_feature": role.central_boiler,
    }
    steps: list[Form | Menu] = [
        Form("user", {"thermostat_type": "thermostat_central_config"}),
        Menu("main"),
        Form("main", {"external_temperature_sensor_entity_id": OUTDOOR_SENSOR}),
        Menu("features"),
        Form("features", features),
    ]
    if role.central_boiler:
        steps += [
            Menu("central_boiler"),
            Form(
                "central_boiler",
                {
                    "central_boiler_activation_service": f"{BOILER_SWITCH}/switch.turn_on",
                    "central_boiler_deactivation_service": f"{BOILER_SWITCH}/switch.turn_off",
                    "central_boiler_activation_delay_sec": 0,
                },
            ),
        ]
    if role.power_manager:
        steps += [
            Menu("power"),
            Form(
                "power",
                {
                    "power_sensor_entity_id": HOUSE_POWER_SENSOR,
                    "max_power_sensor_entity_id": MAX_POWER_INPUT,
                    "power_unit": "w",
                },
            ),
        ]
    steps += [
        Menu("tpi"),
        Form("tpi"),
        Menu("advanced"),
        Form("advanced", {"safety_delay_min": SAFETY_DELAY_MIN}),
        Menu("finalize"),
    ]
    return steps


def vt_thermostat_steps(role: Role, thermostat: Thermostat) -> list[Form | Menu]:
    kind = {"switch": "over_switch", "valve": "over_valve", "climate": "over_climate"}[thermostat.kind]
    main: dict[str, Any] = {
        "name": thermostat.name,
        "temperature_sensor_entity_id": temperature_sensor(thermostat.room),
        "cycle_min": CYCLE_MIN,
        "use_main_central_config": True,
        "used_by_controls_central_boiler": thermostat.central_boiler,
    }
    if thermostat.power_w:
        main |= {"device_power": thermostat.power_w, "power_unit": "w"}
    features = {
        "use_window_feature": thermostat.window_sensor is not None,
        "use_power_feature": role.power_manager and bool(thermostat.power_w),
    }
    steps: list[Form | Menu] = [
        Form("user", {"thermostat_type": f"thermostat_{kind}"}),
        Menu("main"),
        Form("main", main),
        Menu("features"),
        Form("features", features),
        Menu("type"),
        Form("type", vt_type_answers(thermostat)),
    ]
    if thermostat.algorithm == "tpi":
        steps += [Menu("tpi"), Form("tpi", {"use_tpi_central_config": True})]
    if thermostat.window_sensor is not None:
        steps += [
            Menu("window"),
            Form("window", {"window_sensor_entity_id": thermostat.window_sensor, "use_window_central_config": False}),
            Form("window", {"window_delay": 30, "window_off_delay": 30, "window_action": "window_turn_off"}, if_shown=True),
        ]
    steps += [
        Menu("advanced"),
        Form("advanced", {"use_advanced_central_config": False}),
        Form("advanced", {"safety_delay_min": SAFETY_DELAY_MIN}, if_shown=True),
        Menu("finalize"),
    ]
    return steps


def vt_type_answers(thermostat: Thermostat) -> dict[str, Any]:
    if thermostat.kind == "climate":
        return {"underlying_entity_ids": list(thermostat.underlying), "auto_regulation_mode": thermostat.self_regulation}
    return {
        "underlying_entity_ids": list(thermostat.underlying),
        "proportional_function": thermostat.algorithm,
    }


async def stage_vtherm(api: Api, role: Role) -> None:
    titles = {e["title"] for e in await api.entries() if e["domain"] == VT}
    if VT_CENTRAL_TITLE not in titles:
        await run_flow(api.transport, "config", VT, vt_central_steps(role))
        api.log(f"VTherm: {VT_CENTRAL_TITLE}")
    # SmartPI's defaults entry first: the thermostats that use SmartPI find its algorithm at once.
    smartpi_titles = {e["title"] for e in await api.entries() if e["domain"] == SMARTPI}
    if SMARTPI_DEFAULTS_TITLE not in smartpi_titles:
        await run_flow(api.transport, "config", SMARTPI, [])
        api.log(f"SmartPI: {SMARTPI_DEFAULTS_TITLE}")
    ordered = sorted(role.thermostats, key=lambda t: t.kind == "climate")
    for thermostat in ordered:
        if thermostat.name in titles:
            continue
        await run_flow(api.transport, "config", VT, vt_thermostat_steps(role, thermostat))
        missing = await api.settle([thermostat.climate], 120.0)
        if missing:
            raise RuntimeError(f"VTherm {thermostat.name}: {thermostat.climate} did not appear")
        api.log(f"VTherm: {thermostat.name} ({thermostat.kind}, {thermostat.algorithm or 'master'})")


# --- areas ------------------------------------------------------------------------------------


async def stage_areas(api: Api, role: Role) -> None:
    known = {a["name"]: a["area_id"] for a in await api.areas()}
    registry = {e["entity_id"]: e for e in await api.entity_registry()}
    for name, entities in role.areas.items():
        area_id = known.get(name) or await api.create_area(name)
        members = list(entities)
        members += [
            t.climate for t in role.thermostats if temperature_sensor(t.room) in entities
        ]
        for entity_id in members:
            entry = registry.get(entity_id)
            if entry is None:
                raise RuntimeError(f"areas: {entity_id} is not in the entity registry")
            if entry.get("area_id") != area_id:
                await api.set_area(entity_id, area_id, entry.get("device_id"))
        api.log(f"area {name}: {', '.join(members)}")


# --- inputs -----------------------------------------------------------------------------------


async def stage_inputs(api: Api, role: Role) -> None:
    if role.central_boiler:
        by_uid = {
            e["unique_id"]: e["entity_id"]
            for e in await api.entity_registry()
            if e.get("platform") == VT
        }
        for uid, value in (
            (THRESHOLD_COUNT_UID, role.boiler_count_threshold),
            (THRESHOLD_POWER_UID, role.boiler_power_threshold_w),
        ):
            entity_id = by_uid.get(uid)
            if entity_id is None:
                raise RuntimeError(f"inputs: VTherm's {uid} number is missing")
            await api.call("number", "set_value", {"entity_id": entity_id, "value": value})
            api.log(f"{entity_id} = {value}")
    if role.max_power_w is not None:
        await api.call("input_number", "set_value", {"entity_id": MAX_POWER_INPUT, "value": role.max_power_w})
        api.log(f"{MAX_POWER_INPUT} = {role.max_power_w}")


# --- SmartPI entries --------------------------------------------------------------------------


def smartpi_thermostat_steps(role: Role, thermostat: Thermostat) -> list[Form | Menu]:
    settings: dict[str, Any] = {}
    if thermostat.heating_type is not None:
        settings["smart_pi_heating_type"] = thermostat.heating_type
    return [
        Form("thermostat", {"target_vtherm_unique_id": thermostat.climate}),
        Form(
            "thermostat_settings",
            settings,
            optional=frozenset({"smart_pi_heating_type"}) if role.plugin != "qas" else frozenset(),
        ),
    ]


async def stage_smartpi(api: Api, role: Role) -> None:
    titles = {e["title"] for e in await api.entries() if e["domain"] == SMARTPI}
    for thermostat in role.thermostats:
        if not thermostat.smartpi or thermostat.name in titles:
            continue
        await run_flow(api.transport, "config", SMARTPI, smartpi_thermostat_steps(role, thermostat))
        api.log(f"SmartPI: {thermostat.name} ({thermostat.heating_type or 'default type'})")


# --- base options -----------------------------------------------------------------------------


def settings_save_steps(role: Role) -> list[Form | Menu]:
    """A thermostat entry's options saved as they are: its stored options become complete."""
    if role.plugin != "qas":
        return [Form("init")]
    return [Menu("settings"), Form("settings")]


def neighbour_steps(neighbour: Neighbour) -> list[Form | Menu]:
    details: dict[str, Any] = {"mode": neighbour.mode, "door_sensors": list(neighbour.door_sensors)}
    if neighbour.entity.startswith("climate."):
        details["neighbour_window_action"] = neighbour.window_action
    return [
        Menu("dependencies"),
        Menu("neighbour_add"),
        Form("neighbour_add", {"entity": neighbour.entity}),
        Form("neighbour_details", details),
        Form("neighbour_confirm", if_shown=True),
        Form("model_reset", {"reset_model": True}, if_shown=True),
    ]


def zone_steps(zone: Zone) -> list[Form | Menu]:
    return [
        Menu("zone"),
        Form("zone_master", {"master": zone.master}),
        Form("zone_sources", {"main_source": zone.main, "split_rule": zone.rule, "custom_response": False}),
        Form("zone_parameter", {zone.parameter_key: zone.parameter}),
        Form("zone_reset", {"reset_model": True}, if_shown=True),
    ]


def boiler_signals_steps() -> list[Form | Menu]:
    """The defaults entry's "Boiler signals": the simulator's boiler."""
    return [Menu("boiler_signals"), Form("boiler_signals", dict(BOILER_SIGNALS))]


def heating_inputs_steps(role: Role, thermostat: Thermostat, reset: bool = False) -> list[Form | Menu]:
    """A thermostat's "Water and boiler", as its role says. A zone's additional source chooses
    its water only. The model is kept (``reset``): nothing was learned yet."""
    additional = role.zone is not None and role.zone.additional == thermostat.climate
    sources: dict[str, Any] = {}
    if thermostat.water_source is not None:
        sources[WATER_SOURCE] = thermostat.water_source
    if thermostat.call_source is not None and not additional:
        sources[CALL_SOURCE] = thermostat.call_source
    entities: dict[str, Any] = {"smart_pi_boiler_signals": "defaults"}
    if thermostat.water == "entities":
        entities[SUPPLY_ENTITY] = supply_sensor(thermostat.heater)
    return [
        Menu("heating_inputs"),
        Form("heating_inputs", sources),
        Form("heating_entities", entities, if_shown=True),
        Form("heating_reset", {"reset_model": reset}, if_shown=True),
    ]


async def _options(api: Api, role: Role, title: str, steps: list[Form | Menu]) -> None:
    entry = next(
        (e for e in await api.entries() if e["domain"] == SMARTPI and e["title"] == title), None
    )
    if entry is None:
        raise RuntimeError(f"options: no SmartPI entry {title!r}")
    await run_flow(api.transport, "options", entry["entry_id"], steps)


async def stage_options(
    api: Api, role: Role, stored: Callable[[str], Awaitable[Mapping[str, Any]]]
) -> None:
    """``stored(title)``: an entry's stored options (to skip what is already there)."""
    for title in role.smartpi_titles[1:]:
        if not await stored(title):
            await _options(api, role, title, settings_save_steps(role))
            api.log(f"options of {title}: saved once")
    if role.plugin != "qas":
        return
    registry = {e["entity_id"]: e for e in await api.entity_registry()}
    for title, neighbours in role.neighbours.items():
        present = {
            item.get("entity")
            for item in (await stored(title)).get("smart_pi_dependencies") or []
            if item.get("kind") == "neighbour"
        }
        for neighbour in neighbours:
            if registry[neighbour.entity]["id"] in present:
                continue
            await _options(api, role, title, neighbour_steps(neighbour))
            api.log(f"options of {title}: neighbour {neighbour.entity} ({neighbour.mode})")
    if role.zone is not None and role.zone_entry is not None:
        items = (await stored(role.zone_entry)).get("smart_pi_dependencies") or []
        if not any(item.get("kind") == "source" for item in items):
            await _options(api, role, role.zone_entry, zone_steps(role.zone))
            api.log(f"options of {role.zone_entry}: zone with {role.zone.additional}")
    if role.boiler_signals:
        defaults = await stored(SMARTPI_DEFAULTS_TITLE)
        if not all(defaults.get(key) for key in BOILER_SIGNALS):
            await _options(api, role, SMARTPI_DEFAULTS_TITLE, boiler_signals_steps())
            api.log(f"options of {SMARTPI_DEFAULTS_TITLE}: boiler signals")
    for thermostat in role.heating_inputs:
        options = await stored(thermostat.name)
        additional = role.zone is not None and role.zone.additional == thermostat.climate
        if options.get(WATER_SOURCE) == thermostat.water_source and (
            additional or thermostat.call_source is None or options.get(CALL_SOURCE) == thermostat.call_source
        ):
            continue
        await _options(api, role, thermostat.name, heating_inputs_steps(role, thermostat))
        api.log(f"options of {thermostat.name}: water {thermostat.water_source}, call {thermostat.call_source}")


STAGES = ("vtherm", "areas", "inputs", "smartpi", "options")


async def run_stages(
    api: Api,
    role: Role,
    stored: Callable[[str], Awaitable[Mapping[str, Any]]],
    only: str | None = None,
) -> None:
    for stage in STAGES:
        if only is not None and stage != only:
            continue
        if stage == "vtherm":
            await stage_vtherm(api, role)
        elif stage == "areas":
            await stage_areas(api, role)
        elif stage == "inputs":
            await stage_inputs(api, role)
        elif stage == "smartpi":
            await stage_smartpi(api, role)
        else:
            await stage_options(api, role, stored)
