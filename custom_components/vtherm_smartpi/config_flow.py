"""Config flow for vtherm_smartpi."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.helpers import selector
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN

from .const import (
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_DEADBAND_ALLOW_P,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEBUG,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_HYSTERESIS_OFF,
    CONF_SMART_PI_HYSTERESIS_ON,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
    CONF_SMART_PI_MIN_VALVE,
    CONF_SMART_PI_DEPENDENCIES,
    CONF_SMART_PI_RELEASE_TAU_FACTOR,
    CONF_SMART_PI_USE_FF3,
    CONF_SMART_PI_USE_SETPOINT_FILTER,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .smartpi.dependencies import (
    KIND_NEIGHBOUR,
    MODE_ALWAYS,
    MODE_DOOR,
    NEIGHBOUR_MODES,
    WINDOW_ACTION_NOTHING,
    WINDOW_ACTIONS,
    DependencyItem,
    dump_dependencies,
    parse_dependencies,
    validate_dependencies,
)

ERROR_INVALID_VALVE_CURVE = "invalid_valve_curve"
ABORT_DEPENDENCIES_UNCHANGED = "dependencies_unchanged"
FIELD_REMOVE_DEPENDENCIES = "remove"
FIELD_EDIT_DEPENDENCY = "dependency"
FIELD_NEIGHBOUR = "entity"
FIELD_MODE = "mode"
FIELD_DOOR_SENSORS = "door_sensors"
FIELD_WINDOW_ACTION = "neighbour_window_action"
VT_DOMAIN = "versatile_thermostat"
DOOR_DEVICE_CLASSES = ["door", "opening", "garage_door"]
WINDOW_DEVICE_CLASS = "window"
THERMOSTAT_TYPE_VALVE = "thermostat_over_valve"
THERMOSTAT_TYPE_CLIMATE = "thermostat_over_climate"
AUTO_REGULATION_VALVE = "auto_regulation_valve"
CONF_THERMOSTAT_TYPE_KEY = "thermostat_type"
CONF_AUTO_REGULATION_MODE_KEY = "auto_regulation_mode"


def build_main_options_schema(
    defaults: dict[str, Any],
    *,
    include_valve_linearization: bool,
) -> vol.Schema:
    """Build the SmartPI main options schema."""
    schema = {
        vol.Optional(
            CONF_MINIMAL_ACTIVATION_DELAY,
            default=defaults[CONF_MINIMAL_ACTIVATION_DELAY],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_MINIMAL_DEACTIVATION_DELAY,
            default=defaults[CONF_MINIMAL_DEACTIVATION_DELAY],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_SMART_PI_DEADBAND,
            default=defaults[CONF_SMART_PI_DEADBAND],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=2.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_HYSTERESIS_ON,
            default=defaults[CONF_SMART_PI_HYSTERESIS_ON],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=2.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_HYSTERESIS_OFF,
            default=defaults[CONF_SMART_PI_HYSTERESIS_OFF],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=2.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_RELEASE_TAU_FACTOR,
            default=defaults[CONF_SMART_PI_RELEASE_TAU_FACTOR],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=5.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_SMART_PI_USE_SETPOINT_FILTER,
            default=defaults[CONF_SMART_PI_USE_SETPOINT_FILTER],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_USE_FF3,
            default=defaults[CONF_SMART_PI_USE_FF3],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_DEADBAND_ALLOW_P,
            default=defaults[CONF_SMART_PI_DEADBAND_ALLOW_P],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
            default=defaults[CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE],
        ): bool,
        vol.Optional(
            CONF_SMART_PI_DEBUG,
            default=defaults[CONF_SMART_PI_DEBUG],
        ): bool,
    }
    if include_valve_linearization:
        schema[
            vol.Optional(
                CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
                default=defaults[CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION],
            )
        ] = bool
    return vol.Schema(schema)


def build_valve_curve_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the SmartPI valve curve schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_SMART_PI_MIN_VALVE,
                default=defaults[CONF_SMART_PI_MIN_VALVE],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=20,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SMART_PI_KNEE_DEMAND,
                default=defaults[CONF_SMART_PI_KNEE_DEMAND],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50,
                    max=95,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SMART_PI_KNEE_VALVE,
                default=defaults[CONF_SMART_PI_KNEE_VALVE],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=50,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SMART_PI_MAX_VALVE,
                default=defaults[CONF_SMART_PI_MAX_VALVE],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50,
                    max=100,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def build_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the SmartPI defaults schema."""
    return build_main_options_schema(defaults, include_valve_linearization=False)


