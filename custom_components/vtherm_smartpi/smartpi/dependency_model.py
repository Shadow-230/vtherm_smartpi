"""Turns dependency readings into known model inputs, learning gates and diagnostics.

Runs inside the algorithm at each calculation: it learns the exchange with each
neighbour, decides whether SmartPI's own learning must pause, and hands the
exchanges whose coefficient is reliable to the model composition.

Learning gates, by priority:

- ``neighbour_window``: a connected neighbour thermostat has an open window; the
  draught changes the exchange, so nothing is learned, k included.
- ``door_open``: a door of a door-mode connection is open; a, b and dead times
  pause while k learns.
- ``door_settling``: the first minutes after a door opens or closes.

An always-connected neighbour does not gate learning: SmartPI's b absorbs its
exchange as it does today, and the coupling estimator fits b together with k
until k can be published, after which the learning correction cleans b.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..hvac_mode import VThermHvacMode
from .const import DOOR_SETTLING_S
from .coupling_estimator import CouplingEstimator, CouplingTick
from ..hvac_mode import VThermHvacMode_COOL
from .dependencies import MODE_ALWAYS, MODE_DOOR, DependencyState
from .dependency_inputs import DependencyReadings, NeighbourObservation
from .effective_model import ExchangeInput, KnownInputs

GATE_NEIGHBOUR_WINDOW = "neighbour_window"
GATE_DOOR_OPEN = "door_open"
GATE_DOOR_SETTLING = "door_settling"

_SHARED_COUPLING_KEY = "coupling"

ACTION_TURN_OFF = "turn_off"
ACTION_FROST = "frost"
ACTION_ECO = "eco"
# The most protective reaction wins when several neighbours have a window open.
_ACTION_PRIORITY = {ACTION_ECO: 1, ACTION_FROST: 2, ACTION_TURN_OFF: 3}
_HEAT_PRESETS = {ACTION_FROST: "frost_temp", ACTION_ECO: "eco_temp"}
_COOL_PRESETS = {ACTION_ECO: "eco_cool_temp"}


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


class DependencyModel:
    """Dependency layer of one zone, backed by the algorithm's dependency state."""

    def __init__(self, state: DependencyState) -> None:
        self._state = state
        self._coupling = CouplingEstimator()
        self._door_open: dict[str, bool] = {}
        self._settling_until_s: float | None = None
        self.load()

    def load(self) -> None:
        """Rebuild the learned coupling from the persisted state."""
        self._coupling.reset()
        for item_id, item_state in self._state.items.items():
            self._coupling.load_item(item_id, item_state)
        self._coupling.load_shared(self._state.shared.get(_SHARED_COUPLING_KEY, {}))

    def reset(self) -> None:
        self._coupling.reset()
        self._door_open.clear()
        self._settling_until_s = None

    def forget_except(self, item_ids: set[str]) -> None:
        self._coupling.forget_except(item_ids)
        self._save()

    def _save(self) -> None:
        for item_id in list(self._state.items):
            self._state.items[item_id].update(self._coupling.save_item(item_id))
        shared = self._coupling.save_shared()
        if shared["ls_rows"]:
            self._state.shared[_SHARED_COUPLING_KEY] = shared
        else:
            self._state.shared.pop(_SHARED_COUPLING_KEY, None)

    def update(self, readings: DependencyReadings | None, ctx: ModelContext) -> KnownInputs | None:
        """Process the readings of one calculation and return the model inputs."""
        if readings is None:
            return None

        self._track_doors(readings, ctx.now_s)
        connected = [obs for obs in readings.neighbours if obs.connected]
        gate = self._gate(connected, ctx)

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
                    ctx.model_reliable
                    and ctx.deadtime_s is not None
                    and gate not in (GATE_NEIGHBOUR_WINDOW, GATE_DOOR_SETTLING)
                ),
            )
            if self._coupling.update(tick):
                for obs in connected:
                    self._state.for_item(obs.item.item_id)
                self._save()

        exchanges = []
        for obs in connected:
            k = self._coupling.k(obs.item.item_id)
            if k is not None:
                exchanges.append(ExchangeInput(obs.item.item_id, k, obs.reading.temperature))

        always_connected = any(obs.item.mode == MODE_ALWAYS for obs in connected)
        action, setpoint = self._window_action(connected, readings, ctx)
        return KnownInputs(
            exchanges=tuple(exchanges),
            learning_gate=gate,
            loss_coefficient=self._coupling.loss_coefficient() if always_connected else None,
            control_action=action,
            action_setpoint=setpoint,
            status=tuple(self._status(obs, ctx) for obs in readings.neighbours),
        )

    @staticmethod
    def _window_action(
        connected: list[NeighbourObservation], readings: DependencyReadings, ctx: ModelContext
    ) -> tuple[str | None, float | None]:
        """Return the reaction to open windows at connected neighbour thermostats.

        "frost" and "eco" impose the zone's own preset temperature for the
        current HVAC mode; without such a preset the zone is switched off.
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
        if setpoint is None:
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

    def _gate(self, connected: list[NeighbourObservation], ctx: ModelContext) -> str | None:
        if any(obs.reading.is_thermostat and obs.reading.window_open for obs in connected):
            return GATE_NEIGHBOUR_WINDOW
        if any(obs.item.mode == MODE_DOOR for obs in connected):
            return GATE_DOOR_OPEN
        if self._settling_until_s is not None and ctx.now_s < self._settling_until_s:
            return GATE_DOOR_SETTLING
        return None

    def _status(self, obs: NeighbourObservation, ctx: ModelContext) -> dict[str, Any]:
        item_id = obs.item.item_id
        k = self._coupling.k(item_id)
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
            "k_samples": self._coupling.samples(item_id),
            "indistinguishable": self._coupling.is_indistinguishable(item_id),
            "share_of_losses": share,
        }
