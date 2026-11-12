"""Write the helper's learned SmartPI states of the rooms West and East, and of North as a radiator behind a relay (custom_components/smartpi_test_helper/states/<name>.json).

    python tests/qas/learned_state.py           # write the files
    python tests/qas/learned_state.py --check   # exit 1 when a file is not what this gives

SmartPI's state with the room's true model learned, from the test rooms' own values, not learned
in a run: ``a`` the radiator's gain (with the boiler on), ``b`` the room's loss, the heating and
cooling dead times the radiator's delay plus its lag. The two times SmartPI saves are fixed (the
start of learning; the thermal twin's save time 0, which SmartPI takes as a long stop and
resynchronises), so a file changes only with its room's model. ``tests/test_qas_files.py`` checks
that each is up to date and loads into SmartPI as its room's learned model.

One variant, ``west_settled.json``, has West's response lag published as well (its mean residence
time, the radiator's delay plus its lag, from three agreeing estimates) and heating periods of two
hours: SmartPI's settling time of a heating is then three lags (BP29). Another, ``north_relay.json``,
is North of the role boiler-relay, a radiator behind a relay (BP34, BP35).
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
for path in (HERE, HERE.parents[1]):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pack import RELAY_NORTH, Room, room  # noqa: E402

ROOMS = (room("West"), room("East"))
STATES = HERE / "custom_components" / "smartpi_test_helper" / "states"
LEARNING_START = "2026-01-01T00:00:00"


SETTLED = "west_settled"  # West with its response lag published (BP29)
RELAY = "north_relay"  # North of boiler-relay, a radiator behind a relay (BP34, BP35)
SETTLED_HEATING_S = 2 * 3600.0  # its heating periods: half their median is longer than three lags
LAG_UNCERTAINTY_S = 10.0  # each estimate's standard deviation


def file(r: Room) -> pathlib.Path:
    return STATES / f"{r.key}.json"


def settled_file() -> pathlib.Path:
    return STATES / f"{SETTLED}.json"


def relay_file() -> pathlib.Path:
    return STATES / f"{RELAY}.json"


def loaded() -> list[tuple[Room, pathlib.Path]]:
    """Each room's learned model and its file, as the helper loads it."""
    return [(r, file(r)) for r in ROOMS] + [(RELAY_NORTH, relay_file())]


def lag_s(r: Room) -> float:
    """The room's response lag: its mean residence time, the radiator's delay plus its lag."""
    return 60.0 * (r.delay_min + r.lag_min)


def model(r: Room) -> dict[str, float]:
    return {"a": r.gain, "b": r.loss, "deadtime_s": 60.0 * (r.delay_min + r.lag_min)}


def state(r: Room, settled: bool = False) -> dict[str, Any]:
    """SmartPI's saved state with the room's model learned; ``settled``: its response lag
    published too."""
    from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: PLC0415
    from custom_components.vtherm_smartpi.smartpi.const import AB_HISTORY_SIZE  # noqa: PLC0415

    values = model(r)
    algo = SmartPI(hass=MagicMock(), cycle_min=5, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS")
    est = algo.est
    est.a, est.b = values["a"], values["b"]
    est.model_hvac_mode = "heat"
    est.a_meas_hist.extend([values["a"]] * AB_HISTORY_SIZE)
    est.b_meas_hist.extend([values["b"]] * AB_HISTORY_SIZE)
    est.learn_ok_count_a = est.learn_ok_count_b = AB_HISTORY_SIZE
    est.learn_ok_count = 2 * AB_HISTORY_SIZE
    dt_est = algo.dt_est
    dt_est.model_hvac_mode = "heat"
    for channel in ("heat", "cool"):
        setattr(dt_est, f"deadtime_{channel}_s", values["deadtime_s"])
        setattr(dt_est, f"deadtime_{channel}_reliable", True)
        getattr(dt_est, f"_history_{channel}").extend([values["deadtime_s"]] * 6)
    if settled:
        algo.learn_win.response_lag.load_state(
            {
                "estimates": [[lag_s(r), LAG_UNCERTAINTY_S]] * 3,
                "durations": [SETTLED_HEATING_S] * 3,
                "published": lag_s(r),
                "episodes": 3,
                "hvac_mode": "heat",
            }
        )
    saved = json.loads(json.dumps(algo.save_state()))
    saved["lw_state"]["learning_start_date"] = LEARNING_START
    saved["twin_state"]["saved_at"] = 0.0
    return saved


def text(r: Room, settled: bool = False) -> str:
    return json.dumps(state(r, settled), indent=1, sort_keys=True) + "\n"


def files() -> list[tuple[pathlib.Path, str]]:
    """Every file and the text it must hold."""
    return [(path, text(r)) for r, path in loaded()] + [(settled_file(), text(room("West"), settled=True))]


def main(argv: list[str]) -> int:
    if "--check" in argv:
        stale = [path.name for path, content in files() if not path.is_file() or path.read_text(encoding="utf-8") != content]
        if stale:
            print("not up to date:", ", ".join(stale))
            return 1
        return 0
    STATES.mkdir(parents=True, exist_ok=True)
    for r, path in loaded():
        path.write_text(text(r), encoding="utf-8")
        print(f"{path.name}: {model(r)}")
    settled_file().write_text(text(room("West"), settled=True), encoding="utf-8")
    print(f"{settled_file().name}: {model(room('West'))}, response lag {lag_s(room('West')):.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
