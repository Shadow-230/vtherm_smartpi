"""The roles of the QAS bench, in one place (tests/qas/README.md).

Each role says what a test instance running it holds: the simulated rooms (``smartpi_room_sim``),
Versatile Thermostat's central configuration and thermostats, and SmartPI. SmartPI runs with its
"SmartPI defaults" entry only: every thermostat takes its settings. From a role come its
``configuration.yaml`` (``config/build.py``), the setup through the API (``setup/setup_role.py``)
and the runner's base (``run.py``). Which instance runs which role, and when, is the testing
session's choice: a role names no instance. Nothing here belongs to a real installation: every
entity is the simulator's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SIM = "smartpi_room_sim"
OUTDOOR_SENSOR = f"sensor.{SIM}_outdoor_temperature"
BOILER_SWITCH = f"switch.{SIM}_boiler"
# Versatile Thermostat's power manager reads these: the power unit of its central boiler's power
# threshold is the power manager's (kW in the role boiler-power).
HOUSE_POWER_INPUT = "input_number.qas_house_power"
MAX_POWER_INPUT = "input_number.qas_max_power"
SMARTPI_DEFAULTS_TITLE = "SmartPI defaults"

# VTherm sees a sensor that writes only on change as dead after its safety delay (60 min by
# default): a steady simulated room keeps one value for longer (the bench's docs/lessons.md).
SAFETY_DELAY_MIN = 1440
CYCLE_MIN = 10


def temperature_sensor(room: str) -> str:
    return f"sensor.{SIM}_{room}_temperature"


def heater_switch(heater: str) -> str:
    return f"switch.{SIM}_{heater}"


def valve_number(heater: str) -> str:
    return f"number.{SIM}_{heater}"


@dataclass(frozen=True)
class Thermostat:
    """One Versatile Thermostat entry with SmartPI."""

    name: str  # VTherm's name
    kind: str  # "switch" or "valve"
    room: str  # the simulated room whose sensor it reads
    heater: str  # the simulated heater it drives
    power: float  # its device's power, in the role's power unit
    central_boiler: bool = False  # counted by VTherm's central boiler
    setpoint: float = 20.0  # the base target temperature

    @property
    def climate(self) -> str:
        return "climate." + self.name.lower().replace(" ", "_")

    @property
    def underlying(self) -> str:
        return valve_number(self.heater) if self.kind == "valve" else heater_switch(self.heater)


@dataclass(frozen=True)
class Role:
    name: str
    title: str
    sim: dict[str, Any]
    thermostats: tuple[Thermostat, ...]
    central_boiler: bool = False
    boiler_count_threshold: int = 0  # VTherm's active-device threshold (0: not used)
    boiler_power_threshold: float = 0.0  # VTherm's power threshold, in power_unit (0: not used)
    # VTherm's power manager, with HOUSE_POWER_INPUT and MAX_POWER_INPUT in power_unit
    power_manager: bool = False
    power_unit: str = "w"  # "w" or "kw", as VTherm's forms take it
    house_power: float = 0.0
    max_power: float = 0.0

    @property
    def smartpi_titles(self) -> tuple[str, ...]:
        return (SMARTPI_DEFAULTS_TITLE,)

    def thermostat(self, name: str) -> Thermostat:
        return next(t for t in self.thermostats if t.name == name)


# --- the simulated rooms of each role ---------------------------------------------------------

_OUTDOOR = {"mean": 3.0, "swing": 3.0}


def _base_sim() -> dict[str, Any]:
    return {
        "rooms": {"living": {"temperature": 19.5, "b": 0.0012}},
        "heaters": {"living_heater": {"room": "living", "a": 0.035, "delay_min": 3.0}},
        "outdoor": dict(_OUTDOOR),
    }


def _boiler_sim() -> dict[str, Any]:
    # Two rooms heated by water radiators behind valves, on the boiler that Versatile
    # Thermostat's central boiler switches, and an electric heater it does not count.
    return {
        "rooms": {
            "lounge": {"temperature": 19.5, "b": 0.001},
            "bedroom": {"temperature": 19.5, "b": 0.0012},
            "office": {"temperature": 19.5, "b": 0.0012},
        },
        "heaters": {
            "lounge_radiator": {
                "room": "lounge", "kind": "valve", "a": 0.04, "delay_min": 3.0, "lag_min": 10.0,
                "emitter": "radiator",
            },
            "bedroom_radiator": {
                "room": "bedroom", "kind": "valve", "a": 0.04, "delay_min": 3.0, "lag_min": 10.0,
                "emitter": "radiator",
            },
            "office_heater": {"room": "office", "a": 0.035, "delay_min": 2.0},
        },
        "boiler": {"fire_c": 60.0, "idle_c": 12.0, "tau_up_min": 4.0, "tau_down_min": 20.0},
        "outdoor": dict(_OUTDOOR),
    }  # fmt: skip


def _boiler_thermostats(power: float) -> tuple[Thermostat, ...]:
    return (
        Thermostat("Lounge", "valve", "lounge", "lounge_radiator", power, central_boiler=True),
        Thermostat("Bedroom", "valve", "bedroom", "bedroom_radiator", power, central_boiler=True),
        Thermostat("Office", "switch", "office", "office_heater", power),
    )


ROLES: dict[str, Role] = {}


def _add(role: Role) -> None:
    ROLES[role.name] = role


_add(
    Role(
        "base", "SmartPI QAS base", _base_sim(),
        (Thermostat("Living", "switch", "living", "living_heater", 1500.0),),
    )
)  # fmt: skip
# Two valves count: the boiler starts when both thermostats call.
_add(
    Role(
        "boiler-count", "SmartPI QAS boiler, device count", _boiler_sim(),
        _boiler_thermostats(2000.0),
        central_boiler=True, boiler_count_threshold=2,
    )
)  # fmt: skip
# 2.5 kW: one radiator of 2 kW at full power is not enough, two at 63 % are.
_add(
    Role(
        "boiler-power", "SmartPI QAS boiler, power in kW", _boiler_sim(),
        _boiler_thermostats(2.0),
        central_boiler=True, boiler_power_threshold=2.5,
        power_manager=True, power_unit="kw", house_power=0.5, max_power=50.0,
    )
)  # fmt: skip
