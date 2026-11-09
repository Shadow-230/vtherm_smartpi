# QAS test pack — SmartPI on a test Home Assistant

The pack the bench (`ha-testbed`, its `docs/test-pack.md`) uses to test SmartPI on test Home
Assistant instances. It tests SmartPI's learning pause below Versatile Thermostat's central
boiler threshold (`documentation/en/technical_doc.md`, 3.8). Nothing here is named `test_*.py`:
CI does not collect it. Its checks without the bench are `tests/test_qas_files.py`,
`tests/test_qas_rooms.py` and, locally (`SMARTPI_QAS_LOCAL=1`), `tests/test_qas_instance.py`.

## Contents

1. [Running the pack](#1-running-the-pack)
2. [What is in it](#2-what-is-in-it)
3. [The rooms and the roles](#3-the-rooms-and-the-roles)
4. [The catalogue](#4-the-catalogue)
5. [Known points](#5-known-points)

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

`run.py --list` gives every scenario with its roles, its learning at the start, whether it runs
detached, and its typical and maximum time on each of its roles: the plan of the runs.

`--valve-delay S` makes the test rooms' valves report a new opening S seconds after it is set, in
every scenario run (0 s by default):

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE --valve-delay 2 BP18

The scripts find the bench's `lib/` in `TESTBED_ROOT` or the current directory. After a change to
`pack.py`, run `python tests/qas/configuration.py` and `python tests/qas/learned_state.py`; CI
checks both files are up to date.

## 2. What is in it

| File | What |
|---|---|
| `pack.py` | the rooms and the roles |
| `configuration.py`, `config/<role>.yaml` | each role's `configuration.yaml`: the bench's clean instance (`default_config:`), the test rooms, the test helper, SmartPI's log at info |
| `custom_components/smartpi_test_rooms` | the simulated rooms and their scenario service (below) |
| `custom_components/smartpi_test_helper` | `load_learned_state`: a thermostat's SmartPI state replaced by the learned one of its room (`states/`), for the scenarios that start learned |
| `learned_state.py` | writes `states/west.json` and `states/east.json` from the rooms' own values |
| `instance.py` | one instance through Home Assistant's API, and the config flows driven step by step |
| `prepare.py` | prepares an instance as a role |
| `steps.py` | what the scenarios share: the start state, the steps and their checks, the timing, the trace (section 4) |
| `scenarios.py`, `run.py` | the scenarios, and the runner on the bench's harness |

**The test rooms** (`smartpi_test_rooms`). Each room is one thermal mass:
`dT/dt = gain * p - loss * (T - T_out)`, the model SmartPI learns. `p` is the heater's output,
after its delay and through its lag. A water radiator gets heat only while the boiler's switch is
on. The outdoor temperature moves by ±0.2 °C around 3 °C, so its sensor never holds one value
for hours. Entities: each room's temperature sensor (0.1 °C, written when it changes), its valve
or heater switch, the outdoor temperature sensor and the boiler's switch. One tick every 7 s.
One service changes the rooms in place: `smartpi_test_rooms.set_valve_report_delay` (`seconds`):
a valve takes and reports a new opening only that long after it was set, as a real valve head
moves for seconds before it reports (0 s by default, which reports at once).

## 3. The rooms and the roles

| Room | Heater | VTherm's central boiler | Thermostat |
|---|---|---|---|
| West | water radiator, valve | counted | `climate.west` |
| East | water radiator, valve | counted | `climate.east` |
| North | electric heater, switch | not counted | `climate.north` |

| Role | Central boiler | Devices |
|---|---|---|
| `boiler-count` | threshold of 2 active devices | 2000 W each |
| `boiler-power` | threshold of 2500 W: one valve fully open is below it, two open at 63 % reach it | 2000 W each |
| `boiler-delay` | threshold of 2 active devices, activation delay 120 s | 2000 W each |
| `boiler-levels` | threshold of 600 W: West alone with its valve fully open is below it, East alone or both reach it | West 300 W, East 900 W, North 2000 W |
| `boiler-levels-delay` | as `boiler-levels`, activation delay 120 s | as `boiler-levels` |

In every role West has a window sensor, `input_boolean.west_window`.

`prepare.py` sets in Versatile Thermostat only what these rooms need; everything else keeps
VTherm's defaults (a cycle of 5 minutes, for example):
- central configuration: the outdoor sensor; the central boiler, switching the test rooms' boiler,
  with the role's activation delay; a safety delay of 100 hours, longer than the longest run, because a test room's sensor
  writes only when its reading changes and VTherm's 60 minutes would take a steady room for a dead sensor;
- each thermostat: its room's sensor, its device and the device's power (the role's), whether the
  central boiler counts it, SmartPI; power management unticked (VTherm ticks it for a device with
  a power, and warns without a power manager); for a valve, VTherm's opening filters off (0 % and
  0 minutes), as SmartPI's repairs advise; West's window sensor (VTherm's own window settings:
  West turns off 30 s after the window opens, and back 30 s after it closes);
- the role's threshold.

SmartPI runs with its "SmartPI defaults" entry only.

**What the roles test.** The device count, the power and the activation delay each decide in
their own way when the boiler heats, so the pause's steps (BP1–BP3, BP5–BP11, BP18, BP20) run on
`boiler-count`, `boiler-power` and `boiler-delay`, each with what that role makes of a step: with
the delay, West resumes when the threshold is reached and the boiler starts 120 s later. The
power levels of `boiler-levels` let the power alone decide: West's 300 W alone stays below 600 W,
East's 900 W alone or both reach it (BP13). With the power, VTherm counts each device's power
times its valve opening for the next cycle, so a partly open valve counts as that share of the
device; with the device count, any open valve counts as a whole device.

**A thermostat's own start** (BP25) runs on `boiler-count`: with the device count VTherm counts
West only once its valve reports the new opening, and the valves' report delay of 15 s gives the
time a real valve head takes. (With the power, VTherm counts West's power from SmartPI's command,
not from its valve.)

**The valve opening.** For a valve, SmartPI's command is the valve's opening in %: 0 % closed (the
thermostat does not call), 100 % fully open. The pack calls it the valve opening throughout: in
the trace `opening` (the cycle's, from which SmartPI pauses) and `next_opening` (the next cycle's,
from which VTherm counts the power), next to `valve`, the valve's own state.

## 4. The catalogue

**The start state.** Before and after each scenario the runner puts the instance back in one
state (`steps.Bench.base`): every thermostat heating (HVAC mode heat); West and East not calling
(setpoint 7 °C, valves closed, 0 %); the window closed; the role's threshold and the
user's learning setting as prepared; VTherm's boiler off for longer than the role's activation
delay plus 30 s; no thermostat paused. Then the scenario's start: West's and East's learning
**fresh** (`reset_smartpi_learning`) or **learned** (the helper's `load_learned_state`), and
North at the scenario's setpoint (23 °C, calling, unless it says otherwise). A scenario that
does not reach the start state does not run, and says so.

**The steps** (`steps.py`). Each step changes one thing: West or East calling or not, West off and
on, its window, the threshold, a restart. It waits for what the role makes of it, all at once,
each against its own time, and then checks the same things every time: West's and East's
pause, North never paused, VTherm's boiler and the boiler's switch, West's and East's valve
opening, and VTherm's count and power against the valve openings. A thermostat that starts
calling with the threshold reached is watched for a pause it must not take.

**The trace.** While a scenario runs (all but BP25), every 5 s
one reading of the instance keeps what changed: VTherm's count, power and boiler, the boiler's
switch, the window, and each thermostat's mode, setpoint, valve opening, pause, learning
counters, window outcomes and dead-time detection. It is written to
`traces/<date>-<ID>-<role>-<version>-instance<N>-<time>.json` next to the results, and a short
timeline of the boiler, the switch, the count, the power, the pauses and the valve openings goes into
the results, so that a report needs no history of Home Assistant. Every pause in the trace is
checked: no learning counter and no heating dead time changes while paused, and no dead-time
detection is armed.

**The times** are in real time, the sum of each scenario's waits (`steps.py`, `Wait`), for each of
its roles; every wait ends as soon as its condition holds. SmartPI calculates every 60 s:
- SmartPI pauses once it has called below the threshold without a break for a calculation period
  (60 s, less 1 s for the timer), at its first calculation after that;
- a change of a thermostat's own setpoint or mode makes a calculation at once: from not calling,
  it is below the threshold from that calculation, and its pause comes at the first periodic
  calculation a period later, 60–120 s after the setpoint, 90 s on average, as the setpoint falls
  at any moment of the 60 s; a resume it causes comes at once, but a learned thermostat that stops
  calling resumes at its next calculation (48–56 s on the bench), as its 0 % counts only from then;
- a change from outside (East, the threshold, the boiler) reaches it at its next calculation: it is
  below from then, so a pause comes two calculations later, a resume one;
- a closed window: VTherm's delay of 30 s, then two calculations (150 s on the bench, 11 of 11);
- the start state after the scenario, which the runner reaches before the next one: VTherm's
  boiler off for the role's delay plus 30 s when the scenario ends with the boiler on (150 s with
  the activation delay, and the same in BP0 after the preparation's restart), and a learned West
  paused at the end resumes at its next calculation once it stops calling (BP9).

In the columns:
- **typical**: what SmartPI's timing gives, plus the steps' own holds; one value, or the shortest
  to the longest over the scenario's roles. Judge a run against it;
- **maximum**: the sum of the waits' limits, reached only if SmartPI is late, with up to 9 minutes
  for the start state when the boiler was just on. Plan detached runs and reminders from it;
- **time depends on**: what changes a scenario's time beyond SmartPI's timing.

**A thermostat's own start** (BP25). SmartPI pauses only once it has called below the threshold
for a calculation period (`documentation/en/technical_doc.md`, 3.8). At West's own start, which
completes the threshold, VTherm counts West only once its valve reports the new opening, after
seconds with a real valve head: a check of the pause in that time must not pause. BP25 sets the
valves' report delay to 15 s and starts West 20 times from the start state, with East calling
alone below the threshold. It passes when every start took place and SmartPI's log, read through
the bench as its harness reads a failure's evidence, has no "West - SmartPI learning paused"
line; each start's note gives VTherm's boiler start and any pause seen in the diagnostics.

M: in the minimum set, run after any change. Detached: a maximum over the bench's two-hour limit
for a background command.

<!-- catalogue: written by `python tests/qas/run.py --list --markdown` -->
| ID | Start state | Event | Expected result | Roles | Learning at start | Typical | Maximum | Time depends on | Detached | M | Counterpart without the bench |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BP0 | After the preparation's restart | the instance is read | every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published. | all | fresh | < 1 min – 2 min | 35 min | — | no | yes | test_qas_instance.py, test_published_diagnostics_schema.py |
| BP1 | The start state | West calls alone, below the threshold | West pauses once it has called below the threshold for a calculation period (60 s), not before; VTherm's boiler stays off; East and North (not counted) do not pause; West's valve stays open. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 4 min | 32 min | — | no | yes | test_boiler_call.py, test_qas_instance.py |
| BP2 | West calls alone and pauses | East calls too, the threshold is reached | West resumes at its next calculation, VTherm's boiler starts (with an activation delay, 120 s later), East does not pause. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 5 min – 8 min | 40 min | — | no | yes | test_boiler_call.py, test_qas_instance.py |
| BP3 | West calls alone and pauses | West stops calling | West resumes at once, though the threshold is still not reached; the boiler stays off. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 2 min | 38 min | — | no | yes | test_boiler_call.py |
| BP4 | VTherm's active-device and power sensors disabled, so the threshold cannot be read | West calls alone | West does not pause while its valve is open; once the sensors are back, it pauses. | `boiler-count` | fresh | 12 min | 44 min | — | no | no | test_boiler_call.py |
| BP5 | The user's learning paused (set_smartpi_learning off) | West calls alone, then East too | West's boiler pause starts and ends as without it, learning.enabled stays off throughout; the user's setting is put back. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 5 min – 8 min | 44 min | — | no | no | test_boiler_call.py |
| BP6 | West and East call, the boiler on | East stops, the demand drops below the threshold with West heating | VTherm's boiler and its switch go off at once (also with an activation delay), West pauses two calculations later; West stopping then resumes it. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 7 min – 8 min | 57 min | — | no | no | test_boiler_call.py |
| BP7 | West learned, its setpoint 1 degC above its room | a forced calibration heats below the threshold | learning pauses: the dead-time detection stays idle and the learned heating dead time unchanged; once East calls, learning resumes. | `boiler-count`, `boiler-power`, `boiler-delay` | learned | 15 min – 18 min | 112 min | typical when West starts below the calibration's low threshold (setpoint - 0.3 degC), as its setpoint is set 1 degC above the room | no | no | test_boiler_call.py |
| BP8 | The start state | West and East start calling together | VTherm's boiler starts (with an activation delay, 120 s later) and neither pauses, though VTherm counts their devices a moment after SmartPI first reads the threshold. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 6 min – 10 min | 27 min – 29 min | — | no | yes | test_boiler_call.py, test_qas_instance.py |
| BP9 | West learned, calling alone and paused | Home Assistant restarts | once SmartPI runs again, the pause returns within two calculations; nothing is learned in between (accepted updates unchanged, dead-time detection idle); a, b and the dead time are kept. | `boiler-count`, `boiler-power`, `boiler-delay` | learned | 5 min | 52 min | — | no | no | test_boiler_call.py |
| BP10 | West calls alone and pauses | VTherm's threshold lowered to 1 device (or 500 W), then back | lowered, West resumes at its next calculation and the boiler starts (with an activation delay, 120 s later), though only West calls; back, West pauses again two calculations later and the boiler stops at once. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 4 min – 6 min | 46 min | — | no | no | test_boiler_call.py |
| BP11 | West calls alone and pauses | West turned off and on, then its window opened and closed | off, its pause ends within a calculation (SmartPI does not call); heating again, the pause returns after a calculation period below the threshold; window open, VTherm turns West off after 30 s and the pause ends; window closed, West heats again and pauses two calculations later. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 6 min | 58 min | — | no | no | test_boiler_call.py |
| BP13 | West 300 W and East 900 W learned, a power threshold of 600 W | A West calls (300 W), A′ 20 minutes, B East calls (1200 W), C East stops (300 W), D West stops | A: the boiler stays off and West pauses after a calculation period below the threshold; A′: the pause holds without a break, the boiler never starts, the room cools and nothing is learned; B: the boiler starts (with a delay, 120 s later), West resumes at its next calculation, East never pauses; C: the boiler goes off at once, West pauses two calculations later, and nothing from the gap between them is learned; D: the pause ends. | `boiler-levels`, `boiler-levels-delay` | learned | 28 min – 29 min | 77 min | — | no | no | test_boiler_call.py, test_qas_instance.py |
| BP17 | West and East learned, West calling alone and paused | East switched on and off at the threshold, every calculation for 10 minutes, then every 10 s for 5 minutes, then off | no window that short reaches West's or East's learning (accepted updates and the heating dead times unchanged); it records the switches of the pause and of the boiler; West ends paused. | `boiler-count`, `boiler-power` | learned | 18 min | 55 min | — | no | no | test_boiler_call.py |
| BP18 | The start state | East calls, then West, and West's setpoint changes again at once (two of West's calculations a moment apart at the boiler's start) | neither pauses and the boiler starts (with an activation delay, 120 s later), though the pause's confirmation counts two calculations whatever the time between them. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 6 min – 10 min | 27 min – 29 min | — | no | no | test_boiler_call.py, test_qas_instance.py |
| BP20 | West and East learned, both calling, the boiler on, nothing paused | Home Assistant restarts | once SmartPI runs again, neither pauses while VTherm counts their devices again and the boiler comes back (with an activation delay, 120 s later); a, b and the dead times are kept. | `boiler-count`, `boiler-power`, `boiler-delay` | learned | 6 min – 10 min | 42 min – 44 min | — | no | no | test_boiler_call.py |
| BP25 | Fresh, the valves reporting their opening 15 s after it is set, East calling alone below the threshold | West starts and completes the threshold, 20 times, each from the start state | West never pauses: SmartPI's log has no "West - SmartPI learning paused" line, though VTherm counts West only once its valve reports. | `boiler-count` | fresh | 60 min | 10.8 h | — | yes | no | test_boiler_call.py |
<!-- end of catalogue -->

## 5. Known points

- Home Assistant shows SmartPI 0.0.0: the repository keeps that version, only a release writes
  its own. Every result records the commit it tested.
- SmartPI's defaults entry shows the diagnostics of a thermostat created after it once it is set
  up again: the preparation ends with a restart.
- Every scenario resets West's and East's learning, or loads their learned state, at its start:
  what a scenario learned is never carried into the next one.
- BP4 disables VTherm's active-device and power sensors for a while; Home Assistant reloads
  VTherm's central entry when they are disabled and again when they are enabled.
- BP7 sets West's setpoint 1 °C above its room, so that the calibration heats at once.
- BP9 and BP20 restart Home Assistant; the trace misses its readings meanwhile.
- BP25 runs about an hour (20 starts, each from the start state) and reads the instance's log
  through the bench.
