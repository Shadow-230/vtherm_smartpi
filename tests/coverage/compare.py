"""Compare a coverage report with the baseline kept next to this script.

Measure first (documentation/en/testing.md, section 7), then, from the repository's root:

    python tests/coverage/compare.py <scratch>/cov.json
    python tests/coverage/compare.py <scratch>/cov.json --markdown   # the tables of section 7

- Every file of the plugin keeps its coverage: a file whose lines or branches fall below the
  baseline (by more than 0.05 point) is listed under "drops".
- The fork's own lines (``git diff -U0 6a131df HEAD``, the ``+`` lines; a branch counts as the
  fork's when its line does) reach 95 % of the lines and 90 % of the branches.

The exit code is 1 when a file drops or a target is missed. When the change is merged, its report
becomes the new baseline (``tests/coverage/baseline.json``).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
PLUGIN = "custom_components/vtherm_smartpi"
FORK_BASE = "6a131df"  # the author's code the fork started from
LINE_TARGET = 95.0
BRANCH_TARGET = 90.0
TOLERANCE = 0.05


def _ratio(covered: int, total: int) -> float | None:
    return 100.0 * covered / total if total else None


def _lines(summary: dict) -> tuple[int, int]:
    return summary["covered_lines"], summary["num_statements"]


def _branches(summary: dict) -> tuple[int, int]:
    return summary.get("covered_branches", 0), summary.get("num_branches", 0)


def _short(name: str) -> str:
    return name[len(PLUGIN) + 1 :] if name.startswith(PLUGIN + "/") else name


def file_drops(base: dict, new: dict) -> list[tuple[str, str, float, float]]:
    """Files whose lines or branches fall below the baseline."""
    drops = []
    for name, report in sorted(new.items()):
        if name not in base:
            continue
        for kind, part in (("lines", _lines), ("branches", _branches)):
            before = _ratio(*part(base[name]["summary"]))
            after = _ratio(*part(report["summary"]))
            if before is not None and after is not None and after < before - TOLERANCE:
                drops.append((name, kind, before, after))
    return drops


def fork_lines(checkout: pathlib.Path) -> dict[str, set[int]]:
    """The lines the fork added or changed, per file."""
    diff = subprocess.run(
        ["git", "-C", str(checkout), "diff", "-U0", FORK_BASE, "HEAD", "--", PLUGIN],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    own: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current:
                own.setdefault(current, set())
            continue
        match = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if match and current:
            start, count = int(match.group(1)), int(match.group(2) or 1)
            own[current].update(range(start, start + count))
    return own


def fork_coverage(new: dict, own: dict[str, set[int]]) -> dict[str, dict]:
    """Covered and total lines and branches of the fork's own lines, with the gaps, per file."""
    result = {}
    for name, lines in sorted(own.items()):
        report = new.get(name)
        if report is None:
            continue
        executed = set(report["executed_lines"])
        missing = set(report["missing_lines"])
        statements = (executed | missing) & lines
        covered_branches = sum(1 for a, _ in report.get("executed_branches", []) if a in lines)
        missing_branches = [(a, b) for a, b in report.get("missing_branches", []) if a in lines]
        if not statements and not covered_branches and not missing_branches:
            continue
        gaps = [str(n) for n in sorted(statements & missing)]
        # A branch into a line already listed as uncovered adds nothing to the list.
        gaps += [f"{a}→{'exit' if b < 0 else b}" for a, b in missing_branches if b not in missing]
        result[name] = {
            "lines": (len(statements & executed), len(statements)),
            "branches": (covered_branches, covered_branches + len(missing_branches)),
            "gaps": gaps,
        }
    return result


def _cell(covered: int, total: int) -> str:
    ratio = _ratio(covered, total)
    return "—" if ratio is None else f"{covered}/{total} ({ratio:.1f} %)"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} %"


