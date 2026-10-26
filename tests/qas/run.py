"""Run QAS scenarios on one prepared test instance, on the bench's lib/harness.py.

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M     # the role's minimum set
    python <stage>/tests/qas/run.py --list [--markdown]             # the catalogue

``--role`` is the role the instance was prepared as (prepare.py). The harness runs each
scenario from the role's base, SmartPI's defaults entry with no option changed, and reports a
scenario NOT RUN on an instance in another state. Before and after each scenario every thermostat
goes back to heating at its base setpoint (``scenarios.base``).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from instance import bench_lib  # noqa: E402
from pack import ROLES, SMARTPI, SMARTPI_DEFAULTS  # noqa: E402
from scenarios import SCENARIOS, base  # noqa: E402


def selected(ids: list[str], role: str) -> list[str]:
    out: list[str] = []
    for sid in ids:
        if sid == "M":
            out += [s.id for s in SCENARIOS.values() if s.minimum and role in s.roles]
        elif sid not in SCENARIOS:
            raise SystemExit(f"no scenario {sid}: see --list")
        elif role not in SCENARIOS[sid].roles:
            raise SystemExit(f"{sid} runs on {', '.join(SCENARIOS[sid].roles)}, not on {role}")
        else:
            out.append(sid)
    return out


def catalogue() -> str:
    """The catalogue's rows of README.md."""
    rows = []
    for s in SCENARIOS.values():
        roles = "both" if len(s.roles) == len(ROLES) else ", ".join(f"`{name}`" for name in s.roles)
        rows.append(
            f"| {s.id} | {s.checks} | {roles} | {s.minutes} min | {'yes' if s.detached else 'no'} | "
            f"{'yes' if s.minimum else 'no'} | {s.counterpart} |"
        )
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        if args.markdown:
            print(catalogue())
        else:
            for s in SCENARIOS.values():
                print(f"{s.id:4} {'M' if s.minimum else '-'} {s.minutes:3} min  {','.join(s.roles):26} {s.checks}")
        return 0
    if args.role is None:
        raise SystemExit("name the role the instance was prepared as: --role ROLE")
    ids = selected(args.ids, args.role)
    if not ids:
        raise SystemExit("name the scenarios to run, or M for the minimum set")
    bench_lib()
    from harness import Pack  # noqa: PLC0415 — the bench's harness
    from harness import main as harness_main  # noqa: PLC0415

    pack = Pack(domain=SMARTPI, base={args.role: {SMARTPI_DEFAULTS: {}}}, multi_entry=True)
    asyncio.run(harness_main(pack, {sid: SCENARIOS[sid].run for sid in ids}, ids, reset=base, role=args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
