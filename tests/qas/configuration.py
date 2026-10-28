"""Write config/<role>.yaml, the configuration.yaml of an instance with that role, from pack.py.

    python tests/qas/configuration.py           # write the files
    python tests/qas/configuration.py --check   # exit 1 when a file is not what pack.py gives

A role's configuration is the bench's clean instance (``default_config:``) with the test rooms,
the test helper, SmartPI's log at info, so that its learning pause is in the log too, and West's
window sensor where the role has one (no ``initial:``: a restart keeps its state).
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import yaml

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pack import HELPER_DOMAIN, ROLES, ROOMS, ROOMS_DOMAIN, WEST_WINDOW, Role  # noqa: E402

CONFIG = HERE / "config"


def configuration(role: Role) -> dict[str, Any]:
    rooms = {
        r.key: {
            "heater": r.heater,
            "temperature": r.temperature,
            "loss": r.loss,
            "gain": r.gain,
            "delay_min": r.delay_min,
            "lag_min": r.lag_min,
        }
        for r in ROOMS
    }
    config: dict[str, Any] = {
        "default_config": None,
        "logger": {"default": "warning", "logs": {"custom_components.vtherm_smartpi": "info"}},
        ROOMS_DOMAIN: {"outdoor": 3.0, "rooms": rooms},
        HELPER_DOMAIN: None,
    }
    if role.west_window:
        config["input_boolean"] = {WEST_WINDOW.split(".", 1)[1]: {"name": "West window"}}
    return config


def text(role: Role) -> str:
    header = (
        f"# QAS role {role.name}: {role.title}.\n"
        "# Written by tests/qas/configuration.py from tests/qas/pack.py; do not edit by hand.\n"
    )
    body = yaml.safe_dump(configuration(role), sort_keys=False, default_flow_style=False)
    return header + body.replace(": null\n", ":\n")


def main(argv: list[str]) -> int:
    stale = []
    for role in ROLES.values():
        path = CONFIG / f"{role.name}.yaml"
        if "--check" in argv:
            if not path.is_file() or path.read_text(encoding="utf-8") != text(role):
                stale.append(path.name)
        else:
            path.write_text(text(role), encoding="utf-8")
    if stale:
        print("not up to date:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
