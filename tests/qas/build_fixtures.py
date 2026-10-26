"""Write the helpers' learned SmartPI states (custom_components/smartpi_qas_helpers/fixtures/).

    python tests/qas/build_fixtures.py

One fixture per simulated room of the roles, named ``learned_<room>``: SmartPI's state with the
room's true model learned, from the simulator's own values (not learned in a run):

- ``a``: the heater's gain; a water radiator at the boiler's full water, where its power factor
  is 1;
- ``b``: the room's loss to the outdoors;
- the dead times, heating and cooling: the heater's delay plus its emitter's lag.

A scenario that needs a learned SmartPI loads one (the helpers' ``seed_state``) instead of
waiting days for SmartPI to learn. ``tests/test_qas_pack.py`` checks that each fixture loads into
SmartPI as a learned model of its room.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
for path in (HERE, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from roles import ROLES  # noqa: E402

FIXTURES = HERE / "custom_components" / "smartpi_qas_helpers" / "fixtures"


def rooms() -> dict[str, dict[str, float]]:
    """Each simulated room of the roles with its heater: a, b and the dead time in seconds."""
    out: dict[str, dict[str, float]] = {}
    for role in ROLES.values():
        sim = role.sim
        for name, heater in sim["heaters"].items():
            room = heater["room"]
            out[room] = {
                "a": float(heater["a"]),
                "b": float(sim["rooms"][room]["b"]),
                "deadtime_s": 60.0 * (float(heater.get("delay_min", 0.0)) + float(heater.get("lag_min", 0.0))),
            }
    return out


def learned_state(a: float, b: float, deadtime_s: float) -> dict[str, Any]:
    """SmartPI's saved state with this model learned."""
    from custom_components.vtherm_smartpi.algo import SmartPI  # noqa: PLC0415
    from custom_components.vtherm_smartpi.smartpi.const import AB_HISTORY_SIZE  # noqa: PLC0415

    algo = SmartPI(hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="QAS")
    est = algo.est
    est.a, est.b = a, b
    est.model_hvac_mode = "heat"
    for history, value in ((est.a_meas_hist, a), (est.b_meas_hist, b)):
        history.clear()
        history.extend([value] * AB_HISTORY_SIZE)
    est.learn_ok_count_a = est.learn_ok_count_b = AB_HISTORY_SIZE
    est.learn_ok_count = 2 * AB_HISTORY_SIZE
    dt_est = algo.dt_est
    for channel in ("heat", "cool"):
        setattr(dt_est, f"deadtime_{channel}_s", deadtime_s)
        setattr(dt_est, f"deadtime_{channel}_reliable", True)
        history = getattr(dt_est, f"_history_{channel}")
        history.clear()
        history.extend([deadtime_s] * 6)
    dt_est.model_hvac_mode = "heat"
    return json.loads(json.dumps(algo.save_state()))


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for room, model in rooms().items():
        state = learned_state(model["a"], model["b"], model["deadtime_s"])
        path = FIXTURES / f"learned_{room}.json"
        path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{path.name}: a {model['a']}, b {model['b']}, dead time {model['deadtime_s']:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
