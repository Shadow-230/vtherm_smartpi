"""The roles of the QAS bench, in one place (specs/2026-10-14-qas-test-plan.md, §3).

Each role says what a test instance running it holds: the simulated rooms (``smartpi_room_sim``),
the Versatile Thermostat entries, the SmartPI entries and their base options, and the heating
provider the helpers register (``smartpi_qas_helpers``). From it come the role's
``configuration.yaml`` (``config/build.py``), the setup through the API (``setup/setup_role.py``) and the
runner's base for each role (``run.py``). Which instance runs which role, and when, is the testing
session's choice: a role names no instance. Nothing here belongs to a real installation: every
entity is the simulator's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SIM = "smartpi_room_sim"
OUTDOOR_SENSOR = f"sensor.{SIM}_outdoor_temperature"
HOUSE_POWER_SENSOR = f"sensor.{SIM}_house_power"
MAX_POWER_INPUT = "input_number.qas_max_power"
BOILER_SWITCH = f"switch.{SIM}_boiler"
SMARTPI_DEFAULTS_TITLE = "SmartPI defaults"
# The boiler's signals the simulator gives, for SmartPI's "Boiler signals" in the defaults entry.
BOILER_HEAT_AVAILABLE = f"binary_sensor.{SIM}_boiler_heat_available"
BOILER_DHW = f"binary_sensor.{SIM}_boiler_dhw"
BOILER_FAULT = f"binary_sensor.{SIM}_boiler_fault"
# The heating provider of the helpers, as SmartPI's options name it.
PROVIDER = "qas"
PROVIDER_SOURCE = f"provider:{PROVIDER}"

# VTherm sees a sensor that writes only on change as dead after its safety delay (60 min by
# default): a steady simulated room keeps one value for longer (docs/lessons.md of the bench).
SAFETY_DELAY_MIN = 1440
CYCLE_MIN = 10


def temperature_sensor(room: str) -> str:
    return f"sensor.{SIM}_{room}_temperature"


def heater_switch(heater: str) -> str:
    return f"switch.{SIM}_{heater}"


def valve_number(heater: str) -> str:
    return f"number.{SIM}_{heater}"


def opening_sensor(opening: str) -> str:
    return f"binary_sensor.{SIM}_{opening}"


def supply_sensor(heater: str) -> str:
    """The simulator's sensor on a water emitter's supply pipe."""
    return f"sensor.{SIM}_{heater}_supply"


@dataclass(frozen=True)
class Thermostat:
    """One Versatile Thermostat entry."""

    name: str  # VTherm's name; also the title of its SmartPI entry
    kind: str  # "switch", "valve" or "climate" (a master over two thermostats)
    room: str  # the simulated room whose sensor it reads
    underlying: tuple[str, ...]  # entity ids
    algorithm: str = "smartpi"  # "smartpi" or "tpi"; a climate master has none
    heating_type: str | None = "radiator"  # SmartPI's heating type (None: the plugin's default)
    power_w: float = 0.0  # its devices' power for VTherm's power manager and central boiler
    central_boiler: bool = False  # counted by VTherm's central boiler
    window_sensor: str | None = None  # VTherm's window detection with this sensor
    setpoint: float = 20.0  # the base target temperature (a zone's devices follow their master)
    self_regulation: str = "auto_regulation_none"  # a master's VTherm self-regulation
    # SmartPI's "Water and boiler": the water data from "entities" (the heater's supply sensor,
    # the boiler's signals from the defaults) or "provider" (the helpers'); the boiler's call
    # from "provider", "entities" or "none" (None: the option is not set).
    water: str | None = None
    call: str | None = None

    @property
    def climate(self) -> str:
        return "climate." + self.name.lower().replace(" ", "_")

    @property
    def heater(self) -> str | None:
        """The simulated heater of a switch or valve thermostat."""
        if self.kind == "climate" or not self.underlying:
            return None
        return self.underlying[0].split(".", 1)[1].removeprefix(f"{SIM}_")

    @property
    def water_source(self) -> str | None:
        """The water source as SmartPI's options store it."""
        return {"entities": "entities", "provider": PROVIDER_SOURCE}.get(self.water or "")

    @property
    def call_source(self) -> str | None:
        """The boiler's call source as SmartPI's options store it."""
        return PROVIDER_SOURCE if self.call == "provider" else self.call

    @property
    def smartpi(self) -> bool:
        return self.kind != "climate" and self.algorithm == "smartpi"


