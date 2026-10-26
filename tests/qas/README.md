# QAS test pack — SmartPI on a real Home Assistant

The pack the bench (`ha-testbed`, its `docs/test-pack.md`) uses to test SmartPI on test Home
Assistant instances: a room simulator, a test helper, one configuration per instance role, the
setup through the API, the runner and the scenarios. It tests SmartPI only. Nothing here is named
`test_*.py`: the CI does not collect it. Its in-process checks are `tests/test_qas_pack.py`,
`tests/test_qas_room_sim.py` and, locally (`SMARTPI_QAS_LOCAL=1`), `tests/test_qas_setup_flows.py`.

## Contents

1. [Running the pack](#1-running-the-pack)
2. [What is in it](#2-what-is-in-it)
3. [The roles](#3-the-roles)
4. [The catalogue](#4-the-catalogue)
5. [Known points](#5-known-points)

## 1. Running the pack

From the bench's directory, on one commit of `qas` staged at `stage/vtherm_smartpi` and
Versatile Thermostat's release tag staged at `stage/versatile_thermostat`. Which instance runs
which role, and when, is the testing session's choice: a role names no instance.

1. Deploy instance N with its role's configuration:

       bin/deploy.sh --instance N --stage stage/vtherm_smartpi \
           --components stage/vtherm_smartpi/tests/qas/custom_components \
           --components stage/versatile_thermostat/custom_components \
           --config stage/vtherm_smartpi/tests/qas/config/<role>.yaml

2. Set it up as that role through the API; it then restarts:

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/setup/setup_role.py --role ROLE

3. Run the role's minimum set, or named scenarios:

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE M
       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE QS0

   `python stage/vtherm_smartpi/tests/qas/run.py --list` shows the scenarios and their roles.

The runner finds the bench's `lib/` in `TESTBED_ROOT` or the current directory. After a change to
`roles.py`, run `python tests/qas/config/build.py` (CI checks the files are up to date), and after
a change to a simulated room, `python tests/qas/build_fixtures.py`.

## 2. What is in it

| Part | Where | What |
|---|---|---|
| Room simulator | `custom_components/smartpi_room_sim` | rooms, heaters (switches), valves, water radiators that heat only while the boiler's switch is on, the outdoors; services to set or ramp a room, move the outdoors and reset |
| Test helper | `custom_components/smartpi_qas_helpers` | `seed_state`: a thermostat's SmartPI state replaced by a learned one from `fixtures/` |
| Learned states | `custom_components/smartpi_qas_helpers/fixtures`, `build_fixtures.py` | one per simulated room: its true `a`, `b` and dead time, from the simulator's values (not learned in a run) |
| Roles | `roles.py`, `config/build.py`, `config/<role>.yaml` | what each instance holds, and its `configuration.yaml` |
| Setup | `setup/setup_role.py`, `setup/stages.py`, `setup/flows.py` | Versatile Thermostat's central configuration and thermostats, SmartPI's defaults entry, VTherm's central boiler thresholds and power manager inputs, through the API |
| Runner | `run.py`, `bench.py`, `scenarios/` | the scenarios on the bench's `lib/harness.py`, from each role's base |

SmartPI runs with its "SmartPI defaults" entry only: every thermostat takes its settings. Every
role logs SmartPI at info, so its learning pause is in the log as well as in its diagnostics.

## 3. The roles

| Role | Rooms and thermostats | Versatile Thermostat |
|---|---|---|
| `base` | one room, an electric heater (`Living`, switch) | no central boiler |
| `boiler-count` | two rooms with water radiators behind valves (`Lounge`, `Bedroom`), counted by the central boiler; one room with an electric heater (`Office`, switch), not counted | central boiler, threshold of 2 active devices |
| `boiler-power` | as `boiler-count`, devices of 2 kW | central boiler, power threshold of 2.5 kW; power manager in kW (house power 0.5 kW, maximum 50 kW) |

The central boiler switches the simulator's boiler: the radiators get heat only while it is on.

## 4. The catalogue

`M`: in the minimum set, run after any change. Duration in real time. Detached: longer than the
bench's two-hour limit for a background command. In-process counterpart: the test under `tests/`
that checks the same without a real Home Assistant.

<!-- catalogue: written from scenarios/ by `python tests/qas/run.py --list --markdown` -->
| ID | Checks | Roles | Duration | Detached | M | In-process counterpart |
|---|---|---|---|---|---|---|
| QS0 | After the setup's restart every SmartPI and VTherm entry is loaded, neither logs a warning or an error, the only SmartPI entry is its defaults, and each thermostat has SmartPI's diagnostics with the user's learning setting on and the learning pause below the central boiler threshold published. | all | 5 min | no | yes | test_qas_setup_flows.py, test_published_diagnostics_schema.py |
<!-- end of catalogue -->

## 5. Known points

- Home Assistant shows SmartPI 0.0.0: the author's repository keeps that version, and only a
  release writes its own. Every result records the commit it tested.
- SmartPI's defaults entry shows the diagnostics of a thermostat created after it once it is set
  up again: the setup ends with a restart for that.
- The pack tests SmartPI only: there is no role with Smart Boiler.
