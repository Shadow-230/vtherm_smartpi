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
5. [The reference runs on the author's 0.5.2](#5-the-reference-runs-on-the-authors-052)
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

| Role | Central boiler | Other |
|---|---|---|
| `boiler-count` | threshold of 2 active devices | West's window sensor, `input_boolean.west_window` (BP11) |
| `boiler-power` | threshold of 2500 W (each device 2000 W) | — |
| `boiler-delay` | threshold of 2 active devices, activation delay 120 s (BP12) | — |

`prepare.py` sets in Versatile Thermostat only what these rooms need; everything else keeps
VTherm's defaults (a cycle of 5 minutes, for example):
- central configuration: the outdoor sensor; the central boiler, switching the test rooms' boiler,
  with the role's activation delay; a safety delay of 1440 minutes, because a test room's sensor writes only when its reading
  changes and VTherm's 60 minutes would take a steady room for a dead sensor;
- each thermostat: its room's sensor, its device and the device's power (2000 W), whether the
  central boiler counts it, SmartPI; power management unticked (VTherm ticks it for a device with
  a power, and warns without a power manager); for a valve, VTherm's opening filters off (0 % and
  0 minutes), as SmartPI's repairs advise; in `boiler-count`, West's window sensor (VTherm's own
  window settings: West turns off 30 s after the window opens, and back 30 s after it closes);
- the role's threshold.

SmartPI runs with its "SmartPI defaults" entry only.

## 4. The catalogue

Times are in real time, the sum of each scenario's waits (`scenarios.py`, `Wait`); every wait ends
as soon as its condition holds:
- **typical**: what SmartPI's timing gives (it calculates every 60 s): two calculations for a
  pause, one for a resume, plus the steps' own holds. A change of the setpoint or of the HVAC
  mode makes a calculation at once: after it, a pause comes one calculation later, and a resume
  it causes comes at once. Judge a run against it;
- **maximum**: the sum of the waits' limits, reached only if SmartPI is late. Plan detached runs
  and reminders from it;
- **time depends on**: what changes a scenario's time beyond SmartPI's timing.

M: in the minimum set, run after any change. Detached: a maximum over the bench's two-hour limit
for a background command. On 0.5.2: runs on the author's release too (section 5).

