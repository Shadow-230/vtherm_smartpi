# QAS test pack — SmartPI on a test Home Assistant

The pack the bench (`ha-testbed`, its `docs/test-pack.md`) uses to test SmartPI on test Home
Assistant instances. It tests SmartPI's learning pause while Versatile Thermostat's central
boiler is off (`documentation/en/technical_doc.md`, 3.8). Nothing here is named `test_*.py`:
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
| `learned_state.py` | writes `states/west.json` and `states/east.json` from the rooms' own values, and `states/west_settled.json`: West with its response lag published as well (BP29) |
| `instance.py` | one instance through Home Assistant's API, and the config flows driven step by step |
| `prepare.py` | prepares an instance as a role |
| `steps.py` | what the scenarios share: the start state, the steps and their checks, the timing, the trace (section 4) |
| `scenarios.py`, `run.py` | the scenarios, and the runner on the bench's harness |

**The test rooms** (`smartpi_test_rooms`). Each room is one thermal mass:
`dT/dt = gain * p - loss * (T - T_out)`, the model SmartPI learns. `p` is the heater's output,
after its delay and through its lag. A water radiator, behind a valve or behind a relay, gets heat
only while the boiler's switch is on. The outdoor temperature moves by ±0.2 °C around 3 °C, so its sensor never holds one value
for hours. Entities: each room's temperature sensor (0.1 °C, written when it changes), its valve
or its switch (an electric heater's, or a relay's), the outdoor temperature sensor and the
boiler's switch. One tick every 7 s.
Two services change the rooms in place:
- `smartpi_test_rooms.set_valve_report_delay` (`seconds`): a valve takes and reports a new opening
  only that long after it was set, as a real valve head moves for seconds before it reports (0 s
  by default, which reports at once);
- `smartpi_test_rooms.set_sensor_offset` (`room`, `offset`): the room's sensor reads its
  temperature plus `offset` °C, at once, the room itself unchanged (0 by default). +0.1 °C then 0
  is a one-step toggle of the reading, +0.1 °C kept a step that stays; the start state sets every
  offset back to 0.

## 3. The rooms and the roles

| Room | Heater | VTherm's central boiler | Thermostat |
|---|---|---|---|
| West | water radiator, valve | counted | `climate.west` |
| East | water radiator, valve | counted | `climate.east` |
| North | electric heater, switch | not counted | `climate.north` |
| North in `boiler-relay` | water radiator behind an on/off actuator, a relay (switch) | counted | `climate.north` |

| Role | Central boiler | Devices |
|---|---|---|
| `boiler-count` | threshold of 2 active devices | 2000 W each |
| `boiler-power` | threshold of 2500 W: one valve fully open is below it, two open at 63 % reach it | 2000 W each |
| `boiler-delay` | threshold of 2 active devices, activation delay 120 s | 2000 W each |
| `boiler-levels` | threshold of 600 W: West alone with its valve fully open is below it, East alone or both reach it | West 300 W, East 900 W, North 2000 W |
| `boiler-levels-delay` | as `boiler-levels`, activation delay 120 s | as `boiler-levels` |
| `boiler-relay` | threshold of 1 active device: North alone keeps the boiler on | 2000 W each |

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
the delay, the boiler starts 120 s after the threshold is reached and stays off meanwhile, so a
calling thermostat pauses during that time and resumes once the boiler's start counts, 5 s after
it: the step waits for that resume before its checks and before the scenario goes on (a step that
ended at once would stop the boiler within 5 s, and no thermostat would resume; check of a93d6f9).
BP26 to BP28
check what SmartPI's dead-time estimator makes of such a start, which it judges as a start of the
heating at the boiler's start, by its own rules: from a settled room the dead time counts from the
boiler's start and is at least a calculation, 60 s (BP26); a room that calls again less than
600 s after its last heating, the wait included, gets no measurement (BP27), nor one within 600 s
of a reset of its learning (BP28). BP29 and BP30, on `boiler-count`, check when SmartPI times a
room's passive response after a stop: only once the heating before it has settled (BP29, with the
response lag published), and never without a reliable heating dead time (BP30); a heating shorter
than a reliable heating dead time (BP31), or with a published lag longer than the dead time but
shorter than the settling time (BP32), times none either. The
power levels of `boiler-levels` let the power alone decide: West's 300 W alone stays below 600 W,
East's 900 W alone or both reach it (BP13). With the power, VTherm counts each device's power
times its valve opening for the next cycle, so a partly open valve counts as that share of the
device; with the device count, any open valve counts as a whole device.

**A room behind a relay** (BP34–BP37) runs on `boiler-relay`. SmartPI drives a switch in cycles: at
a command of 60 % the relay is on for 60 % of each 5-minute cycle. VTherm counts a switch only while
it is on, so with North alone the boiler goes off in each off part of North's cycle, although
North's own command, not a lack of heat, made that off part. SmartPI takes it for a cut of the heat
only once the relay has been on for 5 s with the boiler off, and gives the heat back only while
both are on (`technical_doc.md` 3.8). BP34 checks the learning pause in a modulating cycle, BP35
the dead-time estimator's heating period, BP36 a real cut (the threshold raised to 2, so that the
boiler does not start for North alone) through the relay's on and off parts, BP37 a cut that
starts in North's own off part (East, whose valve made the second device, stops then): it shows
only at North's next on part, 5 s in, and its late passive measurement must not lower North's
passive dead time. In this role North does not call in the start state, which needs the boiler off.

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
calling with the threshold reached is watched for a pause it must not take; with an activation
delay, every calling thermostat must pause while the boiler waits and resume at the moment it
starts. Those pauses are read from SmartPI's info lines in the instance's log ("West - SmartPI
learning paused (central boiler off)", and "resumed"), whose times are UTC: such a pause can last
seconds, less than a reading.

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
- SmartPI pauses once it has called with VTherm's boiler off without a break for a calculation
  period (60 s, less 1 s for the timer), at its first calculation after that;
- a change of a thermostat's own setpoint or mode makes a calculation at once: from not calling,
  it calls with the boiler off from that calculation, and its pause comes at the first check a
  period later, 60–120 s after the setpoint, 90 s on average, as the setpoint falls at any moment of
  a period (117 pauses on the bench: mean 84 s, median 85 s); a resume it causes comes at once, but a learned thermostat that stops calling resumes at
  its next calculation (48–56 s on the bench), as its 0 % counts only from then;
- a change from outside (East, the threshold) reaches it at its next calculation: a pause comes
  two calculations later; a resume comes at the moment VTherm's boiler starts (with an activation
  delay, 120 s after the threshold is reached), as SmartPI follows the boiler's state;
