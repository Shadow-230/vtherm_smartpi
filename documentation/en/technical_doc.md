# Smart-PI: Technical and Scientific Documentation

## 1. Introduction

**Smart-PI** is a self-adaptive discrete proportional-integral controller, available as a standalone integration for *Versatile Thermostat*. Its goal is to replace a fixed-coefficient TPI with a loop that learns the real thermal behavior of the room online.

The current codebase is built around three guiding ideas:

1. identify a simple first-order thermal model with dead time,
2. adapt the PI command from that model,
3. freeze or limit some adaptations when the physical regime is not considered reliable.

This document describes the behavior that is actually implemented in the current SmartPI code.

This fork adds extensions: known inputs (heat exchanged with neighbouring zones, and heat from a
second heat source in the zone), the temperature of the water reaching the emitters, and the
boiler's call (whether the boiler is told to heat). The water and the boiler's call come from a
source the user chooses in the thermostat's options: Home Assistant entities, or a provider (an
integration that implements SmartPI's contract), and for the call also Versatile Thermostat's
central boiler. Where an extension changes the model, the command chain or the learning, the
sections below include it. The extensions themselves are described in
[Zone dependencies and a second heat source](zone_dependencies.md) and
[Water and boiler inputs](water_input.md), and their tests in [Tests](testing.md).

---

## 2. Thermal model and control law

### 2.1 Thermal model in use

The learned thermal model is:

$$ \frac{dT_{int}}{dt} = a \cdot u(t) - b \cdot (T_{int}(t) - T_{ext}(t)) $$

With:

- $T_{int}$: indoor temperature,
- $T_{ext}$: outdoor temperature,
- $u(t) \in [0,1]$: normalized active-actuator command,
- $a$: signed actuator thermal gain, positive in HEAT and negative in COOL,
- $b > 0$: passive heat-exchange coefficient with outdoors.

A SmartPI VTherm is mono-mode: it learns either a HEAT model or a COOL model.
The equation is identical in both cases; the sign of $a$ carries the direction
of active thermal action.

The associated time constant is:

$$ \tau = \frac{1}{b} $$

The model is intentionally simple. Dead time is learned separately and injected into tuning heuristics and protections.

**Known inputs.** The model is extended with the heat exchanged with neighbouring zones and the
heat added by a second source:

$$ \frac{dT_{int}}{dt} = a \cdot u - b \cdot (T_{int} - T_{ext}) + \sum_i g_i (T_i - T_{int}) + \sum_j q_j $$

Here `g_i` (1/min) links the zone to a temperature `T_i`, such as a neighbouring zone's, and `q_j`
(°C/min) is heat added directly, such as the second source's heat.

The control law takes these inputs in through two values it already uses. Both are computed in a
single place:

$$ b_{eff} = loss + \sum_i g_i, \quad T_{ref} = \frac{loss \cdot T_{ext} + \sum_i g_i T_i + \sum_j q_j}{b_{eff}} $$

