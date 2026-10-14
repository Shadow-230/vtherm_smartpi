"""The QAS runner: the scenarios on one test instance, on the bench's lib/harness.py.

From the bench's directory (its docs/workflow.md), after ``setup/setup_role.py``:

    HA_INSTANCE=N python <stage>/tests/qas/run.py [--role ROLE] ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py M        # the minimum set of the instance's role
    python <stage>/tests/qas/run.py --list [--role ROLE]   # the catalogue; no instance needed

- ``--role``: by default the role the catalogue gives instance N (``roles.INSTANCE_ROLES``).
- Each scenario runs from the role's base: the SmartPI entries the role has, by title, with their
  options (the heating type; the neighbours and the zone compared by entity, mode, doors, window
  action, master and rule — not by the random ids the forms give). A scenario on an instance in
  another state is NOT RUN (the bench's harness).
- Between scenarios the simulator's inputs and the thermostats go back to the base
  (``scenarios.common.reset``).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bench import bench_lib, instance  # noqa: E402
from roles import INSTANCE_ROLES, ROLES, SMARTPI_DEFAULTS_TITLE, Role  # noqa: E402
from scenarios import PLANNED, SCENARIOS  # noqa: E402
from scenarios.common import CONTEXT, reset  # noqa: E402
from stages import SMARTPI  # noqa: E402


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
    for thermostat in role.thermostats:
        if not thermostat.smartpi:
            continue
        options: dict[str, Any] = {}
        if role.plugin == "qas" and thermostat.heating_type is not None:
            options["smart_pi_heating_type"] = thermostat.heating_type
        expected = expected_dependencies(role, thermostat.name)
        if expected or role.plugin == "qas":
            options["smart_pi_dependencies"] = Dependencies(expected, names)
        base[thermostat.name] = options
    return base


def expand(ids: list[str], role: Role) -> list[str]:
    out: list[str] = []
    for sid in ids:
        if sid == "M":
            out += [s.id for s in SCENARIOS.values() if s.minimum and role.name in s.roles]
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
        flags = ("M" if s.minimum else "-") + ("D" if s.detached else "-")
        rows.append(f"{s.id:5} {flags} {s.minutes:5} min  {','.join(s.roles):40}  {s.doc}")
    return "\n".join(rows)


def _duration(minutes: int) -> str:
    return f"{minutes} min" if minutes < 120 else f"{minutes / 60:g} h"


def catalogue() -> str:
    """The catalogue's rows (documentation/en/testing.md, the QAS section), from the registry."""
    instances = {role.name: role.instance for role in ROLES.values()}
    rows = []
    for s in SCENARIOS.values():
        where = ", ".join(f"{instances[name]} `{name}`" for name in s.roles)
        if len(s.roles) == len(ROLES):
            where = "all"
        short = f" Short variant of {', '.join(s.short_of)}." if s.short_of else ""
        rows.append(
            f"| {s.id} | {s.doc}{short} | {where} | {_duration(s.minutes)} | "
            f"{'yes' if s.detached else 'no'} | {s.twin} | {'yes' if s.minimum else 'no'} | {s.set} |"
        )
    for p in PLANNED:
        rows.append(f"| {p.id} | {p.what[0].upper() + p.what[1:]} (planned, not built) | — | — | — | {p.twin} | — | {p.set} |")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="QAS scenarios on one test instance")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--markdown", action="store_true", help="with --list: the catalogue's rows")
    args = parser.parse_args(argv)
    if args.list:
        print(catalogue() if args.markdown else listing(ROLES[args.role] if args.role else None))
        return 0
    role = ROLES[args.role or INSTANCE_ROLES[instance()]]
    ids = expand(args.ids, role)
    if not ids:
        raise SystemExit("name the scenarios to run (or M for the minimum set)")
    bench_lib()
    from harness import Pack  # noqa: PLC0415
    from harness import main as harness_main  # noqa: PLC0415

    CONTEXT.role = role
    base = asyncio.run(build_base(role))
    pack = Pack(domain=SMARTPI, base={role.name: base}, multi_entry=True)
    asyncio.run(harness_main(pack, {sid: SCENARIOS[sid].fn for sid in ids}, ids, reset=reset, role=role.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
