"""The QAS runner: the scenarios on one test instance, on the bench's lib/harness.py.

From the bench's directory (its docs/workflow.md), after ``setup/setup_role.py``:

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M   # the minimum set of the role
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE L   # the learned set (after M)
    python <stage>/tests/qas/run.py --list [--role ROLE]          # the catalogue; no instance needed

- ``--role``: the role instance N was set up as (required to run): the testing session chooses
  which instance runs which role.
- Each scenario runs from the role's base: the SmartPI entries the role has, by title, with their
  options (the heating type; the neighbours and the zone compared by entity, mode, doors, window
  action, master and rule — not by the random ids the forms give; the water and boiler sources and
  the entities they read). A scenario on an instance in another state is NOT RUN (the bench's
  harness).
- Before each scenario the runner reads the phase of the role's SmartPI thermostats: a scenario
  that needs a fresh SmartPI (still learning, as after the setup) or a learned one (as after QL0)
  and does not find it is NOT RUN, with the reason, in the results file; the harness is not
  called for it.
- Between scenarios the simulator's inputs and the thermostats go back to the base
  (``scenarios.common.reset``).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import pathlib
import sys
import types
from collections.abc import Mapping
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bench import bench_lib  # noqa: E402
from roles import ROLES, SMARTPI_DEFAULTS_TITLE, Role, supply_sensor  # noqa: E402
from scenarios import PLANNED, SCENARIOS  # noqa: E402
from scenarios.common import CONTEXT, MARGIN_S, phase_problem, reset  # noqa: E402
from stages import BOILER_SIGNALS, CALL_SOURCE, SMARTPI, SUPPLY_ENTITY, WATER_SOURCE  # noqa: E402


class Dependencies:
    """The neighbours and the zone an entry should store, compared without the forms' ids.

    ``harness.base_drift`` compares ``stored != expected``: a stored list is compared through
    this object's ``__eq__``, by what each item means.
    """

    def __init__(self, expected: list[tuple], names: Mapping[str, str]) -> None:
        self.expected = sorted(expected)
        self.names = names

    def normal(self, stored: Any) -> list[tuple]:
        out = []
        for item in stored or []:
            entity = self.names.get(item.get("entity"), item.get("entity"))
            if item.get("kind") == "neighbour":
                doors = tuple(sorted(self.names.get(d, d) for d in item.get("door_sensors") or []))
                out.append(("neighbour", entity, item.get("mode"), doors, item.get("neighbour_window_action", "nothing")))
            elif item.get("kind") == "source":
                master = self.names.get(item.get("zone_master"), item.get("zone_master"))
                out.append(("source", entity, master, item.get("split_rule"), item.get("lead_band_percent")))
        return sorted(out)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, list) and self.normal(other) == self.expected

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return repr(self.expected)


class Entity:
    """An entity an entry should store, compared by its entity id: a form stores a registered
    entity by its registry id."""

    def __init__(self, entity_id: str, names: Mapping[str, str]) -> None:
        self.entity_id = entity_id
        self.names = names

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and self.names.get(other, other) == self.entity_id

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return repr(self.entity_id)


def expected_dependencies(role: Role, title: str) -> list[tuple]:
    items: list[tuple] = [
        ("neighbour", n.entity, n.mode, tuple(sorted(n.door_sensors)), n.window_action)
        for n in role.neighbours.get(title, ())
    ]
    if role.zone is not None and role.zone_entry == title:
        zone = role.zone
        items.append(("source", zone.additional, zone.master, zone.rule, zone.parameter))
    return items


async def build_base(role: Role) -> dict[str, dict[str, Any]]:
    """The role's base: every SmartPI entry it has, by title, with the options it expects."""
    from ha_client import HA  # noqa: PLC0415 — the bench's client

    async with HA(SMARTPI) as ha:
        registry = await ha.ws_cmd({"type": "config/entity_registry/list"})
    names = {e["id"]: e["entity_id"] for e in registry if e.get("id")}
    base: dict[str, dict[str, Any]] = {SMARTPI_DEFAULTS_TITLE: {}}
    if role.boiler_signals:
        base[SMARTPI_DEFAULTS_TITLE] = {key: Entity(entity_id, names) for key, entity_id in BOILER_SIGNALS.items()}
    for thermostat in role.thermostats:
        if not thermostat.smartpi:
            continue
        options: dict[str, Any] = {}
        if role.plugin == "qas" and thermostat.heating_type is not None:
            options["smart_pi_heating_type"] = thermostat.heating_type
        expected = expected_dependencies(role, thermostat.name)
        if expected or role.plugin == "qas":
            options["smart_pi_dependencies"] = Dependencies(expected, names)
        if thermostat.water_source is not None:
            options[WATER_SOURCE] = thermostat.water_source
        if thermostat.water == "entities":
            options[SUPPLY_ENTITY] = Entity(supply_sensor(thermostat.heater), names)
        additional = role.zone is not None and role.zone.additional == thermostat.climate
        if thermostat.call_source is not None and not additional:
            options[CALL_SOURCE] = thermostat.call_source
        base[thermostat.name] = options
    return base