@dataclass(frozen=True)
class Neighbour:
    """A neighbouring zone of a SmartPI entry, as its options form takes it."""

    entity: str  # the neighbour: a VTherm climate or a temperature sensor
    mode: str  # "door", "always" or "none"
    door_sensors: tuple[str, ...] = ()
    window_action: str = "nothing"  # "nothing", "turn_off", "frost", "eco"


@dataclass(frozen=True)
class Zone:
    """A zone with two heat sources, set up from its master thermostat."""

    master: str  # the master's climate entity
    main: str  # the main source's climate entity
    additional: str
    rule: str = "lead_band"
    parameter: float = 20.0

    @property
    def parameter_key(self) -> str:
        """The zone form's field for the rule's parameter (smartpi/dependencies.py)."""
        return {
            "together": "split_ratio",
            "source_first": "source_alone_percent",
            "lead_band": "lead_band_percent",
        }[self.rule]


@dataclass(frozen=True)
class Role:
    name: str
    title: str
    sim: dict[str, Any]
    thermostats: tuple[Thermostat, ...]
    central_boiler: bool = False
    power_manager: bool = False
    max_power_w: float | None = None
    boiler_count_threshold: int = 0  # VTherm's device-count threshold (0: not used)
    boiler_power_threshold_w: float = 0.0  # VTherm's power threshold (0: not used)
    areas: dict[str, tuple[str, ...]] = field(default_factory=dict)
    neighbours: dict[str, tuple[Neighbour, ...]] = field(default_factory=dict)  # by entry title
    zone: Zone | None = None
    zone_entry: str | None = None  # the SmartPI entry (title) the zone is set up from
    # the SmartPI version the instance runs: "qas", or "0.4.0" (no heating type, no menu)
    plugin: str = "qas"
    # the simulator's boiler signals set in SmartPI's defaults entry ("Boiler signals")
    boiler_signals: bool = False
    # the helpers register a heating provider serving the role's SmartPI thermostats
    provider: bool = False

    @property
    def provider_circuits(self) -> dict[str, str]:
        """The provider's circuits: each SmartPI thermostat's climate and its heater."""
        return {t.climate: t.heater for t in self.thermostats if t.smartpi and t.heater is not None}

    @property
    def heating_inputs(self) -> tuple[Thermostat, ...]:
        """The thermostats whose "Water and boiler" the setup chooses."""
        return tuple(t for t in self.thermostats if t.smartpi and (t.water is not None or t.call is not None))

    @property
    def masters_devices(self) -> set[str]:
        """The climates a master drives: they take the master's setpoint, not their own."""
        return {d for t in self.thermostats if t.kind == "climate" for d in t.underlying}

    @property
    def smartpi_titles(self) -> tuple[str, ...]:
        return (SMARTPI_DEFAULTS_TITLE, *(t.name for t in self.thermostats if t.smartpi))

    def thermostat(self, name: str) -> Thermostat:
        return next(t for t in self.thermostats if t.name == name)


# --- the simulated rooms of each role ---------------------------------------------------------

_OUTDOOR = {"mean": 3.0, "swing": 3.0}


def _base_sim() -> dict[str, Any]:
    # The attic is unheated and linked to nothing: a temperature sensor QB3 adds as a neighbour.
    return {
        "rooms": {
            "living": {"temperature": 19.5, "b": 0.0012, "resolution": 0.01},
            "attic": {"temperature": 14.0, "b": 0.0015},
        },
        "heaters": {
            "living_heater": {"room": "living", "a": 0.035, "delay_min": 3.0, "power_w": 1500.0},
        },
        "outdoor": dict(_OUTDOOR),
    }


