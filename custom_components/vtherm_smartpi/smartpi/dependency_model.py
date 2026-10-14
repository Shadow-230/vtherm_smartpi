"""Turns dependency readings into known model inputs, learning gates and diagnostics.

Runs inside the algorithm at each calculation: it learns the exchange with each
neighbour, decides whether SmartPI's own learning must pause, and hands the
exchanges whose coefficient is reliable to the model composition.

Learning gates, by priority:

- ``neighbour_window``: a connected neighbour thermostat has an open window; the
  draught changes the exchange, so nothing is learned, k included.
- ``door_open``: a door of a door-mode connection is open; a, b and dead times
  pause while k learns.
- ``source_heat``: the zone's additional source heats while the zone's joint
  fit is not published yet; SmartPI's own learning waits for the fit, which
  learns both sources together (at most ``ZONE_FIT_TIMEOUT_S``).
- ``door_settling``: the first minutes after a door opens or closes.

The exchange coefficients k learn only when SmartPI itself could learn, and
not in the first minutes after a door opens or closes.

An always-connected neighbour does not gate learning: SmartPI's b absorbs its
exchange as it does today, and the coupling estimator fits b together with k
until k can be published, after which the learning correction cleans b.

A zone with two heat sources is driven here too (documentation/en/
zone_dependencies.md, section 6). The main source is this thermostat: its
SmartPI regulates. The additional source works from the first day:

- while SmartPI learns, in blocks alternating between two commands, so that the
  zone's joint fit (``zone_fit.py``) tells both sources apart; a cold zone gets
  it at full power and a warm one none;
- afterwards, an allocation loop moves it towards the rule's split, it answers
  the zone's error in proportion, and a predictive brake caps it at what keeps
  the zone at its setpoint once the heat on the way has arrived; the lead's
  feed-forward hands over its load as the source's known heat rises, so the
  brake holds the source only where the lead could not take an overshoot back.

The joint fit gives a, b and the additional source's ``a_f``, the neighbours' k,
the main source's delay and the additional source's time scale, apart for
heating and cooling. SmartPI starts from the fit's a and b and keeps them, the
fit's windows standing for its own learning; once known the additional
source's heat ``a_f * x_f`` is a known input.

A coefficient that becomes known enters the control law gradually over
``KNOWN_INPUT_FADE_S``: the controller's integral had absorbed its effect and
follows smoothly instead of seeing a step in its feed-forward.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Any

from ..hvac_mode import VThermHvacMode, VThermHvacMode_COOL, VThermHvacMode_HEAT
from .const import (
    DOOR_SETTLING_S,
    COUPLING_K_MAX,
    KNOWN_INPUT_FADE_S,
    SOURCE_BOOST_ERROR_C,
    SOURCE_BRAKE_STEPS,
    SOURCE_LEARN_BLOCK_MIN_S,
    SOURCE_LEARN_COLD_C,
    SOURCE_LEARN_HIGH,
    SOURCE_LEARN_LOW,
    SOURCE_LEARN_WARM_C,
    SOURCE_PREDICTION_MIN_S,
    SOURCE_PREDICTION_STEP_S,
    SOURCE_SENSITIVITY_MIN_C,
    SOURCE_TRAJECTORY_MIN,
    SOURCE_X_MIN,
    SOURCE_X_OFF,
    SPLIT_DEMAND_MAX,
    SPLIT_DEMAND_MIN,
    SPLIT_ERROR_GAIN,
    SPLIT_HOLD_ERROR_C,
    SPLIT_LEAD_FULL,
    SPLIT_LEAD_STOPPED,
    SPLIT_LIMIT_ERROR_C,
    SPLIT_PREDICTED_SHARE,
    ZONE_FIT_LEAD_DELAYS_MIN,
    ZONE_FIT_MAX_GAP_S,
    ZONE_FIT_MAX_REL_SE,
    ZONE_FIT_TIMEOUT_S,
)
from .coupling_estimator import CouplingEstimator, CouplingTick
from .dependencies import MODE_ALWAYS, MODE_DOOR, DependencyState
from .dependency_inputs import DependencyReadings, NeighbourObservation, SourceObservation
from .effective_model import ExchangeInput, HeatInput, KnownInputs
from .source_candidates import SourceCandidates
from .source_settings import SourceSettings
from .split import AllocationLoop, AllocationTiming
from .zone_fit import ZoneFit, ZoneFitResult

_LOGGER = logging.getLogger(__name__)

GATE_NEIGHBOUR_WINDOW = "neighbour_window"
GATE_DOOR_OPEN = "door_open"
GATE_DOOR_SETTLING = "door_settling"
GATE_SOURCE_HEAT = "source_heat"

SOURCE_MODE_OFF = "off"
SOURCE_MODE_LEARN = "learn"
SOURCE_MODE_ALLOCATION = "allocation"

_SHARED_COUPLING_KEY = "coupling"
_FIT_HEAT = "heat"
_FIT_COOL = "cool"

ACTION_TURN_OFF = "turn_off"
ACTION_FROST = "frost"
ACTION_ECO = "eco"
# The most protective reaction wins when several neighbours have a window open.
_ACTION_PRIORITY = {ACTION_ECO: 1, ACTION_FROST: 2, ACTION_TURN_OFF: 3}
_HEAT_PRESETS = {ACTION_FROST: "frost_temp", ACTION_ECO: "eco_temp"}
_COOL_PRESETS = {ACTION_ECO: "eco_cool_temp"}


def cool_key(item_id: str) -> str:
    """Key of a source's coefficient learned in cooling."""
    return f"{item_id}:cool"


def _usable_preset(value: float | None, temp_range: tuple[float, float] | None) -> bool:
    if value is None or value <= 0.0:
        return False
    return temp_range is None or temp_range[0] <= value <= temp_range[1]


