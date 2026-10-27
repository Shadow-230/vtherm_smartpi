"""Prepare a test instance as a role, through Home Assistant's API (run by the bench).

    HA_INSTANCE=N python <stage>/tests/qas/prepare.py --role ROLE [--no-restart]

Run after the bench deployed the role's configuration (config/<role>.yaml). What already exists
is kept, so it can run again after a failure. In Versatile Thermostat it sets only what the rooms
need; everything else keeps VTherm's defaults:

1. the central configuration: the outdoor sensor; the central boiler, switching the test rooms'
   boiler, with the role's activation delay; the safety delay of the test rooms' sensors
   (pack.SAFETY_DELAY_MIN);
2. SmartPI's defaults entry, before the thermostats, so that they find SmartPI's algorithm;
3. one thermostat per room, with SmartPI: its sensor, its device and the device's power, whether
   the central boiler counts it; power management unticked (VTherm ticks it for a device with a
   power, and warns without a power manager); for a valve, VTherm's opening filters off, as
   SmartPI's repairs advise; West's window sensor where the role has one (VTherm turns West off
   while the window is open);
4. the central boiler's threshold of the role.

Home Assistant then restarts (unless ``--no-restart``): SmartPI's defaults entry shows the
diagnostics of the thermostats created after it once it is set up again.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from instance import Instance, Step, bench_lib, run_flow  # noqa: E402
from pack import (  # noqa: E402
    BOILER_SWITCH,
    DEVICE_POWER_W,
    OUTDOOR_SENSOR,
    ROLES,
    ROOMS,
    SAFETY_DELAY_MIN,
    SMARTPI,
    SMARTPI_DEFAULTS,
    VT,
    VT_CENTRAL,
    WEST_WINDOW,
    Role,
    Room,
)

COUNT_THRESHOLD_UID = "boiler_activation_threshold"
POWER_THRESHOLD_UID = "boiler_power_activation_threshold"


def central_steps(role: Role) -> list[Step]:
    return [
        ("form", "user", {"thermostat_type": "thermostat_central_config"}),
        ("menu", "main"),
        ("form", "main", {"external_temperature_sensor_entity_id": OUTDOOR_SENSOR}),
        ("menu", "features"),
        ("form", "features", {"use_central_boiler_feature": True}),
        ("menu", "central_boiler"),
        (
            "form",
            "central_boiler",
            {
                "central_boiler_activation_service": f"{BOILER_SWITCH}/switch.turn_on",
                "central_boiler_deactivation_service": f"{BOILER_SWITCH}/switch.turn_off",
                "central_boiler_activation_delay_sec": role.boiler_delay_s,
            },
        ),
        ("menu", "advanced"),
        ("form", "advanced", {"safety_delay_min": SAFETY_DELAY_MIN}),
        ("menu", "finalize"),
    ]


def thermostat_steps(room: Room, role: Role) -> list[Step]:
    kind = "over_valve" if room.heater == "radiator" else "over_switch"
    device: dict[str, Any] = {"underlying_entity_ids": [room.device], "proportional_function": "smartpi"}
    if kind == "over_valve":
        device |= {"auto_regulation_dtemp": 0, "auto_regulation_periode_min": 0}
    window = role.west_window and room.name == "West"
    steps: list[Step] = [
        ("form", "user", {"thermostat_type": f"thermostat_{kind}"}),
        ("menu", "main"),
        (
            "form",
            "main",
            {
                "name": room.name,
                "temperature_sensor_entity_id": room.sensor,
                "device_power": DEVICE_POWER_W,
                "used_by_controls_central_boiler": room.counted,
            },
        ),
        ("menu", "features"),
        ("form", "features", {"use_power_feature": False, "use_window_feature": window}),
        ("menu", "type"),
        ("form", "type", device),
    ]
    if window:
        steps += [
            ("menu", "window"),
            ("form", "window", {"window_sensor_entity_id": WEST_WINDOW, "use_window_central_config": False}),
            ("form", "window"),
        ]
    return steps + [("menu", "finalize")]


async def set_up(api: Any, role: Role, say: Any = print) -> None:
    """The role on the instance ``api`` (an ``Instance``, or Home Assistant in a test)."""
    vt_titles = {e["title"] for e in await api.entries(VT)}
    if VT_CENTRAL not in vt_titles:
        await run_flow(api, VT, central_steps(role))
        say(f"VTherm: {VT_CENTRAL}")
    if SMARTPI_DEFAULTS not in {e["title"] for e in await api.entries(SMARTPI)}:
        await run_flow(api, SMARTPI, [])
        say(f"SmartPI: {SMARTPI_DEFAULTS}")
    for room in ROOMS:
        if room.name in vt_titles:
            continue
        await run_flow(api, VT, thermostat_steps(room, role))
        if not await api.wait_state(room.climate, 120.0):
            raise RuntimeError(f"VTherm {room.name}: {room.climate} did not appear")
        say(f"VTherm: {room.name} ({room.heater}, {'counted' if room.counted else 'not counted'} by the central boiler)")
    for uid, value in ((COUNT_THRESHOLD_UID, role.count_threshold), (POWER_THRESHOLD_UID, role.power_threshold_w)):
        entity_id = await api.entity_id("number", VT, uid)
        if entity_id is None:
            raise RuntimeError(f"VTherm's {uid} is missing")
        await api.call("number", "set_value", {"entity_id": entity_id, "value": value})
        say(f"{entity_id} = {value}")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args(argv)
    bench_lib()
    from ha_client import HA  # noqa: PLC0415 — the bench's client, reads its local.env

    async with HA(SMARTPI) as ha:
        api = Instance(ha)
        await set_up(api, ROLES[args.role])
        if not args.no_restart:
            print(f"Home Assistant restarted in {await api.restart():.0f} s", flush=True)
    print("set up as", args.role, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
