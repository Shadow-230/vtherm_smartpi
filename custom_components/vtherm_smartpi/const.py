"""Constants for the vtherm_smartpi integration."""

from __future__ import annotations

from enum import Enum

DOMAIN = "vtherm_smartpi"
NAME = "Versatile Thermostat SmartPI"

CONF_TARGET_VTHERM = "target_vtherm_unique_id"
CONF_PROP_FUNCTION = "proportional_function"
CONF_MINIMAL_ACTIVATION_DELAY = "minimal_activation_delay"
CONF_MINIMAL_DEACTIVATION_DELAY = "minimal_deactivation_delay"
CONF_SMART_PI_DEADBAND = "smart_pi_deadband"
CONF_SMART_PI_HYSTERESIS_ON = "smart_pi_hysteresis_on"
CONF_SMART_PI_HYSTERESIS_OFF = "smart_pi_hysteresis_off"
CONF_SMART_PI_DEBUG = "smart_pi_debug"
CONF_SMART_PI_USE_SETPOINT_FILTER = "smart_pi_use_setpoint_filter"
CONF_SMART_PI_USE_FF3 = "smart_pi_use_ff3"
CONF_SMART_PI_RELEASE_TAU_FACTOR = "smart_pi_release_tau_factor"
CONF_SMART_PI_DEADBAND_ALLOW_P = "smart_pi_deadband_allow_p"
# How a zone with two heat sources comes back after a setback: "fast" or "economy"
# (smartpi/const.py, ZONE_RECOVERY_*). A zone's own choice may override it.
CONF_SMART_PI_ZONE_RECOVERY = "smart_pi_zone_recovery"
CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE = "smart_pi_allow_pwm_cycle_force"
CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION = "smart_pi_enable_valve_linearization"
CONF_SMART_PI_MIN_VALVE = "smart_pi_min_valve"
CONF_SMART_PI_KNEE_DEMAND = "smart_pi_knee_demand"
CONF_SMART_PI_KNEE_VALVE = "smart_pi_knee_valve"
CONF_SMART_PI_MAX_VALVE = "smart_pi_max_valve"
CONF_SMART_PI_DEPENDENCIES = "smart_pi_dependencies"
# Set by the options when the user asks to reset the zone's learned model after a
# change of the zone; the handler applies each new value once.
CONF_SMART_PI_RESET_TOKEN = "smart_pi_reset_token"
CONF_SMART_PI_HEATING_TYPE = "smart_pi_heating_type"
# Inputs from the heating system (heating_inputs.py): the source of each input, chosen
# in the thermostat's options, and the entities they are read from.
CONF_SMART_PI_WATER_SOURCE = "smart_pi_water_source"
CONF_SMART_PI_BOILER_CALL_SOURCE = "smart_pi_boiler_call_source"
CONF_SMART_PI_SUPPLY_ENTITY = "smart_pi_supply_entity"
# Whether a thermostat takes the boiler's signals from the defaults entry or sets its own.
CONF_SMART_PI_BOILER_SIGNALS = "smart_pi_boiler_signals"
CONF_SMART_PI_HEAT_AVAILABLE_ENTITY = "smart_pi_heat_available_entity"
CONF_SMART_PI_DHW_ENTITY = "smart_pi_dhw_entity"
CONF_SMART_PI_FAULT_ENTITY = "smart_pi_fault_entity"
CONF_SMART_PI_BOILER_CALLED_ENTITY = "smart_pi_boiler_called_entity"
CONF_SMART_PI_BOILER_REASON_ENTITY = "smart_pi_boiler_reason_entity"
BOILER_SIGNAL_KEYS = (
    CONF_SMART_PI_HEAT_AVAILABLE_ENTITY,
    CONF_SMART_PI_DHW_ENTITY,
    CONF_SMART_PI_FAULT_ENTITY,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_REASON_ENTITY,
)
# The water inputs (source and supply sensor) for which the user kept the learned model
# when the options asked: their data appearing is then no change to ask about again.
CONF_SMART_PI_MODEL_KEPT_FOR_WATER = "smart_pi_model_kept_for_water"
# A thermostat's entry takes the shared settings from the "SmartPI defaults" entry (on)
# or holds its own values (off) (documentation/en/vtherm_smartpi.md, "Configuration").
CONF_SMART_PI_USE_DEFAULTS = "smart_pi_use_defaults"
# Set by the migration to minor version 2 in the defaults entry: the "thermostat only"
# settings it held, given to an entry created for each thermostat that ran with them.
CONF_SMART_PI_SETTINGS_TO_MOVE = "smart_pi_settings_to_move"

DEFAULT_MINIMAL_ACTIVATION_DELAY = 0
DEFAULT_MINIMAL_DEACTIVATION_DELAY = 0
DEFAULT_SMART_PI_DEADBAND = 0.05
DEFAULT_SMART_PI_HYSTERESIS_ON = 0.3
DEFAULT_SMART_PI_HYSTERESIS_OFF = 0.5
DEFAULT_SMART_PI_DEBUG = False
DEFAULT_SMART_PI_USE_SETPOINT_FILTER = True
DEFAULT_SMART_PI_USE_FF3 = True
DEFAULT_SMART_PI_RELEASE_TAU_FACTOR = 0.5
DEFAULT_SMART_PI_DEADBAND_ALLOW_P = False
DEFAULT_SMART_PI_ZONE_RECOVERY = "fast"
DEFAULT_SMART_PI_ALLOW_PWM_CYCLE_FORCE = False
DEFAULT_ENABLE_VALVE_LINEARIZATION = False
DEFAULT_MIN_VALVE = 7.0
DEFAULT_KNEE_DEMAND = 80.0
DEFAULT_KNEE_VALVE = 15.0
DEFAULT_MAX_VALVE = 100.0

