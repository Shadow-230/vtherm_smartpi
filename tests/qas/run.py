"""The QAS runner: the scenarios on one test instance, on the bench's lib/harness.py.

From the bench's directory (its docs/workflow.md), after ``setup/setup_role.py``:

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M   # the minimum set of the role
    python <stage>/tests/qas/run.py --list [--role ROLE]          # the catalogue; no instance needed

- ``--role``: the role instance N was set up as (required to run): the testing session chooses
  which instance runs which role.
- Each scenario runs from the role's base: SmartPI's defaults entry, its only entry, with no
  option changed. A scenario on an instance in another state is NOT RUN (the bench's harness).
- Before and after each scenario the simulator's outdoors and the thermostats go back to the base
  (``scenarios.common.reset``).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bench import bench_lib  # noqa: E402
from roles import ROLES, Role  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from scenarios.common import CONTEXT, reset  # noqa: E402
from stages import SMARTPI  # noqa: E402


def base(role: Role) -> dict[str, dict[str, Any]]:
    """The role's base: every SmartPI entry it has, by title, with the options it expects (none
    changed: the defaults entry is never saved through its options)."""
    return {title: {} for title in role.smartpi_titles}


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
    """The catalogue's rows (tests/qas/README.md), from the registry."""
    rows = []
    for s in SCENARIOS.values():
        where = "all" if len(s.roles) == len(ROLES) else ", ".join(f"`{name}`" for name in s.roles)
        rows.append(
            f"| {s.id} | {s.doc} | {where} | {_duration(s.minutes)} | "
            f"{'yes' if s.detached else 'no'} | {'yes' if s.minimum else 'no'} | {s.twin} |"
        )
    return "\n".join(rows)


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
        raise SystemExit("name the scenarios to run (or M for the minimum set)")
    bench_lib()
    from harness import Pack  # noqa: PLC0415
    from harness import main as harness_main  # noqa: PLC0415

    CONTEXT.role = role
    pack = Pack(domain=SMARTPI, base={role.name: base(role)}, multi_entry=True)
    asyncio.run(harness_main(pack, {sid: SCENARIOS[sid].fn for sid in ids}, ids, reset=reset, role=role.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