def _sign(hvac_mode: VThermHvacMode | None) -> float:
    """Heat added by the sources: positive in heating, negative in cooling."""
    return -1.0 if hvac_mode == VThermHvacMode_COOL else 1.0


def _fit_k(fit: ZoneFitResult) -> dict[str, float]:
    """The neighbours' k a published fit gives: within (0, COUPLING_K_MAX] and with a
    relative standard error of at most ZONE_FIT_MAX_REL_SE."""
    se = dict(fit.k_se)
    return {
        item_id: k
        for item_id, k in fit.k
        if (s := se.get(item_id)) is not None and 0.0 < k <= COUPLING_K_MAX and s <= ZONE_FIT_MAX_REL_SE * k
    }


def _fit_key(hvac_mode: VThermHvacMode | None) -> str | None:
    """The fit of an HVAC mode: heating, cooling, or none outside them."""
    if hvac_mode == VThermHvacMode_HEAT:
        return _FIT_HEAT
    if hvac_mode == VThermHvacMode_COOL:
        return _FIT_COOL
    return None


@dataclass(frozen=True)
class ModelContext:
    """What the dependency layer needs from SmartPI at one calculation."""

    now_s: float
    zone_temp: float | None
    ext_temp: float | None
    power: float
    a: float
    b: float
    deadtime_s: float | None
    # SmartPI left its learning phase: a and b are learned.
    model_reliable: bool
    hvac_mode: VThermHvacMode | None = None
    # SmartPI's own learning conditions hold: learning enabled and not paused,
    # the zone heats or cools, no shedding, guard cut or calibration.
    learning_allowed: bool = True
    # SmartPI is still learning a and b (its bootstrap phase).
    learning_phase: bool = False
    # A calibration of the lead's dead times is running.
    calibrating: bool = False
    # The lead's steady demand at the previous calculation (feed-forward plus
    # integral, before its 0..1 limits), and its output.
    lead_demand: float | None = None
    lead_output: float = 0.0
    target_temp: float | None = None
    # Most power the lead gives at full command (its water factor, its maximum on-percent).
    lead_power_cap: float = 1.0
    # The lead's cycle.
    lead_cycle_s: float = 600.0
    # The zone calls but its boiler does not heat it: its power is not heat, the
    # exchanges and the zone's fit learn nothing then.
    heat_withheld: bool = False


class _Source:
    """Runtime state of the zone's additional source."""

    def __init__(self, settings: SourceSettings) -> None:
        self.settings = settings
        self.candidates = SourceCandidates(settings.source)
        delays_s = tuple(minutes * 60.0 for minutes in ZONE_FIT_LEAD_DELAYS_MIN)
        count = len(self.candidates.candidates)
        start = self.candidates.start_index
        self.fits = {
            _FIT_HEAT: ZoneFit(count, start, delays_s),
            _FIT_COOL: ZoneFit(count, start, delays_s),
        }
        self.loop = AllocationLoop(settings.rule, AllocationTiming.for_source(settings.source))
        self.gain_ratio = 1.0
        self.cycles_after_s = -math.inf
        self.mode = SOURCE_MODE_OFF
        self.command = 0.0
        self.x_f = 0.0
        self.active = False
        # Most power the source gives at full command (its water, 1 without water data).
        self.power_cap = 1.0
        self.full_since_s: float | None = None
        # The predictive brake held the command below the allocation at the last calculation.
        self.braked = False
        # The lead's feed-forward leaves the source's heat out while the zone recovers.
        self.boost = False
        # Time each HVAC mode has spent with the source active and its fit not
        # published: the clock of the learning blocks and of the fit's timeout.
        self.learn_elapsed_s = {_FIT_HEAT: 0.0, _FIT_COOL: 0.0}
        self.clock_s: float | None = None
        # The HVAC mode the allocation loop works for.
        self.loop_mode: str | None = None
        # The candidate and gain ratio the loop's timing was computed for.
        self.timing_key = (self.candidates.start_index, 1.0)
        # The source_heat gate, held between SOURCE_X_OFF and SOURCE_X_MIN.
        self.gate = False
        self.restore: dict[str, Any] | None = None

    @property
    def heat(self):
        """The additional source's heat under the response in use."""
        return self.candidates.heat

    def fit(self, hvac_mode: VThermHvacMode | None) -> ZoneFit | None:
        """The joint fit of this HVAC mode (None outside heating and cooling)."""
        key = _fit_key(hvac_mode)
        return self.fits[key] if key is not None else None

    def timed_out(self, hvac_mode: VThermHvacMode | None) -> bool:
        """The fit of this HVAC mode has not come within ZONE_FIT_TIMEOUT_S."""
        key = _fit_key(hvac_mode)
        return key is not None and self.fits[key].result is None and self.learn_elapsed_s[key] >= ZONE_FIT_TIMEOUT_S

    def key(self, hvac_mode: VThermHvacMode | None) -> str:
        item_id = self.settings.item_id
        return cool_key(item_id) if hvac_mode == VThermHvacMode_COOL else item_id