def build_user_target_schema() -> vol.Schema:
    """Build the SmartPI target thermostat schema."""
    return vol.Schema(
        {
            vol.Required(CONF_TARGET_VTHERM): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN)
            )
        }
    )


def build_user_settings_schema(defaults: dict[str, Any], is_valve: bool) -> vol.Schema:
    """Build the SmartPI per-thermostat settings schema."""
    schema = dict(
        build_main_options_schema(
            defaults,
            include_valve_linearization=is_valve,
        ).schema
    )
    return vol.Schema(schema)


def _schema_defaults(user_input: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return form defaults merged with the latest submitted values."""
    defaults = dict(DEFAULT_OPTIONS)
    if user_input is not None:
        defaults.update(user_input)
    return defaults


def _is_valve_state(state: Any) -> bool:
    """Return whether a VTherm state exposes a valve command space."""
    attributes = getattr(state, "attributes", {}) or {}
    configuration = attributes.get("configuration") or {}
    if _is_valve_config(configuration):
        return True
    return (
        attributes.get("vtherm_over_valve") is not None
        or attributes.get("vtherm_over_climate_valve", {}).get("have_valve_regulation")
        is True
    )


def _is_valve_config(config: dict[str, Any]) -> bool:
    """Return whether a VTherm config entry targets valve command space."""
    thermostat_type = config.get(CONF_THERMOSTAT_TYPE_KEY) or config.get("type")
    return (
        thermostat_type == THERMOSTAT_TYPE_VALVE
        or (
            thermostat_type == THERMOSTAT_TYPE_CLIMATE
            and config.get(CONF_AUTO_REGULATION_MODE_KEY) == AUTO_REGULATION_VALVE
        )
        or config.get("have_valve_regulation") is True
    )


def _is_valve_entity(hass: Any, entity_id: str, target_unique_id: str) -> bool:
    """Return whether a registered VTherm entity targets valve command space."""
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id)
    if reg_entry is not None and reg_entry.config_entry_id is not None:
        config_entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
        if config_entry is not None and _is_valve_config(config_entry.data):
            return True

    state = hass.states.get(entity_id)
    if state is not None:
        return _is_valve_state(state)

    for entry in registry.entities.values():
        if entry.domain != CLIMATE_DOMAIN or entry.unique_id != target_unique_id:
            continue
        fallback_state = hass.states.get(entry.entity_id)
        return fallback_state is not None and _is_valve_state(fallback_state)
    return False


def _validate_valve_curve_config(config: dict[str, Any]) -> dict[str, str]:
    """Validate cross-field valve curve constraints."""
    try:
        min_valve = float(config[CONF_SMART_PI_MIN_VALVE])
        knee_demand = float(config[CONF_SMART_PI_KNEE_DEMAND])
        knee_valve = float(config[CONF_SMART_PI_KNEE_VALVE])
        max_valve = float(config[CONF_SMART_PI_MAX_VALVE])
    except (KeyError, TypeError, ValueError):
        return {"base": ERROR_INVALID_VALVE_CURVE}

    if (
        0.0 <= min_valve < knee_valve < max_valve <= 100.0
        and 0.0 < knee_demand < 100.0
    ):
        return {}
    return {"base": ERROR_INVALID_VALVE_CURVE}


class SmartPIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Manage SmartPI plugin config entries."""

    VERSION = 1
    _pending_thermostat_data: dict[str, Any] | None = None
    _pending_thermostat_entity_id: str | None = None
    _pending_thermostat_is_valve: bool = False
    _pending_thermostat_title: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Create default plugin settings on first install."""
        if not self._async_current_entries():
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="SmartPI defaults",
                data=dict(DEFAULT_OPTIONS),
            )

        return await self.async_step_thermostat(user_input)

    async def async_step_global(self, user_input: dict[str, Any] | None = None):
        """Handle the global defaults entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="SmartPI defaults", data=user_input)

        return self.async_show_form(
            step_id="global",
            data_schema=build_options_schema(DEFAULT_OPTIONS),
        )

    async def async_step_thermostat(self, user_input: dict[str, Any] | None = None):
        """Select the target thermostat."""
        if user_input is not None:
            entity_id = user_input.get(CONF_TARGET_VTHERM)
            registry = er.async_get(self.hass)
            reg_entry = registry.async_get(entity_id)
            if reg_entry is None or reg_entry.unique_id is None:
                return self.async_show_form(
                    step_id="thermostat",
                    data_schema=build_user_target_schema(),
                    errors={CONF_TARGET_VTHERM: "invalid_entity"},
                )

            target_unique_id = reg_entry.unique_id
            await self.async_set_unique_id(f"{DOMAIN}-{target_unique_id}")
            self._abort_if_unique_id_configured()

            self._pending_thermostat_data = {CONF_TARGET_VTHERM: target_unique_id}
            self._pending_thermostat_entity_id = entity_id
            self._pending_thermostat_is_valve = _is_valve_entity(
                self.hass,
                entity_id,
                target_unique_id,
            )
            state = self.hass.states.get(entity_id)
            self._pending_thermostat_title = state.name if state is not None else entity_id
            return await self.async_step_thermostat_settings()

        return self.async_show_form(
            step_id="thermostat",
            data_schema=build_user_target_schema(),
        )

    async def async_step_thermostat_settings(
        self, user_input: dict[str, Any] | None = None
    ):
        """Handle the per-thermostat main settings entry."""
        data = dict(self._pending_thermostat_data or {})

        if user_input is not None:
            data.update(user_input)
            self._pending_thermostat_data = data
            if (
                self._pending_thermostat_is_valve
                and user_input.get(CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION)
            ):
                return self.async_show_form(
                    step_id="thermostat_valve_curve",
                    data_schema=build_valve_curve_schema(_schema_defaults(user_input)),
                )

            return self.async_create_entry(
                title=(
                    self._pending_thermostat_title
                    or self._pending_thermostat_entity_id
                ),
                data=data,
            )

        return self.async_show_form(
            step_id="thermostat_settings",
            data_schema=build_user_settings_schema(
                _schema_defaults(data),
                self._pending_thermostat_is_valve,
            ),
        )

    async def async_step_thermostat_valve_curve(
        self, user_input: dict[str, Any] | None = None
    ):
        """Handle the per-thermostat valve curve entry."""
        data = dict(self._pending_thermostat_data or {})

        if user_input is not None:
            data.update(user_input)
            errors = _validate_valve_curve_config(_schema_defaults(data))
            if errors:
                return self.async_show_form(
                    step_id="thermostat_valve_curve",
                    data_schema=build_valve_curve_schema(_schema_defaults(data)),
                    errors=errors,
                )
            return self.async_create_entry(
                title=(
                    self._pending_thermostat_title
                    or self._pending_thermostat_entity_id
                ),
                data=data,
            )

        return self.async_show_form(
            step_id="thermostat_valve_curve",
            data_schema=build_valve_curve_schema(_schema_defaults(data)),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SmartPIOptionsFlow(config_entry)


class SmartPIOptionsFlow(OptionsFlow):
    """Edit SmartPI plugin defaults."""

    def __init__(self, config_entry) -> None:
        """Store the config entry being edited."""
        self._config_entry = config_entry
        self._pending_options_data: dict[str, Any] | None = None
        self._editing: DependencyItem | None = None
        self._neighbour_entity: er.RegistryEntry | None = None
        self._pending_item: DependencyItem | None = None
        self._pending_warnings: list[str] = []

    def _is_valve_target_entry(self) -> bool:
        """Return whether the edited entry targets a valve thermostat."""
        target_unique_id = self._config_entry.data.get(CONF_TARGET_VTHERM)
        if target_unique_id is None:
            return False

        registry = er.async_get(self.hass)
        for entry in registry.entities.values():
            if entry.domain != CLIMATE_DOMAIN or entry.unique_id != target_unique_id:
                continue
            return _is_valve_entity(
                self.hass,
                entry.entity_id,
                target_unique_id,
            )
        return False

    def _current_options(self) -> dict[str, Any]:
        """Return the stored options merged over the defaults."""
        options = dict(DEFAULT_OPTIONS)
        options.update(self._config_entry.options or self._config_entry.data)
        return options

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Open the options: a menu for a thermostat entry, the settings form for the defaults."""
        if self._config_entry.data.get(CONF_TARGET_VTHERM) is None:
            return await self._async_settings_form("init", user_input)
        return await self.async_step_thermostat_menu()

    async def async_step_thermostat_menu(self, user_input: dict[str, Any] | None = None):
        """Offer the option groups of a thermostat entry."""
        del user_input
        menu_options = ["settings", "dependencies"]
        if self._is_valve_target_entry() and self._current_options().get(
            CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION
        ):
            menu_options.append("valve_curve")
        return self.async_show_menu(step_id="thermostat_menu", menu_options=menu_options)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        """Edit the regulator settings of a thermostat entry."""
        return await self._async_settings_form("settings", user_input)

    async def _async_settings_form(self, step_id: str, user_input: dict[str, Any] | None):
        """Show or save the regulator settings form."""
        defaults = self._current_options()
        is_valve_target = self._is_valve_target_entry()

        if user_input is not None:
            data = dict(defaults)
            data.update(user_input)
            self._pending_options_data = data
            if (
                is_valve_target
                and user_input.get(CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION)
            ):
                return self.async_show_form(
                    step_id="valve_curve",
                    data_schema=build_valve_curve_schema(data),
                )
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id=step_id,
            data_schema=build_main_options_schema(
                defaults,
                include_valve_linearization=is_valve_target,
            ),
        )

    def _dependency_label(self, item: DependencyItem) -> str:
        """Return a readable label: the kind and the current entity id of the item."""
        entity_id = er.async_resolve_entity_id(er.async_get(self.hass), item.entity)
        return f"{item.kind}: {entity_id or item.entity}"

    def _dependency_items(self) -> list[DependencyItem]:
        return parse_dependencies(self._current_options().get(CONF_SMART_PI_DEPENDENCIES))

    def _save_dependencies(self, items: list[DependencyItem]):
        data = self._current_options()
        data[CONF_SMART_PI_DEPENDENCIES] = dump_dependencies(items)
        return self.async_create_entry(title="", data=data)

    def _target_registry_entry(self) -> er.RegistryEntry | None:
        """Registry entry of the thermostat this options entry belongs to."""
        target = self._config_entry.data.get(CONF_TARGET_VTHERM)
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(CLIMATE_DOMAIN, VT_DOMAIN, target) if target else None
        return registry.async_get(entity_id) if entity_id else None

    def _entity_id(self, registry_id: str) -> str | None:
        return er.async_resolve_entity_id(er.async_get(self.hass), registry_id)

    async def async_step_dependencies(self, user_input: dict[str, Any] | None = None):
        """Offer to add a neighbouring zone, or to edit or remove a dependency."""
        del user_input
        items = self._dependency_items()
        menu_options = ["neighbour_add"]
        if any(item.is_neighbour for item in items):
            menu_options.append("dependency_edit")
        if items:
            menu_options.append("dependency_remove")
        listing = "\n".join(f"- {self._dependency_label(item)}" for item in items) or "-"
        return self.async_show_menu(
            step_id="dependencies",
            menu_options=menu_options,
            description_placeholders={"count": str(len(items)), "items": listing},
        )

    async def async_step_dependency_remove(self, user_input: dict[str, Any] | None = None):
        """Remove the selected dependencies."""
        items = self._dependency_items()
        if user_input is not None:
            removed = set(user_input.get(FIELD_REMOVE_DEPENDENCIES, []))
            if not removed:
                return self.async_abort(reason=ABORT_DEPENDENCIES_UNCHANGED)
            return self._save_dependencies([item for item in items if item.item_id not in removed])
        return self.async_show_form(
            step_id="dependency_remove",
            data_schema=vol.Schema(
                {
                    vol.Optional(FIELD_REMOVE_DEPENDENCIES, default=[]): self._item_selector(
                        items, multiple=True
                    )
                }
            ),
        )

    async def async_step_dependency_edit(self, user_input: dict[str, Any] | None = None):
        """Choose the neighbour to edit."""
        neighbours = [item for item in self._dependency_items() if item.is_neighbour]
        if user_input is not None:
            item = next(i for i in neighbours if i.item_id == user_input[FIELD_EDIT_DEPENDENCY])
            self._editing = item
            self._neighbour_entity = er.async_get(self.hass).async_get(item.entity)
            if self._neighbour_entity is None:
                return self.async_abort(reason="neighbour_missing")
            return await self.async_step_neighbour_details()
        return self.async_show_form(
            step_id="dependency_edit",
            data_schema=vol.Schema(
                {vol.Required(FIELD_EDIT_DEPENDENCY): self._item_selector(neighbours, multiple=False)}
            ),
        )

    def _item_selector(self, items: list[DependencyItem], multiple: bool) -> selector.SelectSelector:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=item.item_id, label=self._dependency_label(item))
                    for item in items
                ],
                multiple=multiple,
                mode=selector.SelectSelectorMode.LIST,
            )
        )

    async def async_step_neighbour_add(self, user_input: dict[str, Any] | None = None):
        """Choose the neighbouring zone: a Versatile Thermostat or a temperature sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            registry = er.async_get(self.hass)
            entry = registry.async_get(user_input[FIELD_NEIGHBOUR])
            own = self._target_registry_entry()
            if entry is None:
                errors[FIELD_NEIGHBOUR] = "entity_without_unique_id"
            elif own is not None and entry.id == own.id:
                errors[FIELD_NEIGHBOUR] = "self_reference"
            elif any(i.is_neighbour and i.entity == entry.id for i in self._dependency_items()):
                errors[FIELD_NEIGHBOUR] = "duplicate_neighbour"
            else:
                self._editing = None
                self._neighbour_entity = entry
                return await self.async_step_neighbour_details()
        return self.async_show_form(
            step_id="neighbour_add",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_NEIGHBOUR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            filter=[
                                selector.EntityFilterSelectorConfig(
                                    integration=VT_DOMAIN, domain=CLIMATE_DOMAIN
                                ),
                                selector.EntityFilterSelectorConfig(
                                    domain="sensor", device_class="temperature"
                                ),
                            ]
                        )
                    )
                }
            ),
            errors=errors,
        )

    def _suggested_door_sensors(self) -> list[str]:
        """Door sensors placed in the area of the zone thermostat."""
        own = self._target_registry_entry()
        if own is None:
            return []
        devices = dr.async_get(self.hass)

        def area_of(entry: er.RegistryEntry) -> str | None:
            if entry.area_id:
                return entry.area_id
            device = devices.async_get(entry.device_id) if entry.device_id else None
            return device.area_id if device else None

        area = area_of(own)
        if area is None:
            return []
        return sorted(
            entry.entity_id
            for entry in er.async_get(self.hass).entities.values()
            if entry.domain == "binary_sensor"
            and (entry.device_class or entry.original_device_class) in DOOR_DEVICE_CLASSES
            and area_of(entry) == area
        )

    def _details_schema(self, defaults: dict[str, Any], is_thermostat: bool) -> vol.Schema:
        schema: dict[Any, Any] = {
            vol.Required(FIELD_MODE, default=defaults[FIELD_MODE]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(NEIGHBOUR_MODES), translation_key="dependency_mode"
                )
            ),
            vol.Optional(FIELD_DOOR_SENSORS, default=defaults[FIELD_DOOR_SENSORS]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    multiple=True,
                    filter=[
                        selector.EntityFilterSelectorConfig(
                            domain="binary_sensor",
                            device_class=DOOR_DEVICE_CLASSES + [WINDOW_DEVICE_CLASS],
                        )
                    ],
                )
            ),
        }
        if is_thermostat:
            schema[
                vol.Required(FIELD_WINDOW_ACTION, default=defaults[FIELD_WINDOW_ACTION])
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WINDOW_ACTIONS), translation_key="neighbour_window_action"
                )
            )
        return vol.Schema(schema)

    async def async_step_neighbour_details(self, user_input: dict[str, Any] | None = None):
        """Connection mode, door sensors and, for a thermostat, the reaction to its open window."""
        neighbour = self._neighbour_entity
        is_thermostat = neighbour.domain == CLIMATE_DOMAIN
        editing = self._editing
        errors: dict[str, str] = {}
        if user_input is not None:
            item, errors = self._build_neighbour(user_input, is_thermostat)
            if not errors:
                self._pending_item = item
                warnings = self._neighbour_warnings(item)
                if warnings:
                    self._pending_warnings = warnings
                    return await self.async_step_neighbour_confirm()
                return self._store_neighbour(item)
            defaults = {
                FIELD_MODE: user_input.get(FIELD_MODE, MODE_ALWAYS),
                FIELD_DOOR_SENSORS: user_input.get(FIELD_DOOR_SENSORS, []),
                FIELD_WINDOW_ACTION: user_input.get(FIELD_WINDOW_ACTION, WINDOW_ACTION_NOTHING),
            }
        elif editing is not None:
            defaults = {
                FIELD_MODE: editing.mode,
                FIELD_DOOR_SENSORS: [e for e in map(self._entity_id, editing.door_sensors) if e],
                FIELD_WINDOW_ACTION: editing.neighbour_window_action,
            }
        else:
            suggested = self._suggested_door_sensors()
            defaults = {
                FIELD_MODE: MODE_DOOR if suggested else MODE_ALWAYS,
                FIELD_DOOR_SENSORS: suggested,
                FIELD_WINDOW_ACTION: WINDOW_ACTION_NOTHING,
            }
        return self.async_show_form(
            step_id="neighbour_details",
            data_schema=self._details_schema(defaults, is_thermostat),
            errors=errors,
            description_placeholders={"neighbour": neighbour.entity_id},
        )

    def _build_neighbour(
        self, user_input: dict[str, Any], is_thermostat: bool
    ) -> tuple[DependencyItem | None, dict[str, str]]:
        registry = er.async_get(self.hass)
        sensor_ids: list[str] = []
        for entity_id in user_input.get(FIELD_DOOR_SENSORS, []):
            entry = registry.async_get(entity_id)
            if entry is None:
                return None, {FIELD_DOOR_SENSORS: "entity_without_unique_id"}
            if (entry.device_class or entry.original_device_class) == WINDOW_DEVICE_CLASS:
                return None, {FIELD_DOOR_SENSORS: "window_sensor_rejected"}
            sensor_ids.append(entry.id)
        item = DependencyItem(
            item_id=self._editing.item_id if self._editing is not None else uuid.uuid4().hex,
            kind=KIND_NEIGHBOUR,
            entity=self._neighbour_entity.id,
            mode=user_input[FIELD_MODE],
            door_sensors=tuple(sensor_ids),
            neighbour_window_action=(
                user_input.get(FIELD_WINDOW_ACTION, WINDOW_ACTION_NOTHING)
                if is_thermostat
                else WINDOW_ACTION_NOTHING
            ),
        )
        others = [i for i in self._dependency_items() if i.item_id != item.item_id]
        own = self._target_registry_entry()
        for issue in validate_dependencies([*others, item], own.id if own else None):
            if issue.item_id == item.item_id:
                field = "base" if issue.code in ("self_reference", "duplicate_neighbour") else FIELD_DOOR_SENSORS
                return None, {field: issue.code}
        return item, {}

    def _neighbour_warnings(self, item: DependencyItem) -> list[str]:
        """Consistency checks that do not block saving."""
        warnings = []
        own = self._target_registry_entry()
        neighbour = self._neighbour_entity
        if neighbour.domain == CLIMATE_DOMAIN and own is not None:
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.data.get(CONF_TARGET_VTHERM) != neighbour.unique_id:
                    continue
                options = entry.options or entry.data
                back = parse_dependencies(options.get(CONF_SMART_PI_DEPENDENCIES))
                if not any(i.is_neighbour and i.entity == own.id for i in back):
                    warnings.append(
                        f"{neighbour.entity_id} has its own SmartPI entry that does not list this zone as a neighbour."
                    )
        state = self.hass.states.get(own.entity_id) if own is not None else None
        attributes = state.attributes if state is not None else {}
        window_manager = attributes.get("window_manager") or {}
        window_sensor = window_manager.get("window_sensor_entity_id")
        door_entities = [self._entity_id(sensor) for sensor in item.door_sensors]
        if window_sensor and window_sensor in door_entities:
            warnings.append(f"{window_sensor} is also the window sensor of this thermostat.")
        if item.mode == MODE_DOOR and attributes.get("is_window_auto_configured"):
            warnings.append(
                "Automatic window detection of this thermostat may react to the open door."
            )
        return warnings

    async def async_step_neighbour_confirm(self, user_input: dict[str, Any] | None = None):
        """Show the warnings and save on confirmation."""
        if user_input is not None:
            return self._store_neighbour(self._pending_item)
        return self.async_show_form(
            step_id="neighbour_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "warnings": "\n".join(f"- {warning}" for warning in self._pending_warnings)
            },
        )

    def _store_neighbour(self, item: DependencyItem):
        items = self._dependency_items()
        replaced = [item if existing.item_id == item.item_id else existing for existing in items]
        if not any(existing.item_id == item.item_id for existing in items):
            replaced.append(item)
        return self._save_dependencies(replaced)

    async def async_step_valve_curve(self, user_input: dict[str, Any] | None = None):
        """Handle the options valve curve flow."""
        defaults = dict(DEFAULT_OPTIONS)
        defaults.update(self._config_entry.options or self._config_entry.data)
        data = dict(self._pending_options_data or defaults)

        if user_input is not None:
            data.update(user_input)
            errors = _validate_valve_curve_config(_schema_defaults(data))
            if errors:
                return self.async_show_form(
                    step_id="valve_curve",
                    data_schema=build_valve_curve_schema(_schema_defaults(data)),
                    errors=errors,
                )
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="valve_curve",
            data_schema=build_valve_curve_schema(_schema_defaults(data)),
        )
