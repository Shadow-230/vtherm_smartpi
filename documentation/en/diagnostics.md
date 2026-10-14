# SmartPI diagnostics

SmartPI exposes a dedicated Home Assistant diagnostic sensor. Its state is the
current regulation phase and its attributes follow schema version 2.

## Attribute envelope

```yaml
schema_version: 2
live:     # Complete live diagnostics
history:  # Stable time-series contract
```

`live` is the source for dashboards, Markdown cards and interactive analysis.
It has the same content in normal and debug mode. `history` contains only the
values intended for historical charts.

There is no separate `debug` attribute block.

## Recorder profiles

Normal mode excludes the complete `live` attribute from Home Assistant
Recorder. Only `schema_version`, the sensor state and `history` are retained.

Debug mode records both `live` and `history`. The content is not duplicated
inside `history`: the historical contract remains identical in both modes.
Debug mode therefore changes retention, not publication.

The `history` block contains these eight series:

| Path under `history` | Meaning |
|---|---|
| `temperature.indoor` | Indoor temperature |
| `setpoint.filtered_setpoint` | Filtered control setpoint |
| `power.applied_percent` | Physically applied power |
| `power.command_percent` | Requested command before actuator effects |
| `power.pi_percent` | PI contribution |
| `power.ff_percent` | Feed-forward contribution |
| `model.a` | Learned heating gain |
| `model.b` | Learned heat-loss coefficient |

## Live diagnostics

The `live` block is organized by responsibility:

| Block | Content |
|---|---|
| `control` | Phase, regulation mode, gains, saturation and regulation bands |
| `power` | Current and next-cycle power, PI/FF split, limits and applied power |
| `temperature` | Indoor/outdoor temperatures, errors and integral state |
| `model` | A/B model, confidence, time constant and dead times |
| `learning` | Learning stage, bootstrap progress, accepted updates and drift state |
| `governance` | Thermal regime and model-update decision |
| `feedforward` | FF3 status, dead-band source and canonical FFTrim diagnostics |
| `setpoint` | Filtered setpoint, trajectory, boost and landing summary |
| `autocalib` | Automatic calibration supervisor state |
| `calibration` | Calibration state, retry count and last completion time |
| `analysis` | Advanced fields used by the supplied diagnostic cards |

`analysis` groups the advanced live values into `control`, `learning`,
`trajectory`, `landing`, `deadtime`, `governance`, `feedforward` and, when
available, `twin`.

FFTrim uses nested canonical blocks:

```yaml
live:
  feedforward:
    fftrim:
      stationary: {}
      periodic: {}
      transfer: {}
      command_ownership: {}
      observation_mode: ...
      last_reject_reason: ...
      last_update_reason: ...
      last_result: ...
      last_transaction: ...
      windows_since_update: ...
```

## Publication cadence

The diagnostic sensor is refreshed for meaningful control inputs, cycle
boundaries, explicit forced calculations and diagnostic services. The internal
60-second recalculation timer does not publish when its inputs and committed
power are unchanged. The sensor also suppresses a write when both its state and
complete attribute envelope are identical to the preceding publication.

This limits state-machine and Recorder churn without changing SmartPI control
timing.

## Zone dependencies

When a thermostat has zone dependencies (neighbouring zones, or a zone with two heat
sources), `live` gains a `dependencies` block: the composed model (`b_eff`, `t_ref`),
the learning gate, the window reaction applied, one entry per neighbour with its learned
exchange and, for the main source of a zone with two heat sources, a `second_source`
block (the additional source's command and heat, its power cap, the recovery boost, the
zone's fit). A thermostat working as another's additional source publishes only its
role, the main source and the command it applies. The block is absent without
dependencies. Its fields are listed in
[Zone dependencies, §9](zone_dependencies.md#9-diagnostics).

## Water input

For a thermostat whose heating type uses water, `live` gains a `water` block: the factor
applied, its source, the heating type, the averaged supply water, the last reasons from
Smart Boiler, the hot-water hold (`no_heat`), the low-heat integral hold (`low_heat`) and
the "water too cold" flag. With water data the `power` values are power at the reference
water, while `current_cycle_percent` and `next_cycle_percent` are the command sent to the
emitter. Its fields are listed in [Water input, §8](water_input.md#8-diagnostics).

## Boiler call

For a water heating type whose boiler's call is known (from Smart Boiler, or from Versatile
Thermostat's central boiler), `live` gains a `boiler` block: the source, whether the boiler is
called, why not, how learning treats it (`emitter_cooling`, `held`, `emitter_warming`) and, from
Smart Boiler, the demand against its start thresholds. Its fields are listed in
[Water input, §8](water_input.md#8-diagnostics) and explained in
[§11](water_input.md#11-a-boiler-that-is-not-called).

## Consumer paths

Live consumers must read from `attributes.live`. Historical graph consumers
must read from `attributes.history`. Consumers should verify
`attributes.schema_version == 2` before interpreting the nested structure.
