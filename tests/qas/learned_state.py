"""Write the helper's learned SmartPI state of the room West (custom_components/smartpi_test_helper/states/west.json).

    python tests/qas/learned_state.py           # write the file
    python tests/qas/learned_state.py --check   # exit 1 when the file is not what this gives

SmartPI's state with the room's true model learned, from the test rooms' own values, not learned
in a run: ``a`` the radiator's gain (with the boiler on), ``b`` the room's loss, the heating and
cooling dead times the radiator's delay plus its lag. The two times SmartPI saves are fixed (the
start of learning; the thermal twin's save time 0, which SmartPI takes as a long stop and
resynchronises), so the file changes only with the model. ``tests/test_qas_files.py`` checks that
it is up to date and loads into SmartPI as the room's learned model.
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

from pack import room  # noqa: E402

ROOM = room("West")
FILE = HERE / "custom_components" / "smartpi_test_helper" / "states" / f"{ROOM.key}.json"
LEARNING_START = "2026-01-01T00:00:00"


def model() -> dict[str, float]:
    return {"a": ROOM.gain, "b": ROOM.loss, "deadtime_s": 60.0 * (ROOM.delay_min + ROOM.lag_min)}


def state() -> dict[str, Any]:
    """SmartPI's saved state with the room's model learned."""
    from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: PLC0415
    from custom_components.vtherm_smartpi.smartpi.const import AB_HISTORY_SIZE  # noqa: PLC0415

    values = model()
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
    saved = json.loads(json.dumps(algo.save_state()))
    saved["lw_state"]["learning_start_date"] = LEARNING_START
    saved["twin_state"]["saved_at"] = 0.0
    return saved


def text() -> str:
    return json.dumps(state(), indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    if "--check" in argv:
        if not FILE.is_file() or FILE.read_text(encoding="utf-8") != text():
            print(f"not up to date: {FILE.name}")
            return 1
        return 0
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(text(), encoding="utf-8")
    print(f"{FILE.name}: {model()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