def _zone_sim(room: str, valve: bool, spares: bool = False) -> dict[str, Any]:
    # A radiator (main) and a water floor (additional): as the in-process Z1 scenarios
    # (tests/test_smartpi_sim_zone_set.py), the floor's water not modelled (no boiler).
    # ``spares``: what QE1's faulty masters need — a SmartPI thermostat in another room, and on
    # the zone's sensor a TPI and a SmartPI thermostat whose heaters give no heat.
    sim = {
        "rooms": {room: {"temperature": 19.0, "b": 0.001}},
        "heaters": {
            f"{room}_radiator": {"room": room, "a": 0.03, "delay_min": 10.0, "power_w": 1500.0},
            f"{room}_floor": {
                "room": room,
                "kind": "valve" if valve else "switch",
                "a": 0.03,
                "delay_min": 30.0,
                "lag_min": 180.0,
                "power_w": 1500.0,
            },
        },
        "outdoor": dict(_OUTDOOR),
    }
    if spares:
        sim["rooms"]["spare"] = {"temperature": 18.0, "b": 0.0012}
        sim["heaters"]["spare_heater"] = {"room": "spare", "a": 0.03, "delay_min": 3.0, "power_w": 1000.0}
        sim["heaters"][f"{room}_dummy"] = {"room": room, "a": 0.0, "power_w": 10.0}
        sim["heaters"][f"{room}_extra"] = {"room": room, "a": 0.0, "power_w": 10.0}
    return sim


def _neighbours_sim() -> dict[str, Any]:
    return {
        "rooms": {
            "kitchen": {"temperature": 19.5, "b": 0.001},
            "hall": {"temperature": 17.5, "b": 0.0015},
            "dining": {"temperature": 19.5, "b": 0.001},
        },
        "heaters": {
            "kitchen_heater": {"room": "kitchen", "a": 0.035, "delay_min": 3.0, "power_w": 1500.0},
            "hall_heater": {"room": "hall", "a": 0.03, "delay_min": 3.0, "power_w": 1000.0},
            "dining_heater": {"room": "dining", "a": 0.03, "delay_min": 3.0, "power_w": 1200.0},
        },
        "links": [
            {"rooms": ["kitchen", "hall"], "g": 0.004, "door": "kitchen_door"},
            {"rooms": ["kitchen", "dining"], "g": 0.003},
        ],
        "openings": {
            "kitchen_door": {"device_class": "door"},
            "hall_window": {"device_class": "window", "room": "hall", "g": 0.02},
        },
        "outdoor": dict(_OUTDOOR),
    }


def _boiler_sim() -> dict[str, Any]:
    return {
        "rooms": {
            "bedroom": {"temperature": 19.0, "b": 0.001},
            "office": {"temperature": 19.0, "b": 0.0012},
        },
        "heaters": {
            "bedroom_radiator": {
                "room": "bedroom", "a": 0.03, "delay_min": 3.0, "lag_min": 4.0,
                "emitter": "radiator", "power_w": 2000.0,
            },
            "office_radiator": {
                "room": "office", "a": 0.04, "delay_min": 3.0, "lag_min": 4.0,
                "emitter": "radiator", "power_w": 3000.0,
            },
        },
        "boiler": {"fire_c": 60.0, "idle_c": 12.0, "tau_up_min": 4.0, "tau_down_min": 20.0},
        "outdoor": dict(_OUTDOOR),
    }  # fmt: skip


def _electric_sim() -> dict[str, Any]:
    return {
        "rooms": {
            "den": {"temperature": 19.0, "b": 0.001},
            "workshop": {"temperature": 18.0, "b": 0.0015},
        },
        "heaters": {
            "den_mat": {"room": "den", "a": 0.025, "delay_min": 5.0, "lag_min": 60.0, "power_w": 1200.0},
            "workshop_heater": {"room": "workshop", "a": 0.05, "delay_min": 1.0, "power_w": 2000.0},
        },
        "outdoor": dict(_OUTDOOR),
    }