Here `loss` is the learned `b`, or the loss fitted without the always-connected neighbours. The
shift of `T_ref` caused by the heat term is limited to `KNOWN_HEAT_MAX_SHIFT_C`. Without known
inputs, `b_eff = b` and `T_ref = T_ext`. Learning keeps using the raw `b` and `T_ext`
([Zone dependencies, §3](zone_dependencies.md#3-the-model-with-known-inputs)).

### 2.2 Command chain

The current command follows this structure:

$$ u = u_{FF} + u_{PI} $$

with:

- `u_ff1`: structural feed-forward derived from $b/a$,
- `u_ff2`: slow `FFTrim` bias,
- `u_ff3`: bounded short-horizon predictive correction, enabled by default and zero outside eligible contexts,
- `u_pi`: discrete PI term.

The value effectively injected into the controller is `u_ff_eff + u_pi`, then it goes through:

- rate limiting,
- anti-windup,
- cycle constraints and protections; with water heating the output is limited to
  `max_on_percent × max(φ, 0.05)`. This is what a full command gives at the water's factor `φ`
  (the heat the emitter gives with the current water, compared with the reference water).

With water heating, the controller works in power at a reference water temperature. The command
sent to the emitter is `power / max(φ, 0.05)`, at most 1, before the valve curve. Learning sees the
command times `φ` ([Water and boiler inputs, §3.4](water_input.md#34-use-in-smartpi)).

---

## 3. Online identification

### 3.1 Bootstrap order

In phase `HYSTERESIS`, learning follows a strict order:

1. the active-response dead time must be reliable before useful collection for
   `a` (`heat` in HEAT, `cool` in COOL),
2. the passive-response dead time must be reliable before useful collection
   for `b` (`cool` in HEAT, `heat` in COOL),
3. `b` must progress before `a` through a soft gate.

Rules effectively coded:

- `AB_MIN_SAMPLES_B = 8`,
- `AB_MIN_SAMPLES_A = 6`,
- `AB_MIN_SAMPLES_A_CONVERGED = 6`,
- `AB_A_SOFT_GATE_MIN_B = 8`.

SmartPI leaves `HYSTERESIS` once `b` has 8 measurements and `a` has
6 measurements. Both buffers continue filling up to `AB_HISTORY_SIZE = 31`
during normal SmartPI regulation.

In a zone with two heat sources, the zone has a joint fit: one model fitted for both sources
together. Once this fit is published, SmartPI's `a`, `b` and these histories are set from it, and
SmartPI then leaves `HYSTERESIS`. Outside `HYSTERESIS`, the fit's `a` and `b`, with the counts that
its reliability checks use, are kept at every calculation
([Zone dependencies, §5.5](zone_dependencies.md#55-learning)).

The `b` convergence used for drift-aware `a` learning relies on `b_converged_for_a()`:

1. `learn_ok_count_b >= 11`,
2. `len(_b_hat_hist) >= 5`,
3. `MAD(b_hat)/Med(b_hat) <= 0.30`,
4. `range(last_5_b_hat)/Med(b_hat) <= 0.10`.

### 3.2 Effective learning window

The code no longer uses a `WINDOW_MIN_MINUTES` constant. Learning now relies on `LearningWindowManager` and on a sliding window that:

- requires outdoor temperature to be available,
- obeys thermal governance except during calibration,
- blocks bootstrap collection until the required dead times are reliable,
- applies a post-resume pause (`LEARNING_PAUSE_RESUME_MIN = 20`),
- anchors the window start after the dead-time end when needed,
- monitors power stability through the coefficient of variation (`U_CV_MAX`),
- extends the window while the slope is not yet considered robust,
- gives up after `DT_MAX_MIN = 240` minutes if no robust signal emerges,
- pauses while a learning gate is set (a neighbour's door open or settling, a neighbour's open
  window, the second source's heat not yet known, the emitter cooling down because the boiler is
  not called or warming up again once it starts, a humidity event: a shower or cooking, §4.7),
  and discards the learning window in progress
  each time a gate starts or ends,
- subtracts from the measured temperature change the part that the known inputs cause: their rate
  `known_rate = Σ g_i·(T_i − T) + Σ q_j`, integrated over the window,
- counts the power as 0 while the zone calls for heat and the boiler is not called
  ([Water and boiler inputs, §4](water_input.md#4-a-boiler-that-is-not-called)).

In practice, the decision to submit or extend the window depends on slope robustness, not on a fixed minimum duration.

### 3.3 Robust slope estimation

`ABEstimator.robust_dTdt_per_min()` applies slope guardrails:

- minimum amplitude: `DT_DERIVATIVE_MIN_ABS = 0.05°C`,
- minimum number of level changes: `OLS_MIN_JUMPS = 3`,
- statistical significance: `OLS_T_MIN = 2.5`.

The slope is computed on `dt_est.tin_history`, which holds the **raw** measured temperature (a
new sample when the reading changes, or every 60 s). The guardrails count level changes, so they
rely on the sensor's steps: the EMA-filtered temperature `t_int_lp` would turn one 0.1 °C step
into several small levels, and a slow slope would pass too early and come out too flat (about
half of the true cooling rate). The filtered samples are kept apart (`_tin_lp_history`) and only
serve the dead-time detection (section 3.5).

Validated slopes are then fed into `ABEstimator.learn()`, which:

- rejects physical outliers (`max_abs_dT_per_min = 0.35`),
- learns `b` if `u < U_OFF_MAX`,
- learns `a` if `u > U_ON_MIN`,
- requires `a > 0` in HEAT and `a < 0` in COOL,
- publishes values with robust aggregation (median or weighted median depending on configuration).

### 3.4 Estimation of `a` and `b`

The calculation followed by `learn()` is:

- OFF phase:

$$ b = -\frac{dT/dt}{T_{int} - T_{ext}} $$

- ON phase:

$$ a = \frac{dT/dt + b \cdot (T_{int} - T_{ext})}{u} $$

Histories are filtered through median/MAD. The published magnitude of `a` is
bounded while preserving the mode sign; `b` remains strictly positive.

With water heating, `u` is the power at the reference water (the command times `φ`).

When the measurements of `a` or of `b` move for good (a lasting drift), `ab_drift.py` recentres
that history. It only accepts drift candidates that follow each other closely (sequence numbers
at most three apart). Each channel numbers its own candidates (`learn_seq_a`, `learn_seq_b`), so
heating measurements between two cooling measurements do not hide a real drift of `b` (and the
other way round). A state saved with the older shared counter `learn_seq` starts both counters
from it.

### 3.5 Dead-time estimation

`DeadTimeEstimator` implements an independent FSM. Its channels describe the
physical room response, not the power-transition direction:

- `deadtime_heat`: delay before temperature starts rising,
- `deadtime_cool`: delay before temperature starts falling.

Therefore OFF -> ON learns `heat` in HEAT mode but `cool` in COOL mode. The
ON -> OFF transition learns the opposite passive response.

Reliability becomes true as soon as at least one valid measurement has been captured on the corresponding channel. Published values are the averages of the `heat` and `cool` histories.

The estimator pauses while a learning gate is set. Each time a gate starts or ends, the
observation in progress is reset. With water heating it gets the command, not the power (0 during
domestic hot water). While the zone calls for heat and the boiler is not called, it gets 0, so it
times no response. The boiler's start after such a hold is not timed either: that delay is the
water's, not the valve's.

---

## 4. Smart-PI control

### 4.1 PI gains

`GainScheduler.calculate()` applies the following logic:

1. if `tau`, the sign of `a`, or the active dead time is unreliable, fall
   back to `KP_SAFE = 0.55` and `KI_SAFE = 0.010`,
2. otherwise compute IMC/SIMC from the active-gain magnitude:

$$ K_p = \frac{1}{|a| \cdot (\lambda + L)} $$

3. compute $K_i = K_p / \tau$; with active heat exchanges, $\tau = 1 / (loss + \sum_i g_i)$, which the exchanges make shorter (section 2.1),
4. clamp the gains and apply governance freezes.

The active dead time is `deadtime_heat` in HEAT and `deadtime_cool` in COOL.

Important:

- near-band damping is applied continuously between the configured near-band edge and the setpoint,
- `NEAR_BAND_TAPER_KI_MIN` reduces the integral gain more strongly than `NEAR_BAND_TAPER_KP_MIN` near the setpoint.

### 4.2 Feed-forward

The structural feed-forward is computed from:

$$ k_{ff} = \frac{b_{eff}}{a} $$

and:

$$ u_{ff1} = clamp(k_{ff} \cdot (SP - T_{ref}), 0, 1) \cdot warmup\_scale $$

Without known inputs, `b_eff = b` and `T_ref = T_ext` (section 2.1). FF3, the setpoint
trajectory, the thermal twin, the near band and the FF trim windows use the same `b_eff` and
`T_ref`.

In COOL, $a < 0$ and therefore $k_{ff} < 0$. When outdoors is warmer than the
setpoint, the two negative differences correctly produce positive cooling
power.

`warmup_scale` is not a simple on/off switch. It depends on:

- the number of valid learning episodes,
- the number of cycles since reset,
- the reliability of `tau`.

This is combined with:

- `u_ff2`: slow `FFTrim` correction,
- `ABConfidence`: confidence policy for `a/b`,
- fallback in `AB_BAD` mode to a zero feed-forward after `AB_BAD_PERSIST_CYCLES = 3`.

`FFTrim` is also frozen (`boiler_not_heating`) while the zone calls for heat and the boiler is not
called, and while the zone's emitter settles before or after such a period.

### 4.3 FF3

`ff3.py` adds a predictive correction that is enabled by default and can be disabled by configuration:

- horizon computed by `ff3_predictor.compute_ff3_horizon()`: `deadtime_cycles + FF3_RESPONSE_LOOKAHEAD_CYCLES`, clamped between `FF3_MIN_HORIZON_CYCLES` and `FF3_MAX_HORIZON_CYCLES`,
- local open-loop prediction with the exact discrete ZOH 1R1C model, without calling `ThermalTwin1R1C.step()` and without updating the twin observer,
- max authority `FF3_MAX_AUTHORITY = 0.20`, multiplied by `authority_factor`,
- exploration step `FF3_DELTA_U = 0.05`,
- local quadratic scoring with tracking cost, terminal cost, overshoot cost, and movement cost.

FF3 is disabled if any of the following conditions is not met:

- configuration enabled,
- heating mode,
- outdoor temperature available,
- reliable `tau`,
- thermal twin initialized and prediction usable,
- thermal twin not warming up,
- valid twin steady state,
- no calibration,
- no power shedding,
- no recent setpoint change,
- not in deadband,
- in near-band,
- compatible governance regime,
- no active `setpoint` trajectory,
- a credible external-disturbance context is present.

The disturbance context accepts a structurally usable model even when `model_reliable = False`.
In that case, `prediction_quality = "degraded"` and authority is reduced through `FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR`.
`warming_up = True` and `T_steady_valid = False` remain absolute blockers.

This disturbance context does not use `T_steady` as an RMSE false negative.
It relies on:

- a persistent mismatch between the twin prediction and the observed behavior, interpreted as a credible residual (`bias_warning` or `external_gain_detected` or `external_loss_detected`),
- then dynamic coherence through `perturbation_dTdt` and, when needed, the measured thermal slope.

Under this contract, FF3 is no longer a generic near-band optimizer. It is reserved for disturbance recovery, and it no longer triggers a dedicated cycle restart on deadband entry.

### 4.4 Setpoint management

`SmartPISetpointManager` now drives an analytical trajectory for the P branch.
This trajectory is activated only when a significant thermal gap appears, the model is considered reliable, and the setpoint change calls for shaping the proportional reference.

The principle is:

- the I branch keeps using the raw setpoint,
- the P branch receives `filtered_setpoint`,
- the P branch keeps the raw setpoint until the predicted braking zone is reached,
- a significant setpoint change arms late braking once; in HEAT, its subsequent entry compares the predicted rise with the distance to `target - LANDING_SAFETY_MARGIN_C`, without reapplying the arming threshold,
- disturbance-triggered entry continues to require the significant-gap threshold,
- the exact learned 1R1C model, `deadtime_cool`, the remaining cycle latency, and the committed cycle power are used to detect the braking zone,
- a smooth late-braking trajectory is then applied near the target while preserving a minimum positive proportional demand,
- for HEAT setpoint trajectories, a landing cap can constrain the internal command after PI computation when the model predicts that stored heat is enough to reach the target,
- when braking is no longer needed, the filtered reference returns progressively to the raw target before the trajectory stops,
- for a trajectory triggered by a setpoint change, entering the `release` phase keeps that phase locked until the trajectory ends, with no return to `tracking`,
- `trajectory_active` indicates whether the analytical trajectory is running,
- the trajectory ends only once the handoff remains bumpless, the measured temperature is close enough to the target, and the landing state allows release, or when the reliability conditions are no longer met.

The landing cap uses the discrete 1R1C form in internal linear command space:

$$
\alpha = e^{-b \cdot h}
$$

$$
T_{pred} = T_{ext} + (T - T_{ext}) \cdot \alpha + \frac{a}{b}(1-\alpha) \cdot u
$$

The cap solves the maximum command that keeps the predicted temperature below `target - LANDING_SAFETY_MARGIN_C`. It is applied after normal PI computation and before soft constraints, so `u_pi` remains the raw PI diagnostic while `landing_u_cap` explains the final command reduction.

The canonical `live.setpoint` block publishes the summary:

- `filtered_setpoint`,
- `trajectory_active`,
- `trajectory_source`,
- `landing_active`,
- `landing_reason`,
- `landing_u_cap`,
- `landing_coast_required`.

The same payload in both modes exposes trajectory and landing details under
`live.analysis.trajectory` and `live.analysis.landing`:

- `start_setpoint`, `target_setpoint`, `tau_ref_min`, `elapsed_s` and `phase`,
- `pending_target_change_braking`, `braking_needed` and `model_ready`,
- `remaining_cycle_min`, `next_cycle_reference`, `bumpless_delta` and `bumpless_ready`,
- `setpoint_for_p_cap`, `predicted_temperature`, `predicted_rise` and `target_margin`,
- `release_allowed`, `time_to_target_min`, `release_blocked_by_slope`,
- `command_before_cap` and `command_after_cap`.

The setpoint reference shaping remains limited to the P branch so the integral path keeps the raw setpoint untouched and learning is not disturbed. The landing cap is a separate post-PI command governor for HEAT setpoint trajectories; it does not rewrite the integral and does not change the valve linearization curve.

The current code also applies an explicit guard on positive integral growth during catch-up phases:

- after a significant setpoint change,
- after resuming from `hvac_off`,
- after resuming from detected window opening,
- after resuming from power shedding,
- during a disturbance-recovery trajectory.

This guard does not block integral discharge. If the stored integral already
points against the requested recovery, the guard
allows that opposing memory to unwind as far as zero. It still blocks any new
accumulation beyond zero in the protected direction. This prevents a negative
integral inherited from the previous setpoint from holding the room below a
new, higher setpoint.

The guard is released only when two conditions are met:

- the release error has returned close to the configured deadband scale,
- the signed recovery slope has collapsed persistently.

The slope test combines two criteria:

- a relative threshold based on the maximum recovery slope observed during the catch-up phase,
- an absolute floor in `°C/h` so a very small peak slope cannot make release overly permissive.

The signal used for that release error depends on the servo context:

- during an active setpoint trajectory, release is based on `error_p`, i.e. the filtered-setpoint error actually followed by the proportional branch,
- outside a trajectory, release is based on `error_i`, i.e. the raw setpoint error.

This separation prevents the guard from remaining active after the trajectory has already converged to its filtered reference while the system is physically stabilized below the raw setpoint.

The signed raw error still remains the safety reference: if `error_i <= 0`, the guard is released immediately.

The recovery slope is handled symmetrically:

- in heating mode, a positive slope means moving toward the setpoint,
- in cooling mode, the slope is logically inverted so the same physical reading remains valid.

As long as the guard stays active, `TRAJECTORY_I_RUN_SCALE` may limit positive integral growth during the trajectory. Once the guard is released, that attenuation is no longer applied: the I branch returns to its normal dynamics to correct the static residual.

Window resumes and power-shedding resumes in heating mode add one more stage:

- on resume, SmartPI first arms an explicit `I:HOLD`,
- that hold remains active while the heating response is still inside the usable `deadtime_heat` window,
- when that phase ends, SmartPI re-evaluates the signed residual error,
- it arms the positive guard only if that residual error is still large enough to characterize a meaningful catch-up phase,
- otherwise the I branch returns directly to normal behavior.

This sequence prevents the integral from learning a resume transient that is still dominated by heating dead time.

Transient catch-up states are not restored after a reboot:

- the active analytical trajectory is cleared,
- the integral guard is reset,
- any temporary `integral_hold_mode` is cleared.

The restart therefore keeps only useful persistent PI memory, without restoring transient servo states that are no longer physically valid outside their original runtime session. The wall-clock timestamp of the last valid active regulation tick is persisted and is not refreshed while HVAC is OFF. If inactivity exceeds 96 hours, controller PI memory is purged on resume while the learned A/B model remains available.

The HVAC mode of the saved model (`est.model_hvac_mode`) is restored too. When Home Assistant
restarts in the other mode (for example the state was saved in HEAT and the thermostat starts in
COOL), the first calculation sees the mode change and resets the PI state, as a change of mode
while running does: an integral learned on the heating plant does not drive the cooling.

Some gaps in the regulation restart the time tracking, as OFF does: a missing temperature
sensor, a guard cut, and a thermostat working as another thermostat's additional source
([Zone dependencies, §5.7](zone_dependencies.md#57-executive-path)). The first calculation after
the gap then integrates nothing, instead of the error over the whole gap.

### 4.5 Deadband, near-band, and protections

`DeadbandManager` handles:

- a symmetric deadband with absolute hysteresis (`DEADBAND_HYSTERESIS = 0.025`),
- an asymmetric near-band in heating mode,
- automatic near-band sizing when `deadtime_heat` and the model are usable,
- fallback to configured thresholds otherwise.

Additional protections currently present are:

- `SmartPIGuards`: `guard_cut` and `guard_kick`,
- tracking anti-windup,
- signed integral guard during resumes, catch-up phases, and setpoint changes,
- explicit `I:HOLD` during the post-resume `deadtime_heat` phase for `window_resume` and `power_shedding_resume` in heating mode,
- deadband hold logic,
- with water heating, the `no_heat` hold: the command and the integral are held during domestic hot water, and only the integral is held while the water brings almost no heat ([Water and boiler inputs, §3.4](water_input.md#34-use-in-smartpi)),
- a humidity event (a shower or cooking): learning pauses and the integral is held (section 4.7),
- a neighbour's open window, while the connection to that neighbour is active: the reaction chosen for that neighbour. It is either off, through the window-off path with its protected resume, or the zone's frost or eco preset, and it never asks for more heating or cooling than the setpoint does ([Zone dependencies, §4.5](zone_dependencies.md#45-a-window-at-a-connected-neighbour)).

The calculation also distinguishes two zones around the setpoint:

- the hysteretic deadband, used to keep the `in_deadband` state stable,
- the core deadband, defined by `abs(error_i) < deadband_c` once the time constant is reliable, used to actually freeze P and I.

This split avoids keeping P and I frozen only because the hysteresis still holds the deadband state while the error has already left the configured deadband.

### 4.6 Auto-calibration

`AutoCalibTrigger` supervises the algorithm outside the control law.

Implemented behavior:

- initial snapshot when `tau`, `deadtime_heat`, and `deadtime_cool` are reliable,
- fallback snapshot after `AUTOCALIB_DT_COOL_FALLBACK_DAYS = 7` days without reliable `deadtime_cool`,
- rolling snapshot every `AUTOCALIB_SNAPSHOT_PERIOD_H = 120` hours,
- hourly check loop,
- cooldown guard `AUTOCALIB_COOLDOWN_H = 24` hours,
- stagnation thresholds `AUTOCALIB_A_MAD_THRESHOLD = 0.25` and `AUTOCALIB_B_MAD_THRESHOLD = 0.30`,
- positive exit only after at least `AUTOCALIB_EXIT_NEW_OBS_MIN = 1` new observation on both `a` and `b`, plus coherent dead times,
- up to `AUTOCALIB_MAX_RETRIES = 3` attempts,
- planned retry delay `AUTOCALIB_RETRY_DELAY_H = 6` hours.

The forced cycle is managed by `CalibrationManager`:

- `COOL_DOWN` down to `sp - 0.3°C`,
- `HEAT_UP`,
- `COOL_DOWN_FINAL` back to `sp`.

A calibration in progress is cancelled when a neighbour's open window triggers a reaction. It is
also cancelled when the zone calls for heat and the boiler is not called, because the zone's
response to heat would not come.

The supervisor's decisions are published as Home Assistant events. Every event carries `entity_id`, the thermostat's climate entity, and `name`, the thermostat's name:

| Event                              | Fired when                                                                    | Other fields                                                          |
| ---------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `smartpi_autocalib_snapshot_taken` | a snapshot is taken                                                           | `reason` (`initial`, `fallback_7d`, `rolling`), `dt_cool_unavailable` |
| `smartpi_autocalib_triggered`      | a calibration is triggered by stagnation or by `force_smartpi_calibration`    | `triggered_params`, `retry_count`                                     |
| `smartpi_autocalib_success`        | a supervised calibration exits with all criteria met                          | `improved_params`, `retry_count`                                      |
| `smartpi_autocalib_retry`          | a supervised calibration exits with missing criteria and a retry is scheduled | `retry_count`, `missing_criteria`                                     |
| `smartpi_autocalib_degraded`       | the last retry fails and the model is marked degraded                         | `retry_count`                                                         |

Before this format, the events carried the thermostat's name in `entity_id`, and Home Assistant's
recorder rejected it. An automation that looked for the name in `entity_id` should now look for it
in `name`.

### 4.7 Humidity events

A shower or cooking heats the room with heat SmartPI does not know, and the room's humidity
shows it. When Versatile Thermostat publishes the room's humidity (its humidity feature,
`current_humidity`, from a configured or found sensor), `HumidityEvent` follows it at each
calculation. Nothing is configured: without a humidity sensor nothing changes.

- An event starts when the humidity rises by `HUMIDITY_EVENT_RISE` within
  `HUMIDITY_EVENT_WINDOW_S`; the lowest level of that window is the event's baseline.
- It ends when the humidity falls below the baseline plus `HUMIDITY_EVENT_END_SHARE` of the rise
  (from the event's peak), and at the latest after `HUMIDITY_EVENT_MAX_S`.
- `HUMIDITY_EVENT_SETTLE_S` of settling follow; the levels after it start a new history, so the
  event's slow tail is no new rise.
- A missing or non-numeric reading starts nothing; an event already running ends by its clock.

While an event or its settling runs:

- the `humidity_event` learning gate pauses the learning of `a`, `b` and dead times, as the other
  gates (section 3.2), and the zone's dependencies do not learn,
- the integral is held (`I:HOLD`, hold source `humidity_event`): the event's heat would teach it a
  smaller heating need (a larger cooling one), and the room would miss its setpoint for hours
  after the event. A guard in one direction is not enough: it still lets the integral's held need
  unwind towards zero,
- the feed-forward trim takes no window.

The regulation itself goes on: the proportional term and the guards answer the event's heat.

| Constant                   | Value | Why |
| -------------------------- | ----- | --- |
| `HUMIDITY_EVENT_RISE`      | 5 %RH | a shower rises 20–30 %RH, cooking about 10 %RH; the weather moves the humidity much less within 10 min |
| `HUMIDITY_EVENT_WINDOW_S`  | 600 s | a shower reaches its peak within 2–16 min |
| `HUMIDITY_EVENT_END_SHARE` | 0.25  | the humidity falls back to a quarter of the rise 40–90 min after the peak |
| `HUMIDITY_EVENT_MAX_S`     | 3 h   | a humidity that stays high (a drying laundry) does not pause learning for good |
| `HUMIDITY_EVENT_SETTLE_S`  | 30 min | the walls and the air give back the event's heat |

The values come from eight weeks of two bathrooms and a kitchen: one event a day in a bathroom
(0.4 in the kitchen), learning paused 1.6–1.7 h a day in a bathroom and 0.6 h in the kitchen.

---

## 5. Software architecture

### 5.1 Orchestrators

| File                      | Class            | Role                                                                     |
| ------------------------- | ---------------- | ------------------------------------------------------------------------ |
| `prop_algo_smartpi.py`    | `SmartPI`        | algorithm facade, full orchestration                                     |
| `prop_handler_smartpi.py` | `SmartPIHandler` | Home Assistant bridge: persistence, periodic timer, services, attributes |

The handler reads the inputs from the heating system through the plugin's `heating_inputs.py`:
the water data and the boiler's call from the source chosen in the thermostat's options (entities,
a provider, or Versatile Thermostat's central boiler).

The handler reads the thermostat's settings per key, through the plugin's `settings.py`, when
the algorithm starts. First Versatile Thermostat's entry, without the settings that belong to
one thermostat (Versatile Thermostat's own TPI delays are not used). Then the shared settings
(`SHARED_SETTINGS` in `const.py`: deadband, hysteresis, release tau factor, setpoint filter, FF3,
damped P, full diagnostics) from the "SmartPI defaults" entry, or from the thermostat's own entry
when its switch "Use the SmartPI defaults" is off. Then the thermostat's own entry: its
"thermostat only" settings (`THERMOSTAT_SETTINGS`: heating type, activation delays, PWM cycle
restart, valve curve), its neighbours, its zone and its water source. A "thermostat only"
setting that is not set is not used. The code's starting values (`DEFAULT_OPTIONS`) only fill
the forms; a shared setting missing from an entry edited by hand takes the form's value and is
logged once as a configuration error. Saved options replace the entry's first data as a whole,
so a setting removed in the options stays removed.

Each change reloads only the thermostats it concerns, each once (`__init__.py`, taken from the
author's newer code):

- a save of a thermostat's entry reloads that thermostat, and the main source of a zone whose
  additional source it is;
- a save of the defaults entry reloads the thermostats without an entry of their own, and the
  entries that use the defaults (their setup reloads their thermostat);
- a new entry reloads its thermostat;
- a removed entry reloads, once it has left Home Assistant's list of entries, the thermostats that
  used it;
- unloading an entry (Home Assistant stopping, an entry disabled) reloads nothing.

The SmartPI algorithm and the four services are registered once, when the integration starts,
and stay registered: reloading or removing an entry does not leave Versatile Thermostat without
its algorithm.

### 5.2 `smartpi/` modules effectively used

| Module                 | Main role                                                   |
| ---------------------- | ----------------------------------------------------------- |
| `const.py`             | constants, enums, governance matrix                         |
| `learning.py`          | slope estimation, `ABEstimator`, `DeadTimeEstimator`        |
| `ab_aggregator.py`     | median or weighted-median aggregation of `a/b` measurements |
| `ab_drift.py`          | persistent drift detection and recentering for `a/b`        |
| `learning_window.py`   | learning-window management and guardrails                   |
| `gains.py`             | gain calculation and freezing                               |
| `controller.py`        | discrete PI, anti-windup, hold, hysteresis                  |
| `deadband.py`          | deadband and near-band                                      |
| `setpoint.py`          | analytical setpoint trajectory and HEAT landing cap         |
| `integral_guard.py`    | guard against positive integral growth in recovery phases   |
| `feedforward.py`       | `u_ff1/u_ff2/u_ff3` orchestration                           |
| `ff_trim.py`           | causal slow bias and bounded trim/integral ownership plans  |
| `command_ownership.py` | frozen command projection and scheduler binding tracker     |
| `ff_trim_periodic.py`  | phase-closed periodic-equilibrium window selection          |
| `ff_ab_confidence.py`  | confidence policy for `a/b`                                 |
| `ff3.py`               | bounded FF3 predictive correction                           |
| `governance.py`        | freeze decisions                                            |
| `calibration.py`       | forced-calibration FSM                                      |
| `autocalib.py`         | supervision and automatic triggering                        |
| `guards.py`            | `guard_cut` and `guard_kick`                                |
| `thermal_twin_1r1c.py` | thermal twin and predictive diagnostics                     |
| `diagnostics.py`       | canonical live diagnostic payload                           |
| `diagnostic_history.py` | versioned envelope and stable Recorder series               |
| `tint_filter.py`       | adaptive indoor-temperature filtering                       |
| `timestamp_utils.py`   | monotonic / wall-clock conversion                           |
| `effective_model.py`   | known inputs and their composition into `b_eff`, `T_ref`    |
| `dependencies.py`, `dependency_inputs.py` | dependency items, their validation and state; readings of one calculation |
| `dependency_model.py`  | known inputs, learning gates, a neighbour's window reaction; the zone with two sources |
| `coupling_estimator.py` | learning of the exchange coefficients `k`                  |
| `zone_fit.py`, `source_model.py`, `source_candidates.py` | the zone's joint fit, the second source's heat and time scales |
| `split.py`, `source_settings.py`, `source_link.py` | split rules and allocation loop, source settings, the link between sources |
| `water_input.py`       | the water's factor `φ` from the water data                  |
| `boiler_call.py`       | whether the boiler heats a calling zone                     |
| `humidity_event.py`    | humidity events (a shower, cooking) from the room's humidity |

The modules of the extensions are described in
[Zone dependencies, §6](zone_dependencies.md#6-components-and-interfaces) and
[Water and boiler inputs, §5](water_input.md#5-components-and-interfaces).

### 5.3 Internal composition of `SmartPI`

Components instantiated by `SmartPI.__init__()` notably include:

```python
self.gov = SmartPIGovernance(name)
self.sp_mgr = SmartPISetpointManager(name, enabled=use_setpoint_filter)
self.ctl = SmartPIController(name)
self.est = ABEstimator(mode=aggregation_mode)
self.learn_win = LearningWindowManager(name)
self.deadband_mgr = DeadbandManager(name, near_band_deg)
self.calibration_mgr = CalibrationManager(name)
self.gain_scheduler = GainScheduler(name)
self.tint_filter = AdaptiveTintFilter(name, enabled=ENABLE_ADAPTIVE_TINT_FILTER)
self.integral_guard = SmartPIIntegralGuard(name)
self.twin = ThermalTwin1R1C(dt_s=SMARTPI_RECALC_INTERVAL_SEC, gamma=0.1)
self.guards = SmartPIGuards()
self.autocalib = AutoCalibTrigger(name)
self._ff_trim = FFTrim()
self._fftrim_observer = CausalFFTrimObserver(cycle_min)
self._fftrim_periodic_observer = PeriodicFFTrimObserver(cycle_min)
self._command_ownership = CommandOwnershipTracker()
self._ab_confidence = ABConfidence()
self.dependency_state = DependencyState()
self.dependency_model = DependencyModel(self.dependency_state)
self.water = WaterInput()
self.boiler = BoilerCall()
```

### 5.4 Current persistence

The causal observer aligns a second, time-weighted control-ownership timeline
with the physical-power timeline. A strict deadband window can therefore
separate the stable integral bias $J=mean(K_i integral)$ from the physical
deficit $D=H-mean(u_{applied})$. After three independent coherent windows, one
bounded transaction applies
`delta_trim = alpha * lambda * (J + D)` and
`delta_u_i = -alpha * lambda * J`, hence
`delta_u_cmd = alpha * lambda * D`. The same factor `alpha` handles trim
authority, FF branch bounds, integral capacity, and command headroom. The
transaction runs only at the post-anti-windup cycle boundary and is identical
in the signed HEAT and COOL model power space.
Transfers below the generic actuator-precision threshold remain ignored unless
they strictly reduce opposing stored trim and integral contributions. This
ownership compaction keeps the same atomic bounds and command invariant.

The stationary and periodic observers reuse one physical power and ownership
timeline but keep separate thermal samples, washout state, and persistence
buffers. The periodic path accepts lower saturation and integral hold as parts
of a controller-driven cycle. It closes only after a return to the same thermal
phase, following at least `max(6 VT cycles, 2 × dead time)`, so its horizon has
no fixed thirty-minute floor. Its time-weighted causal estimate never performs
an integral ownership transfer; persistent corrections remain subject to the
same trim learning rate and authority limit.

The current payload from `SmartPI.save_state()` notably contains:

```python
{
    "est_state": {...},
    "dt_est_state": {...},
    "gov_state": {...},
    "ctl_state": {...},
    "sp_mgr_state": {...},
    "lw_state": {...},
    "db_state": {...},
    "cal_state": {...},
    "gs_state": {...},
    "integral_guard_state": {...},
    "twin_state": {...},
    "guards_state": {...},
    "ac_state": {...},
    "ff_v2_trim": {...},
    "tint_filter_state": {...},
    # only when used:
    "dependencies": {...},          # learned exchanges, the zone's fit and the source's state
    "dependency_reset_token": "...",  # the last reset asked by a form, applied once
    "water": {...},                 # the water input's availability and averages
}
```

The optional keys are described in [Zone dependencies, §8](zone_dependencies.md#8-persistent-state)
and [Water and boiler inputs, §7](water_input.md#7-persistent-state). A payload without these keys loads as before.

**When the state is written.** `state_store.py` (`SmartPIStateStore`) writes it under the key
`vtherm_smartpi.<thermostat name>` and still reads a state found under the older key
`versatile_thermostat.smartpi.<thermostat name>`. The control loop asks for a delayed write at
most once every five minutes (`SAVE_DELAY_SEC = 300`); the state is taken when the write happens,
and Home Assistant writes a pending one when it stops. The services, the removal of the
thermostat and a cancelled calibration write at once. So does a reset of the learned model asked
by the zone's form (`dependency_reset_token`): a restart within the five minutes would otherwise
reset the model a second time.

Only the learned trim remains persistent. Ownership windows, pending
proposals, and integral transfers are transient; after restart the observer is
rebuilt and the existing three-cycle trim freeze still applies.

For switch commands, SmartPI stages a frozen ownership snapshot immediately
before submitting the scheduler request. The cycle-start callback binds it only
when HVAC mode, projected ON/OFF durations, and realized power agree. Normal
second quantization is represented by the projection; a minimum timing
constraint remains a rejected causal segment. A missing or mismatched callback
never falls back to the live controller state. Valve command snapshots follow
the same frozen ownership rule in actuator space before their linear model
mapping.

### 5.5 Published diagnostics

`diagnostics.py` exposes the algorithm and canonical live views:

- `build_diagnostics()`: compact or full version depending on `debug_mode`,
- `build_published_diagnostics()`: canonical `live` payload for the dedicated diagnostic sensor,
- `build_debug_diagnostics()`: compatibility accessor returning that same canonical payload.

`diagnostic_history.py` wraps this payload in `schema_version`, `live` and the
stable eight-series `history` block. Normal sensor entities exclude `live` from
Recorder; the debug sensor subclass records it directly. Debug mode changes
retention only and does not add a second diagnostic block.

`analysis.learning.learn_ok_count` and `learn_skip_count` are runtime counters
relative to the current Home Assistant process. Persisted model counters remain
internal and are used for model quality and warm-up decisions. When the thermal
twin is usable, its public diagnostic fields are exposed under `analysis.twin`.

`live` also carries these blocks, only when they are used:

- `dependencies`: the composed model (`b_eff`, `t_ref`), the learning gate, the window reaction,
  one entry per neighbour, and `second_source` for the main source of a zone with two heat
  sources; a thermostat that works as an additional source shows only its role
  ([Zone dependencies, §9](zone_dependencies.md#9-diagnostics));
- `water`: the water's factor, where it comes from, the holds, and the source of each water signal
  (`inputs`), for a water heating type;
- `boiler`: whether the boiler is called, the chosen source (`entities`, `provider:<name>`,
  `vtherm_central_boiler`) and its signals (`inputs`), the reason and the demand, and the learning
  mode that follows from it; shown when a call source is chosen
  ([Water and boiler inputs, §8](water_input.md#8-diagnostics));
- `humidity`: the room's humidity, the event's state (`idle`, `event`, `settling`), its baseline
  and peak, and the number of events since the start; shown once a humidity reading has been seen
  (section 4.7).

With water data, the `power` values are power at the reference water, while
`current_cycle_percent` and `next_cycle_percent` are the command sent to the emitter.

`feedforward.fftrim.command_ownership` exposes the latest binding status,
rejection reason, request sequence, requested/projected/realized powers and
durations, their power delta, and the timing-constraint flag. These values are
observational: they explain a causal acceptance or rejection without changing
the learning decision.

---

## 6. Important constants

### 6.1 Learning

| Constant                       | Value  |
| ------------------------------ | ------ |
| `AB_HISTORY_SIZE`              | `31`   |
| `AB_MIN_SAMPLES_B`             | `8`    |
| `AB_MIN_SAMPLES_A`             | `6`    |
| `AB_MIN_SAMPLES_A_CONVERGED`   | `6`    |
| `AB_A_SOFT_GATE_MIN_B`         | `8`    |
| `AB_CONFIDENCE_MIN_SAMPLES_A`  | `11`   |
| `AB_CONFIDENCE_MIN_SAMPLES_B`  | `11`   |
| `AB_B_CONVERGENCE_MIN_SAMPLES` | `11`   |
| `AB_B_CONVERGENCE_MIN_BHIST`   | `5`    |
| `AB_B_CONVERGENCE_MAD_RATIO`   | `0.30` |
| `AB_B_CONVERGENCE_RANGE_RATIO` | `0.10` |
| `DT_DERIVATIVE_MIN_ABS`        | `0.05` |
| `OLS_MIN_JUMPS`                | `3`    |
| `OLS_T_MIN`                    | `2.5`  |
| `DT_MAX_MIN`                   | `240`  |
| `U_OFF_MAX`                    | `0.05` |
| `U_ON_MIN`                     | `0.20` |

### 6.2 Regulation

| Constant                      | Value   |
| ----------------------------- | ------- |
| `KP_SAFE`                     | `0.55`  |
| `KI_SAFE`                     | `0.010` |
| `MAX_STEP_PER_MINUTE`         | `0.25`  |
| `SETPOINT_BOOST_RATE`         | `0.50`  |
| `AW_TRACK_TAU_S`              | `120.0` |
| `SMARTPI_RECALC_INTERVAL_SEC` | `60`    |
| `LEARNING_PAUSE_RESUME_MIN`   | `20`    |

### 6.3 Bands and setpoint shaping

| Constant                   | Value   |
| -------------------------- | ------- |
| `DEFAULT_DEADBAND_C`       | `0.05`  |
| `DEADBAND_HYSTERESIS`      | `0.025` |
| `DEFAULT_NEAR_BAND_DEG`    | `0.40`  |
| `NEAR_BAND_TAPER_KP_MIN`   | `0.75`  |
| `NEAR_BAND_TAPER_KI_MIN`   | `0.45`  |
| `SETPOINT_BOOST_THRESHOLD` | `0.3`   |
| `SETPOINT_BOOST_ERROR_MIN` | `0.3`   |

### 6.4 Auto-calibration and FF

| Constant                          | Value  |
| --------------------------------- | ------ |
| `AUTOCALIB_SNAPSHOT_PERIOD_H`     | `120`  |
| `AUTOCALIB_DT_COOL_FALLBACK_DAYS` | `7`    |
| `AUTOCALIB_COOLDOWN_H`            | `24`   |
| `AUTOCALIB_A_MAD_THRESHOLD`       | `0.25` |
| `AUTOCALIB_B_MAD_THRESHOLD`       | `0.30` |
| `AUTOCALIB_MAX_RETRIES`           | `3`    |
| `AUTOCALIB_RETRY_DELAY_H`         | `6`    |
| `AUTOCALIB_EXIT_NEW_OBS_MIN`      | `1`    |
| `FF_TRIM_RHO`                     | `0.15` |
| `FF_TRIM_LAMBDA`                  | `0.05` |
| `AB_BAD_PERSIST_CYCLES`           | `3`    |
| `FF3_DELTA_U`                     | `0.05` |
| `FF3_MAX_AUTHORITY`               | `0.20` |
| `FF3_NEARBAND_GAIN`               | `0.50` |
| `FF3_MIN_HORIZON_CYCLES`          | `2`    |
| `FF3_RESPONSE_LOOKAHEAD_CYCLES`   | `2`    |
| `FF3_MAX_HORIZON_CYCLES`          | `8`    |
| `FF3_ACTION_SENSITIVITY_EPS_C`    | `1e-4` |
| `FF3_SCORE_EPS_COST`              | `1e-4` |
| `FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR` | `0.5` |

---

## 7. Safety-First governance

The matrix actually used in `const.py` is:

| Regime           | Thermal (`a/b`) | Gains              |
| ---------------- | --------------- | ------------------ |
| `WARMUP`         | `ADAPT_ON`      | `FREEZE`           |
| `EXCITED_STABLE` | `ADAPT_ON`      | `ADAPT_ON`         |
| `NEAR_BAND`      | `ADAPT_ON`      | `ADAPT_ON`         |
| `DEAD_BAND`      | `HARD_FREEZE`   | `HARD_FREEZE`      |
| `SATURATED`      | `ADAPT_ON`      | `FREEZE`           |
| `HOLD`           | `HARD_FREEZE`   | `SOFT_FREEZE_DOWN` |
| `PERTURBED`      | `HARD_FREEZE`   | `HARD_FREEZE`      |
| `DEGRADED`       | `HARD_FREEZE`   | `HARD_FREEZE`      |

This governance is central because:

- it prevents learning on data considered polluted,
- it avoids moving gains in poorly informative regimes,
- it still keeps thermal learning active in some constrained regimes such as `SATURATED`.

---

## 8. References

1. **Astrom K.J. and Hagglund T.**, work on PID regulation and FOPDT model usage.
2. **Sundaresan K.R. and Krishnaswamy P.R.**, estimation of delay and time-constant parameters.
3. **OLS** regression with Student t-test for slope validation.