def _learned_order(ids: list[str]) -> list[str]:
    """The learned set's order: what loads the models first (it changes the state but needs no
    phase), then the scenarios that keep the state, then those that change it."""
    def rank(sid: str) -> int:
        s = SCENARIOS[sid]
        if s.phase.need == "any":
            return 0
        return 1 if s.state.keeps else 2

    return sorted(ids, key=rank)


def expand(ids: list[str], role: Role) -> list[str]:
    out: list[str] = []
    for sid in ids:
        if sid == "M":
            out += [s.id for s in SCENARIOS.values() if s.minimum and role.name in s.roles]
        elif sid == "L":
            out += _learned_order([s.id for s in SCENARIOS.values() if s.learned_set and role.name in s.roles])
        elif sid in SCENARIOS:
            if role.name not in SCENARIOS[sid].roles:
                raise SystemExit(f"{sid} is not a scenario of role {role.name} ({', '.join(SCENARIOS[sid].roles)})")
            out.append(sid)
        else:
            raise SystemExit(f"no scenario {sid}; see --list")
    return out


def listing(role: Role | None) -> str:
    rows = []
    for s in SCENARIOS.values():
        if role is not None and role.name not in s.roles:
            continue
        flags = s.run_set.replace("—", "-") + ("D" if s.detached else "-")
        rows.append(
            f"{s.id:5} {flags} {s.minutes:5} min  {s.state.label:42} {s.phase.label:20} {','.join(s.roles):40}  {s.doc}"
        )
    return "\n".join(rows)


def _duration(minutes: int) -> str:
    return f"{minutes} min" if minutes < 120 else f"{minutes / 60:g} h"


def catalogue() -> str:
    """The catalogue's rows (documentation/en/testing.md, the QAS section), from the registry."""
    rows = []
    for s in SCENARIOS.values():
        where = ", ".join(f"`{name}`" for name in s.roles)
        if len(s.roles) == len(ROLES):
            where = "all"
        short = f" Short variant of {', '.join(s.short_of)}." if s.short_of else ""
        rows.append(
            f"| {s.id} | {s.doc}{short} | {where} | {_duration(s.minutes)} | "
            f"{'yes' if s.detached else 'no'} | {s.twin} | {s.run_set} | {s.state.label} | {s.phase.label} | {s.set} |"
        )
    for p in PLANNED:
        rows.append(
            f"| {p.id} | {p.what[0].upper() + p.what[1:]} (planned, not built) | — | — | — | {p.twin} | — | — | — | {p.set} |"
        )
    return "\n".join(rows)


async def not_run(pack: Any, sid: str, reason: str) -> str:
    """A scenario NOT RUN for its phase, written to the results file as the harness writes one."""
    from harness import results_file, tested_commit  # noqa: PLC0415

    out = results_file(pack)
    out.parent.mkdir(parents=True, exist_ok=True)
    commit = await asyncio.to_thread(tested_commit)
    now = dt.datetime.now(dt.UTC)
    text = (
        f"\n### {sid} — NOT RUN ({now:%H:%M}–{now:%H:%M} UTC)\n{SCENARIOS[sid].doc}\n"
        f"-      commit {commit}\n- NOT RUN — SmartPI is not in the phase the scenario needs: {reason}\n"
    )
    with out.open("a", encoding="utf-8") as f:
        f.write(text)
    print(text, flush=True)
    return text


async def run_all(pack: Any, ids: list[str], role: Role) -> None:
    """Each scenario on its own: its phase checked first (NOT RUN when it is not met), then the
    harness runs it from the role's base."""
    from ha_client import HA  # noqa: PLC0415 — the bench's client
    from harness import main as harness_main  # noqa: PLC0415

    for sid in ids:
        scenario = SCENARIOS[sid]
        reason = None
        if scenario.phase.need != "any":
            async with HA(SMARTPI) as ha:
                reason = await phase_problem(types.SimpleNamespace(ha=ha), scenario, role, within_s=MARGIN_S)
        if reason:
            await not_run(pack, sid, reason)
            continue
        await harness_main(pack, {sid: scenario.fn}, [sid], reset=reset, role=role.name)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="QAS scenarios on one test instance, as its role")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--markdown", action="store_true", help="with --list: the catalogue's rows")
    args = parser.parse_args(argv)
    if args.list:
        print(catalogue() if args.markdown else listing(ROLES[args.role] if args.role else None))
        return 0
    if args.role is None:
        raise SystemExit("name the role this instance was set up as: --role ROLE")
    role = ROLES[args.role]
    ids = expand(args.ids, role)
    if not ids:
        raise SystemExit("name the scenarios to run (or M for the minimum set, L for the learned set)")
    bench_lib()
    from harness import Pack  # noqa: PLC0415

    CONTEXT.role = role
    base = asyncio.run(build_base(role))
    pack = Pack(domain=SMARTPI, base={role.name: base}, multi_entry=True)
    asyncio.run(run_all(pack, ids, role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
