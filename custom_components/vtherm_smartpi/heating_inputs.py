"""Inputs from the heating system: the water data and the boiler's call.

documentation/en/water_input.md. Each input comes from the source chosen in the
thermostat's options ("Water and boiler"):

- ``none``: no data;
- ``entities``: Home Assistant entities the user picks (any integration, or a
  plain sensor on a pipe). The supply water sensor is the thermostat's own; the
  signals of the whole boiler are set once in the "SmartPI defaults" entry, or
  per thermostat;
- ``provider:<name>``: an integration that implements SmartPI's provider
  contract (v1, below);
- for the boiler's call also ``vtherm_central_boiler``: Versatile Thermostat's
  central boiler, for a thermostat it counts.

The provider contract, v1. A provider puts itself in
``hass.data["vtherm_smartpi_heating_providers"]`` (a dict, name -> provider)
when it starts and removes itself when it unloads; nothing is imported either
way. It has ``name`` (str), ``version`` (int, 1) and ``heating_inputs(entity_id)``,
which returns a mapping for that climate entity, or None when it does not serve
it. It is called from the event loop at each calculation: fast, without I/O.
The fields (``PROVIDER_FIELDS``) may be missing or None.

Everything is read defensively: an unknown version, an exception, a wrong type
or an unknown reason mean "no data from this source now". It is logged at debug
level only; the diagnostics show each source's state.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from homeassistant.const import UnitOfTemperature
from homeassistant.core import valid_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_REASON_ENTITY,
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_FAULT_ENTITY,
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_SUPPLY_ENTITY,
    CONF_SMART_PI_WATER_SOURCE,
)
from .smartpi.boiler_call import REASON_NOT_CALLED, SOURCE_VTHERM, BoilerReading
from .smartpi.water_input import HEAT_REASON_DHW, WaterReading

_LOGGER = logging.getLogger(__name__)

# Sources (the values of the options).
SOURCE_NONE = "none"
SOURCE_ENTITIES = "entities"
PROVIDER_PREFIX = "provider:"
WATER_SOURCES_FIXED = (SOURCE_NONE, SOURCE_ENTITIES)
CALL_SOURCES_FIXED = (SOURCE_NONE, SOURCE_VTHERM, SOURCE_ENTITIES)
# Where a thermostat takes the boiler's signals from.
SIGNALS_DEFAULTS = "defaults"
SIGNALS_OWN = "own"

# The source of one signal, in the diagnostics.
SIGNAL_ENTITY = "entity"

# The providers' registry and the contract's version.
PROVIDERS_KEY = "vtherm_smartpi_heating_providers"
CONTRACT_VERSION = 1
# The contract's fields (any may be missing or None).
PROVIDER_FIELDS = (
    "supply_temperature",  # float, °C: the water reaching the thermostat's circuit
    "supply_reason",  # str: why there is no supply temperature
    "heat_available",  # bool: the boiler can heat the circuit now
    "heat_reason",  # str: why not (dhw_active, flame_off, boiler_off, fault, ...)
    "boiler_called",  # bool: the boiler is told to heat
    "call_reason",  # str: why not, one of CALL_REASONS
    "demand",  # mapping: the demand against the boiler's start criteria (DEMAND_FIELDS)
)
# Why the boiler is not called.
CALL_REASONS = ("below_threshold", "no_demand", "activation_delay", "boiler_fault")
REASON_BELOW_THRESHOLD, REASON_NO_DEMAND, REASON_ACTIVATION_DELAY, REASON_BOILER_FAULT = CALL_REASONS
# The demand against the boiler's start criteria, for the diagnostics only.
DEMAND_FIELDS = {
    "demand_count": int,
    "count_threshold": int,
    "count_by": str,
    "demand_power_kw": float,
    "power_threshold_kw": float,
    "demand_opening": float,
    "opening_threshold": float,
}

# Why there is no supply temperature, when SmartPI itself cannot read it. None is a
# reason of the water input: each is a momentary gap, lasting after 24 h.
SUPPLY_UNAVAILABLE = "unavailable"  # the supply entity has no number
SUPPLY_NOT_SERVED = "not_served"  # the provider does not serve this thermostat
SUPPLY_PROVIDER_MISSING = "provider_missing"  # the chosen provider is not registered
SUPPLY_PROVIDER_ERROR = "provider_error"  # the provider's answer cannot be used
# Why the boiler cannot heat the circuit, from entities.
HEAT_REASON_NO_HEAT = "no_heat"
HEAT_REASON_FAULT = "fault"

# How a provider answered.
PROVIDER_OK = "ok"
PROVIDER_MISSING = "missing"
PROVIDER_NOT_SERVED = "not_served"
PROVIDER_ERROR = "error"
_SUPPLY_REASON_OF = {
    PROVIDER_MISSING: SUPPLY_PROVIDER_MISSING,
    PROVIDER_NOT_SERVED: SUPPLY_NOT_SERVED,
    PROVIDER_ERROR: SUPPLY_PROVIDER_ERROR,
}
# A chosen provider missing this long is reported once (it may register after SmartPI).
PROVIDER_MISSING_WARNING_S = 10 * 60.0

# Versatile Thermostat's central boiler: its entities, by unique ID.
VT_DOMAIN = "versatile_thermostat"
VT_CENTRAL_BOILER_UNIQUE_ID = "central_boiler_state"
VT_ACTIVE_COUNT_UNIQUE_ID = "nb_device_active_boiler"
VT_ACTIVE_POWER_UNIQUE_ID = "total_power_active_boiler"
VT_COUNT_THRESHOLD_UNIQUE_ID = "boiler_activation_threshold"
VT_POWER_THRESHOLD_UNIQUE_ID = "boiler_power_activation_threshold"
# Versatile Thermostat's own power unit name for kW; anything else counts as W, as VT does.
_KILO_WATT_UNITS = ("kW", "kw")

_STATE_ON = "on"
_STATE_OFF = "off"
_NO_STATE = ("unavailable", "unknown", "")


def provider_source(name: str) -> str:
    """The source value of a provider."""
    return f"{PROVIDER_PREFIX}{name}"


def provider_name(source: str | None) -> str | None:
    """The provider's name of a source value (None: not a provider)."""
    if isinstance(source, str) and source.startswith(PROVIDER_PREFIX) and len(source) > len(PROVIDER_PREFIX):
        return source[len(PROVIDER_PREFIX) :]
    return None


