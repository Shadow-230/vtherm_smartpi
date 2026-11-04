"""Run QAS scenarios on one prepared test instance, on the bench's lib/harness.py.

    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE ID [ID ...]
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE M     # the role's minimum set
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role boiler-count --author-release BP0 BR1 BR2
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role boiler-power --author-release BP14
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE --valve-delay 2 BP18      # valves reporting after 2 s
    HA_INSTANCE=N python <stage>/tests/qas/run.py --role ROLE --repeat 5 --valve-delays 0,2 BP22
    python <stage>/tests/qas/run.py --list [--markdown]             # the catalogue
    python <stage>/tests/qas/run.py --compare QAS.json AUTHOR.json  # BR1 or BR2 on both versions

``--role`` is the role the instance was prepared as (prepare.py). ``--author-release``: the
instance runs the author's release (``pack.AUTHOR_RELEASE``); only the scenarios that run there are
allowed. The harness runs each scenario from the role's base, SmartPI's defaults entry with no
option changed, and reports a scenario NOT RUN on an instance in another state. Before and after
each scenario the instance goes back to the start state (``steps.Bench.base``); then the scenario's
learning start and North's setpoint are set, and its trace runs while it does (``steps.py``).
``--valve-delay``: the test rooms' valves report a new opening that long after it is set, in every
scenario (0 s by default). ``--repeat`` and ``--valve-delays``: BP22-BP24's starts for each
combination and the valve delays they measure.
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
from pack import AUTHOR_RELEASE, ROLES, SMARTPI, SMARTPI_DEFAULTS  # noqa: E402
from scenarios import CHECKPOINT_HOURS, SCENARIOS, STARTS_FOR_VALIDITY, Scenario, reference_truth  # noqa: E402
from steps import CONTEXT, REPEAT, VALVE_DELAYS_S, Bench, Trace  # noqa: E402


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
            raise SystemExit(f"{sid} does not run on the author's {AUTHOR_RELEASE}")
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
            f"{'yes' if s.detached else 'no'} | {'yes' if s.minimum else 'no'} | {'yes' if s.author_release else 'no'} | "
            f"{'the measurement' if s.measured else 'its checks'} | {s.counterpart} |"
        )
    return "\n".join(rows)


def runner(s: Scenario):
    """The scenario on the harness: its learning start and North's setpoint, its trace, its run."""

    async def run(r: Any) -> None:
        CONTEXT.scenario = s.id
        b = await Bench.open(r)
        if not await b.start("fresh" if s.start == "both" else s.start, s.north):
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


TOLERANCE = 0.20  # the decision rule: an error within 20 % of the true value


def _at(samples: list[dict[str, Any]], hours: float) -> dict[str, Any] | None:
    """The last sample kept at or before ``hours``."""
    kept = [s for s in samples if s.get("hours", 0.0) <= hours + 0.01]
    return kept[-1] if kept else None


def _error(value: Any, true: float) -> float | None:
    return value / true - 1.0 if isinstance(value, (int, float)) and true else None


def _when(hours: float, samples: Any) -> str:
    """'at 48 h', or for the last checkpoint 'at the end (95.8 h)': the run's last sample."""
    if hours < CHECKPOINT_HOURS[-1]:
        return f"at {hours:g} h"
    last = max((s.get("hours", 0.0) for s in samples if s), default=None)
    return "at the end" if last is None else f"at the end ({last:.1f} h)"


def _cell(sample: dict[str, Any] | None, key: str, true: float) -> str:
    value = (sample or {}).get(key)
    error = _error(value, true)
    return "—" if error is None else f"{value:g} ({error:+.0%})"


def compare(qas_file: str, author_file: str) -> str:
    """BR1 or BR2 on this version and on the author's release: West's heating dead time at 48 and
    96 hours against the room's true value (delay plus lag), whether the comparison is valid, and,
    for BR1, the decision rule; a and b only as auxiliary readings."""
    runs = {name: json.loads(pathlib.Path(path).read_text(encoding="utf-8")) for name, path in (("qas", qas_file), (f"author {AUTHOR_RELEASE}", author_file))}
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
        when = _when(hours, at.values())
        lines.append(f"| heating dead time, s, {when} | " + " | ".join(
            _cell(s, "deadtime_heat_s", true_dt) + ("" if (s or {}).get("deadtime_heat_reliable") else ", not reliable") for s in at.values()
        ) + " |")
        for key, label in (("a", "a"), ("b", "b")):
            lines.append(f"| {label} {when} (auxiliary) | " + " | ".join(
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
        at = [_at(run["samples"], hours) for run in runs.values()]
        when = _when(hours, at)
        qas, author = (_error((s or {}).get("deadtime_heat_s"), true_dt) for s in at)
        if qas is None or author is None:
            lines.append(f"{when.capitalize()}: a dead time is missing.")
            continue
        lines.append(
            f"{when.capitalize()}: qas's error {qas:+.0%}, {AUTHOR_RELEASE}'s {author:+.0%}; qas's error not larger: "
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
    parser.add_argument("--valve-delay", type=float, default=0.0, metavar="S")
    parser.add_argument("--repeat", type=int, default=REPEAT, metavar="N")
    parser.add_argument("--valve-delays", default=",".join(f"{d:g}" for d in VALVE_DELAYS_S), metavar="S,S")
    args = parser.parse_args(argv)
    if args.compare:
        print(compare(*args.compare))
        return 0
    if args.list:
        if args.markdown:
            print(catalogue())
        else:
            for s in SCENARIOS.values():
                print(f"{s.id:4} {'M' if s.minimum else '-'} {s.start:7} {'detached' if s.detached else '-':8} {s.checks}")
                for name in s.roles:
                    w = s.time_for(ROLES[name])
                    print(f"     {name:20} typical {duration(w.typical_s):>8}, maximum {duration(w.maximum_s):>8}")
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
    CONTEXT.role = ROLES[args.role]
    CONTEXT.valve_delay_s = args.valve_delay
    CONTEXT.repeat = args.repeat
    CONTEXT.valve_delays_s = tuple(float(d) for d in args.valve_delays.split(","))
    pack = Pack(domain=SMARTPI, base={args.role: {SMARTPI_DEFAULTS: {}}}, multi_entry=True)
    asyncio.run(harness_main(pack, {sid: runner(SCENARIOS[sid]) for sid in ids}, ids, reset=base, role=args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
