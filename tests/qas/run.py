"""Run QAS scenarios on one prepared test instance, on the bench's lib/harness.py.

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M     # the role's minimum set
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role boiler-count --author-release BP0 BR1
    python <stage>/tests/qas/run.py --list [--markdown]             # the catalogue
    python <stage>/tests/qas/run.py --compare QAS.json AUTHOR.json  # BR1 on both versions

``--role`` is the role the instance was prepared as (prepare.py). ``--author-release``: the
instance runs the author's release 0.5.2 (c75d803); only the scenarios that run there are
allowed. The harness runs each scenario from the role's base, SmartPI's defaults entry with no
option changed, and reports a scenario NOT RUN on an instance in another state. Before and after
each scenario every thermostat goes back to heating at its base setpoint (``scenarios.base``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from instance import bench_lib  # noqa: E402
from pack import ROLES, SMARTPI, SMARTPI_DEFAULTS  # noqa: E402
from scenarios import CONTEXT, SCENARIOS, base, reference_truth  # noqa: E402


def selected(ids: list[str], role: str, author_release: bool) -> list[str]:
    out: list[str] = []
    for sid in ids:
        if sid == "M":
            out += [s.id for s in SCENARIOS.values() if s.minimum and role in s.roles and (s.author_release or not author_release)]
        elif sid not in SCENARIOS:
            raise SystemExit(f"no scenario {sid}: see --list")
        elif role not in SCENARIOS[sid].roles:
            raise SystemExit(f"{sid} runs on {', '.join(SCENARIOS[sid].roles)}, not on {role}")
        elif author_release and not SCENARIOS[sid].author_release:
            raise SystemExit(f"{sid} does not run on the author's 0.5.2")
        else:
            out.append(sid)
    return out


def duration(seconds: float) -> str:
    if seconds < 60.0:
        return "< 1 min"
    minutes = round(seconds / 60.0)
    return f"{minutes} min" if minutes < 120 else f"{round(seconds / 3600.0, 1):g} h"


def catalogue() -> str:
    """The catalogue's rows of README.md."""
    rows = []
    for s in SCENARIOS.values():
        roles = "both" if len(s.roles) == len(ROLES) else ", ".join(f"`{name}`" for name in s.roles)
        rows.append(
            f"| {s.id} | {s.checks} | {roles} | {duration(s.time.typical_s)} | {duration(s.time.maximum_s)} | "
            f"{s.depends or '—'} | {'yes' if s.detached else 'no'} | {'yes' if s.minimum else 'no'} | "
            f"{'yes' if s.author_release else 'no'} | {s.counterpart} |"
        )
    return "\n".join(rows)


def _ratio(value: Any, true: float) -> str:
    return f"{value} ({value / true:.2f})" if isinstance(value, (int, float)) and true else str(value)


def compare(qas_file: str, author_file: str) -> str:
    """BR1 on this version and on the author's 0.5.2: West's a, b and dead times at the end and
    their extremes, against the room's true values."""
    runs = {name: json.loads(pathlib.Path(path).read_text(encoding="utf-8")) for name, path in (("qas", qas_file), ("author 0.5.2", author_file))}
    truth = reference_truth()
    rows = [
        "| Value | True | " + " | ".join(f"{name}: end (ratio) | {name}: min–max" for name in runs) + " |",
        "|---|---|" + "---|---|" * len(runs),
    ]
    for key, true, label in (
        ("a", truth["a"], "a"),
        ("b", truth["b"], "b"),
        ("deadtime_heat_s", truth["deadtime_delay_lag_s"], "heating dead time, s"),
        ("deadtime_cool_s", truth["deadtime_delay_lag_s"], "cooling dead time, s"),
    ):
        cells = []
        for run in runs.values():
            values = [s[key] for s in run["samples"] if isinstance(s.get(key), (int, float))]
            end = run["samples"][-1].get(key) if run["samples"] else None
            cells += [_ratio(end, true), f"{min(values)}–{max(values)}" if values else "—"]
        rows.append(f"| {label} | {true} | " + " | ".join(cells) + " |")
    rows.append(
        f"\nDead times: true between {truth['deadtime_delay_s']:.0f} s (the radiator's delay) and "
        f"{truth['deadtime_delay_lag_s']:.0f} s (delay plus lag, the learned start); ratios against the latter."
    )
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--author-release", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--compare", nargs=2, metavar=("QAS_JSON", "AUTHOR_JSON"))
    args = parser.parse_args(argv)
    if args.compare:
        print(compare(*args.compare))
        return 0
    if args.list:
        if args.markdown:
            print(catalogue())
        else:
            for s in SCENARIOS.values():
                print(
                    f"{s.id:4} {'M' if s.minimum else '-'} {duration(s.time.typical_s):>7} / {duration(s.time.maximum_s):>7}"
                    f"  {','.join(s.roles):26} {s.checks}"
                )
        return 0
    if args.role is None:
        raise SystemExit("name the role the instance was prepared as: --role ROLE")
    ids = selected(args.ids, args.role, args.author_release)
    if not ids:
        raise SystemExit("name the scenarios to run, or M for the minimum set")
    bench_lib()
    from harness import Pack  # noqa: PLC0415 — the bench's harness
    from harness import main as harness_main  # noqa: PLC0415

    CONTEXT.author_release = args.author_release
    pack = Pack(domain=SMARTPI, base={args.role: {SMARTPI_DEFAULTS: {}}}, multi_entry=True)
    asyncio.run(harness_main(pack, {sid: SCENARIOS[sid].run for sid in ids}, ids, reset=base, role=args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
