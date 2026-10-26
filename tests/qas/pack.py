"""What the QAS test pack builds on a test instance: the simulated rooms and the roles.

Three rooms, the same in every role:

- West and East: a water radiator behind a valve, counted by Versatile Thermostat's central
  boiler; it gives heat only while the boiler's switch is on;
- North: an electric heater, not counted by the central boiler.

The two roles differ only in the central boiler's threshold: two active devices, or 2500 W
(each device is 2000 W, so one at full power is below it and two at 63 % reach it). Everything
else in Versatile Thermostat keeps its default. SmartPI runs with its "SmartPI defaults" entry
only.
"""

from __future__ import annotations

from dataclasses import dataclass

ROOMS_DOMAIN = "smartpi_test_rooms"
HELPER_DOMAIN = "smartpi_test_helper"
SMARTPI = "vtherm_smartpi"
VT = "versatile_thermostat"
SMARTPI_DEFAULTS = "SmartPI defaults"
VT_CENTRAL = "Central configuration"

OUTDOOR_SENSOR = f"sensor.{ROOMS_DOMAIN}_outdoor_temperature"
BOILER_SWITCH = f"switch.{ROOMS_DOMAIN}_boiler"
# A simulated sensor writes only when its reading changes: VTherm's safety delay (60 min by
# default) would take a steady room for a dead sensor.
SAFETY_DELAY_MIN = 1440
DEVICE_POWER_W = 2000.0


@dataclass(frozen=True)
class Room:
    name: str  # also the thermostat's name
    heater: str  # "radiator" (water, behind a valve) or "electric" (a switch)
    counted: bool  # counted by VTherm's central boiler
    temperature: float = 19.5  # degC at the start
    loss: float = 0.001  # 1/min, SmartPI's b
    gain: float = 0.04  # degC/min at full power, SmartPI's a
    delay_min: float = 3.0
    lag_min: float = 10.0

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def climate(self) -> str:
        return f"climate.{self.key}"

    @property
    def sensor(self) -> str:
        return f"sensor.{ROOMS_DOMAIN}_{self.key}_temperature"

    @property
    def device(self) -> str:
        """The entity VTherm drives: the radiator's valve, or the heater's switch."""
        if self.heater == "radiator":
            return f"number.{ROOMS_DOMAIN}_{self.key}_valve"
        return f"switch.{ROOMS_DOMAIN}_{self.key}_heater"


ROOMS = (
    Room("West", "radiator", counted=True),
    Room("East", "radiator", counted=True, loss=0.0012),
    Room("North", "electric", counted=False, loss=0.0012, gain=0.035, delay_min=2.0, lag_min=0.0),
)


@dataclass(frozen=True)
class Role:
    name: str
    title: str
    count_threshold: int  # VTherm's active-device threshold (0: not used)
    power_threshold_w: float  # VTherm's power threshold (0: not used)


ROLES = {
    role.name: role
    for role in (
        Role("boiler-count", "SmartPI QAS, central boiler by device count", 2, 0.0),
        Role("boiler-power", "SmartPI QAS, central boiler by power", 0, 2500.0),
    )
}


def room(name: str) -> Room:
    return next(r for r in ROOMS if r.name == name)
