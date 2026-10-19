"""Write the learned states the helpers' ``seed_state`` loads (``smartpi_qas_helpers/fixtures``).

    python tests/qas/build_fixtures.py           # write the files
    python tests/qas/build_fixtures.py --check   # exit 1 when a file holds no learned model

Run from the repository's root, with the test dependencies (``tests/sim`` and its fakes). Each
fixture is SmartPI's saved state after the in-process simulator ran the role's room until SmartPI
left its learning phase (and, for a zone, published the zone's model), plus a few hours of
regulation, and at least a day. The room's values come from ``roles.py``, so the learned model fits the room the bench
simulates. ``learned_room.json`` (the role ``base``'s living room) is older and is kept as it is:
SmartPI 0.4.0 loads it unchanged (QB2).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURES = HERE / "custom_components" / "smartpi_qas_helpers" / "fixtures"
for path in (HERE, ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from roles import ROLES  # noqa: E402

AFTER_LEARNED_H = 6.0  # regulation after SmartPI left its learning phase
MIN_H = 24.0  # and at least this long, so that a and b have settled
MAX_H = 72.0
STEP_H = 1.0


@dataclass(frozen=True)
class Fixture:
    """A learned state: the role's room, its main heater and, for a zone, its additional source."""

    name: str
    role: str
    room: str
    heater: str
    water_data: str | None = None
    source_heater: str | None = None  # the zone's additional source
    heating_type: str = "radiator"
    kept: bool = False  # an older file, checked but never written again

    @property
    def zone(self) -> bool:
        return self.source_heater is not None


FIXTURE_SET: tuple[Fixture, ...] = (
    Fixture("learned_room", "base", "living", "living_heater", kept=True),
    Fixture("learned_studio", "water", "studio", "studio_radiator", water_data="entities"),
    Fixture("learned_lounge_zone", "water", "lounge", "lounge_radiator", water_data="provider", source_heater="lounge_floor"),
)


def _spec(fixture: Fixture) -> tuple[Any, Any]:
    """The in-process zone (and weather) of a fixture, from the role's simulated room."""
    from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE  # noqa: PLC0415
    from sim.boiler import SimBoiler  # noqa: PLC0415
    from sim.runner import SourceSpec, ZoneSpec  # noqa: PLC0415
    from sim.weather import Weather  # noqa: PLC0415

    role = ROLES[fixture.role]
    sim = role.sim
    room = sim["rooms"][fixture.room]
    heater = sim["heaters"][fixture.heater]
    boiler = sim.get("boiler")

    def water() -> Any:
        return SimBoiler(water_c=boiler["fire_c"]) if boiler is not None else None

    source = None
    if fixture.zone:
        floor = sim["heaters"][fixture.source_heater]
        zone = role.zone
        source_type = floor.get("emitter", "floor_water")
        source = SourceSpec(
            a_f=floor["a"],
            deadtime_min=floor["delay_min"],
            tau_min=floor.get("lag_min", 0.0),
            split={
                "split_rule": zone.rule,
                "lead_emitter": fixture.heating_type,
                "source_emitter": source_type,
                zone.parameter_key: zone.parameter,
            },
            boiler=water(),
            emitter=source_type,
            options={CONF_SMART_PI_HEATING_TYPE: source_type},
        )
    spec = ZoneSpec(
        name=fixture.name,
        a=heater["a"],
        b=room["b"],
        deadtime_min=heater["delay_min"],
        initial_c=room["temperature"],
        sensor_resolution_c=room.get("resolution", 0.1),
        source=source,
        boiler=water(),
        emitter=heater.get("emitter", fixture.heating_type),
        water_data=fixture.water_data,
        options={CONF_SMART_PI_HEATING_TYPE: fixture.heating_type},
    )
    outdoor = sim["outdoor"]
    return spec, Weather(base_c=outdoor["mean"], daily_amplitude_c=outdoor["swing"])


def learned(fixture: Fixture, state: dict[str, Any]) -> list[str]:
    """What a fixture's state lacks to count as learned (empty: nothing)."""
    from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: PLC0415
    from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase  # noqa: PLC0415

    algo = SmartPI(None, cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name=fixture.name)
    algo.load_state(state)
    missing = []
    if algo.phase != SmartPIPhase.STABLE:
        missing.append(f"phase {algo.phase.value}")
    if fixture.zone and not _zone_fit(state).get("result"):
        missing.append("no published zone model")
    return missing


def _zone_fit(state: dict[str, Any]) -> dict[str, Any]:
    items = ((state.get("dependencies") or {}).get("items")) or {}
    for item in items.values():
        fit = item.get("zone_fit") if isinstance(item, dict) else None
        if isinstance(fit, dict):
            return fit
    return {}


async def build(fixture: Fixture, monkeypatch: Any) -> dict[str, Any]:
    from custom_components.vtherm_smartpi.smartpi.const import SmartPIPhase  # noqa: PLC0415
    from sim.runner import Simulation  # noqa: PLC0415

    spec, weather = _spec(fixture)
    sim = Simulation(monkeypatch, spec, weather)
    learned_at = None
    while sim.clock.elapsed_s / 3600.0 < MAX_H:
        await sim.run_for(STEP_H)
        published = not fixture.zone or sim.algo.dependency_diagnostics()["second_source"]["zone_fit"]["published"]
        if learned_at is None and sim.algo.phase == SmartPIPhase.STABLE and published:
            learned_at = sim.clock.elapsed_s / 3600.0
        elapsed_h = sim.clock.elapsed_s / 3600.0
        if learned_at is not None and elapsed_h >= max(learned_at + AFTER_LEARNED_H, MIN_H):
            state = json.loads(json.dumps(sim.algo.save_state()))
            print(f"{fixture.name}: learned at {learned_at:.0f} h, a {sim.algo.est.a:.4f}, b {sim.algo.est.b:.5f}")
            return state
    raise SystemExit(f"{fixture.name}: SmartPI did not learn the room in {MAX_H:.0f} h")


def main(argv: list[str]) -> int:
    import pytest  # noqa: PLC0415 — the simulator patches time through pytest's monkeypatch

    parser = argparse.ArgumentParser(description="the learned states seed_state loads")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    failed = 0
    for fixture in FIXTURE_SET:
        path = FIXTURES / f"{fixture.name}.json"
        if args.check:
            missing = learned(fixture, json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else ["no file"]
            if missing:
                print(f"{path.name}: {', '.join(missing)}")
                failed += 1
            continue
        if fixture.kept:
            continue
        with pytest.MonkeyPatch.context() as monkeypatch:
            state = asyncio.run(build(fixture, monkeypatch))
        path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