- a closed window: VTherm's delay of 30 s, then two calculations (150 s on the bench, 11 of 11);
- after a restart, a thermostat calling with the boiler off pauses again a calculation period
  after SmartPI runs again (BP9);
- the start state after the scenario, which the runner reaches before the next one: VTherm's
  boiler off for the role's delay plus 30 s when the scenario ends with the boiler on or just
  after a restart, which starts VTherm's boiler-off time again (150 s with the activation delay;
  BP0 after the preparation's restart, BP9), and a learned West paused at the end resumes at its
  next calculation once it stops calling (BP9).

In the columns:
- **typical**: what SmartPI's timing gives, plus the steps' own holds; one value, or the shortest
  to the longest over the scenario's roles. Judge a run against it;
- **maximum**: the sum of the waits' limits, reached only if SmartPI is late, with up to 9 minutes
  for the start state when the boiler was just on. Plan detached runs and reminders from it;
- **time depends on**: what changes a scenario's time beyond SmartPI's timing.

**A thermostat's own start** (BP25). SmartPI pauses only once it has called with VTherm's boiler
off for a calculation period (`documentation/en/technical_doc.md`, 3.8). At West's own start,
which completes the threshold, VTherm counts West, and turns the boiler on, only once its valve
reports the new opening, after seconds with a real valve head: a check of the pause in that time
must not pause. BP25 sets the
valves' report delay to 15 s and starts West 20 times from the start state, with East calling
alone below the threshold. It passes when every start took place and SmartPI's log, read through
the bench as its harness reads a failure's evidence, has no "West - SmartPI learning paused"
line; each start's note gives VTherm's boiler start and any pause seen in the diagnostics.

M: in the minimum set, run after any change. Detached: a maximum over the bench's two-hour limit
for a background command.

