"""Set up one test instance through the API, from its role (tests/qas/roles.py).

Run by the bench's testing session, from the bench's directory, after a deploy:

    HA_INSTANCE=N python <stage>/tests/qas/setup/setup_role.py --role ROLE [--stage STAGE] [--no-restart]

- ``--role``: the role to set up on instance N (required): the testing session chooses which
  instance runs which role.
- ``--stage``: one stage only (``vtherm``, ``inputs``); by default both, in that order. Each stage
  skips what is already there.
- At the end Home Assistant restarts (unless ``--no-restart``): SmartPI's defaults entry then
  shows the diagnostics of the thermostats created after it, and the scenarios read a log that
  starts after the setup.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bench import BenchApi, bench_lib, instance  # noqa: E402
from roles import ROLES  # noqa: E402
from stages import SMARTPI, STAGES, run_stages  # noqa: E402


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--stage", choices=STAGES, default=None)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args(argv)
    role = ROLES[args.role]

    bench_lib()
    from ha_client import HA  # noqa: PLC0415 — reads the bench's local.env

    async with HA(SMARTPI) as ha:
        api = BenchApi(ha)
        print(f"setup of instance {instance()} as {role.name} ({role.title})", flush=True)
        await run_stages(api, role, only=args.stage)
        if not args.no_restart and args.stage is None:
            took = await api.restart()
            print(f"Home Assistant restarted in {took:.0f} s", flush=True)
    print("setup done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
