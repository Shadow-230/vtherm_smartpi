"""Linear thermal network for SmartPI simulations (re-exported).

The physics lives in one place, the QAS room simulator's
``tests/qas/custom_components/smartpi_room_sim/physics.py``, which imports nothing from Home
Assistant; the in-process simulator uses the same file. It is loaded by its path, so that the
simulator integration's ``__init__`` (Home Assistant glue) is not imported here.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_NAME = "smartpi_room_sim_physics"
_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "qas"
    / "custom_components"
    / "smartpi_room_sim"
    / "physics.py"
)

if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, _PATH)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
_physics = sys.modules[_NAME]

MAX_STEP_S = _physics.MAX_STEP_S
PLANT_EMITTERS = _physics.PLANT_EMITTERS
SIM_ROOM_REFERENCE_C = _physics.SIM_ROOM_REFERENCE_C
DelayedPower = _physics.DelayedPower
PowerHistory = _physics.PowerHistory
Schedule = _physics.Schedule
ThermalNetwork = _physics.ThermalNetwork
water_factor = _physics.water_factor

__all__ = [
    "MAX_STEP_S",
    "PLANT_EMITTERS",
    "SIM_ROOM_REFERENCE_C",
    "DelayedPower",
    "PowerHistory",
    "Schedule",
    "ThermalNetwork",
    "water_factor",
]
