"""An instance set up through Home Assistant's API, stage by stage, from its role (roles.py).

Stages, in this order (``STAGES``); each one skips what already exists, so a stage can be run
again after a failure:

1. ``vtherm`` — Versatile Thermostat's central configuration (with the central boiler and the
   power manager where the role has them), SmartPI's defaults entry (so that SmartPI's algorithm
   is there for the thermostats), then the thermostats (over_switch, over_valve) with SmartPI;
2. ``inputs`` — VTherm's central boiler thresholds, and the power manager's house and maximum
   power.

SmartPI needs nothing more: every thermostat takes the defaults entry's settings.

``Api`` is where it runs: the bench's client (``setup/setup_role.py``) or Home Assistant in a test.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from flows import Form, Menu, Transport, run_flow
from roles import (
    BOILER_SWITCH,
    CYCLE_MIN,
    HOUSE_POWER_INPUT,
    MAX_POWER_INPUT,
    OUTDOOR_SENSOR,
    SAFETY_DELAY_MIN,
    SMARTPI_DEFAULTS_TITLE,
    Role,
    Thermostat,
    temperature_sensor,
)

VT = "versatile_thermostat"
SMARTPI = "vtherm_smartpi"
VT_CENTRAL_TITLE = "Central configuration"
THRESHOLD_COUNT_UID = "boiler_activation_threshold"
THRESHOLD_POWER_UID = "boiler_power_activation_threshold"
STAGES = ("vtherm", "inputs")


class Api(Protocol):
    transport: Transport

    async def entries(self) -> list[dict[str, Any]]:
        """Config entries: entry_id, domain, title."""

    async def entity_registry(self) -> list[dict[str, Any]]:
        """Entity registry: entity_id, id, unique_id, platform, device_id, config_entry_id."""

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
                    "power_sensor_entity_id": HOUSE_POWER_INPUT,
                    "max_power_sensor_entity_id": MAX_POWER_INPUT,
                    "power_unit": role.power_unit,
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
    kind = {"switch": "over_switch", "valve": "over_valve"}[thermostat.kind]
    main: dict[str, Any] = {
        "name": thermostat.name,
        "temperature_sensor_entity_id": temperature_sensor(thermostat.room),
        "cycle_min": CYCLE_MIN,
        "device_power": thermostat.power,
        "power_unit": role.power_unit,
        "use_main_central_config": True,
        "used_by_controls_central_boiler": thermostat.central_boiler,
    }
    features = {"use_power_feature": role.power_manager}
    return [
        Form("user", {"thermostat_type": f"thermostat_{kind}"}),
        Menu("main"),
        Form("main", main),
        Menu("features"),
        Form("features", features),
        Menu("type"),
        Form("type", {"underlying_entity_ids": [thermostat.underlying], "proportional_function": "smartpi"}),
        Menu("advanced"),
        Form("advanced", {"use_advanced_central_config": False}),
        Form("advanced", {"safety_delay_min": SAFETY_DELAY_MIN}, if_shown=True),
        Menu("finalize"),
    ]


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
    for thermostat in role.thermostats:
        if thermostat.name in titles:
            continue
        await run_flow(api.transport, "config", VT, vt_thermostat_steps(role, thermostat))
        missing = await api.settle([thermostat.climate], 120.0)
        if missing:
            raise RuntimeError(f"VTherm {thermostat.name}: {thermostat.climate} did not appear")
        counted = ", counted by the central boiler" if thermostat.central_boiler else ""
        api.log(f"VTherm: {thermostat.name} ({thermostat.kind}, SmartPI{counted})")


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
            (THRESHOLD_POWER_UID, role.boiler_power_threshold),
        ):
            entity_id = by_uid.get(uid)
            if entity_id is None:
                raise RuntimeError(f"inputs: VTherm's {uid} number is missing")
            await api.call("number", "set_value", {"entity_id": entity_id, "value": value})
            api.log(f"{entity_id} = {value}")
    if role.power_manager:
        for entity_id, value in ((HOUSE_POWER_INPUT, role.house_power), (MAX_POWER_INPUT, role.max_power)):
            await api.call("input_number", "set_value", {"entity_id": entity_id, "value": value})
            api.log(f"{entity_id} = {value}")


async def run_stages(api: Api, role: Role, only: str | None = None) -> None:
    """The role's stages in order, or ``only`` one."""
    for name, stage in (("vtherm", stage_vtherm), ("inputs", stage_inputs)):
        if only is None or only == name:
            await stage(api, role)
