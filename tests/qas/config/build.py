"""Write config/<role>.yaml, each role's configuration.yaml, from roles.py.

    python tests/qas/config/build.py           # write the files
    python tests/qas/config/build.py --check   # exit 1 when a file is not what roles.py gives

The bench deploys one of these files as an instance's configuration.yaml (``--config``). No
``default_config``: nothing scans the network. No ``initial:`` on an input a run sets: a restart
would put it back (the bench's docs/lessons.md).
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from roles import MAX_POWER_INPUT, PROVIDER, ROLES, SIM, Role  # noqa: E402


def configuration(role: Role) -> dict[str, Any]:
    config: dict[str, Any] = {
        "homeassistant": {"name": role.title},
        "frontend": None,
        "config": None,
        "api": None,
        "history": None,
        "logbook": None,
        "system_log": {"max_entries": 500},
        "recorder": {"purge_keep_days": 7},
        "logger": {
            "default": "info",
            "logs": {
                "custom_components.vtherm_smartpi": "info",
                "custom_components.versatile_thermostat": "info",
                f"custom_components.{SIM}": "info",
                "custom_components.smartpi_qas_helpers": "info",
            },
        },
        "smartpi_qas_helpers": (
            {"provider": {"name": PROVIDER, "circuits": role.provider_circuits}} if role.provider else None
        ),
        SIM: role.sim,
    }
    if role.power_manager:
        config["input_number"] = {
            MAX_POWER_INPUT.split(".", 1)[1]: {
                "name": "QAS max power",
                "min": 0,
                "max": 30000,
                "step": 100,
                "unit_of_measurement": "W",
                "mode": "box",
            }
        }
    return config


def render(role: Role) -> str:
    header = (
        f"# QAS role {role.name!r}: {role.title}.\n"
        "# Written by tests/qas/config/build.py from tests/qas/roles.py; do not edit by hand.\n"
    )
    body = yaml.safe_dump(configuration(role), sort_keys=False, default_flow_style=False)
    return header + body.replace(": null\n", ":\n")


def main(argv: list[str]) -> int:
    stale = []
    for role in ROLES.values():
        path = HERE / f"{role.name}.yaml"
        text = render(role)
        if "--check" in argv:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print("not up to date with roles.py:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