<!-- catalogue: written by `python tests/qas/run.py --list --markdown` -->
| ID | Start state | Event | Expected result | Roles | Learning at start | Typical | Maximum | Time depends on | Detached | M | Counterpart without the bench |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BP0 | After the preparation's restart | the instance is read | every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet and its warnings that Home Assistant deprecates part of its entity API (until 2027.11), VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published. | all | fresh | < 1 min – 2 min | 35 min | — | no | yes | test_qas_instance.py, test_published_diagnostics_schema.py |
| BP1 | The start state | West calls alone, below the threshold, the boiler off | West pauses once it has called with the boiler off for a calculation period (60 s), not before; VTherm's boiler stays off; East and North (not counted) do not pause; West's valve stays open. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 4 min | 32 min | — | no | yes | test_boiler_call.py, test_qas_instance.py |
| BP2 | West calls alone and pauses | East calls too, the threshold is reached | VTherm's boiler starts (with an activation delay, 120 s later) and West resumes once its start counts (5 s), not before; East does not pause (with the delay, it pauses too while the boiler waits). | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 4 min – 6 min | 33 min | — | no | yes | test_boiler_call.py, test_qas_instance.py |
| BP3 | West calls alone and pauses | West stops calling | West resumes at once, though the threshold is still not reached; the boiler stays off. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | < 1 min | 38 min | — | no | yes | test_boiler_call.py |
| BP4 | VTherm's central boiler state disabled, so SmartPI cannot read it (VTherm, which needs that entity, does not drive the boiler meanwhile) | West calls alone | West does not pause while its valve is open; once the boiler's state is back, off, it pauses. | `boiler-count` | fresh | 12 min | 44 min | — | no | no | test_boiler_call.py |
| BP5 | The user's learning paused (set_smartpi_learning off) | West calls alone, then East too | West's boiler pause starts and ends as without it, learning.enabled stays off throughout; the user's setting is put back. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 4 min – 6 min | 38 min | — | no | no | test_boiler_call.py |
| BP6 | West and East call, the boiler on for a calculation period | East stops, the demand drops below the threshold with West heating | VTherm's boiler and its switch go off at once (also with an activation delay); West's heat is held for 120 s, then its stop and its pause come 120 s after the boiler's stop (SmartPI's log); West stopping then resumes it, and as its heat stopped with the boiler, SmartPI arms no detection of West's passive response (never WAITING_COOL_RESPONSE for five calculations). | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 11 min | 59 min | — | no | no | test_boiler_call.py |
| BP7 | West learned, its setpoint 1 degC above its room | a forced calibration heats with the boiler off | learning pauses: the calibration arms no dead-time detection and the learned heating dead time stays unchanged; once East calls and the boiler is on, learning resumes; at the closing (West off and on again, which cancels the calibration, then West and East stop), shortly after the boiler's start, neither West nor East times a passive response for 5 calculations: the heat the boiler gave lasted less than their dead time. | `boiler-count`, `boiler-power`, `boiler-delay` | learned | 19 min – 21 min | 110 min – 111 min | typical when West starts below the calibration's low threshold (setpoint - 0.3 degC), as its setpoint is set 1 degC above the room | no | no | test_boiler_call.py |
| BP8 | The start state | West and East start calling together | VTherm's boiler starts and neither pauses, though VTherm counts their devices a moment after SmartPI first reads the boiler's state; with an activation delay (120 s) the boiler stays off meanwhile, both pause and resume once it is on. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 6 min – 10 min | 27 min – 29 min | — | no | yes | test_boiler_call.py, test_qas_instance.py |
| BP9 | West learned, calling alone and paused | Home Assistant restarts | once SmartPI runs again, the pause returns within two calculations; nothing is learned in between (accepted updates unchanged, no dead-time detection armed); a, b and the dead time are kept. | `boiler-count`, `boiler-power`, `boiler-delay` | learned | 3 min – 5 min | 52 min | — | no | no | test_boiler_call.py |
| BP10 | West calls alone and pauses | VTherm's threshold lowered to 1 device (or 500 W), the boiler on for a calculation period, then the threshold back | lowered, the boiler starts (with an activation delay, 120 s later), though only West calls, and West resumes once its start counts (5 s); back, the boiler stops at once, West's heat is held for 120 s, then its stop and its pause come 120 s after the boiler's stop (SmartPI's log). | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 4 min – 6 min | 43 min – 45 min | — | no | no | test_boiler_call.py |
| BP11 | West calls alone and pauses | West turned off and on, then its window opened and closed | off, its pause ends within a calculation (SmartPI does not call); heating again, the pause returns after a calculation period below the threshold; window open, VTherm turns West off after 30 s and the pause ends; window closed, West heats again and pauses two calculations later. | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 5 min | 58 min | — | no | no | test_boiler_call.py |
| BP13 | West 300 W and East 900 W learned, a power threshold of 600 W | A West calls (300 W), A′ 20 minutes, B East calls (1200 W), C East stops (300 W), D West stops | A: the boiler stays off and West pauses after a calculation period with the boiler off; A′: the pause holds without a break, the boiler never starts, the room cools and nothing is learned; B: the boiler starts (with a delay, 120 s later) and West resumes once its start counts (5 s); East does not pause (with the delay, it pauses too while the boiler waits); the boiler runs a calculation period; C: the boiler goes off at once, West's heat is held for 120 s, then its stop and its pause come 120 s after the boiler's stop (SmartPI's log), and nothing from the gap between them is learned; D: the pause ends. | `boiler-levels`, `boiler-levels-delay` | learned | 27 min | 74 min | — | no | no | test_boiler_call.py, test_qas_instance.py |
| BP17 | West and East learned, West calling alone and paused | East switched on and off at the threshold, every calculation for 10 minutes, then every 10 s for 5 minutes, then off | no window that short reaches West's or East's learning (accepted updates and the heating dead times unchanged); it records the switches of the pause and of the boiler; West ends paused. | `boiler-count`, `boiler-power` | learned | 18 min | 55 min | — | no | no | test_boiler_call.py |
| BP18 | The start state | East calls, then West, and West's setpoint changes again at once (two of West's calculations a moment apart at the boiler's start) | neither pauses and the boiler starts, though two of West's calculations come a moment apart with the boiler still off; with an activation delay (120 s) both pause while the boiler waits and resume once its start counts (5 s). | `boiler-count`, `boiler-power`, `boiler-delay` | fresh | 6 min – 10 min | 27 min – 29 min | — | no | no | test_boiler_call.py, test_qas_instance.py |
| BP20 | West and East learned, both calling, the boiler on, nothing paused | Home Assistant restarts | once SmartPI runs again, VTherm counts their devices again and the boiler comes back; neither pauses, but with an activation delay VTherm shows the boiler off for 120 s, both pause meanwhile and resume once it is back; a, b and the dead times are kept; SmartPI's log has no "heat back with the central boiler after a blink" line: the boiler first read off after the restart is no stop; each room's heat counts from the boiler's return (its active period, analysis.deadtime.active_elapsed_s, no longer than the time since). | `boiler-count`, `boiler-power`, `boiler-delay` | learned | 6 min – 10 min | 42 min – 44 min | — | no | no | test_boiler_call.py |
| BP25 | Fresh, the valves reporting their opening 15 s after it is set, East calling alone below the threshold, the boiler off | West starts and completes the threshold, 20 times, each from the start state | West never pauses: SmartPI's log has no "West - SmartPI learning paused" line, though VTherm counts West only once its valve reports. | `boiler-count` | fresh | 60 min | 10.8 h | — | yes | no | test_boiler_call.py |
| BP26 | Fresh, an activation delay of 120 s, West without heat and its learning reset at least 600 s before and its room 0.1 degC below its highest reading since, East calling alone below the threshold, the boiler off | West starts and completes the threshold; the boiler starts 120 s later | West pauses while the boiler waits and resumes once its start counts (5 s); the heating dead-time detection armed at the boiler's start counts from it, not from West's call, and ends detected with a dead time of at least 60 s; the dead time is recorded against the room's true 780 s and against 900 s (the true value plus the wait). | `boiler-delay` | fresh | 30 min | 2.6 h | typical when the rooms are already 0.1 degC below their highest reading 600 s after their valves closed; after a heating, a room may still rise and then has to cool | yes | no | test_boiler_call.py |
| BP27 | Fresh, an activation delay of 120 s, West and East without heat and their learning reset at least 600 s before and their rooms 0.1 degC below their highest reading since, East calling alone below the threshold, the boiler off | West calls and heats for 5 minutes with the boiler on, stops, and calls again about 300 s later, completing the threshold; the boiler starts 120 s later | West pauses while the boiler waits and resumes once its start counts (5 s); West was without heat for less than 600 s before the boiler's start (the wait counts), so SmartPI's dead-time estimator measures nothing: for 15 minutes from the boiler's start West's detection never waits for a response, its outcome never turns detected and its heating dead time stays unchanged; East, heated until the boiler went off at West's stop, gets no new measurement either, as the boiler comes back within 600 s of going off, and its heating dead time is none or at least 60 s; West's stop after its 5 minutes of heat arms no detection of its passive response, nor the boiler's stop one of East's, and East's heating detection is dropped once the boiler has stayed off 120 s (a valve's hold; SmartPI's log). | `boiler-delay` | fresh | 43 min | 2.1 h | typical when the rooms are already 0.1 degC below their highest reading 600 s after their valves closed; after a heating, a room may still rise and then has to cool | yes | no | test_boiler_call.py |
| BP28 | Fresh, an activation delay of 120 s, East calling alone below the threshold; West heats for 5 minutes with the boiler on and stops, and its learning is reset right after, its room still warming | West calls about 90 s after the reset and waits for the boiler; then West stops, settles (600 s from the reset and from its valve closing, its room 0.1 degC below its highest reading) and calls again | within 600 s of the reset SmartPI measures nothing: for 5 minutes from the boiler's start West's detection never waits for a response, and it has no outcome and no heating dead time, and West's stop after that heating arms no detection of its passive response; the settled start's detection, armed at the boiler's start, ends detected with a dead time of at least 60 s. | `boiler-delay` | fresh | 49 min | 3.3 h | typical when the rooms are already 0.1 degC below their highest reading 600 s after their valves closed; after a heating, a room may still rise and then has to cool | yes | no | test_boiler_call.py |
| BP29 | West learned with its response lag published (780 s, heating periods of 2 h), East learned, North off | West and East call, the boiler on, and West heats for 45 minutes, longer than its settling time of three lags (39 min), then stops, which turns the boiler off while East still calls; East stops only once its passive detection shows | SmartPI arms a detection of West's passive response at West's stop and one of East's at the boiler's stop (East, heated 45 minutes, still calls then, so it is the boiler's stop that arms it); both go on without a break and end detected, and SmartPI's log gives passive dead times of at least 570 s (the rooms' shortest on their model, less a calculation), timed from West's stop as applied (within 5 s). | `boiler-count` | learned | 69 min | 2.3 h | the passive response's time, estimated: how long the room keeps warming after the stop | yes | no | test_boiler_call.py |
| BP30 | Fresh, North off | West and East call right after the reset, so SmartPI measures no heating dead time (600 s after a reset); the boiler on, West heats for 30 minutes and stops | without a reliable heating dead time SmartPI arms no detection of West's passive response: for five calculations no WAITING_COOL_RESPONSE and no passive outcome. | `boiler-count` | fresh | 38 min | 66 min | — | no | no | test_boiler_call.py |
| BP31 | West learned (heating dead time 780 s, no response lag published), East learned, North off | West and East call, the boiler on; West heats for 5 minutes, shorter than its dead time, and stops; after 600 s without heat it calls again, heats for 20 minutes and stops | after the short heating SmartPI arms no detection of West's passive response for 300 s, nor the boiler's stop one of East's (learned, heated 5 minutes as well); after the long one the detection arms and ends detected, and SmartPI's log gives a passive dead time of at least 570 s (the room's shortest on its model, less a calculation), timed from West's stop as applied (within 5 s). | `boiler-count` | learned | 59 min | 2.3 h | the passive response's time: how long the room keeps warming after the stop | yes | no | test_boiler_call.py |
| BP32 | West learned with its response lag published (780 s, heating periods of 2 h, so a settling time of 2340 s), East learned, North off | West and East call, the boiler on; West heats for 25 minutes, longer than its dead time but shorter than its settling time, and stops | SmartPI arms no detection of West's passive response for 300 s. | `boiler-count` | learned | 32 min | 61 min | — | no | no | test_boiler_call.py |
| BP33 | East learned (heating dead time 780 s, no response lag published), West and North not calling | VTherm's threshold lowered to 1 device, East calls and heats 20 minutes with the boiler on; the threshold back to 2 turns the boiler off while East calls, East stops itself about 30 s later, within a valve's hold (120 s) and before its learning pause, and calls again 120 s after its stop, the boiler still off (East alone is below the threshold) | SmartPI times East's passive response from the boiler's stop, and neither East's own stop nor its new call changes it: exactly one "Cool Deadtime detected" line after the boiler's stop, of at least 570 s (the room's shortest on its model, less a calculation), whose start lies within 1 s of the boiler's stop; the detection goes on without a break through the new call (its age never falls) and ends detected; East never waits for a heating response and the log has no "Heat Deadtime detected" line while the boiler stays off; the threshold is back to 2. | `boiler-count` | learned | 42 min | 112 min | the passive response's time: how long the room keeps warming after the boiler's stop | no | no | test_boiler_call.py |
| BP34 | North learned (a water radiator behind a relay, the boiler's threshold one device), West and East not calling | North's setpoint 1 degC above its room, so that SmartPI modulates: the relay is on for part of each 5-minute cycle and off for the rest, and VTherm's boiler, kept on by North alone, goes off in each off part | the role shows the case: the boiler goes off at least 3 times in 30 minutes while North's command stays above 0; North's own off part is not a lack of heat: North never pauses (diagnostics and SmartPI's log) and SmartPI logs no "North - SmartPI heat stopped with the central boiler" line. | `boiler-relay` | fresh | 30 min | 52 min | — | no | no | test_boiler_call.py |
| BP35 | North learned (a water radiator behind a relay, the boiler's threshold one device), West and East not calling; North settled, 600 s after its learned state was loaded and its room not warming | North's setpoint 2 degC above its room: SmartPI heats at 100 %, then modulates, the relay off in part of each cycle and VTherm's boiler with it; North heats two cycles more and stops | the role shows the case: the boiler went off at least once while North called (the off parts' lengths recorded); North's heating counts from the step, not from the relay's last start: no "North - SmartPI heat stopped with the central boiler" line while North called; North's stop arms a detection of its passive response, shown within 495 s, which goes on without a break and ends detected with at least 60 s, judged against North's outcome at the stop, and timed from North's stop as applied (within 5 s). | `boiler-relay` | fresh | 82 min | 4 h | when SmartPI starts to modulate, and the passive response's time | yes | no | test_boiler_call.py |
| BP36 | North learned (a water radiator behind a relay), West and East not calling | VTherm's threshold raised to 2 devices, North calls 1 degC above its room, alone, for 15 minutes: its relay switches on and off in its cycle while the boiler does not start | the role shows the case: the relay turned on and off at least twice (its switching times recorded) and the boiler stayed off; North gets no heat in the relay's on parts as in its off parts: SmartPI's dead-time estimator sees no power in every reading, North pauses a calculation period after its call and stays paused (one pause line, no resume line while it calls), and the threshold is back to 1. | `boiler-relay` | fresh | 15 min | 40 min | — | no | no | test_boiler_call.py |
| BP37 | North learned (a water radiator behind a relay), East fresh | VTherm's threshold raised to 2 devices; North calls 2 degC above its room and East calls, so the boiler heats with North's relay on (East's valve and the relay make 2 devices); once SmartPI modulates North (above 80 %, with short off parts, its heating settled), East stops during one of North's off parts, so the boiler does not start at North's next on part | the role shows the case: the boiler heated North before East's stop and stays off after it; SmartPI takes it for a cut only 5 s into North's next on part (its "North - SmartPI heat stopped with the central boiler" line 5-20 s after the relay's start), and North pauses 59 s after that line, by its own check (within 1 s; SmartPI's log); the passive measurement, which can only start late, does not lower North's passive dead time: it ends ignored, or detected no shorter than before, and SmartPI's log has no shorter passive dead time; East's own stop comes within 120 s of the boiler's stop, while its heat is held (its stop is then dated from the boiler's, 3.8.2): East never pauses, its dead-time estimator sees no power after its stop and waits for no response (fresh, it measures none); the threshold is back to 1. | `boiler-relay` | fresh | 74 min | 3.3 h | when SmartPI starts to modulate, and the passive response's time | yes | no | test_boiler_call.py |
| BP38 | North learned (a water radiator behind a relay), East fresh, settled (its learning reset at least 600 s before, its room 0.1 degC below its highest reading since) | North calls 2 degC above its room alone (threshold 1) until SmartPI modulates it with off parts under 120 s; then VTherm's threshold raised to 2 devices and East calls at 100 %, so the boiler heats East only in North's on parts (raising the threshold stops the boiler for milliseconds, and East's call comes in that blink) | the role shows the case: East's start is recorded (its active period published; no "East - SmartPI heat back with the central boiler after a blink" line: its heat had not flowed); the boiler goes off in North's off parts while East waits for its response, each off part under 120 s (at least one, or the run decides nothing); East's heat is held through them: no "heat stopped" line and no pause of East but 120 s after a longer off part, its active period never starts again; East's heating detection ends detected, with a dead time of at least 60 s, recorded against the room's true 780 s, and counted from the boiler's return (its start from SmartPI's line from 0.2 s before it to 5.2 s after it); the threshold is back to 1. | `boiler-relay` | fresh | 64 min | 4 h | when SmartPI starts to modulate North, and East's settling and response | yes | no | test_boiler_call.py |
| BP39 | West learned with its model trusted for the feed-forward (its FF trim learns), its setpoint its room's temperature, and East calling, so the boiler heats, for a calculation period at least, until the trim's freeze after the state's restore (three cycles) has ended and its windows collect again | East stops: the boiler goes off while West calls, for 56 minutes (the trim's shortest window, 30 minutes, plus twice West's dead time) | West's trim is unfrozen before the stop; through the stop West pauses and its trim does not change: no new applied correction and the same output (the reasons its windows were rejected are recorded; the mark of the boiler's stop is checked by test_boiler_ff_trim.py). | `boiler-count` | fresh | 71 min | 110 min | when the trim's freeze after the state's restore ends | no | no | test_boiler_call.py, test_boiler_ff_trim.py |
| BP40 | North learned (a water radiator behind a relay), West and East not calling | North calls 2 degC above its room alone (threshold 1): SmartPI heats at 100 %, its relay on and the boiler with it, for a calculation period; VTherm's threshold raised to 2 devices for 60 s, then back to 1, so the boiler goes off while North's relay stays on; then raised for 150 s | the role shows the case: North's relay stays on through both stops; through the 60-s stop North's heat is held (3.8.4): North never pauses, SmartPI's dead-time estimator sees its command in every reading, and the log has no "North - SmartPI heat stopped with the central boiler" line; the 150-s stop is a cut dated from the boiler's stop: North's "heat stopped" line and its pause 120 s after it (SmartPI's log); the threshold is back to 1. | `boiler-relay` | fresh | 8 min | 48 min | — | no | no | test_boiler_call.py |
| BP41 | North learned (a water radiator behind a relay), West and East not calling, VTherm's threshold raised to 3 devices, North turned off and back to heat (SmartPI's periodic calculation starts again) | half a calculation period later, North calls 2 degC above its room and East at 100 %, the boiler stays off; once both are paused, the threshold at 1 for 2 s runs the boiler for less than 5 s, then back to 3 | the role shows the case: North pauses a calculation period after its call (59 s from its "Target change detected" line, within 2 s; SmartPI's log), not at its next calculation; through the run and 120 s after it North and East stay paused in every reading, their dead-time estimators see no power, and the log has no "learning resumed" and no "heat stopped with the central boiler" line for either; the run lasts less than 5 s by VTherm's own time stamps (or the run decides nothing); the threshold is back to 1. | `boiler-relay` | fresh | 5 min | 36 min | — | no | no | test_boiler_call.py |
<!-- end of catalogue -->

## 5. Known points

- Home Assistant shows SmartPI 0.0.0: the repository keeps that version, only a release writes
  its own. Every result records the commit it tested.
- SmartPI's log lines carry their time with its zone from Home Assistant 2026.11.1
  (`2026-11-16 08:35:29.733+00:00`), without it on 2026.9.3. The pack reads both, and a time
  without a zone as UTC, as the instances log. A line with the text a check looks for that its
  pattern does not read fails the run (`steps.UnreadLogLine`): on 2026.11.1, before the patterns
  read the zone, four checks passed on lines read as none (check of 8972367).
- On Home Assistant 2026.11.1 VTherm logs at each start that Home Assistant deprecates part of
  its entity API ("provides the deprecated temperature_unit, this will stop working in Home
  Assistant 2027.11"): two system log entries, three messages each. They are VTherm's, out of the
  pack's scope (owner, 2026-11-16): BP0 notes them and fails on any other warning or error of
  SmartPI or VTherm. Home Assistant's own warning that VTherm writes a deprecated attribute
  (`homeassistant.helpers.frame`) is not counted, its logger being Home Assistant's.
- SmartPI's defaults entry shows the diagnostics of a thermostat created after it once it is set
  up again: the preparation ends with a restart.
- Every scenario resets West's and East's learning, or loads their learned state, at its start:
  what a scenario learned is never carried into the next one.
- BP4 disables VTherm's central boiler state for a while; Home Assistant reloads VTherm's
  central entry when it is disabled and again when it is enabled, and VTherm does not drive the
  boiler meanwhile.
- BP26 measures a heating dead time from a fresh start: one detection on the bench spreads by
  hundreds of seconds, so its value is recorded against 780 s and 900 s, not judged; it passes on
  the detection armed at the boiler's start counting from it and ending detected with at least
  60 s (nothing arms at West's call: SmartPI's estimator sees no power until the boiler starts).
  SmartPI measures no start within 600 s of a reset of learning, but a room can still be warming
  then, so BP26 itself waits before West's start until West's valve has been closed for 600 s,
  its learning was reset 600 s before, and its room is 0.1 °C below its highest reading since: a
  room still warming from the previous run rose early and gave 357 s and 535 s on the bench.
- BP27 starts the same way, from settled West and East: back-to-back runs had measured rooms still
  warming, 0 s for both in one run. It resets nothing between West's two calls: SmartPI's own
  record of West's last stop decides that the start at the boiler's start is not measured. East,
  heated until the boiler went off at West's stop, gets no new measurement either: SmartPI records
  its stop at the boiler's stop, and the boiler comes back within 600 s. It checks West's
  diagnostics, not the log's "Heat Deadtime detected" line, which names no
  thermostat (East measures in the same run). West's stop after 5 minutes of heat, shorter than
  its settling time, arms no passive detection; East's heating detection, armed at the first
  boiler start, is dropped as the boiler goes off, read from SmartPI's info line "East - SmartPI
  heat stopped with the central boiler (heating dead-time detection aborted)".
- BP28 resets West's learning right after it heated, its room still warming: within 600 s of the
  reset SmartPI measures nothing, and West's stop after that heating arms no passive detection;
  the start after West has settled is measured.
- BP29 loads `west_settled` for West: its response lag of 780 s is published, so its heating
  settles after three lags (39 min), and West heats 45 minutes. West's stop turns the boiler off
  while East, heated as long, still calls: SmartPI's estimator then sees East's power fall to 0 at
  the boiler's stop and times East's passive response from it. East keeps calling until its
  detection shows in its diagnostics, and only then stops, so that the boiler's stop, not East's
  own, armed it. Both detections must go on without a break and end detected. The log's "Cool
  Deadtime detected" lines name no room, so the pack asks one line per room, each of at least 60 s
  (North is off). The passive response took 1275 s from the stop on the bench (check of
  11e3071), its typical time here.
- BP29, BP31 and BP33 accept a passive dead time of West or East only from 570 s
  (`PASSIVE_SHORTEST_S`): one calculation below the shortest the test rooms' model gives after
  20-60 minutes at 100 %, without the sensor's step (634 s, East, a room at 21.5 degC). On
  a65202d a step of the sensor near the boiler's stop gave 31.8 s (BP33, FAIL) and 120.1 s
  (BP29, which then asked only 60 s): the stop dated back took its peak from the filter's own
  history (check of a65202d). No scenario puts such a step on purpose yet; the unit tests cover
  it (`tests/test_smartpi_deadtime_back_dating.py`), and a rerun of BP29 or BP33 is only a
  regression check. `set_sensor_offset` (section 2) can put the step for a probe or a scenario.
- BP33 lowers VTherm's threshold to 1 device so that the boiler heats East alone, then puts it
  back to 2 while East calls: the boiler goes off and East stops itself about 30 s later, within a
  valve's hold (120 s) and before its learning pause, then calls again 120 s after its stop, the
  boiler still off (East alone stays below the threshold). SmartPI's estimator sees no power while
  the boiler is off, so East's own stop and its new call change nothing: it times East's passive
  response from the boiler's stop.
  The check reads the start of the only passive dead time in the log (the line's time less the
  dead time and the drop's delay, both logged to 0.1 s) and East's stop from SmartPI's "East -
  Target change detected" line, both on Home Assistant's clock like the boiler's change. That the
  detection goes on through the new call is read from East's diagnostics: its age
  (`analysis.deadtime.waiting_elapsed_s`) never falls, as it would for a new detection. A second
  run starts with East's outcome "detected" from the first (a learned start keeps it), so the
  outcome alone proves nothing then: the run notes it, and the log's line decides. West does not
  call, North is off.
- The boiler's stop is a stop of heat for every room it heated that still calls: BP27 and BP31
  check that it arms no passive detection of East after a short heating (fresh in BP27, learned
  in BP31). For a valve, SmartPI holds the boiler's stop as heat for two calculation periods (120 s:
  the water in the emitter goes on heating), then dates the stop from the boiler's; BP27 reads
  East's "heat stopped" line 120 s after the boiler's stop, and BP33's East, which stops within
  those 120 s, has its stop dated from the boiler's. In BP13's step C the boiler goes off while West calls: West may get a passive
  detection from the boiler's stop during its pause, a real response of the room to the end of
  its heat; the trace's check of the pauses looks at the heating side only.
- BP34 and BP35 failed on the plugin of `qas` a6d0f78 and f7101b7, which counted the off part of
  a relay's own cycle as the boiler cutting the room's heat (check of f7101b7, §5); they pass once
  the relay's rule is in (3.8). Their values come from the pack's rooms run locally with SmartPI
  and VTherm (`test_qas_instance.py`), not from the bench:
  - BP34: 1 °C above the room, SmartPI modulates at 50-65 %, so the relay's off part (111-144 s of
    the 5-minute cycle on the bench) is longer than the pause's 59 s; before the rule North paused
    and logged "heat stopped with the central boiler" in every cycle;
  - BP35: 2 °C above the room, SmartPI heats at 100 % for about 40 minutes and then modulates (12-s
    off parts at 96 % on the bench); a start at 80-99 %, which the first off part would end, needs a
    step within a few tenths of a degree that depends on the room's temperature, so BP35 waits for
    the modulation instead. Before the rule each off part was a stop and each on part a new start,
    so the room's real stop armed no passive detection. BP35 checks that the boiler did go off
    while North called, and judges North's passive outcome against its value at the stop, which may
    be left from an earlier run.
- BP36 raises VTherm's threshold to 2 devices for 15 minutes, so that the boiler does not start
  for North alone: a real cut. Its log window ends at North's own stop, which ends the pause by
  design (the check of 8904a66 counted that resume, 4 ms after the stop).
- BP34, BP35 and BP36 read SmartPI's log from North's call as SmartPI logged it ("Target change
  detected"), not from the bench's time less a margin: run twice in a row, BP36 caught the
  previous run's stop and resume, 1.8 s before the new call (check of b873c33).
- BP37 starts West's and East's learning fresh, so that East, whose heating the boiler cuts with
  North's off parts, measures nothing; North's relay state is read every second to stop East in an
  off part. North heats 2 °C above its room, as in BP35: a passive response is timed only after a
  heating above 80 % (the author's floor), so North must modulate above it, with off parts of
  about 12 s. The cut's line must come 5-20 s after North's relay turns on again: before 5 s, the
  relay's own off part would have been taken for a cut; much later, the cut would be found by
  chance. With off parts that short, the passive measurement starts only about 17 s late, within
  the measurement's own spread: BP37 checks the case is found and does not lower North's passive
  dead time, but it cannot tell the plugin before this change (8904a66) from after it. The error
  that matters comes with long off parts (an hour's cycle: 295 s for 780 s in the check of
  8904a66), which the unit tests cover (`tests/test_boiler_relay.py`). East stops 0.5 s after the
  boiler's stop on the bench (check of 785a4c7), within the valve's hold of 120 s: BP37 checks that
  East then never pauses, its estimator sees no power and waits for no response. The stop's date
  (from the boiler's stop) is not published; BP33 and the unit tests check it. North's cut comes
  between calculations, 5 s into its relay's on part, so BP37 also reads North's pause 59 s after
  its "heat stopped" line, by SmartPI's own check (481eea4: 104 s, at its next calculation).
- BP6, BP10 and BP13 let the boiler run a calculation period before the step that stops it, then
  read the calling West's "heat stopped" line and its pause 120 s after the boiler's stop (the
  valve's hold). SmartPI counts a boiler's start only once it has run 5 s; on the delay roles the
  steps stopped it after 0.7-1.0 s (check of 785a4c7), with no heat to hold.
- BP40 shows a relay room's hold of a boiler's stop: North heats at 100 % alone, its relay on
  throughout, and VTherm's threshold raised to 2 devices stops the boiler while the relay stays
  on, once for 60 s and once for 150 s. The water in the radiator goes on heating, whatever drives
  it (Bohac 2019, Table 6, p. 77): the short stop is held, the long one is a cut 120 s after the
  boiler's stop. Before the hold, North's heat was cut 5 s into such a stop. Locally
  (`test_qas_instance.py`) VTherm stopped the boiler within a reading (7 s) of the threshold's
  change, and North paused 120 s after it.
- BP41 shows a run of the boiler shorter than 5 s while the rooms wait for it: VTherm's threshold
  at 3 devices keeps the boiler off with North (relay) and East (valve) calling, then the threshold
  at 1 for 2 s runs it. It gave the rooms no heat: neither resumes, their estimators see no power,
  and no heat stops (a change counts once it has held for a set time: Adnan 2013, section 3.3,
  p. 30). Before, North resumed at once, its estimator saw a start and its stop was held 120 s;
  East resumed until its next calculation (check of 785a4c7: West resumed for 63-119 s after runs
  of 0.03-1.0 s). BP41 also reads North's pause 59 s after its call, by SmartPI's own check, not at
  its next calculation. A call makes a calculation of its own, and the old timing paused at the first
  periodic calculation 59 s or more after it: the calls come half a calculation period after North
  is turned off and back to heat, which starts SmartPI's periodic calculation again, so that the old
  timing would pause about 90 s after them. Loading a learned state does not restart that timer:
  with the calls 0.3 s after an instance's restart, 481eea4 also gave 59.7 s (check of a93d6f9), and
  later in a run the margin depended on the timer's phase (check of 96401e6). Locally (`test_qas_instance.py`) the
  boiler ran up to 3 s (VTherm turns it off at the next tick of the test's clock) and North paused
  59 s after its call; on 481eea4 it paused 85 s after it.
- BP38 measures a valve room's heating dead time through a relay room's cycle: North, learned,
  modulates alone first (threshold 1), East settles fresh (as BP26), then the threshold goes to 2
  and East calls at 100 %. A step response asks for a room at rest and a step as large as possible
  (Astrom and Hagglund 1995, section 2.4, pp. 11-12), the dead time counted from the step's onset
  (p. 16). The run decides only when the boiler goes off at least once while East waits, each time
  for less than the valve's hold, 120 s; the readings, every 2 s, see off parts of 6 s and more
  (the shortest in the check of 785a4c7). Locally (`test_qas_instance.py`): three off parts of
  10-20 s, a heating dead time of 852 s for the room's 780 s; on the plugin before the hold
  (b873c33) East's detection ends at the first off part. Off parts of 59-120 s tell the hold of
  120 s from the one of 59 s before it; North, modulating near full power on its 5-minute cycle,
  rarely makes them, so the unit tests cover them (`tests/test_boiler_cut_heat.py`).
- BP38 on `qas` 387d79f found a fault of the plugin: raising the threshold to 2 stopped the boiler
  for about 8 ms, East's call came in that stop, and the boiler's return was taken for a blink of
  East's heat, which had not flowed: no start was recorded, and East measured nothing (check of
  387d79f). BP38 now checks that East's start is recorded (its active period published, no "East
  ... after a blink" line), and counts East's "heat stopped" and pause lines only against the off
  parts under 120 s: a longer off part, once North's command falls below 60 %, gives them as
  designed.
- The blink rule's other cases stay with the unit tests (`tests/test_boiler_cut_heat.py`,
  `tests/test_boiler_relay.py`): a call in the 5 s after the boiler's return, a room on a relay, and
  a room with an earlier start that calls in a blink (its new start replaces the old one, so its
  next stop arms no passive detection after too short a heating). The bench cannot bring them on:
  they hang on the order of VTherm's events within milliseconds. BP38 shows the one it can, a valve
  room that calls in the blink of a threshold raised, and that its start counts from the boiler's
  return: the start of East's heating detection, from SmartPI's line (its time less the dead time
  and the rise's delay, each logged to 0.1 s), from 0.2 s before the return to 5.2 s after it (a
  start is not dated before the estimator's last sample: technical_doc.md 3.8.7). The published active
  period cannot date it closer than a calculation: it counts from the estimator's last sample. The
  call came milliseconds before the return, closer than either tells apart (check of 4cea1ba: the
  bench's data place the start within about 0.05 s of the return).
- BP29, BP31 and BP35 check that every passive dead time logged after the room's stop was timed
  from the stop as applied: within 5 s of the room's "Target change detected" line, from SmartPI's
  "Cool Deadtime detected" lines (the line's time less the value and the drop's delay). SmartPI now
  feeds its dead-time estimator when Versatile Thermostat confirms the applied command, not at its
  next calculation (technical_doc.md 3.6): in the check of 785a4c7, West's passive measurement in
  BP29 started 59.6 s after its stop. A boiler's stop that the room's stop causes comes with it, so
  the lines of the rooms it heated count too.
- BP39 needs the boiler on before its stop (a calculation period at least, as BP6, BP10 and BP13)
  and the trim past its freeze after the state's restore: that freeze shows in the windows'
  rejections (`frozen_reboot`), not in the freeze reason (check of 387d79f, where East called
  10 ms and BP39 passed without its preconditions).
- BP39 shows on the bench what the FF trim does through a long stop of the boiler: West's learned
  state `west_trim` trusts its model for the feed-forward (its loss estimates' history full, the
  warm-up over), so the trim is unfrozen (the other learned states leave it frozen, `ab_ab_bad`).
  The boiler stays off for a trim window (30 minutes) plus twice West's dead time. On the test rooms
  the trim's own gates reject the windows first (an error above 0.3 degC, a change of regime, too
  few distinct readings of a 0.1-degC sensor): an adaptive estimator learns only while the
  measurement moves with the control signal (Hagglund and Astrom 2000, sections 4 and 5,
  pp. 1174-1175). The mark of the boiler's stop (`causal_power_not_covered`) is checked by
  `tests/test_boiler_ff_trim.py`; BP39 checks that the unfrozen trim does not change and records
  the reasons its windows were rejected.
- BP20 reads each room's active period (`analysis.deadtime.active_elapsed_s`) after the restart:
  a start recorded at the boiler's return (the check of f7101b7 found none, after a first reading
  off was taken for a blink). It reads the power SmartPI's dead-time estimator saw
  (`analysis.deadtime.last_power`) in every reading, and records the relay's switching times from
  the switch's own time stamps.
- BP30 has West and East call right after the reset, so that SmartPI measures no heating dead time
  in its 30 minutes of heat; without one, no passive response is timed.
- SmartPI counts a heating from its committed power, up to a cycle (5 min) after the valve opens
  (about 3 min in BP29). BP31's long heating (20 min) and BP32's (25 min) keep that margin around
  the thresholds they test, 780 s and 2340 s; each scenario checks the thresholds in West's
  diagnostics first (`analysis.learning.response_lag`).
- BP7 sets West's setpoint 1 °C above its room, so that the calibration heats at once. Its closing
  (West off and on again, which cancels the calibration, then West and East stop) comes shortly
  after the boiler's start: BP7 watches both for 5 calculations, as a passive detection there
  would show at once and BP8's reset of learning would drop it before it ends (25.7 s detected on
  boiler-delay in the check of f7101b7).
- BP20 reads SmartPI's log after the restart: the boiler first read off is no stop, so no "heat
  back with the central boiler after a blink" line (four in the check of f7101b7).
- BP9 and BP20 restart Home Assistant; the trace misses its readings meanwhile.
- BP25 runs about an hour (20 starts, each from the start state) and reads the instance's log
  through the bench.
