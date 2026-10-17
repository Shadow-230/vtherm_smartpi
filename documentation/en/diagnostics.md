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

The `history` block contains these eleven series:

| Path under `history` | Meaning |
|---|---|
| `temperature.indoor` | Indoor temperature |
| `setpoint.filtered_setpoint` | Filtered control setpoint |
| `setpoint.nominal_reference` | Nominal profile reference evaluated by the governor |
| `power.applied_percent` | Applied command after output constraints |
| `power.command_percent` | Requested command before actuator effects |
| `power.pi_percent` | PI contribution |
| `power.ff_percent` | Feed-forward contribution |
| `power.governor_cap_percent` | Governor command ceiling, in % |
| `power.before_governor_cap_percent` | PI request before the cap is applied, in % |
| `model.a` | Learned heating gain |
| `model.b` | Learned heat-loss coefficient |

The nominal reference is recorded to 0.01 °C, and the cap and pre-cap request
to 0.1 %. These three fields extend schema v2; their sources remain under
`live.analysis.reference_governor`. An absent reference or cap is `null`,
not zero. A genuinely zero cap is 0 %. After braking ends, the pre-cap request
may remain available while the nominal reference and cap disappear.

The pre-cap request already uses the filtered P reference; it is not a PI
simulation on the user setpoint. The applied command does not measure delivered
heat. Charts must break the cap curve at `null` and tolerate missing fields
from older versions.

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
| `setpoint` | Filtered setpoint, trajectory, boost and reference-governor summary |
| `autocalib` | Automatic calibration supervisor state |
| `calibration` | Calibration state, retry count and last completion time |
| `analysis` | Advanced fields used by the supplied diagnostic cards |
| `dependencies` | Only with zone dependencies: the composed model, learning gate, window reaction, neighbours and the zone with two heat sources |
| `water` | Only for a water heating type: the water's factor, where it comes from, the holds, and the source of each water signal |
| `boiler` | Only for a water heating type with a boiler's call source chosen: the source, the call and the learning mode that follows from it |

`analysis` groups the advanced live values into `control`, `learning`,
`trajectory`, `reference_governor`, `deadtime`, `governance`, `feedforward` and, when
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

### `dependencies`

When a thermostat has zone dependencies (neighbouring zones, or a zone with two heat
sources), `live` gains a `dependencies` block. It holds the composed model (`b_eff`,
`t_ref`), the learning gate, the window reaction applied, and one entry per neighbour with
its learned heat exchange. For the main source of a zone with two heat sources, it also
holds a `second_source` block: the additional source's command and heat, its power cap,
the recovery boost and the zone's fit. A thermostat that works as another thermostat's
additional source publishes only its role, the main source and the command it applies.
Without dependencies, the block is absent. Its fields are listed in
[Zone dependencies, §9](zone_dependencies.md#9-diagnostics).

### `water`

For a thermostat with a water heating type, `live` gains a `water` block. It holds the
water factor applied, where it comes from, the heating type, the averaged supply water
temperature, the last reasons the water data source gave, the hot-water hold (`no_heat`), the
low-heat integral hold (`low_heat`), the "water too cold" flag, and `inputs`: for each water
signal, its source (an entity, a provider, or none) and whether it gives data now. With water
data, the `power` values are power at the reference water, while `current_cycle_percent` and
`next_cycle_percent` are the command sent to the emitter. Its fields are listed in
[Water and boiler inputs, §8](water_input.md#8-diagnostics).

### `boiler`

For a water heating type with a boiler's call source chosen in its options (Versatile
Thermostat's central boiler, entities or a provider), `live` gains a `boiler` block, also while
the source gives no data. It holds the source, whether the boiler is called and, if not, why
not, how learning treats this (`emitter_cooling`, `held`, `emitter_warming`), the demand
compared with the boiler's start thresholds where the source gives it, and `inputs`: where each
signal comes from. Its fields are listed in
[Water and boiler inputs, §8](water_input.md#8-diagnostics) and explained in
[§4](water_input.md#4-a-boiler-that-is-not-called).

### `humidity`

When Versatile Thermostat gives the room's humidity, `live` gains a `humidity` block once a
reading has been seen. It holds the humidity (`humidity`), the state of a humidity event (`idle`,
`event`, `settling`), the event's `baseline` and `peak`, and `events`, the number of events since
the start. During an event and its settling `learning_gate` is `humidity_event`. They are explained
in [the technical documentation, §4.7](technical_doc.md#47-humidity-events).

## Publication cadence

The diagnostic sensor is refreshed for meaningful control inputs, cycle
boundaries, explicit forced calculations and diagnostic services. The internal
60-second recalculation timer does not publish when its inputs and committed
power are unchanged. The sensor also suppresses a write when both its state and
complete attribute envelope are identical to the preceding publication.

This limits state-machine and Recorder churn without changing SmartPI control
timing.

## Consumer paths

Live consumers must read from `attributes.live`. Historical graph consumers
must read from `attributes.history`. Consumers should verify
`attributes.schema_version == 2` before interpreting the nested structure.