def plugin_table(base: dict, new: dict, base_totals: dict, new_totals: dict) -> list[str]:
    """The table of section 7.1: every file, baseline → now."""
    rows = [
        "| File | Lines (baseline → now) | Branches (baseline → now) | Combined (baseline → now) |",
        "|---|---:|---:|---:|",
    ]
    for name, report in sorted(new.items()):
        summary = report["summary"]
        old = base.get(name, {}).get("summary")
        cells = []
        for part in (_lines, _branches):
            before = "new" if old is None else _pct(_ratio(*part(old)))
            cells.append(f"{before} → {_cell(*part(summary))}")
        before = "new" if old is None else _pct(old["percent_covered"])
        cells.append(f"{before} → {_pct(summary['percent_covered'])}")
        rows.append(f"| `{_short(name)}` | " + " | ".join(cells) + " |")
    totals = [
        f"{_pct(_ratio(*part(base_totals)))} → {_cell(*part(new_totals))}" for part in (_lines, _branches)
    ]
    combined = f"{_pct(base_totals['percent_covered'])} → {_pct(new_totals['percent_covered'])}"
    rows.append("| **Total** | " + " | ".join(totals) + f" | {combined} |")
    return rows


def fork_table(fork: dict[str, dict]) -> list[str]:
    """The table of section 7.2: the fork's files not at 100 %, then the others."""
    rows = ["| File | Lines | Branches | Uncovered |", "|---|---:|---:|---|"]
    full = []
    for name, item in fork.items():
        if item["gaps"]:
            rows.append(
                f"| `{_short(name)}` | {_cell(*item['lines'])} | {_cell(*item['branches'])} | "
                + ", ".join(item["gaps"])
                + " |"
            )
        else:
            full.append(f"`{_short(name)}`")
    lines = [sum(i["lines"][k] for i in fork.values()) for k in (0, 1)]
    branches = [sum(i["branches"][k] for i in fork.values()) for k in (0, 1)]
    rows.append(f"| **Total** | {_cell(*lines)} | {_cell(*branches)} | |")
    rows.append("")
    rows.append("The other files of the fork are at 100 %: " + ", ".join(full) + ".")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=pathlib.Path, help="the new coverage.py JSON report")
    parser.add_argument("--baseline", type=pathlib.Path, default=HERE / "baseline.json")
    parser.add_argument("--checkout", type=pathlib.Path, default=HERE.parent.parent)
    parser.add_argument("--markdown", action="store_true", help="print the tables of section 7")
    args = parser.parse_args(argv)

    base_report = json.loads(args.baseline.read_text(encoding="utf-8"))
    new_report = json.loads(args.report.read_text(encoding="utf-8"))
    base, new = base_report["files"], new_report["files"]
    fork = fork_coverage(new, fork_lines(args.checkout))

    if args.markdown:
        print("\n".join(plugin_table(base, new, base_report["totals"], new_report["totals"])))
        print()
        print("\n".join(fork_table(fork)))
        return 0

    drops = file_drops(base, new)
    print("drops:", "none" if not drops else "")
    for name, kind, before, after in drops:
        print(f"  {_short(name)} {kind}: {before:.1f} % → {after:.1f} %")

    lines = [sum(i["lines"][k] for i in fork.values()) for k in (0, 1)]
    branches = [sum(i["branches"][k] for i in fork.values()) for k in (0, 1)]
    line_ratio = _ratio(*lines) or 100.0
    branch_ratio = _ratio(*branches) or 100.0
    print(f"fork's lines {_cell(*lines)}, branches {_cell(*branches)}")
    for name, item in fork.items():
        if item["gaps"]:
            print(f"  gaps {_short(name)}: " + ", ".join(item["gaps"]))
    print(
        f"total {base_report['totals']['percent_covered']:.2f} % → "
        f"{new_report['totals']['percent_covered']:.2f} %"
    )
    missed = line_ratio < LINE_TARGET or branch_ratio < BRANCH_TARGET
    if missed:
        print(f"targets missed: {LINE_TARGET:.0f} % of the lines, {BRANCH_TARGET:.0f} % of the branches")
    return 1 if drops or missed else 0


if __name__ == "__main__":
    sys.exit(main())
