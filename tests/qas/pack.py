"""What the QAS test pack builds on a test instance: the simulated rooms and the roles.

Three rooms, the same in every role:

- West and East: a water radiator behind a valve, counted by Versatile Thermostat's central
  boiler; it gives heat only while the boiler's switch is on;
- North: an electric heater, not counted by the central boiler.

The roles differ in the central boiler: its threshold, two active devices or a power, the power
of each device, and its activation delay:

- boiler-count, boiler-delay: two active devices, without and with an activation delay;
- boiler-power: 2500 W, each device 2000 W, so one at full power is below it and two at 63 %
  reach it;
- boiler-levels, boiler-levels-delay: 600 W, West's device 300 W and East's 900 W, so West alone
  at full power is below it and East alone or both reach it; without and with a delay.

West has a window sensor (an ``input_boolean``) in every role. Everything else in Versatile
Thermostat keeps its default. SmartPI runs with its "SmartPI defaults" entry only.
"""

from __future__ import annotations

from dataclasses import dataclass

ROOMS_DOMAIN = "smartpi_test_rooms"
HELPER_DOMAIN = "smartpi_test_helper"
SMARTPI = "vtherm_smartpi"
VT = "versatile_thermostat"
SMARTPI_DEFAULTS = "SmartPI defaults"
VT_CENTRAL = "Central configuration"
# The author's release the reference runs compare with (run.py --author-release).
AUTHOR_RELEASE = "0.6.0"
AUTHOR_COMMIT = "de15302"

OUTDOOR_SENSOR = f"sensor.{ROOMS_DOMAIN}_outdoor_temperature"
BOILER_SWITCH = f"switch.{ROOMS_DOMAIN}_boiler"
# A simulated sensor writes only when its reading changes: VTherm's safety delay (60 min by
# default) would take a steady room for a dead sensor. 100 hours: longer than the longest run
# (the 96-hour reference runs), in which North's steady room wrote nothing for 24 hours.
SAFETY_DELAY_MIN = 100 * 60
DEVICE_POWER_W = 2000.0  # every device, unless the role gives its own
WEST_WINDOW = "input_boolean.west_window"


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
    boiler_delay_s: int = 0  # VTherm's central boiler activation delay
    powers_w: tuple[tuple[str, float], ...] = ()  # (room, device power) where not DEVICE_POWER_W

    def device_power_w(self, name: str) -> float:
        """The power of a room's device (its thermostat's ``device_power``), in W."""
        return dict(self.powers_w).get(name, DEVICE_POWER_W)


LEVELS = (("West", 300.0), ("East", 900.0))
ROLES = {
    role.name: role
    for role in (
        Role("boiler-count", "SmartPI QAS, central boiler by device count", 2, 0.0),
        Role("boiler-power", "SmartPI QAS, central boiler by power", 0, 2500.0),
        Role("boiler-delay", "SmartPI QAS, central boiler with an activation delay", 2, 0.0, boiler_delay_s=120),
        Role("boiler-levels", "SmartPI QAS, central boiler by power, devices of 300 and 900 W", 0, 600.0, powers_w=LEVELS),
        Role(
            "boiler-levels-delay",
            "SmartPI QAS, central boiler by power, devices of 300 and 900 W, with an activation delay",
            0,
            600.0,
            boiler_delay_s=120,
            powers_w=LEVELS,
        ),
    )
}


def room(name: str) -> Room:
    return next(r for r in ROOMS if r.name == name)