<!-- catalogue: written by `python tests/qas/run.py --list --markdown` -->
| ID | Checks | Roles | Typical | Maximum | Time depends on | Detached | M | On 0.5.2 | Counterpart without the bench |
|---|---|---|---|---|---|---|---|---|---|
| BP0 | After the preparation's restart every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published (not on the author's 0.5.2, which has no such field). | both | < 1 min | 15 min | — | no | yes | yes | test_qas_instance.py, test_published_diagnostics_schema.py |
| BP1 | West calls alone below VTherm's threshold of 2 devices: it pauses its learning, VTherm's boiler stays off, East (not calling) and North (calling, not counted) do not pause, and West's command goes on. | `boiler-count` | 4 min | 12 min | — | no | yes | no | test_boiler_call.py, test_qas_instance.py |
| BP2 | After West paused alone, East calls too: the threshold is reached, VTherm's boiler starts and West resumes its learning. | `boiler-count` | 2 min | 18 min | — | no | yes | no | test_boiler_call.py, test_qas_instance.py |
| BP3 | After West paused alone, it stops calling: it resumes its learning though the threshold is still not reached. | `boiler-count` | 1 min | 18 min | — | no | yes | no | test_boiler_call.py |
| BP4 | With VTherm's active-device and power sensors disabled, the threshold cannot be read: West calling alone (command above 0) does not pause; the sensors are enabled again and read. | `boiler-count` | 10 min | 15 min | — | no | no | no | test_boiler_call.py |
| BP5 | With the user's learning paused (set_smartpi_learning off), West's boiler pause starts and ends while learning.enabled stays off; the user's setting is put back. | `boiler-count` | 2 min | 22 min | — | no | no | no | test_boiler_call.py |
| BP6 | With VTherm's power threshold (2500 W, devices of 2000 W): West calling alone pauses while the boiler stays off, East calling too resumes it, and West alone again resumes when it stops calling. | `boiler-power` | 6 min | 38 min | — | no | no | no | test_boiler_call.py |
| BP7 | With West's learned state loaded, a forced calibration's heating step below the threshold pauses learning: the dead-time detection stays idle and the learned heating dead time unchanged; once East calls and the boiler starts, learning resumes and the next heating starts a new dead-time window, its episode starting after the resume. | `boiler-count` | 16 min | 97 min | typical when West starts below the calibration's low threshold (setpoint - 0.3 degC); above it, the calibration first cools the room, up to 60 min more | no | no | no | test_boiler_call.py |
| BP8 | West and East start calling together and VTherm's boiler starts: neither pauses its learning, though VTherm counts their devices a moment after SmartPI first reads the threshold. | `boiler-count`, `boiler-power` | 6 min | 14 min | — | no | yes | no | test_boiler_call.py, test_qas_instance.py |
| BP9 | Home Assistant restarts while West is paused: once SmartPI runs again, the pause returns within two calculations; nothing is learned in between (accepted updates unchanged, dead-time detection idle, no heating episode from before the restart); a, b and the dead times are kept. | `boiler-count`, `boiler-power` | 5 min | 32 min | — | no | no | no | test_boiler_call.py |
| BP10 | VTherm's threshold changed while West is paused: lowered to 1 device (or 500 W), West resumes at its next calculation and the boiler starts, though only West calls; back to the role's value, West pauses again after two calculations and the boiler stops. | `boiler-count`, `boiler-power` | 4 min | 26 min | — | no | no | no | test_boiler_call.py |
| BP11 | While West is paused: turned off, its pause ends within a calculation (SmartPI does not call); heating again, the pause returns a calculation later. The same with its window: opened, VTherm turns West off and the pause ends; closed, West heats and pauses again. | `boiler-count` | 4 min | 38 min | — | no | no | no | test_boiler_call.py |
| BP12 | Measures the limit of a central boiler with an activation delay (120 s): with West's learned state, after a pause, East calls and West resumes at once while the boiler starts 120 s later; it records that delay, when the next heating episode (the dead-time window) starts against the boiler's start, and the heating dead time for 15 minutes after. It passes on the measurement. | `boiler-delay` | 22 min | 46 min | — | no | no | no | test_boiler_call.py |
| BR1 | Reference run, the same on this version and on the author's 0.5.2: from a fresh start (learning reset), for 96 hours West's setpoint switches between 19 and 21 degC and East's call (so the boiler) on and off, each by its own pseudo-random sequence (steps of 30 and 40 minutes); every 10 minutes West's dead time, a and b are kept (BR1-<version>-instance<N>.json in the results), and run.py --compare judges both versions' heating dead time at 48 and 96 hours against the room's true value. | `boiler-count`, `boiler-power` | 96 h | 96 h | — | yes | no | yes | test_boiler_call.py |
| BR2 | Measures a limit of the pause, the same on this version and on the author's 0.5.2: as BR1, but East calls in fixed phases of 3 hours, off first, so West mostly starts heating while the boiler is off; kept as BR2-<version>-instance<N>.json and shown by run.py --compare, not judged. It passes on the measurement. | `boiler-count` | 96 h | 96 h | — | yes | no | yes | test_boiler_call.py |
<!-- end of catalogue -->

## 5. The reference runs on the author's 0.5.2

BR1 and BR2 run the same case on this version and on the author's release 0.5.2 (`c75d803`, tag
0.5.2, in `qas`'s history), which has no learning pause. They show what the pause protects: West's
heating dead time, from which SmartPI computes its gain (`documentation/en/technical_doc.md`, 3.7).

**A run.**
1. West's learning is reset (`reset_smartpi_learning`): SmartPI starts from its default values,
   as after an installation. From a learned model SmartPI accepts almost no new measurement, in
   either version, so there would be nothing to compare. A reset is a normal function of an
   adaptive controller (Hägglund and Åström 2000, §8, p. 1179).
2. For 96 hours:
   - West's setpoint switches between 19 and 21 °C by a pseudo-random binary sequence: a fixed
     order of the two levels that looks random, each held 30 minutes (order 6, repeating after
     31.5 hours);
   - East's setpoint switches between 23 °C (East calls, so the boiler runs) and 7 °C (East does
     not call, so the boiler stops). In BR1 by a second sequence, each level held 40 minutes
     (order 5, repeating after 20.7 hours); in BR2 in fixed phases of 3 hours, off first, so West
     mostly starts heating while the boiler is off.
3. Every minute the run reads West and the boiler. It counts West's heating starts with the
   boiler off and on, and checks that the boiler is off while West calls alone and, on this
   version, that West's learning is paused then. Every 10 minutes it keeps West's dead time, `a`,
   `b`, learning counters, pause, the boiler, the room's temperature and West's command in
   `<ID>-<version>-instance<N>.json`, next to the results.

