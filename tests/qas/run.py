"""Run QAS scenarios on one prepared test instance, on the bench's lib/harness.py.

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M     # the role's minimum set
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE --valve-delay 2 BP18      # valves reporting after 2 s
    python <stage>/tests/qas/run.py --list [--markdown]             # the catalogue

``--role`` is the role the instance was prepared as (prepare.py). The harness runs each scenario
from the role's base, SmartPI's defaults entry with no option changed, and reports a scenario NOT
RUN on an instance in another state. Before and after each scenario the instance goes back to the
start state (``steps.Bench.base``); then the scenario's learning start and North's setpoint are
set, and its trace runs while it does (``steps.py``). ``--valve-delay``: the test rooms' valves
report a new opening that long after it is set, in every scenario (0 s by default).
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

from instance import bench_lib  # noqa: E402
from pack import ROLES, SMARTPI, SMARTPI_DEFAULTS  # noqa: E402
from scenarios import SCENARIOS, Scenario  # noqa: E402
from steps import CONTEXT, Bench, Trace  # noqa: E402


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


def duration(seconds: float) -> str:
    if seconds < 60.0:
        return "< 1 min"
    minutes = round(seconds / 60.0)
    return f"{minutes} min" if minutes < 120 else f"{round(seconds / 3600.0, 1):g} h"


def span(values: list[float]) -> str:
    """A duration over a scenario's roles: one value, or the shortest to the longest."""
    low, high = duration(min(values)), duration(max(values))
    return low if low == high else f"{low} – {high}"


def catalogue() -> str:
    """The catalogue's rows of README.md: the start state → the event → the expected result."""
    rows = []
    for s in SCENARIOS.values():
        roles = "all" if len(s.roles) == len(ROLES) else ", ".join(f"`{name}`" for name in s.roles)
        given, event, expected = s.parts
        rows.append(
            f"| {s.id} | {given} | {event} | {expected} | {roles} | {s.start} | "
            f"{span([w.typical_s for w in s.times])} | {span([w.maximum_s for w in s.times])} | {s.depends or '—'} | "
            f"{'yes' if s.detached else 'no'} | {'yes' if s.minimum else 'no'} | {s.counterpart} |"
        )
    return "\n".join(rows)


def listing() -> str:
    """The plan of the runs: each scenario with its typical and maximum time on each of its roles."""
    lines = []
    for s in SCENARIOS.values():
        lines.append(f"{s.id:4} {'M' if s.minimum else '-'} {s.start:7} {'detached' if s.detached else '-':8} {s.checks}")
        for name in s.roles:
            w = s.time_for(ROLES[name])
            lines.append(f"     {name:20} typical {duration(w.typical_s):>8}, maximum {duration(w.maximum_s):>8}")
    return "\n".join(lines)


def runner(s: Scenario):
    """The scenario on the harness: its learning start and North's setpoint, its trace, its run."""

    async def run(r: Any) -> None:
        CONTEXT.scenario = s.id
        b = await Bench.open(r)
        if not await b.start(s.start, s.north):
            r.check(False, "the start state was not reached: the scenario did not run")
            return
        if s.trace:
            b.trace = Trace(b, s.id)
            b.trace.start()
        try:
            await s.run(b)
        finally:
            if b.trace is not None:
                await b.trace.stop()

    run.__doc__ = s.run.__doc__
    return run


async def base(r: Any) -> None:
    """The start state (``steps.Bench.base``), before and after each scenario."""
    await (await Bench.open(r)).base()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--valve-delay", type=float, default=0.0, metavar="S")
    args = parser.parse_args(argv)
    if args.list:
        if args.markdown:
            print(catalogue())
        else:
            print(listing())
        return 0
    if args.role is None:
        raise SystemExit("name the role the instance was prepared as: --role ROLE")
    ids = selected(args.ids, args.role)
    if not ids:
        raise SystemExit("name the scenarios to run, or M for the minimum set")
    bench_lib()
    from harness import Pack  # noqa: PLC0415 — the bench's harness
    from harness import main as harness_main  # noqa: PLC0415

    CONTEXT.role = ROLES[args.role]
    CONTEXT.valve_delay_s = args.valve_delay
    pack = Pack(domain=SMARTPI, base={args.role: {SMARTPI_DEFAULTS: {}}}, multi_entry=True)
    asyncio.run(harness_main(pack, {sid: runner(SCENARIOS[sid]) for sid in ids}, ids, reset=base, role=args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
