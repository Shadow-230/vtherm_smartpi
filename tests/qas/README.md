# QAS test pack — SmartPI on a test Home Assistant

The pack the bench (`ha-testbed`, its `docs/test-pack.md`) uses to test SmartPI on test Home
Assistant instances. It tests SmartPI's learning pause below Versatile Thermostat's central
boiler threshold (`documentation/en/technical_doc.md`, 3.7). Nothing here is named `test_*.py`:
CI does not collect it. Its checks without the bench are `tests/test_qas_files.py`,
`tests/test_qas_rooms.py` and, locally (`SMARTPI_QAS_LOCAL=1`), `tests/test_qas_instance.py`.

## Contents

1. [Running the pack](#1-running-the-pack)
2. [What is in it](#2-what-is-in-it)
3. [The rooms and the roles](#3-the-rooms-and-the-roles)
4. [The catalogue](#4-the-catalogue)
5. [The reference run on the author's 0.5.2](#5-the-reference-run-on-the-authors-052)
6. [Known points](#6-known-points)

## 1. Running the pack

From the bench's directory, on one commit of `qas` staged at `stage/vtherm_smartpi` and Versatile
Thermostat's release tag staged at `stage/versatile_thermostat`. Which instance runs which role
is the testing session's choice.

1. Deploy instance N with its role's configuration:

       bin/deploy.sh --instance N --stage stage/vtherm_smartpi \
           --components stage/vtherm_smartpi/tests/qas/custom_components \
           --components stage/versatile_thermostat/custom_components \
           --config stage/vtherm_smartpi/tests/qas/config/<role>.yaml

2. Prepare it as that role through the API; it then restarts:

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/prepare.py --role ROLE

3. Run the role's minimum set, or named scenarios:

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE M
       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE BP4 BP5

The scripts find the bench's `lib/` in `TESTBED_ROOT` or the current directory. After a change to
`pack.py`, run `python tests/qas/configuration.py` and `python tests/qas/learned_state.py`; CI
checks both files are up to date.

## 2. What is in it

| File | What |
|---|---|
| `pack.py` | the rooms and the roles |
| `configuration.py`, `config/<role>.yaml` | each role's `configuration.yaml`: the bench's clean instance (`default_config:`), the test rooms, the test helper, SmartPI's log at info |
| `custom_components/smartpi_test_rooms` | the simulated rooms (below) |
| `custom_components/smartpi_test_helper` | `load_learned_state`: a thermostat's SmartPI state replaced by the learned one of its room (`states/`), for BP7 |
| `learned_state.py` | writes `states/west.json` from the room's own values |
| `instance.py` | one instance through Home Assistant's API, and the config flows driven step by step |
| `prepare.py` | prepares an instance as a role |
| `scenarios.py`, `run.py` | the scenarios, and the runner on the bench's harness |

**The test rooms** (`smartpi_test_rooms`). Each room is one thermal mass:
`dT/dt = gain * p - loss * (T - T_out)`, the model SmartPI learns. `p` is the heater's output,
after its delay and through its lag. A water radiator gets heat only while the boiler's switch is
on. The outdoor temperature moves by ±0.2 °C around 3 °C, so its sensor never holds one value
for hours. Entities: each room's temperature sensor (0.1 °C, written when it changes), its valve
or heater switch, the outdoor temperature sensor and the boiler's switch. One tick every 7 s.

## 3. The rooms and the roles

| Room | Heater | VTherm's central boiler | Thermostat |
|---|---|---|---|
| West | water radiator, valve | counted | `climate.west` |
| East | water radiator, valve | counted | `climate.east` |
| North | electric heater, switch | not counted | `climate.north` |

| Role | Central boiler threshold |
|---|---|
| `boiler-count` | 2 active devices |
| `boiler-power` | 2500 W (each device 2000 W) |

`prepare.py` sets in Versatile Thermostat only what these rooms need; everything else keeps
VTherm's defaults (a cycle of 5 minutes, for example):
- central configuration: the outdoor sensor; the central boiler, switching the test rooms' boiler;
  a safety delay of 1440 minutes, because a test room's sensor writes only when its reading
  changes and VTherm's 60 minutes would take a steady room for a dead sensor;
- each thermostat: its room's sensor, its device and the device's power (2000 W), whether the
  central boiler counts it, SmartPI; power management unticked (VTherm ticks it for a device with
  a power, and warns without a power manager); for a valve, VTherm's opening filters off (0 % and
  0 minutes), as SmartPI's repairs advise;
- the role's threshold.

SmartPI runs with its "SmartPI defaults" entry only.

## 4. The catalogue

Times are in real time, the sum of each scenario's waits (`scenarios.py`, `Wait`); every wait ends
as soon as its condition holds:
- **typical**: what SmartPI's timing gives (it calculates every 60 s): two calculations for a
  pause, one for a resume, plus the steps' own holds. Judge a run against it;
- **maximum**: the sum of the waits' limits, reached only if SmartPI is late. Plan detached runs
  and reminders from it;
- **time depends on**: what changes a scenario's time beyond SmartPI's timing.

M: in the minimum set, run after any change. Detached: a maximum over the bench's two-hour limit
for a background command. On 0.5.2: runs on the author's release too (section 5).

<!-- catalogue: written by `python tests/qas/run.py --list --markdown` -->
| ID | Checks | Roles | Typical | Maximum | Time depends on | Detached | M | On 0.5.2 | Counterpart without the bench |
|---|---|---|---|---|---|---|---|---|---|
| BP0 | After the preparation's restart every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published (not on the author's 0.5.2, which has no such field). | both | < 1 min | 15 min | — | no | yes | yes | test_qas_instance.py, test_published_diagnostics_schema.py |
| BP1 | West calls alone below VTherm's threshold of 2 devices: it pauses its learning, VTherm's boiler stays off, East (not calling) and North (calling, not counted) do not pause, and West's command goes on. | `boiler-count` | 5 min | 12 min | — | no | yes | no | test_boiler_call.py, test_qas_instance.py |
| BP2 | After West paused alone, East calls too: the threshold is reached, VTherm's boiler starts and West resumes its learning. | `boiler-count` | 3 min | 18 min | — | no | yes | no | test_boiler_call.py, test_qas_instance.py |
| BP3 | After West paused alone, it stops calling: it resumes its learning though the threshold is still not reached. | `boiler-count` | 3 min | 18 min | — | no | yes | no | test_boiler_call.py |
| BP4 | With VTherm's active-device and power sensors disabled, the threshold cannot be read: West calling alone (command above 0) does not pause; the sensors are enabled again and read. | `boiler-count` | 10 min | 15 min | — | no | no | no | test_boiler_call.py |
| BP5 | With the user's learning paused (set_smartpi_learning off), West's boiler pause starts and ends while learning.enabled stays off; the user's setting is put back. | `boiler-count` | 3 min | 22 min | — | no | no | no | test_boiler_call.py |
| BP6 | With VTherm's power threshold (2500 W, devices of 2000 W): West calling alone pauses while the boiler stays off, East calling too resumes it, and West alone again resumes when it stops calling. | `boiler-power` | 9 min | 38 min | — | no | no | no | test_boiler_call.py |
| BP7 | With West's learned state loaded, a forced calibration's heating step below the threshold pauses learning: the dead-time detection stays idle and the learned heating dead time unchanged; once East calls and the boiler starts, learning resumes and the next heating starts a new dead-time window, its episode starting after the resume. | `boiler-count` | 16 min | 97 min | typical when West starts below the calibration's low threshold (setpoint - 0.3 degC); above it, the calibration first cools the room, up to 60 min more | no | no | no | test_boiler_call.py |
| BP8 | West and East start calling together and VTherm's boiler starts: neither pauses its learning, though VTherm counts their devices a moment after SmartPI first reads the threshold. | both | 6 min | 14 min | — | no | yes | no | test_boiler_call.py, test_qas_instance.py |
| BR1 | Reference run, the same on this version and on the author's 0.5.2: West starts from its room's true model, then for 24 hours repeats 40 minutes cooling, 40 minutes calling alone with the boiler off, and 40 minutes heating with East and the boiler on; every 10 minutes its a, b and dead times are kept (BR1-<version>-instance<N>.json in the results), to compare both versions with the room's true values (run.py --compare). | `boiler-count` | 24 h | 24 h | — | yes | no | yes | test_boiler_call.py |
<!-- end of catalogue -->

## 5. The reference run on the author's 0.5.2

BR1 runs the same case on this version and on the author's release 0.5.2 (`c75d803`, tag 0.5.2,
in `qas`'s history), which has no learning pause: West starts from its room's true model, then for
24 hours repeats three phases of 40 minutes:
1. both West and East cooling (no call);
2. West calling alone, below the threshold: VTherm's boiler stays off, the radiator gets no heat.
   Its power step comes while the boiler is off, so a SmartPI without the pause can take the wait
   for the boiler as the room's dead time;
3. West and East heating: the boiler is on.

Every 10 minutes West's `a`, `b`, dead times, learning counters, `learning.boiler_paused`, the
boiler and the room are kept in `BR1-<version>-instance<N>.json`, next to the results.
`run.py --compare QAS.json AUTHOR.json` then gives, for both versions, `a`, `b` and the dead times
at the end and their extremes, against West's true values (`pack.py`): `a` 0.04, `b` 0.001, a dead
time between 180 s (the radiator's delay) and 780 s (delay plus lag, the learned start).

On 0.5.2: deploy the author's checkout as the plugin, the pack from `qas` as usual
(`--components stage/vtherm_smartpi/tests/qas/custom_components`), prepare the instance as
`boiler-count`, and run with `--author-release`: only BP0 and BR1 run there, and BP0 does not look
for `learning.boiler_paused`, which 0.5.2 does not publish. Both runs start from freshly prepared
instances.

## 6. Known points

- Home Assistant shows SmartPI 0.0.0: the repository keeps that version, only a release writes
  its own. Every result records the commit it tested.
- SmartPI's defaults entry shows the diagnostics of a thermostat created after it once it is set
  up again: the preparation ends with a restart.
- BP4 disables VTherm's active-device and power sensors for a while; Home Assistant reloads
  VTherm's central entry when they are disabled and again when they are enabled.
- BP7 loads West's learned state; West keeps it after the scenario.
