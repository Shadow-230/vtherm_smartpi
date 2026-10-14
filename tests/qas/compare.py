"""Compare what two instances kept: QB1 (1 against 7) and QE5 (3 against 4).

    python <stage>/tests/qas/compare.py qb1  [RESULTS_DIR]
    python <stage>/tests/qas/compare.py qb1s [RESULTS_DIR]
    python <stage>/tests/qas/compare.py qe5  [RESULTS_DIR]
    python <stage>/tests/qas/compare.py qe5s [RESULTS_DIR]

RESULTS_DIR is the bench's results/vtherm_smartpi/ (the default, from the bench's directory).
Exit status 0: passed; 1: failed; 2: a measurement is missing.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

PAIRS = {"qb1": (1, 7), "qb1s": (1, 7), "qe5": (3, 4), "qe5s": (3, 4)}


def load(directory: pathlib.Path, name: str, instance: int) -> dict[str, Any] | None:
    path = directory / f"{name}-instance{instance}.json"
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
    if not argv or argv[0] not in PAIRS:
        print(__doc__)
        return 2
    name = argv[0]
    directory = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path("results/vtherm_smartpi")
    first, second = PAIRS[name]
    new, ref = load(directory, name, first), load(directory, name, second)
    if new is None or ref is None:
        print(f"{name}: missing {name}-instance{first if new is None else second}.json in {directory}")
        return 2
    checks = judge(name, new, ref)
    for ok, what in checks:
        print(("ok   " if ok else "FAIL ") + what)
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