# The starting values of the forms. At run time they only stand in for a shared setting
# missing from an entry edited by hand (settings.py). The heating type has none: it starts
# "Not set".
DEFAULT_OPTIONS: dict[str, float | bool | int | str] = {
    CONF_MINIMAL_ACTIVATION_DELAY: DEFAULT_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY: DEFAULT_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_DEADBAND: DEFAULT_SMART_PI_DEADBAND,
    CONF_SMART_PI_USE_SETPOINT_FILTER: DEFAULT_SMART_PI_USE_SETPOINT_FILTER,
    CONF_SMART_PI_USE_FF3: DEFAULT_SMART_PI_USE_FF3,
    CONF_SMART_PI_HYSTERESIS_ON: DEFAULT_SMART_PI_HYSTERESIS_ON,
    CONF_SMART_PI_HYSTERESIS_OFF: DEFAULT_SMART_PI_HYSTERESIS_OFF,
    CONF_SMART_PI_RELEASE_TAU_FACTOR: DEFAULT_SMART_PI_RELEASE_TAU_FACTOR,
    CONF_SMART_PI_DEADBAND_ALLOW_P: DEFAULT_SMART_PI_DEADBAND_ALLOW_P,
    CONF_SMART_PI_ZONE_RECOVERY: DEFAULT_SMART_PI_ZONE_RECOVERY,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE: DEFAULT_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_DEBUG: DEFAULT_SMART_PI_DEBUG,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION: DEFAULT_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_MIN_VALVE: DEFAULT_MIN_VALVE,
    CONF_SMART_PI_KNEE_DEMAND: DEFAULT_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE: DEFAULT_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE: DEFAULT_MAX_VALVE,
}

# Where each setting is set. Shared: in the "SmartPI defaults" entry, and in a
# thermostat's entry only when it does not use the defaults. Thermostat only: in the
# thermostat's own entry; not set means not used.
SHARED_SETTINGS: tuple[str, ...] = (
    CONF_SMART_PI_DEADBAND,
    CONF_SMART_PI_HYSTERESIS_ON,
    CONF_SMART_PI_HYSTERESIS_OFF,
    CONF_SMART_PI_RELEASE_TAU_FACTOR,
    CONF_SMART_PI_USE_SETPOINT_FILTER,
    CONF_SMART_PI_USE_FF3,
    CONF_SMART_PI_DEADBAND_ALLOW_P,
    CONF_SMART_PI_ZONE_RECOVERY,
    CONF_SMART_PI_DEBUG,
)
THERMOSTAT_SETTINGS: tuple[str, ...] = (
    CONF_SMART_PI_HEATING_TYPE,
    CONF_MINIMAL_ACTIVATION_DELAY,
    CONF_MINIMAL_DEACTIVATION_DELAY,
    CONF_SMART_PI_ALLOW_PWM_CYCLE_FORCE,
    CONF_SMART_PI_ENABLE_VALVE_LINEARIZATION,
    CONF_SMART_PI_MIN_VALVE,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
)
VALVE_CURVE_POINTS: tuple[str, ...] = (
    CONF_SMART_PI_MIN_VALVE,
    CONF_SMART_PI_KNEE_DEMAND,
    CONF_SMART_PI_KNEE_VALVE,
    CONF_SMART_PI_MAX_VALVE,
)

HVAC_OFF_REASON_WINDOW_DETECTION = "hvac_off_window_detection"
PROP_FUNCTION_SMART_PI = "smartpi"

DATA_FACTORY_REGISTERED = "factory_registered"
DATA_SERVICES_REGISTERED = "services_registered"
# Link between lead thermostats and their second sources (smartpi/source_link.py).
DATA_SOURCE_LINK = "source_link"
SIGNAL_SMARTPI_TARGET_UPDATED = "vtherm_smartpi_target_updated"
DIAGNOSTIC_SENSOR_UNIQUE_ID_PREFIX = "smartpi_diag"
# The entries one save of the options writes (config_flow.mark_save): each reloads once.
DATA_SAVE_GROUP = "save_group"
# The diagnostic sensors added to Home Assistant now, by thermostat unique id (sensor.py).
# Kept outside hass.data[DOMAIN], whose other keys tell whether a SmartPI entry is still loaded.
DATA_DIAGNOSTIC_SENSORS = f"{DOMAIN}_diagnostic_sensors"

# The repair notice listing the entries the migration created (__init__.py).
ISSUE_THERMOSTAT_ENTRIES_CREATED = "thermostat_entries_created"

SERVICE_RESET_SMARTPI_LEARNING = "reset_smartpi_learning"
SERVICE_SET_SMARTPI_LEARNING = "set_smartpi_learning"
SERVICE_FORCE_SMARTPI_CALIBRATION = "force_smartpi_calibration"
SERVICE_RESET_SMARTPI_INTEGRAL = "reset_smartpi_integral"
ATTR_LEARNING_ENABLED = "learning_enabled"


class EventType(Enum):
    """Plugin events exposed by vtherm_smartpi."""

    SMART_PI_EVENT = "vtherm_smartpi_event"
