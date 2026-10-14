"""Compare what two instances kept: QB1 (the role base against ref-0.4.0) and QE5 (the role zone
against zone-ref).

    python <stage>/tests/qas/compare.py qb1  NEW REF [RESULTS_DIR]
    python <stage>/tests/qas/compare.py qb1s NEW REF [RESULTS_DIR]
    python <stage>/tests/qas/compare.py qe5  NEW REF [RESULTS_DIR]
    python <stage>/tests/qas/compare.py qe5s NEW REF [RESULTS_DIR]

NEW and REF are the two results files, or the numbers of the instances that kept them (then
``RESULTS_DIR/<name>-instance<N>.json``): for QB1 NEW ran the role ``base`` and REF ``ref-0.4.0``;
for QE5 NEW ran ``zone`` and REF ``zone-ref``. Which instance ran which role is the testing
session's choice. RESULTS_DIR is the bench's results/vtherm_smartpi/ (the default, from the bench's
directory). Exit status 0: passed; 1: failed; 2: a measurement is missing.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

NAMES = ("qb1", "qb1s", "qe5", "qe5s")


def results_path(directory: pathlib.Path, name: str, which: str) -> pathlib.Path:
    """A results file named on the command line, or the one instance ``which`` kept."""
    if which.isdigit():
        return directory / f"{name}-instance{which}.json"
    return pathlib.Path(which)


def load(path: pathlib.Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def judge(name: str, new: dict[str, Any], ref: dict[str, Any]) -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []
    err_new, err_ref = new.get("mean_error_second_half"), ref.get("mean_error_second_half")
    if name == "qb1":
        for key in ("a", "b"):
            a, b = new.get(key), ref.get(key)
            ok = a is not None and b is not None and b != 0 and abs(a / b - 1.0) <= 0.10
            checks.append((ok, f"{key}: qas {a}, 0.4.0 {b} (within ±10 %)"))
        ok = err_new is not None and err_ref is not None and err_new <= err_ref + 0.03
        checks.append((ok, f"mean error 24–48 h: qas {err_new}, 0.4.0 {err_ref} (no more than +0.03 °C)"))
    elif name == "qe5":
        ok = err_new is not None and err_ref is not None and err_new <= err_ref + 0.05
        checks.append((ok, f"mean error 24–48 h: zone {err_new}, independent pair {err_ref} (no more than +0.05 °C)"))
        share = new.get("floor_share")
        checks.append((share is not None and share > 0.20, f"the floor's share of the heat: {share} (over 20 %)"))
        published = new.get("fit_published_h")
        checks.append((published is not None and published <= 30.0, f"the zone's model published after {published} h (before about 30 h)"))
        checks.append((bool(new.get("fit_published_after_restart")), "the model kept across the restart at 36 h"))
    else:  # the short variants: both instances ran and measured
        checks.append((err_new is not None and err_ref is not None, f"both measured: {err_new}, {err_ref}"))
    return checks


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] not in NAMES:
        print(__doc__)
        return 2
    name = argv[0]
    directory = pathlib.Path(argv[3]) if len(argv) > 3 else pathlib.Path("results/vtherm_smartpi")
    new_path, ref_path = results_path(directory, name, argv[1]), results_path(directory, name, argv[2])
    new, ref = load(new_path), load(ref_path)
    if new is None or ref is None:
        print(f"{name}: missing {new_path if new is None else ref_path}")
        return 2
    checks = judge(name, new, ref)
    for ok, what in checks:
        print(("ok   " if ok else "FAIL ") + what)
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
