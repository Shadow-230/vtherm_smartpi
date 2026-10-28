"""Run QAS scenarios on one prepared test instance, on the bench's lib/harness.py.

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M     # the role's minimum set
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role boiler-count --author-release BP0 BR1 BR2
    python <stage>/tests/qas/run.py --list [--markdown]             # the catalogue
    python <stage>/tests/qas/run.py --compare QAS.json AUTHOR.json  # BR1 or BR2 on both versions

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
from scenarios import CHECKPOINT_HOURS, CONTEXT, SCENARIOS, STARTS_FOR_VALIDITY, base, reference_truth  # noqa: E402


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


TOLERANCE = 0.20  # the decision rule: an error within 20 % of the true value


def _at(samples: list[dict[str, Any]], hours: float) -> dict[str, Any] | None:
    """The last sample kept at or before ``hours``."""
    kept = [s for s in samples if s.get("hours", 0.0) <= hours + 0.01]
    return kept[-1] if kept else None


def _error(value: Any, true: float) -> float | None:
    return value / true - 1.0 if isinstance(value, (int, float)) and true else None


def _cell(sample: dict[str, Any] | None, key: str, true: float) -> str:
    value = (sample or {}).get(key)
    error = _error(value, true)
    return "—" if error is None else f"{value:g} ({error:+.0%})"


def compare(qas_file: str, author_file: str) -> str:
    """BR1 or BR2 on this version and on the author's 0.5.2: West's heating dead time at 48 and
    96 hours against the room's true value (delay plus lag), whether the comparison is valid, and,
    for BR1, the decision rule; a and b only as auxiliary readings."""
    runs = {name: json.loads(pathlib.Path(path).read_text(encoding="utf-8")) for name, path in (("qas", qas_file), ("author 0.5.2", author_file))}
    sids = {run.get("scenario") for run in runs.values()}
    if len(sids) != 1:
        raise SystemExit(f"not the same run: {sorted(map(str, sids))}")
    sid = sids.pop()
    truth = reference_truth()
    true_dt = truth["deadtime_delay_lag_s"]
    lines = [
        f"{sid}: West's heating dead time against the true {true_dt:.0f} s (delay plus lag; the delay alone is {truth['deadtime_delay_s']:.0f} s)",
        "",
        "| Value | " + " | ".join(runs) + " |",
        "|---|" + "---|" * len(runs),
    ]
    for hours in CHECKPOINT_HOURS:
        at = {name: _at(run["samples"], hours) for name, run in runs.items()}
        lines.append(f"| heating dead time, s, at {hours} h | " + " | ".join(
            _cell(s, "deadtime_heat_s", true_dt) + ("" if (s or {}).get("deadtime_heat_reliable") else ", not reliable") for s in at.values()
        ) + " |")
        for key, label in (("a", "a"), ("b", "b")):
            lines.append(f"| {label} at {hours} h (auxiliary) | " + " | ".join(
                f"{_cell(s, key, truth[key])}, {(s or {}).get(f'accepted_updates_{key}')} updates" for s in at.values()
            ) + " |")
    lines.append("| West's heating starts, boiler off / on | " + " | ".join(
        f"{run.get('starts', {}).get('boiler_off')} / {run.get('starts', {}).get('boiler_on')}" for run in runs.values()
    ) + " |")
    lines.append("")
    if sid != "BR1":
        lines.append(f"{sid} measures a limit of the pause: shown, not judged by the rule.")
        return "\n".join(lines)
    reasons = []
    for name, run in runs.items():
        last = run["samples"][-1] if run["samples"] else {}
        if not last.get("deadtime_heat_reliable"):
            reasons.append(f"{name}: no reliable heating dead time at the end")
        if (run.get("starts", {}).get("boiler_off") or 0) < STARTS_FOR_VALIDITY:
            reasons.append(f"{name}: fewer than {STARTS_FOR_VALIDITY} heating starts with the boiler off")
    if reasons:
        lines.append("Not valid: " + "; ".join(reasons) + ".")
        return "\n".join(lines)
    lines.append(f"Valid: both heating dead times reliable, at least {STARTS_FOR_VALIDITY} heating starts with the boiler off in each run.")
    for hours in CHECKPOINT_HOURS:
        qas, author = (_error((_at(run["samples"], hours) or {}).get("deadtime_heat_s"), true_dt) for run in runs.values())
        if qas is None or author is None:
            lines.append(f"At {hours} h: a dead time is missing.")
            continue
        lines.append(
            f"At {hours} h: qas's error {qas:+.0%}, 0.5.2's {author:+.0%}; qas's error not larger: "
            f"{'yes' if abs(qas) <= abs(author) else 'no'}; qas within {TOLERANCE:.0%}: {'yes' if abs(qas) <= TOLERANCE else 'no'}."
        )
    return "\n".join(lines)


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