def _water_sim() -> dict[str, Any]:
    # The boiler fires from the start, at 40 degC: the radiators' water is well below their
    # reference (60 degC, factor 0.41 at 20 degC), the floors' a little (45 degC, factor 0.78).
    # Their power a is given at the reference water. The lounge is a zone: a radiator (main)
    # and a water floor (additional).
    return {
        "rooms": {
            "studio": {"temperature": 19.5, "b": 0.001},
            "nursery": {"temperature": 19.5, "b": 0.001},
            "lounge": {"temperature": 19.0, "b": 0.001},
        },
        "heaters": {
            "studio_radiator": {
                "room": "studio", "a": 0.08, "delay_min": 3.0, "lag_min": 4.0,
                "emitter": "radiator", "power_w": 2000.0,
            },
            "nursery_floor": {
                "room": "nursery", "kind": "valve", "a": 0.04, "delay_min": 20.0, "lag_min": 60.0,
                "emitter": "floor_water", "power_w": 1500.0,
            },
            "lounge_radiator": {
                "room": "lounge", "a": 0.06, "delay_min": 10.0, "emitter": "radiator", "power_w": 1500.0,
            },
            "lounge_floor": {
                "room": "lounge", "a": 0.03, "delay_min": 30.0, "lag_min": 180.0,
                "emitter": "floor_water", "power_w": 1500.0,
            },
        },
        "boiler": {"fire_c": 40.0, "idle_c": 12.0, "tau_up_min": 4.0, "tau_down_min": 20.0, "on_at_start": True},
        "outdoor": dict(_OUTDOOR),
    }  # fmt: skip


def _switch(name: str, room: str, heater: str, **kw: Any) -> Thermostat:
    return Thermostat(name, "switch", room, (heater_switch(heater),), **kw)


def _zone_thermostats(room: str, title: str, valve: bool, water: str | None = None) -> tuple[Thermostat, ...]:
    main = _switch(f"{title} radiator", room, f"{room}_radiator", power_w=1500.0, water=water)
    if valve:
        additional = Thermostat(
            f"{title} floor", "valve", room, (valve_number(f"{room}_floor"),),
            heating_type="floor_water", power_w=1500.0, water=water,
        )  # fmt: skip
    else:
        additional = _switch(
            f"{title} floor", room, f"{room}_floor", heating_type="floor_water", power_w=1500.0, water=water
        )
    master = Thermostat(
        title, "climate", room, (main.climate, additional.climate), algorithm="", heating_type=None
    )
    return (main, additional, master)


def _zone(title: str) -> Zone:
    slug = title.lower()
    return Zone(f"climate.{slug}", f"climate.{slug}_radiator", f"climate.{slug}_floor")


ROLES: dict[str, Role] = {}


def _add(role: Role) -> None:
    ROLES[role.name] = role


