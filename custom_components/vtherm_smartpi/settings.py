"""How a thermostat's SmartPI settings are read at run time: per key, from the entries that hold them.

- Versatile Thermostat's entry first, without the "thermostat only" settings: the delays
  are SmartPI's own, VTherm's TPI value is never read.
- The shared settings (``SHARED_SETTINGS``) from the "SmartPI defaults" entry, or from the
  thermostat's own entry when it does not use the defaults.
- The thermostat's own entry for everything else: the "thermostat only" settings
  (``THERMOSTAT_SETTINGS``; not set means not used), the neighbours, the zone, the water
  source.

The code's values (``DEFAULT_OPTIONS``) only fill a form. A shared key missing all the same
(an entry edited by hand) is logged once and takes the value the form would show.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_USE_DEFAULTS,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    SHARED_SETTINGS,
    THERMOSTAT_SETTINGS,
)
from .smartpi.const import HEATING_TYPES

_LOGGER = logging.getLogger(__name__)

# The missing shared keys already reported, by entry and key: each one is logged once.
_REPORTED: set[tuple[str, str]] = set()


def entry_options(entry: Any) -> dict[str, Any]:
    """An entry's settings: its options once saved, else the data it was created with.

    Saved options replace the data as a whole: a key the options left out (a heating type
    not set, the shared settings of a thermostat that uses the defaults) is not taken back
    from the data.
    """
    if entry is None:
        return {}
    return dict(entry.options) if entry.options else dict(entry.data)


def is_defaults_entry(entry: Any) -> bool:
    """The "SmartPI defaults" entry: the one without a target thermostat."""
    return entry.data.get(CONF_TARGET_VTHERM) is None


def defaults_entry(hass: Any) -> Any:
    """The "SmartPI defaults" entry, or None."""
    entries = hass.config_entries.async_entries(DOMAIN)
    return next((entry for entry in entries if entry.unique_id == DOMAIN), None) or next(
        (entry for entry in entries if is_defaults_entry(entry)), None
    )


def own_entry(hass: Any, unique_id: str | None) -> Any:
    """The SmartPI entry of the thermostat with this unique id, or None."""
    if not unique_id:
        return None
    return next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_TARGET_VTHERM) == unique_id
        ),
        None,
    )


def uses_defaults(options: Mapping[str, Any]) -> bool:
    """Whether a thermostat's entry takes the shared settings from the defaults entry.

    An entry made before the switch existed holds a full copy of the settings: off.
    """
    return options.get(CONF_SMART_PI_USE_DEFAULTS) is True


def heating_type_of(options: Mapping[str, Any]) -> str | None:
    """The heating type an entry sets, or None when it is not set."""
    heating_type = options.get(CONF_SMART_PI_HEATING_TYPE)
    return heating_type if heating_type in HEATING_TYPES else None


def own_heating_type(hass: Any, unique_id: str | None) -> str | None:
    """The heating type set in the thermostat's own entry, or None."""
    return heating_type_of(entry_options(own_entry(hass, unique_id)))


def shared_settings(
    defaults: Mapping[str, Any],
    own: Mapping[str, Any] | None = None,
    entry_id: str = "",
) -> dict[str, Any]:
    """The shared settings: the own entry's values when it does not use the defaults, else the
    defaults entry's. A missing key takes the value the form would show, logged once."""
    values: dict[str, Any] = {}
    for key in SHARED_SETTINGS:
        held = own if own is not None else defaults
        if key in held:
            values[key] = held[key]
            continue
        # A thermostat with its own values that lacks one starts from the defaults' value,
        # as its form would.
        values[key] = defaults.get(key, DEFAULT_OPTIONS[key])
        if (entry_id, key) not in _REPORTED:
            _REPORTED.add((entry_id, key))
            _LOGGER.error(
                "SmartPI configuration error: the setting %s is missing in %s; using %r",
                key,
                "the thermostat's entry" if own is not None else 'the "SmartPI defaults" entry',
                values[key],
            )
    return values


def effective_settings(
    hass: Any,
    unique_id: str | None,
    vtherm_config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], Any]:
    """A thermostat's settings, read per key (module docstring), and its own entry (or None)."""
    config = {key: value for key, value in (vtherm_config or {}).items() if key not in THERMOSTAT_SETTINGS}
    own = own_entry(hass, unique_id)
    own_options = entry_options(own)
    defaults = defaults_entry(hass)
    with_own_values = own is not None and not uses_defaults(own_options)
    config.update(
        shared_settings(
            entry_options(defaults),
            own_options if with_own_values else None,
            getattr(own if with_own_values else defaults, "entry_id", ""),
        )
    )
    config.update({key: value for key, value in own_options.items() if key not in SHARED_SETTINGS})
    return config, own