**Why these inputs, in plain words.**
- Learning needs the temperature to move because of the heating. With a fixed setpoint West
  starts heating almost only after it has cooled with the boiler off. Changes of the setpoint
  are a good excitation while the regulation runs (Hägglund and Åström 2000, §1, p. 1172, and §4,
  p. 1174).
- The best test signal is a pseudo-random binary sequence. Two inputs need two sequences that are
  not related to each other, and neither may repeat after 24 hours, the period of the outdoor
  temperature and the sun (IEA EBC Annex 58, appendix E, p. 54–55; Bacher and Madsen 2011,
  p. 10). For heated rooms, pseudo-random changes of the setpoint are a proposed test (Kull,
  Thalfeldt and Kurnitski 2020, p. 1).
- A level is held about as long as the room's shortest time constant, here the radiator's delay
  and lag of 3 and 10 minutes (Annex 58, p. 55). The longest one, the room's cooling of about 17
  hours, is not covered: Annex 58 uses a separate signal of 300 hours for it.
- At full power SmartPI stops learning, as the literature advises (Hägglund and Åström 2000, §7,
  p. 1178). With the boiler off for long, West is often at full power, in both versions alike.
- A simulation is only a start; tests on real installations must follow (Hägglund 2026, slide
  8). Both runs were tried first in the fork's simulator.

Chosen in the simulator, not taken from a source: the levels of 19 and 21 °C, the steps of 30
and 40 minutes, the phases of 3 hours and the 96 hours.

**Comparing.** `run.py --compare QAS.json AUTHOR.json`, two files of the same run and role,
gives for both versions:
- West's heating dead time at 48 and 96 hours, with its error against the true 780 s (the
  radiator's delay plus lag; the delay alone is 180 s);
- `a` and `b`, as auxiliary readings only: the pause is judged on the dead time;
- West's heating starts with the boiler off and on.

BR1 is valid when both versions end with a reliable heating dead time and at least 6 heating
starts with the boiler off, as many as the detections whose median SmartPI publishes
(`technical_doc.md`, 3.6). Then the decision rule of the bench's plan applies: this version's
error not larger than 0.5.2's and within 20 % means the pause is at least harmless; clearly
smaller, useful; larger, the change goes back to the coding session. BR2 measures a limit: it is
shown, not judged.

What the simulator gave (fresh start, threshold of 2 devices; true dead time 780 s):

| Run | This version, 48 h / 96 h | 0.5.2, 48 h / 96 h |
|---|---|---|

In both runs this version stayed in SmartPI's first phase for the whole 96 hours; 0.5.2 left it
at the end of BR1.

**On the bench.** BR1 on `boiler-count` and on `boiler-power`, BR2 on `boiler-count`, each on this
version and on 0.5.2: six instances for 96 hours, started together from freshly prepared
instances. On 0.5.2: deploy the author's checkout as the plugin, the pack from `qas` as usual
(`--components stage/vtherm_smartpi/tests/qas/custom_components`), prepare the instance as its
role, and run with `--author-release`: only BP0, BR1 and BR2 run there, and BP0 does not look for
`learning.boiler_paused`, which 0.5.2 does not publish.

**Sources.**
- Hägglund T., Åström K.J., Supervision of adaptive control algorithms, *Automatica* 36(8), 2000,
  1171–1180.
- Madsen H., Bacher P. and others, *Thermal performance characterisation using time series data –
  statistical guidelines*, IEA EBC Annex 58, KU Leuven, 2016.
- Bacher P., Madsen H., Identifying suitable models for the heat dynamics of buildings, *Energy
  and Buildings* 43, 2011, 1511–1522.
- Kull T.M., Thalfeldt M., Kurnitski J., PI parameter influence on underfloor heating energy
  consumption and setpoint tracking in nZEBs, *Energies* 13(8), 2020, 2068.
- Hägglund T., Lessons learned during 50 years in control, slides, Nordic Process Control
  Workshop, 2026.

## 6. Known points

- Home Assistant shows SmartPI 0.0.0: the repository keeps that version, only a release writes
  its own. Every result records the commit it tested.
- SmartPI's defaults entry shows the diagnostics of a thermostat created after it once it is set
  up again: the preparation ends with a restart.
- BP4 disables VTherm's active-device and power sensors for a while; Home Assistant reloads
  VTherm's central entry when they are disabled and again when they are enabled.
- BP7 and BP12 load West's learned state; West keeps it after the scenario.
- BP9 restarts Home Assistant.
- BR1 and BR2 reset West's learning; West keeps what it learned in the run.
- BP12 measures a limit (a central boiler that starts after a delay): it passes on the
  measurement; whether the limit is acceptable is decided on its numbers.