# --- configuration ------------------------------------------------------------


@dataclass(frozen=True)
class BoilerSignals:
    """The entities of the boiler's signals (entity ids, None: not set)."""

    heat_available: str | None = None
    dhw: str | None = None
    fault: str | None = None
    called: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class HeatingInputsConfig:
    """The sources chosen for a thermostat and the entities they read."""

    water_source: str = SOURCE_NONE
    call_source: str = SOURCE_NONE
    supply_entity: str | None = None
    signals: BoilerSignals = field(default_factory=BoilerSignals)

    @property
    def water_inputs(self) -> str:
        """The water source and its supply sensor: a change of either changes what the
        learned power means."""
        return water_inputs_key(self.water_source, self.supply_entity)


def water_inputs_key(water_source: str, supply_entity: str | None) -> str:
    """One text for the water source and its supply sensor (the supply only with entities)."""
    if water_source == SOURCE_ENTITIES:
        return f"{water_source}|{supply_entity or ''}"
    return water_source


def _entity(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def signals_from(options: Mapping[str, Any]) -> BoilerSignals:
    """The boiler's signals set in one entry's options."""
    return BoilerSignals(
        heat_available=_entity(options.get(CONF_SMART_PI_HEAT_AVAILABLE_ENTITY)),
        dhw=_entity(options.get(CONF_SMART_PI_DHW_ENTITY)),
        fault=_entity(options.get(CONF_SMART_PI_FAULT_ENTITY)),
        called=_entity(options.get(CONF_SMART_PI_BOILER_CALLED_ENTITY)),
        reason=_entity(options.get(CONF_SMART_PI_BOILER_REASON_ENTITY)),
    )


def default_call_source(used_by_central_boiler: bool) -> str:
    """The boiler's call without a choice: VT's central boiler where it serves the thermostat."""
    return SOURCE_VTHERM if used_by_central_boiler else SOURCE_NONE


def chosen_water_source(options: Mapping[str, Any]) -> str:
    """The water source chosen in the options (``none`` without a valid choice)."""
    water = options.get(CONF_SMART_PI_WATER_SOURCE)
    return water if water in WATER_SOURCES_FIXED or provider_name(water) is not None else SOURCE_NONE


def chosen_call_source(options: Mapping[str, Any], used_by_central_boiler: bool) -> str:
    """The call source chosen in the options (the default without a valid choice)."""
    call = options.get(CONF_SMART_PI_BOILER_CALL_SOURCE)
    if call in CALL_SOURCES_FIXED or provider_name(call) is not None:
        return call
    return default_call_source(used_by_central_boiler)


def config_from_options(
    options: Mapping[str, Any],
    defaults: Mapping[str, Any] | None,
    used_by_central_boiler: bool,
) -> HeatingInputsConfig:
    """The thermostat's sources from its options, the boiler's signals from the defaults
    entry unless the thermostat sets its own. An entry without these keys keeps the
    behaviour from before them: no water data, the call from VT's central boiler where
    it serves the thermostat."""
    water = chosen_water_source(options)
    supply = _entity(options.get(CONF_SMART_PI_SUPPLY_ENTITY))
    if water == SOURCE_ENTITIES and supply is None:
        # Water data from entities need the supply temperature.
        water = SOURCE_NONE
    call = chosen_call_source(options, used_by_central_boiler)
    own = options.get(CONF_SMART_PI_BOILER_SIGNALS) == SIGNALS_OWN
    signals = signals_from(options if own or defaults is None else defaults)
    return HeatingInputsConfig(water, call, supply if water == SOURCE_ENTITIES else None, signals)


# --- providers ------------------------------------------------------------------


def registered_providers(hass: Any) -> dict[str, Any]:
    """The providers registered now, by name."""
    data = getattr(hass, "data", None)
    registry = data.get(PROVIDERS_KEY) if isinstance(data, Mapping) else None
    if not isinstance(registry, Mapping):
        return {}
    return {name: provider for name, provider in registry.items() if isinstance(name, str) and name}


def provider_names(hass: Any) -> list[str]:
    """The names of the registered providers, sorted."""
    return sorted(registered_providers(hass))


@dataclass(frozen=True)
class ProviderAnswer:
    """A provider's answer for one thermostat: how it answered, and its fields."""

    status: str
    fields: Mapping[str, Any] | None = None


def ask_provider(hass: Any, name: str, entity_id: str | None) -> ProviderAnswer:
    """The provider's fields for a climate entity, read defensively."""
    provider = registered_providers(hass).get(name)
    if provider is None:
        return ProviderAnswer(PROVIDER_MISSING)
    try:
        version = getattr(provider, "version", None)
        if isinstance(version, bool) or version != CONTRACT_VERSION:
            _LOGGER.debug("Heating provider %s: contract version %r not supported", name, version)
            return ProviderAnswer(PROVIDER_ERROR)
        method = getattr(provider, "heating_inputs", None)
        if not callable(method):
            _LOGGER.debug("Heating provider %s has no heating_inputs()", name)
            return ProviderAnswer(PROVIDER_ERROR)
        # A thermostat without its entity id yet cannot be asked for.
        answer = method(entity_id) if entity_id else None
        if answer is None:
            return ProviderAnswer(PROVIDER_NOT_SERVED)
        if not isinstance(answer, Mapping):
            _LOGGER.debug("Heating provider %s gave %r for %s, not a mapping", name, type(answer), entity_id)
            return ProviderAnswer(PROVIDER_ERROR)
        return ProviderAnswer(PROVIDER_OK, dict(answer))
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.debug("Heating provider %s failed for %s: %s", name, entity_id, err)
        return ProviderAnswer(PROVIDER_ERROR)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float
        return None
    return number if math.isfinite(number) else None


def water_reading_of(fields: Mapping[str, Any]) -> WaterReading:
    """A provider's water fields, keeping only well-typed values."""
    heat_available = fields.get("heat_available")
    return WaterReading(
        supply_c=_number(fields.get("supply_temperature")),
        supply_reason=_text(fields.get("supply_reason")),
        heat_available=heat_available if isinstance(heat_available, bool) else None,
        heat_reason=_text(fields.get("heat_reason")),
    )


def demand_of(value: Any) -> dict | None:
    """The demand fields given with a well-formed value (diagnostics only)."""
    if not isinstance(value, Mapping):
        return None
    demand = {}
    for name, kind in DEMAND_FIELDS.items():
        try:
            item = value.get(name)
        except Exception:  # pylint: disable=broad-except
            return None
        if isinstance(item, bool):
            continue
        if kind is str and isinstance(item, str) and item:
            demand[name] = item
        elif kind is int and isinstance(item, int):
            demand[name] = item
        elif kind is int and isinstance(item, float) and item.is_integer():
            demand[name] = int(item)
        elif kind is float and (number := _number(item)) is not None:
            demand[name] = round(number, 3)
    return demand or None


def boiler_reading_of(fields: Mapping[str, Any], source: str) -> BoilerReading | None:
    """A provider's call of the boiler, or None when it does not say (it does not decide
    the boiler, a wrong type, a reason SmartPI does not know)."""
    called = fields.get("boiler_called")
    reason = fields.get("call_reason")
    if called is True:
        return BoilerReading(True, None, source, demand_of(fields.get("demand")))
    if called is False and reason in CALL_REASONS:
        return BoilerReading(False, reason, source, demand_of(fields.get("demand")))
    return None


class ProviderWatch:
    """Reports once when a chosen provider stays missing (it may register after SmartPI)."""

    def __init__(self) -> None:
        self._missing_since: dict[str, float] = {}
        self._reported: set[str] = set()

    def seen(self, name: str, answer: ProviderAnswer, now_s: float) -> bool:
        """Note an answer; True once when the provider has been missing too long."""
        if answer.status != PROVIDER_MISSING:
            self._missing_since.pop(name, None)
            self._reported.discard(name)
            return False
        since = self._missing_since.setdefault(name, now_s)
        if name in self._reported or now_s - since < PROVIDER_MISSING_WARNING_S:
            return False
        self._reported.add(name)
        return True


# --- entities -------------------------------------------------------------------


def resolve_entity(hass: Any, value: str | None) -> str | None:
    """The entity id of a stored entity: kept by its registry id where it has one (it
    survives a rename), else by its entity id. Not found: the stored value (no state)."""
    if not value:
        return None
    if valid_entity_id(value):
        return value
    try:
        return er.async_resolve_entity_id(er.async_get(hass), value) or value
    except Exception:  # pylint: disable=broad-except
        return value


def stored_entity(hass: Any, entity_id: str | None) -> str | None:
    """How an entity chosen in a form is stored: its registry id where it has one."""
    if not entity_id:
        return None
    try:
        entry = er.async_get(hass).async_get(entity_id)
    except Exception:  # pylint: disable=broad-except
        entry = None
    return entry.id if entry is not None else entity_id


def _state(hass: Any, entity_id: str | None):
    if not entity_id:
        return None
    try:
        return hass.states.get(entity_id)
    except Exception:  # pylint: disable=broad-except
        return None


def entity_on(hass: Any, entity_id: str | None) -> bool | None:
    """An on/off entity's state (None: not known)."""
    state = _state(hass, entity_id)
    if state is None or state.state not in (_STATE_ON, _STATE_OFF):
        return None
    return state.state == _STATE_ON


def entity_text(hass: Any, entity_id: str | None) -> str | None:
    """An entity's state as text (None: not known)."""
    state = _state(hass, entity_id)
    if state is None or not isinstance(state.state, str) or state.state in _NO_STATE:
        return None
    return state.state


def _entity_number(hass: Any, entity_id: str | None) -> tuple[float | None, str | None]:
    """An entity's state as a number, and its unit (None: not a number)."""
    state = _state(hass, entity_id)
    if state is None:
        return None, None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None, None
    if not math.isfinite(value):
        return None, None
    attributes = getattr(state, "attributes", None) or {}
    unit = attributes.get("unit_of_measurement") if isinstance(attributes, Mapping) else None
    return value, unit if isinstance(unit, str) else None


def entity_temperature_c(hass: Any, entity_id: str | None) -> float | None:
    """A temperature entity in °C (converted from its unit; no unit: °C)."""
    value, unit = _entity_number(hass, entity_id)
    if value is None:
        return None
    if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
        return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
    return value


def _signal(source: str, entity: str | None = None, data: bool | None = None, available: bool | None = None) -> dict:
    """Where one signal comes from, for the diagnostics."""
    signal: dict[str, Any] = {"source": source}
    if entity is not None:
        signal["entity"] = entity
    if available is not None:
        signal["available"] = available
    if data is not None:
        signal["data"] = data
    return signal


def _entity_signal(entity_id: str | None, value: Any) -> dict:
    if entity_id is None:
        return _signal(SOURCE_NONE)
    return _signal(SIGNAL_ENTITY, entity_id, value is not None)


def resolved(hass: Any, config: HeatingInputsConfig) -> HeatingInputsConfig:
    """The configuration with the entity ids of its stored entities (None: not found)."""
    signals = config.signals
    return replace(
        config,
        supply_entity=resolve_entity(hass, config.supply_entity),
        signals=BoilerSignals(
            heat_available=resolve_entity(hass, signals.heat_available),
            dhw=resolve_entity(hass, signals.dhw),
            fault=resolve_entity(hass, signals.fault),
            called=resolve_entity(hass, signals.called),
            reason=resolve_entity(hass, signals.reason),
        ),
    )


def water_from_entities(hass: Any, config: HeatingInputsConfig) -> tuple[WaterReading, dict]:
    """The water data from the thermostat's supply sensor and the boiler's signals."""
    config = resolved(hass, config)
    signals = config.signals
    supply = entity_temperature_c(hass, config.supply_entity)
    heat = entity_on(hass, signals.heat_available)
    dhw = entity_on(hass, signals.dhw)
    fault = entity_on(hass, signals.fault)
    if dhw is True:
        heat_available, heat_reason = False, HEAT_REASON_DHW
    elif fault is True:
        heat_available, heat_reason = False, HEAT_REASON_FAULT
    elif heat is False:
        heat_available, heat_reason = False, HEAT_REASON_NO_HEAT
    else:
        heat_available, heat_reason = heat, None
    reading = WaterReading(
        supply_c=supply,
        supply_reason=None if supply is not None else SUPPLY_UNAVAILABLE,
        heat_available=heat_available,
        heat_reason=heat_reason,
    )
    inputs = {
        "supply": _entity_signal(config.supply_entity, supply),
        "heat": _entity_signal(signals.heat_available, heat),
        "dhw": _entity_signal(signals.dhw, dhw),
        "fault": _entity_signal(signals.fault, fault),
    }
    return reading, inputs


def boiler_from_entities(hass: Any, config: HeatingInputsConfig) -> tuple[BoilerReading | None, dict]:
    """The boiler's call from the boiler's signals: on/off, then the fault and the reason."""
    signals = resolved(hass, config).signals
    called = entity_on(hass, signals.called)
    fault = entity_on(hass, signals.fault)
    reason_text = entity_text(hass, signals.reason)
    inputs = {
        "called": _entity_signal(signals.called, called),
        "reason": _entity_signal(signals.reason, reason_text),
        "fault": _entity_signal(signals.fault, fault),
    }
    if called is None:
        return None, inputs
    if called:
        return BoilerReading(True, None, SOURCE_ENTITIES), inputs
    if fault is True:
        reason = REASON_BOILER_FAULT
    elif reason_text in CALL_REASONS:
        reason = reason_text
    else:
        reason = REASON_NOT_CALLED
    return BoilerReading(False, reason, SOURCE_ENTITIES), inputs


def water_from_provider(answer: ProviderAnswer, source: str) -> tuple[WaterReading, dict]:
    """The water data a provider gave (a reason of the gap when it gave none)."""
    available = answer.status != PROVIDER_MISSING
    if answer.status != PROVIDER_OK or answer.fields is None:
        reading = WaterReading(supply_reason=_SUPPLY_REASON_OF.get(answer.status, SUPPLY_PROVIDER_ERROR))
    else:
        reading = water_reading_of(answer.fields)
    heat_known = reading.heat_available is not None
    inputs = {
        "supply": _signal(source, data=reading.supply_c is not None, available=available),
        "heat": _signal(source, data=heat_known, available=available),
        # Hot water and a fault come in the provider's heat reason.
        "dhw": _signal(source, data=heat_known, available=available),
        "fault": _signal(source, data=heat_known, available=available),
    }
    return reading, inputs


def boiler_from_provider(answer: ProviderAnswer, source: str) -> tuple[BoilerReading | None, dict]:
    """The boiler's call a provider gave."""
    available = answer.status != PROVIDER_MISSING
    reading = boiler_reading_of(answer.fields, source) if answer.status == PROVIDER_OK and answer.fields else None
    inputs = {
        "called": _signal(source, data=reading is not None, available=available),
        "reason": _signal(source, data=reading is not None and reading.reason is not None, available=available),
    }
    return reading, inputs


def water_inputs_none() -> dict:
    """The water block's inputs without a source."""
    return {name: _signal(SOURCE_NONE) for name in ("supply", "heat", "dhw", "fault")}


# --- Versatile Thermostat's central boiler -------------------------------------


def _vt_entity_id(hass: Any, domain: str, unique_id: str) -> str | None:
    try:
        return er.async_get(hass).async_get_entity_id(domain, VT_DOMAIN, unique_id)
    except Exception:  # pylint: disable=broad-except
        return None


def _kw(value: float | None, unit: str | None) -> float | None:
    """A VT power in kW: VT counts anything but kW as W."""
    if value is None:
        return None
    return value if unit in _KILO_WATT_UNITS else value / 1000.0


def vtherm_demand(hass: Any) -> dict | None:
    """The demand against VT's start criteria, from its live entities: the number of
    active devices and their power, and the thresholds (0: the criterion is off)."""
    count, _ = _entity_number(hass, _vt_entity_id(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID))
    count_threshold, _ = _entity_number(hass, _vt_entity_id(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID))
    power, power_unit = _entity_number(hass, _vt_entity_id(hass, "sensor", VT_ACTIVE_POWER_UNIQUE_ID))
    power_threshold, threshold_unit = _entity_number(hass, _vt_entity_id(hass, "number", VT_POWER_THRESHOLD_UNIQUE_ID))
    demand = {
        "demand_count": int(count) if count is not None else None,
        "count_threshold": int(count_threshold) if count_threshold is not None else None,
        "count_by": "devices",
        "demand_power_kw": _kw(power, power_unit),
        "power_threshold_kw": _kw(power_threshold, threshold_unit),
    }
    if all(demand[name] is None for name in ("demand_count", "count_threshold", "demand_power_kw", "power_threshold_kw")):
        return None
    return demand_of(demand)


def vtherm_reason(demand: Mapping[str, Any] | None) -> str:
    """Why VT's central boiler is off, by VT's own rule: a criterion is met when its
    threshold is above 0 and the demand reaches it. Met while off: the activation
    delay runs. Nothing active: no demand. Otherwise under the thresholds."""
    if not demand:
        return REASON_NOT_CALLED
    count = demand.get("demand_count")
    count_threshold = demand.get("count_threshold")
    power = demand.get("demand_power_kw")
    power_threshold = demand.get("power_threshold_kw")
    count_met = count is not None and count_threshold and count >= count_threshold
    power_met = power is not None and power_threshold and power >= power_threshold
    if count_met or power_met:
        return REASON_ACTIVATION_DELAY
    if count is None and power is None:
        return REASON_NOT_CALLED
    if not count and not power:
        return REASON_NO_DEMAND
    return REASON_BELOW_THRESHOLD


def boiler_from_vtherm(hass: Any, counted: bool = True) -> tuple[BoilerReading | None, dict]:
    """The call of VT's central boiler (its binary sensor), with the reason and the demand.

    ``counted``: VT counts the thermostat for its central boiler; otherwise its call
    says nothing about this zone.
    """
    if not counted:
        return None, {"called": _signal(SOURCE_VTHERM, data=False), "reason": _signal(SOURCE_VTHERM, data=False)}
    entity_id = _vt_entity_id(hass, "binary_sensor", VT_CENTRAL_BOILER_UNIQUE_ID)
    on = entity_on(hass, entity_id)
    demand = vtherm_demand(hass) if on is not None else None
    inputs = {
        "called": _signal(SOURCE_VTHERM, entity_id, on is not None),
        "reason": _signal(SOURCE_VTHERM, data=on is not None and (on or demand is not None)),
    }
    if on is None:
        return None, inputs
    if on:
        return BoilerReading(True, None, SOURCE_VTHERM, demand), inputs
    return BoilerReading(False, vtherm_reason(demand), SOURCE_VTHERM, demand), inputs
