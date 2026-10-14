# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

- Fix a false warning about a zone's master. After a zone was saved, or any SmartPI entry
  was reloaded, the log said that the zone's master "no longer has exactly this thermostat
  and ... as its devices", although it had exactly these two. At a reload, Versatile
  Thermostat builds SmartPI before Home Assistant gives the thermostat its entity ID, so
  the check compared the master's devices with an empty ID. The devices are now compared
  by unique ID. A full restart did not show it: there, SmartPI is set up after Versatile
  Thermostat has built its thermostats, and starts later, once they have their IDs.
- Add the QAS test pack (`tests/qas/`): tests on a real Home Assistant, run on the bench's eight
  test instances, for the set without Smart Boiler. It holds a room simulator as a Home
  Assistant integration (its physics is now the in-process simulator's file, which
  `tests/sim/plant.py` loads), test-only helpers, a `configuration.yaml` per instance role,
  setup scripts that create every entry through the API, and the runner with 35 scenarios on
  the bench's harness. The catalogue is in `testing.md`, section 6; the set with Smart Boiler
  is listed there as planned. New tests check the simulator and the pack in CI, and, locally,
  each role's setup through the real forms of Versatile Thermostat 10.5.1 and SmartPI.
- Rework the documentation. Where the fork changes SmartPI's existing behaviour, the
  technical, diagnostics and user documents now describe it in place. `zone_dependencies.md`
  and `water_input.md` share one layout and give the reasons behind the design, the data
  read from Versatile Thermostat and Smart Boiler, and the formulas. All tests, the
  simulator and the scenarios with their criteria are in `testing.md`. The documentation is
  written in plain language and is also in French and Polish, translated from the English.
  The French user, technical and diagnostics documents and README now say everything the
  English ones say (some of the author's French text was behind: the license, the logo,
  several wordings). The technical documentation lists the positive-integral guard, its
  saved keys and the core deadband again, and the user guide names the settings as the
  interface shows them.
- Learn nothing wrong from a boiler that is not called. A zone calling for heat while
  its boiler is held off (a start threshold of devices, power or valve opening, an
  activation delay) took its command for heat and lost its learned emitter power. SmartPI
  now reads the boiler's call — Smart Boiler's `boiler_called` and `call_reason` where
  Smart Boiler controls the boiler, otherwise Versatile Thermostat's central boiler for a
  thermostat it serves — and, once the call has gone unanswered for 15 min (plus the
  cycle for a switched device), learns with the power 0 (only how the zone cools),
  waiting while the emitter cools after the stop and warms after the start; the
  dead-time estimator does not time the boiler's start, a calibration is cancelled, and
  the feed-forward trim and the zone exchanges pause. Regulation is unchanged. A `boiler`
  block in the diagnostics shows the call and, from Smart Boiler, the demand against its
  thresholds. Documented in `documentation/en/water_input.md`, section 11.
- Fix the fade-in of known exchanges. Always-connected neighbours enter together once
  each is known, and the loss fitted without them replaces SmartPI's b at the pace
  their exchanges enter, so the feed-forward no longer dips for an hour when they are
  published (nor drops a neighbour still unknown). In a zone with two sources their k
  from the fit is in the model at once, like the fit's b. A zone's fit known before a
  restart puts the neighbours' k it gives, and the source's a_f of both HVAC modes,
  fully in the model at once.