class DependencyModel:
    """Dependency layer of one zone, backed by the algorithm's dependency state."""

    def __init__(self, state: DependencyState) -> None:
        self._state = state
        self._coupling = CouplingEstimator()
        self._door_open: dict[str, bool] = {}
        self._settling_until_s: float | None = None
        self._source: _Source | None = None
        # (a, b) of the zone's joint fit for SmartPI to start from, signed for the
        # HVAC mode (set when the fit is published during SmartPI's learning).
        self.pending_seed: tuple[float, float] | None = None
        # When each learned coefficient became known (fade-in of the control law).
        self._known_since: dict[str, float] = {}
        self.load()

    # --- second source ----------------------------------------------------

    def configure_source(self, settings: SourceSettings | None) -> None:
        """Set the zone's additional source (None without one)."""
        if settings is None:
            self._source = None
            self._coupling.set_heat_ids(set())
            return
        if self._source is not None and self._source.settings == settings:
            return
        self._source = _Source(settings)
        self._coupling.set_heat_ids({settings.item_id, cool_key(settings.item_id)})
        self._source.restore = dict(self._state.items.get(settings.item_id, {}))

    @property
    def has_source(self) -> bool:
        return self._source is not None

    @property
    def source_cycles_after_s(self) -> float:
        """Start of the last source cycle already read."""
        return self._source.cycles_after_s if self._source is not None else -math.inf

    @property
    def source_command(self) -> float | None:
        return self._source.command if self._source is not None else None

    def zone_fit(self, hvac_mode: VThermHvacMode | None) -> ZoneFitResult | None:
        """The published joint fit of the zone for this HVAC mode, if any."""
        if self._source is None:
            return None
        fit = self._source.fit(hvac_mode)
        return fit.result if fit is not None else None

    def load(self) -> None:
        """Rebuild the learned coupling from the persisted state.

        A state that cannot be read is dropped: the exchanges are learned again.
        """
        self._coupling.reset()
        self._known_since.clear()
        try:
            for item_id, item_state in self._state.items.items():
                self._coupling.load_item(item_id, item_state)
                cool = item_state.get("cool")
                if isinstance(cool, dict):
                    self._coupling.load_item(cool_key(item_id), cool)
            self._coupling.load_shared(self._state.shared.get(_SHARED_COUPLING_KEY, {}))
        except (TypeError, ValueError, AttributeError) as err:
            _LOGGER.warning("Ignoring the saved neighbour exchanges, which cannot be read: %s", err)
            self._coupling.reset()
        # Coefficients known before a restart are fully in the control law.
        for item_id in self._state.items:
            for key in (item_id, cool_key(item_id)):
                if self._coupling.k(key) is not None:
                    self._known_since[key] = -math.inf
        if self._source is not None:
            self._source.restore = dict(self._state.items.get(self._source.settings.item_id, {}))

    def reset(self) -> None:
        self._coupling.reset()
        self._door_open.clear()
        self._settling_until_s = None
        self._known_since.clear()
        self.pending_seed = None
        if self._source is not None:
            self._source = _Source(self._source.settings)

    def forget_except(self, item_ids: set[str]) -> None:
        keep = set(item_ids)
        self._coupling.forget_except(keep | {cool_key(item_id) for item_id in keep})
        self._save()

    def record_realized_power(
        self, start_s: float, end_s: float, power: float, switched: bool = False, level: float = 1.0
    ) -> None:
        """Average power a completed cycle delivered, used by the exchange learning and the zone fit."""
        self._coupling.record_realized_power(start_s, end_s, power, switched, level)

    def _save(self) -> None:
        for item_id in list(self._state.items):
            self._state.items[item_id].update(self._coupling.save_item(item_id))
        shared = self._coupling.save_shared()
        if any(shared.values()):
            self._state.shared[_SHARED_COUPLING_KEY] = shared
        else:
            self._state.shared.pop(_SHARED_COUPLING_KEY, None)

    def update(self, readings: DependencyReadings | None, ctx: ModelContext) -> KnownInputs | None:
        """Process the readings of one calculation and return the model inputs."""
        if readings is None:
            return None

        self._track_doors(readings, ctx.now_s)
        connected = [obs for obs in readings.neighbours if obs.connected]
        source = self._source
        x_f = self._update_source_heat(readings.source, ctx)
        source_key = source.key(ctx.hvac_mode) if source is not None else None
        fit = self.zone_fit(ctx.hvac_mode)
        if source is not None:
            self._tick_learning_clock(ctx)
            if fit is not None:
                # Each HVAC mode has its own fit and its own time scale.
                source.candidates.select(fit.candidate)
                x_f = self._source_heat_now(ctx)
        a_f = fit.a_f if fit is not None else None
        source_heat_unknown = self._source_gate(x_f, a_f, ctx.hvac_mode)
        gate = self._gate(connected, ctx, source_heat_unknown)

        if ctx.zone_temp is not None and ctx.ext_temp is not None:
            tick = CouplingTick(
                now_s=ctx.now_s,
                zone_temp=ctx.zone_temp,
                ext_temp=ctx.ext_temp,
                power=ctx.power,
                a=ctx.a,
                b=ctx.b,
                deadtime_s=ctx.deadtime_s or 0.0,
                connected={obs.item.item_id: obs.reading.temperature for obs in connected},
                always=frozenset(
                    obs.item.item_id for obs in connected if obs.item.mode == MODE_ALWAYS
                ),
                learning_allowed=(
                    ctx.learning_allowed
                    and not ctx.heat_withheld
                    and ctx.model_reliable
                    and ctx.deadtime_s is not None
                    and gate != GATE_NEIGHBOUR_WINDOW
                    and not self._settling(ctx.now_s)
                ),
                heats=self._source_regressor(ctx, x_f),
            )
            if self._coupling.update(tick):
                for obs in connected:
                    self._state.for_item(obs.item.item_id)
                if source is not None:
                    self._state.for_item(source.settings.item_id)
                self._save()
        if source is not None:
            self._fit_zone(readings, connected, ctx)
            fit = self.zone_fit(ctx.hvac_mode)
            a_f = fit.a_f if fit is not None else None
            if fit is not None and ctx.learning_phase:
                # SmartPI learns again (a mode change resets it, for example)
                # while this mode's fit is known: it starts from the fit at once.
                self.pending_seed = (_sign(ctx.hvac_mode) * fit.a, fit.b)

        known_k = {obs.item.item_id: self._neighbour_k(obs.item.item_id, fit) for obs in connected}
        always = [obs.item.item_id for obs in connected if obs.item.mode == MODE_ALWAYS]
        if fit is None and any(known_k[item_id] is None for item_id in always):
            # SmartPI's b holds every always-connected exchange, and the fitted loss
            # leaves them all out: they enter together, once each of them is known.
            known_k.update(dict.fromkeys(always))
        elif fit is not None:
            # The fit's b, which replaces SmartPI's at once, leaves the always-connected
            # exchanges out: they are in the model at once too.
            for item_id in always:
                if known_k[item_id] is not None:
                    self._known_since[item_id] = -math.inf
        exchanges = []
        always_k = always_weighted = 0.0
        for obs in connected:
            k = known_k[obs.item.item_id]
            weight = self._fade(obs.item.item_id, k, ctx.now_s)
            if k is not None:
                exchanges.append(ExchangeInput(obs.item.item_id, weight * k, obs.reading.temperature))
                if obs.item.mode == MODE_ALWAYS:
                    always_k += k
                    always_weighted += weight * k

        drive_exchanges = tuple(exchanges)
        if source is not None and fit is not None:
            # The predictions use the exchanges at their full value, like the fit's a, b and a_f.
            drive_exchanges = tuple(
                ExchangeInput(obs.item.item_id, k, obs.reading.temperature)
                for obs in connected
                if (k := self._neighbour_k(obs.item.item_id, fit)) is not None
                and obs.reading.temperature is not None
            )
        if source is not None:
            source.boost = self._recovery_boost(ctx, fit, drive_exchanges)

        heats = []
        if source is not None and x_f is not None and not source.boost:
            weight = self._fade(source_key, a_f, ctx.now_s)
            if a_f is not None and weight > 0.0:
                rate = _sign(ctx.hvac_mode) * weight * a_f
                heats.append(
                    HeatInput(
                        source.settings.item_id,
                        rate * x_f,
                        tuple(
                            (minutes, rate * source.heat.at(ctx.now_s, minutes * 60.0))
                            for minutes in SOURCE_TRAJECTORY_MIN
                        ),
                    )
                )
        action, setpoint = self._window_action(connected, readings, ctx)
        if source is not None:
            self._drive_source(ctx, fit, action, setpoint, drive_exchanges)
            self._save_source()

        # With the zone's fit SmartPI's b is the fit's, already without the neighbours.
        # Without it the fitted loss replaces SmartPI's b at the share of the
        # always-connected exchanges already in the model: b_eff does not step.
        always_known = fit is None and always_k > 0.0
        return KnownInputs(
            exchanges=tuple(exchanges),
            heats=tuple(heats),
            learning_gate=gate,
            loss_coefficient=self._coupling.loss_coefficient() if always_known else None,
            loss_weight=always_weighted / always_k if always_known else 0.0,
            control_action=action,
            action_setpoint=setpoint,
            status=tuple(self._status(obs, ctx, fit) for obs in readings.neighbours),
            source_command=source.command if source is not None else None,
            source=self._source_status(readings.source, ctx) if source is not None else None,
        )

    def _recovery_boost(
        self, ctx: ModelContext, fit: ZoneFitResult | None, exchanges: tuple[ExchangeInput, ...]
    ) -> bool:
        """The zone recovers: the lead's feed-forward leaves the source's heat out.

        Far from its setpoint (after a setback, say) the zone needs more than the
        steady load. Counting the additional source's rising heat, the lead's
        feed-forward would hand its share over while the zone is still cold, and
        only its gentle proportional part would drive the recovery; two
        independent SmartPI recover faster because each counts the whole load.
        So while the zone is colder than its setpoint by more than
        SOURCE_BOOST_ERROR_C, the lead works as if it were alone. The boost ends
        early, once the fitted model says the zone reaches that band within the
        lead's delay plus one of its cycles at the present powers: the heat the
        lead has already sent then lands the zone instead of overshooting it.
        """
        source = self._source
        if fit is None or source.mode != SOURCE_MODE_ALLOCATION:
            return False
        error = self._zone_error(ctx)
        if error is None or error <= SOURCE_BOOST_ERROR_C or ctx.zone_temp is None or ctx.ext_temp is None:
            return False
        sign = _sign(ctx.hvac_mode)
        lead = min(max(ctx.lead_output, 0.0), 1.0) * min(max(ctx.lead_power_cap, 0.0), 1.0)
        exchange = sum(item.conductance * (ctx.zone_temp - item.temperature) for item in exchanges)
        rate = sign * fit.a * lead + sign * fit.a_f * source.x_f - fit.b * (ctx.zone_temp - ctx.ext_temp) - exchange
        horizon_min = (fit.lead_delay_s + max(ctx.lead_cycle_s, 0.0)) / 60.0
        return error - sign * rate * horizon_min > SOURCE_BOOST_ERROR_C

    def _neighbour_k(self, item_id: str, fit: ZoneFitResult | None) -> float | None:
        """The exchange coefficient in use: the zone's joint fit's once published, which
        learns it together with both sources, else the one learned in its own windows.

        The fit's value counts only within (0, COUPLING_K_MAX] and with a relative
        standard error of at most ZONE_FIT_MAX_REL_SE: a door seldom open is
        fitted from few windows.
        """
        if fit is not None:
            k = _fit_k(fit).get(item_id)
            if k is not None:
                return k
        return self._coupling.k(item_id)

    def _fade(self, key: str, coefficient: float | None, now_s: float) -> float:
        """Share of a coefficient already in the control law (0..1)."""
        if coefficient is None:
            self._known_since.pop(key, None)
            return 0.0
        since = self._known_since.setdefault(key, now_s)
        if since == -math.inf:
            return 1.0
        return min(1.0, max(0.0, (now_s - since) / KNOWN_INPUT_FADE_S))

    def _fade_weight(self, key: str, now_s: float) -> float:
        """Share of a known coefficient already in the control law, without starting its fade."""
        since = self._known_since.get(key)
        if since is None:
            return 0.0
        if since == -math.inf:
            return 1.0
        return min(1.0, max(0.0, (now_s - since) / KNOWN_INPUT_FADE_S))

    def _update_source_heat(self, obs: SourceObservation | None, ctx: ModelContext) -> float | None:
        """Read the source's new cycles; return its x_f now, or None without a known source."""
        source = self._source
        if source is None:
            return None
        if source.restore is not None:
            restore, source.restore = source.restore, None
            source.loop.command = min(max(float(restore.get("allocation", 0.0) or 0.0), 0.0), 1.0)
            if self._same_response(restore.get("response_start")):
                # Fits learned with another emitter type or response are dropped.
                source.candidates.load_state(restore.get("response"), restore, ctx.now_s)
                for mode, key in ((_FIT_HEAT, "zone_fit"), (_FIT_COOL, "zone_fit_cool")):
                    source.fits[mode].load_state(restore.get(key))
                self._load_learning_clock(restore.get("learn_elapsed_s"))
            else:
                source.candidates.load_state(None, restore, ctx.now_s)
            fit = self.zone_fit(ctx.hvac_mode)
            if fit is not None:
                source.candidates.select(fit.candidate)
            # A fit known before a restart is fully in the control law: the
            # source's a_f and the neighbours' k it gives, in either HVAC mode.
            for hvac_mode in (VThermHvacMode_HEAT, VThermHvacMode_COOL):
                known = self.zone_fit(hvac_mode)
                if known is not None:
                    self._known_since[source.key(hvac_mode)] = -math.inf
                    for item_id in _fit_k(known):
                        self._known_since[item_id] = -math.inf
        source.active = obs is not None and obs.active
        source.power_cap = obs.power_cap if obs is not None else 1.0
        if obs is not None:
            for cycle in obs.cycles:
                if cycle.start_s <= source.cycles_after_s:
                    continue
                source.candidates.record_cycle(
                    cycle.start_s, cycle.on_s, cycle.duration_s, cycle.power, cycle.switched, cycle.level
                )
                source.cycles_after_s = cycle.start_s
        if not source.active:
            # The source does not apply the lead's commands: its heat is not known.
            source.candidates.record(ctx.now_s, 0.0)
        return self._source_heat_now(ctx)

    def _source_heat_now(self, ctx: ModelContext) -> float | None:
        """x_f now under the response in use, None while the source is not active."""
        source = self._source
        source.x_f = source.heat.value(ctx.now_s)
        return source.x_f if source.active else None

    def _same_response(self, saved: Any) -> bool:
        """The saved fits were learned with the configured emitter response.

        A state saved without it (none was) is kept.
        """
        if saved is None:
            return True
        start = self._source.settings.source
        try:
            lag_s, tau_s = float(saved[0]), float(saved[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return False
        return math.isclose(lag_s, start.lag_s, abs_tol=1.0) and math.isclose(tau_s, start.tau_s, abs_tol=1.0)

    def _load_learning_clock(self, saved: Any) -> None:
        source = self._source
        if isinstance(saved, (int, float)):
            # An older state kept one clock, for heating.
            saved = {_FIT_HEAT: saved}
        if not isinstance(saved, dict):
            return
        for key in (_FIT_HEAT, _FIT_COOL):
            value = saved.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0.0:
                source.learn_elapsed_s[key] = float(value)

    def _tick_learning_clock(self, ctx: ModelContext) -> None:
        """Count the time this HVAC mode's fit has been awaited with the source at work.

        Gaps between calculations longer than ZONE_FIT_MAX_GAP_S (a restart,
        Home Assistant stopped) do not count.
        """
        source = self._source
        last_s, source.clock_s = source.clock_s, ctx.now_s
        key = _fit_key(ctx.hvac_mode)
        if key is None or last_s is None or not source.active or source.fits[key].result is not None:
            return
        step_s = ctx.now_s - last_s
        if 0.0 < step_s <= ZONE_FIT_MAX_GAP_S:
            source.learn_elapsed_s[key] += step_s

    def _save_source(self) -> None:
        """Keep the source's command, heat, response, fits and cooling coefficient."""
        source = self._source
        item_state = self._state.for_item(source.settings.item_id)
        item_state["allocation"] = round(source.loop.command, 4)
        item_state["x_f"] = round(source.x_f, 4)
        item_state["response"] = source.candidates.save_state()
        item_state["response_start"] = [source.settings.source.lag_s, source.settings.source.tau_s]
        item_state["zone_fit"] = source.fits[_FIT_HEAT].save_state()
        item_state["zone_fit_cool"] = source.fits[_FIT_COOL].save_state()
        item_state["learn_elapsed_s"] = {key: round(value, 1) for key, value in source.learn_elapsed_s.items()}
        cool = self._coupling.save_item(cool_key(source.settings.item_id))
        if cool.get("k_history"):
            item_state["cool"] = cool

    def _source_regressor(self, ctx: ModelContext, x_f: float | None) -> dict[str, float]:
        """The source's regressor for the measurement windows: x_f, negative in cooling."""
        if self._source is None or x_f is None or ctx.hvac_mode not in (VThermHvacMode_HEAT, VThermHvacMode_COOL):
            return {}
        return {self._source.key(ctx.hvac_mode): _sign(ctx.hvac_mode) * x_f}

    def _source_gate(self, x_f: float | None, a_f: float | None, hvac_mode: VThermHvacMode | None) -> bool:
        """The source_heat gate: on once x_f reaches SOURCE_X_MIN while the zone's fit
        is not published, off below SOURCE_X_OFF or once it is.

        If the fit has not been published after ZONE_FIT_TIMEOUT_S of the source
        at work in this HVAC mode, the gate opens for good: SmartPI then learns
        on its own.
        """
        source = self._source
        if source is None:
            return False
        if x_f is None or a_f is not None or x_f < SOURCE_X_OFF or source.timed_out(hvac_mode):
            source.gate = False
        elif x_f >= SOURCE_X_MIN:
            source.gate = True
        return source.gate

    def _fit_zone(self, readings: DependencyReadings, connected: list[NeighbourObservation], ctx: ModelContext) -> None:
        """Feed the joint fit of the zone for the current HVAC mode."""
        source = self._source
        fit = source.fit(ctx.hvac_mode)
        if fit is None:
            # Off: no window may span the pause.
            for engine in source.fits.values():
                engine.reset_window()
            return
        fit.set_neighbours(tuple(obs.item.item_id for obs in readings.neighbours))
        usable = (
            source.active
            and ctx.zone_temp is not None
            and ctx.ext_temp is not None
            and not ctx.calibrating
            and not ctx.heat_withheld
            and not self._settling(ctx.now_s)
        )
        exchanges: dict[str, float] = {}
        if usable:
            for obs in connected:
                if obs.reading.is_thermostat and obs.reading.window_open:
                    usable = False
                    break
                exchanges[obs.item.item_id] = obs.reading.temperature - ctx.zone_temp
        if not usable:
            fit.reset_window()
            return
        # In cooling the sources remove heat: their columns change sign, the
        # fitted strengths stay positive.
        sign = _sign(ctx.hvac_mode)

        def delayed_power(start_s: float, end_s: float, delay_s: float) -> float:
            return sign * self._coupling._delayed_power(start_s - delay_s, end_s - delay_s)

        changed = fit.add_sample(
            ctx.now_s,
            ctx.zone_temp,
            ctx.ext_temp,
            tuple(sign * value for value in source.candidates.values(ctx.now_s)),
            delayed_power,
            exchanges,
        )
        if changed and fit.result is not None:
            source.candidates.select(fit.result.candidate)
            if ctx.learning_phase:
                self.pending_seed = (sign * fit.result.a, fit.result.b)

    def _drive_source(
        self,
        ctx: ModelContext,
        fit: ZoneFitResult | None,
        action: str | None = None,
        setpoint: float | None = None,
        exchanges: tuple[ExchangeInput, ...] = (),
    ) -> None:
        """Decide the source's command for this calculation.

        The reaction to a neighbour's open window applies to the source too:
        switched off with the lead, or held to the same frost or eco setpoint.
        A calibration of the lead holds the source where it is. Whenever the
        allocation loop does not run, it pauses: it does not integrate over the
        pause once it runs again.
        """
        source = self._source
        if (
            not source.active
            or ctx.hvac_mode not in (VThermHvacMode_HEAT, VThermHvacMode_COOL)
            or action == ACTION_TURN_OFF
        ):
            source.mode, source.command, source.braked = SOURCE_MODE_OFF, 0.0, False
            self._pause_loop()
            return
        mode_key = _fit_key(ctx.hvac_mode)
        if source.loop_mode is not None and source.loop_mode != mode_key:
            # Heating and cooling have their own split: the loop starts again.
            source.loop.reset()
            source.braked = False
        source.loop_mode = mode_key
        if ctx.calibrating:
            self._pause_loop()
            return
        if setpoint is not None and ctx.target_temp is not None:
            if ctx.hvac_mode == VThermHvacMode_COOL:
                ctx = replace(ctx, target_temp=max(setpoint, ctx.target_temp))
            else:
                ctx = replace(ctx, target_temp=min(setpoint, ctx.target_temp))
        if ctx.learning_phase:
            source.mode, source.braked = SOURCE_MODE_LEARN, False
            source.command = self._learn_command(ctx)
            self._pause_loop()
            return
        if source.mode == SOURCE_MODE_LEARN:
            # Out of the learning blocks without a step in the heat: the loop
            # starts from the heat the source gives now.
            source.loop.reset(source.x_f)
        self._update_loop_timing(ctx, fit)
        source.mode = SOURCE_MODE_ALLOCATION
        demand = self._allocation_demand(ctx)
        error = self._zone_error(ctx)
        if demand is None:
            self._pause_loop()
        else:
            # While the brake holds the source below its allocation, the loop
            # does not wind further up.
            source.loop.update(
                ctx.now_s,
                demand,
                hold_up=source.braked
                or (error is not None and error < -SPLIT_HOLD_ERROR_C and demand > SPLIT_DEMAND_MIN),
                hold_down=error is not None and error > SPLIT_HOLD_ERROR_C and demand < SPLIT_DEMAND_MAX,
                applied=source.command,
            )
        correction = SPLIT_ERROR_GAIN * error / source.gain_ratio if error is not None else 0.0
        braked = self._predictive_command(ctx, fit, exchanges, source.loop.command)
        source.braked = braked < source.loop.command
        source.command = min(max(min(source.loop.command + correction, braked), 0.0), 1.0)

    def _pause_loop(self) -> None:
        source = self._source
        source.loop.pause()
        source.full_since_s = None

    def _update_loop_timing(self, ctx: ModelContext, fit: ZoneFitResult | None) -> None:
        """The loop's speed follows the response in use and the source's power relative to the lead's."""
        source = self._source
        ratio = source.gain_ratio
        if fit is not None and ctx.a != 0.0:
            measured = max(1.0, fit.a_f / abs(ctx.a))
            if abs(measured - ratio) > 0.2 * ratio:
                ratio = measured
        key = (source.candidates.current_index, ratio)
        if key != source.timing_key:
            source.timing_key, source.gain_ratio = key, ratio
            source.loop.timing = AllocationTiming.for_source(source.candidates.current, gain_ratio=ratio)

    def _learn_command(self, ctx: ModelContext) -> float:
        """While SmartPI learns: the source works too, in blocks alternating between
        two commands so that the fit can tell its heat apart; never letting the
        zone go cold, nor heating a zone already too warm. The blocks follow the
        mode's learning clock."""
        source = self._source
        error = self._zone_error(ctx)
        if error is not None and error >= SOURCE_LEARN_COLD_C:
            return 1.0
        if error is not None and error <= -SOURCE_LEARN_WARM_C:
            return 0.0
        block_s = max(SOURCE_LEARN_BLOCK_MIN_S, 2.0 * source.candidates.current.settle_s)
        key = _fit_key(ctx.hvac_mode)
        elapsed_s = source.learn_elapsed_s[key] if key is not None else 0.0
        block = int(elapsed_s // block_s)
        return SOURCE_LEARN_HIGH if block % 2 == 0 else SOURCE_LEARN_LOW

    def _predictive_command(
        self,
        ctx: ModelContext,
        fit: ZoneFitResult | None,
        exchanges: tuple[ExchangeInput, ...],
        allocation: float,
    ) -> float:
        """The largest command that leaves the predicted zone at its setpoint.

        The model predicts the zone's error ``e(c)`` at the end of the source's
        response time with the source at command ``c``. The source may correct
        ``SPLIT_PREDICTED_SHARE`` of the error expected at its allocation: the
        cap is the largest ``c`` with ``e(c) >= (1 - share) * e(allocation)``.
        A zone too warm whatever the source does (the lead's own heat) only asks
        the source not to add to it: ``e(c) >= e(0)``. The cap is found by
        halving: ``e`` falls as ``c`` rises, flat while the lead hands over its
        load and steeper once the lead is down to 0. Heat already on the way is
        in the prediction, so the source eases off before the zone reaches its
        setpoint.
        """
        cold = self._predicted_error(ctx, fit, exchanges, 0.0)
        full = self._predicted_error(ctx, fit, exchanges, 1.0)
        if cold is None or full is None or cold - full <= SOURCE_SENSITIVITY_MIN_C:
            return 1.0
        at_allocation = self._predicted_error(ctx, fit, exchanges, allocation)
        floor = min((1.0 - SPLIT_PREDICTED_SHARE) * at_allocation, cold) - SOURCE_SENSITIVITY_MIN_C
        if full >= floor:
            return 1.0
        low, high = 0.0, 1.0
        for _ in range(SOURCE_BRAKE_STEPS):
            middle = 0.5 * (low + high)
            if self._predicted_error(ctx, fit, exchanges, middle) >= floor:
                low = middle
            else:
                high = middle
        return low

    def _predicted_error(
        self,
        ctx: ModelContext,
        fit: ZoneFitResult | None,
        exchanges: tuple[ExchangeInput, ...],
        command: float,
    ) -> float | None:
        """Zone error expected once the source has responded, its delay plus lag ahead.

        The zone follows the fitted model with the source at ``command`` (at most
        what its water allows) after the heat already on the way, and the outdoor
        and neighbour temperatures held. The lead stays at its steady demand,
        less what its feed-forward takes off as the source's known heat changes
        (``a_f / a`` per unit of ``x_f``, as far as that heat is in its control
        law); held at its demand, the lead would never hand over its load and
        the brake would keep the split where it is. Without a published fit
        there is no prediction.
        """
        source = self._source
        if (
            fit is None
            or ctx.zone_temp is None
            or ctx.target_temp is None
            or ctx.ext_temp is None
            or ctx.hvac_mode not in (VThermHvacMode_HEAT, VThermHvacMode_COOL)
        ):
            return None
        horizon_s = max(source.candidates.current.settle_s, SOURCE_PREDICTION_MIN_S)
        steps = max(1, math.ceil(horizon_s / SOURCE_PREDICTION_STEP_S))
        step_s = horizon_s / steps
        x_values = source.heat.forecast(
            ctx.now_s, [index * step_s for index in range(steps)], min(command, source.power_cap)
        )
        sign = _sign(ctx.hvac_mode)
        model_a, model_b, rate_f = sign * fit.a, fit.b, sign * fit.a_f
        demand = ctx.lead_demand if ctx.lead_demand is not None else ctx.lead_output
        handover = self._fade_weight(source.key(ctx.hvac_mode), ctx.now_s) * fit.a_f / fit.a
        lead_cap = min(max(ctx.lead_power_cap, 0.0), 1.0)
        # While the lead recovers without counting the source's heat, it counts
        # all of it again once the recovery is over.
        counted_now = 0.0 if source.boost else source.x_f
        temp = ctx.zone_temp
        step_min = step_s / 60.0
        for x_f in x_values:
            lead = min(max(demand - handover * (x_f - counted_now), 0.0), lead_cap)
            exchange = sum(item.conductance * (temp - item.temperature) for item in exchanges)
            temp += step_min * (model_a * lead - model_b * (temp - ctx.ext_temp) - exchange + rate_f * x_f)
        error = ctx.target_temp - temp
        return -error if ctx.hvac_mode == VThermHvacMode_COOL else error

    @staticmethod
    def _zone_error(ctx: ModelContext) -> float | None:
        """How far the zone is from its setpoint in the direction of the HVAC mode."""
        if ctx.zone_temp is None or ctx.target_temp is None:
            return None
        error = ctx.target_temp - ctx.zone_temp
        return -error if ctx.hvac_mode == VThermHvacMode_COOL else error

    def _allocation_demand(self, ctx: ModelContext) -> float | None:
        """Demand the allocation loop follows: the lead's steady demand, or the end
        of the demand range when the lead is stuck at a limit."""
        source = self._source
        if ctx.lead_demand is None:
            return None
        if ctx.zone_temp is None or ctx.target_temp is None:
            source.full_since_s = None
            return ctx.lead_demand
        error = ctx.target_temp - ctx.zone_temp
        if ctx.hvac_mode == VThermHvacMode_COOL:
            error = -error
        if ctx.lead_output <= SPLIT_LEAD_STOPPED and error < -SPLIT_LIMIT_ERROR_C:
            source.full_since_s = None
            return SPLIT_DEMAND_MIN
        if ctx.lead_output >= SPLIT_LEAD_FULL and error > SPLIT_LIMIT_ERROR_C:
            if source.full_since_s is None:
                source.full_since_s = ctx.now_s
            if ctx.now_s - source.full_since_s >= source.candidates.current.settle_s:
                return SPLIT_DEMAND_MAX
            return ctx.lead_demand
        source.full_since_s = None
        return ctx.lead_demand

    def _source_status(self, obs: SourceObservation | None, ctx: ModelContext) -> dict[str, Any]:
        source = self._source
        fit_engine = source.fit(ctx.hvac_mode)
        fit = fit_engine.result if fit_engine is not None else None
        settings = source.settings
        response = source.candidates.current
        timed_out = source.timed_out(ctx.hvac_mode)
        return {
            "id": settings.item_id,
            "entity": obs.entity_id if obs is not None else None,
            "active": source.active,
            "rule": settings.rule.rule,
            "mode": source.mode,
            "command": round(source.command, 3),
            "x_f": round(source.x_f, 3),
            "power_cap": round(source.power_cap, 3),
            "recovery_boost": source.boost,
            "a_f": round(fit.a_f, 5) if fit is not None else None,
            "a_f_reliable": fit is not None,
            "lead_emitter": settings.lead_emitter,
            "source_emitter": settings.source_emitter,
            "lag_min": round(response.lag_s / 60.0, 1),
            "tau_min": round(response.tau_s / 60.0, 1),
            "lead_demand_avg": (
                round(source.loop.lead_average, 3) if source.loop.lead_average is not None else None
            ),
            "zone_fit": {
                "published": fit is not None,
                "windows": round(fit_engine.windows, 1) if fit_engine is not None else 0.0,
                "a": round(fit.a, 6) if fit is not None else None,
                "b": round(fit.b, 6) if fit is not None else None,
                "a_f": round(fit.a_f, 6) if fit is not None else None,
                "lead_delay_min": round(fit.lead_delay_s / 60.0, 1) if fit is not None else None,
                "k": {item_id: round(k, 6) for item_id, k in fit.k} if fit is not None else {},
                "timed_out": timed_out,
            },
        }

    @staticmethod
    def _window_action(
        connected: list[NeighbourObservation], readings: DependencyReadings, ctx: ModelContext
    ) -> tuple[str | None, float | None]:
        """Return the reaction to open windows at connected neighbour thermostats.

        "frost" and "eco" impose the zone's own preset temperature for the
        current HVAC mode; without such a preset (Versatile Thermostat reports 0)
        or with one outside the thermostat's range, the zone is switched off.
        """
        actions = [
            obs.item.neighbour_window_action
            for obs in connected
            if obs.reading.is_thermostat
            and obs.reading.window_open
            and obs.item.neighbour_window_action in _ACTION_PRIORITY
        ]
        if not actions:
            return None, None
        action = max(actions, key=_ACTION_PRIORITY.__getitem__)
        if action == ACTION_TURN_OFF:
            return ACTION_TURN_OFF, None
        presets = _COOL_PRESETS if ctx.hvac_mode == VThermHvacMode_COOL else _HEAT_PRESETS
        setpoint = readings.presets.get(presets.get(action, ""))
        if not _usable_preset(setpoint, readings.temp_range):
            return ACTION_TURN_OFF, None
        return action, float(setpoint)

    def _track_doors(self, readings: DependencyReadings, now_s: float) -> None:
        for obs in readings.neighbours:
            if obs.item.mode != MODE_DOOR:
                continue
            previous = self._door_open.get(obs.item.item_id)
            if previous is not None and previous != obs.door_open:
                self._settling_until_s = now_s + DOOR_SETTLING_S
            self._door_open[obs.item.item_id] = obs.door_open

    def _settling(self, now_s: float) -> bool:
        return self._settling_until_s is not None and now_s < self._settling_until_s

    def _gate(
        self, connected: list[NeighbourObservation], ctx: ModelContext, source_heat_unknown: bool = False
    ) -> str | None:
        if any(obs.reading.is_thermostat and obs.reading.window_open for obs in connected):
            return GATE_NEIGHBOUR_WINDOW
        if any(obs.item.mode == MODE_DOOR for obs in connected):
            return GATE_DOOR_OPEN
        if source_heat_unknown:
            return GATE_SOURCE_HEAT
        if self._settling(ctx.now_s):
            return GATE_DOOR_SETTLING
        return None

    def _status(
        self, obs: NeighbourObservation, ctx: ModelContext, fit: ZoneFitResult | None = None
    ) -> dict[str, Any]:
        item_id = obs.item.item_id
        k = self._neighbour_k(item_id, fit)
        share = None
        if (
            k is not None
            and obs.connected
            and ctx.zone_temp is not None
            and ctx.ext_temp is not None
        ):
            exchange = k * (ctx.zone_temp - obs.reading.temperature)
            losses = ctx.b * (ctx.zone_temp - ctx.ext_temp) + exchange
            share = round(exchange / losses, 3) if abs(losses) > 1e-9 else None
        return {
            "id": item_id,
            "kind": obs.item.kind,
            "entity": obs.reading.entity_id,
            "mode": obs.item.mode,
            "available": obs.reading.usable,
            "active": obs.connected,
            "t_neighbour": obs.reading.temperature,
            "neighbour_window": obs.reading.window_open,
            "k": round(k, 6) if k is not None else None,
            "k_reliable": k is not None,
            "k_from_zone_fit": k is not None and k != self._coupling.k(item_id),
            "k_samples": self._coupling.samples(item_id),
            "indistinguishable": self._coupling.is_indistinguishable(item_id),
            "share_of_losses": share,
        }