_add(
    Role(
        "base", "SmartPI QAS base", _base_sim(),
        (_switch("Living", "living", "living_heater", power_w=1500.0),),
        areas={
            "Living": (temperature_sensor("living"), heater_switch("living_heater")),
            "Attic": (temperature_sensor("attic"),),
        },
    )
)  # fmt: skip
_add(
    Role(
        "neighbours", "SmartPI QAS neighbours", _neighbours_sim(),
        (
            _switch("Kitchen", "kitchen", "kitchen_heater", power_w=1500.0),
            _switch("Hall", "hall", "hall_heater", algorithm="tpi", heating_type=None,
                    power_w=1000.0, window_sensor=opening_sensor("hall_window"), setpoint=18.0),
            _switch("Dining", "dining", "dining_heater", algorithm="tpi", heating_type=None,
                    power_w=1200.0),
        ),
        areas={
            "Kitchen": (temperature_sensor("kitchen"), heater_switch("kitchen_heater")),
            "Hall": (temperature_sensor("hall"), heater_switch("hall_heater"),
                     opening_sensor("kitchen_door"), opening_sensor("hall_window")),
            "Dining": (temperature_sensor("dining"), heater_switch("dining_heater")),
        },
        neighbours={
            "Kitchen": (
                Neighbour("climate.hall", "door", (opening_sensor("kitchen_door"),)),
                Neighbour("climate.dining", "always"),
            )
        },
    )
)  # fmt: skip
_add(
    Role(
        "zone", "SmartPI QAS zone", _zone_sim("lounge", valve=False, spares=True),
        (
            *_zone_thermostats("lounge", "Lounge", valve=False),
            _switch("Spare", "spare", "spare_heater", power_w=1000.0, setpoint=18.0),
            _switch("Lounge dummy", "lounge", "lounge_dummy", algorithm="tpi", heating_type=None,
                    power_w=10.0, setpoint=7.0),
            _switch("Lounge extra", "lounge", "lounge_extra", power_w=10.0, setpoint=7.0),
        ),
        areas={"Lounge": (temperature_sensor("lounge"),), "Spare": (temperature_sensor("spare"),)},
        zone=_zone("Lounge"), zone_entry="Lounge radiator",
    )
)  # fmt: skip
_add(
    Role(
        "zone-ref", "SmartPI QAS zone reference", _zone_sim("lounge", valve=False),
        _zone_thermostats("lounge", "Lounge", valve=False),
        areas={"Lounge": (temperature_sensor("lounge"),)},
    )
)  # fmt: skip
_add(
    Role(
        "zone-valve", "SmartPI QAS zone with a valve", _zone_sim("study", valve=True),
        _zone_thermostats("study", "Study", valve=True),
        power_manager=True, max_power_w=6000.0,
        areas={"Study": (temperature_sensor("study"),)},
        zone=_zone("Study"), zone_entry="Study radiator",
    )
)  # fmt: skip
_add(
    Role(
        "central-boiler", "SmartPI QAS central boiler", _boiler_sim(),
        (
            _switch("Bedroom", "bedroom", "bedroom_radiator", power_w=2000.0, central_boiler=True),
            _switch("Office", "office", "office_radiator", power_w=3000.0, central_boiler=True),
        ),
        central_boiler=True, power_manager=True, max_power_w=20000.0,
        boiler_count_threshold=0, boiler_power_threshold_w=2500.0,
        areas={"Bedroom": (temperature_sensor("bedroom"),), "Office": (temperature_sensor("office"),)},
    )
)  # fmt: skip
_add(
    Role(
        "ref-0.4.0", "SmartPI QAS 0.4.0 reference", _base_sim(),
        (_switch("Living", "living", "living_heater", heating_type=None, power_w=1500.0),),
        areas={
            "Living": (temperature_sensor("living"), heater_switch("living_heater")),
            "Attic": (temperature_sensor("attic"),),
        },
        plugin="0.4.0",
    )
)  # fmt: skip
_add(
    Role(
        "electric", "SmartPI QAS electric", _electric_sim(),
        (
            _switch("Den", "den", "den_mat", heating_type="floor_electric", power_w=1200.0),
            _switch("Workshop", "workshop", "workshop_heater", heating_type="electric_fast",
                    power_w=2000.0),
        ),
        areas={"Den": (temperature_sensor("den"),), "Workshop": (temperature_sensor("workshop"),)},
        provider=True,
    )
)  # fmt: skip
_add(
    Role(
        "water", "SmartPI QAS water", _water_sim(),
        (
            _switch("Studio", "studio", "studio_radiator", power_w=2000.0, water="entities", call="provider"),
            Thermostat("Nursery", "valve", "nursery", (valve_number("nursery_floor"),),
                       heating_type="floor_water", power_w=1500.0, water="provider", call="none"),
            *_zone_thermostats("lounge", "Lounge", valve=False, water="provider"),
        ),
        areas={
            "Studio": (temperature_sensor("studio"), heater_switch("studio_radiator")),
            "Nursery": (temperature_sensor("nursery"),),
            "Lounge": (temperature_sensor("lounge"),),
        },
        zone=_zone("Lounge"), zone_entry="Lounge radiator",
        boiler_signals=True, provider=True,
    )
)  # fmt: skip