- The zone form no longer asks for the main source's delay and lag, which nothing
  used (the zone's fit learns its delay), and offers the reset only when the
  additional source's delay or lag really changes.
- Add the options form of a zone with two heat sources: "Zone with two heat sources"
  in the menu of a thermostat entry sets the zone up from its master thermostat (a
  Versatile Thermostat over climate whose two devices are SmartPI thermostats), checks
  it (self-regulation off, two SmartPI devices, one temperature sensor, not in another
  zone), and offers Source 1 and Source 2 with the main source, the split rule
  recommended for their heating types and its parameter. The zone is saved in the main
  source's entry; the other thermostat gets an entry if it has none, and its options
  show the zone and only the settings it still uses. Changing the main source moves the
  zone with its neighbours. Changes that affect what the zone has learned, including
  neighbour changes, offer to reset the learned model. Saving the additional source's
  entry restarts the zone's main source too, and the main source warns at start when
  the master no longer has exactly these two thermostats.
- Add a zone with two heat sources: a room heated by two SmartPI thermostats, for
  example a heated floor next to a radiator. The main source's SmartPI regulates; the
  additional source applies its commands with its own cycle, delays and water data.
  A joint fit of the zone learns the losses, both sources, the main source's delay,
  the additional source's time scale and the neighbours' exchanges, apart for heating
  and cooling; while SmartPI learns, the additional source works in learning blocks,
  and SmartPI starts from the fit and keeps its model. Afterwards an allocation loop moves the
  additional source towards the split rule (`together`, `source_first`, `lead_band`),
  it answers the zone's error, and a predictive brake keeps its heat on the way from
  overshooting the setpoint. While the zone recovers from a setback, the main source
  works as if alone until it is about to reach its setpoint. A main source that stops publishing hands the zone to the
  additional source's own SmartPI until it is back; switched off, it keeps telling its
  source so.
  Documented in `documentation/en/zone_dependencies.md`, section 6.
- Raise the bound of the known heat's shift of the reference temperature to 40 °C, and
  record switched cycles at their water factor in the exchange learning.
- Add the water input: a heating type in the regulator settings (water radiator by
  default, water underfloor heating, electric mat, fast electric). For a water type,
  SmartPI reads the supply water of the thermostat's circuit from the Smart Boiler
  plugin (its feature manager or public API) and turns the command into power at a
  reference water: the emitter gets power divided by the water factor, learning sees
  the command times the factor, hot water with priority holds the command and the
  integral, the cold water of an idle boiler holds only the integral (the zone can
  still call for heat), and water data appearing or going for good offer a model reset
  as a repair.
  SmartPI publishes the factor it applies as `water_factor` in the thermostat's
  `specific_states`. Without water data nothing changes. Documented in
  `documentation/en/water_input.md`.
- Add zone dependencies to a thermostat entry: neighbouring zones that exchange
  heat with the zone through a door or permanently. SmartPI learns the exchange
  and folds it into its model at a single substitution point, and a neighbour's
  open window can pause learning or switch the zone off, to frost or to eco.
  The options of a per-thermostat entry now open a menu: regulator settings, zone
  dependencies and, for a linearized valve, the valve curve. Without dependencies
  nothing changes.
- Document zone dependencies, the zone with two heat sources and the water input in
  `documentation/en/zone_dependencies.md` and `documentation/en/water_input.md`, with
  links from the user, technical and diagnostics documentation and the README.
- Fire AutoCalib events with the thermostat climate `entity_id` and move the
  thermostat name to a `name` field, so the recorder no longer fails with
  `Invalid entity ID` when an entity filter is configured.

## [0.4.0]

- Reworked attributes schema and HA database recording rules.
  Now it only records the "history" dict in normal mode and booth "live" and "history" dicts in debug mode.
  This will prevent HA database to be bloated by smartpi diagnostics in non debug mode.
- Add action service to pause and resume SmartPI thermal learning
  This can be usefull to trigger when a secondary heating system could
  bias the thermal model.
- Make the mono-mode 1R1C model fully signed in COOL: learn a negative active
  coefficient, use COOL dead time for IMC gains, and enable signed structural
  feed-forward while preserving the positive passive coefficient.
- Classify learned dead times by physical temperature response in both modes
  and migrate incompatible legacy COOL A/dead-time state conservatively;
  prevent HEAT's passive-dead-time AutoCalib fallback from validating COOL.
- Align FF trim learning with distinct temperature measurements and the delayed
  power that caused them, using independent thermal windows and the learned
  1R1C model.
- Transfer only the causally observed stable integral bias into FF trim at the
  post-anti-windup cycle boundary, while retaining the physical deficit as a
  real bounded command change in both HEAT and COOL.
- Expose the aligned P/I/Ki ownership terms, requested and applied transaction
  deltas, command invariant, rejection reason, and actuator engagement state.
- Bind valve ownership to the integer-percent opening published by the
  thermostat, while retaining scheduler realization as independent evidence.
- Synchronize SmartPI cycle projections with the scheduler effective minimum
  activation and deactivation delays.
- Preserve frozen switch command ownership across successive scheduler
  automatic PWM repeat quantizations without accepting unrelated cycles.
- Allow periodic FF trim windows to use observed cycles with negligible
  scheduler limiting, up to five equivalent cycle seconds.

## [0.3.8] 2026-08-18

- Allow the directional integral guard to unwind opposing PI memory to zero, preventing setpoint recovery from stalling below target.
- Remove the obsolete `hysteresis_thermal_guard` control path and diagnostic attribute, preventing stale guard state from blocking positive integral correction after setpoint changes.
- Expire persisted PI controller memory after more than 96 hours without active regulation while preserving learned A/B thermal coefficients.

## [0.3.7] - 2026-08-09

- Fix SmartPI device linking for single config entry ownership ( multi config entry went obsolete on ha 2026.8.0 )

## [0.3.6] - 2026-05-31

- Fix SmartPI diagnostic sensor recreation after VT reload
- Restrict SmartPI A learning fallback to calibration. Require robust ON slope for SmartPI A learning
- Fix restored SmartPI AB confidence state
- Add HVAC and target context to SmartPI learning windows, and skip B learning in HEAT when the passive heat-loss gradient is too weak or outdoor temperature is too close to target.

## [0.3.5] - 2026-05-23

- Fix SmartPI diagnostic entity persistence

## [0.3.4] - 2026-05-22

- Clean up SmartPI diagnostic entity when target algo changes
- Unlink registered device to config entry when not active

## [0.3.3] - 2026-05-20

- Add SmartPI PWM cycle force option for band transitions ( disabled by default )
- publish ab_bootstrap to confidence attribute in bootstrap mode
- publish specific_states.regulation_diagnostics attribute in vtherm to point diagnostic entitiy path

## [0.3.2] - 2026-05-19

- Dampen proportional output inside SmartPI deadband ( when enable P in deadband is activated )
- some attributes changes
- updated markdown cards

## [0.3.1] - 2026-05-04

- Fix SmartPI climate state publishing on sensor updates
- Deadtime-aware open-loop prediction for FF3
- Stabilize SmartPI valve output near minimum activation delay

## [0.3.0] - 2026-04-30

- Improve SmartPI landing release safety with slope timing guard
- Add guarded SmartPI landing cap release
  Release landing when its cap is non-constraining in the residual zone, keep the release sticky, and reset counters on inactive landing paths.
- Make SmartPI landing residual release sticky
  Prevent the landing cap from reactivating after residual release during the same trajectory, while allowing rearm when demand becomes significant again. Add focused landing coverage.
- updated markdown cards

## [0.2.2] - 2026-04-29

- Fix landing cap residual release
  Avoid keeping the setpoint landing cap active in release phase when only a small residual error remains. Add coverage for the stuck filtered setpoint case.

## [0.2.1] - 2026-04-29

- Implement setpoint landing cap for SmartPI
  Adds model-aware landing control to the setpoint filter so heating setpoint increases can reduce internal demand before reaching the target. The proportional trajectory now exposes a HEAT-only landing cap, applied after PI computation and before soft constraints, while keeping the raw PI diagnostic and integral behavior unchanged.

## [0.2.0] - 2026-04-26

- Valve power curve linearisation
  Inspired from @caiusseverus blueprint
  improve valve non linear power curve. more info: https://github.com/jmcollin78/versatile_thermostat/discussions/1704

## [0.1.10] - 2026-04-24

- Sync SmartPI committed power after valve mid-cycle updates
- Fix SmartPI default diagnostics registration
- Quicker boostrap phase:
  Adjust SmartPI bootstrap thresholds and AB publication
  (now needs only 8 B & 6 A before quitting bootstrap mode.)

## [0.1.9] - 2026-04-22

- remove vtherm_api dependency ( use VT installed one )

## [0.1.8] - 2026-04-21

- Align prop handler state change hook with changed flag API

## [0.1.7] - 2026-04-20

- switch to official vtherm_api package

## [0.1.6] - 2026-04-20

- Fix diagnostics sensor creation for default-bound thermostats
- Expose SmartPI phase as diagnostics sensor state
- Mark SmartPI diagnostics sensor as diagnostic entity

## [0.1.5] - 2026-04-19

- Remove default config entry in menu

## [0.1.4] - 2026-04-19

- Fix GuardState runtime typing in SmartPI guards
- Auto-create SmartPI default config entry on first setup

## [0.1.3] - 2026-04-18

- harden plugin services and refresh state after reset

## [0.1.2] - 2026-04-18

- replace vtherm api to main repo

## [0.1.0] - 2026-04-15

- Initial release of Versatile Thermostat SmartPI integration
- SmartPI proportional algorithm implementation
- HACS integration support
- Comprehensive documentation in French and English
- GitHub Actions for automated validation and releases
