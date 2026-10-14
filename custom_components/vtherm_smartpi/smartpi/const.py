"""
Constants and Enums for Smart-PI Algorithm.
"""
from enum import Enum
import logging

_LOGGER = logging.getLogger(__name__)

def clamp(x: float, lo: float, hi: float) -> float:
    """Clamp x into [lo, hi]."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x

class SmartPIPhase(str, Enum):
    """Phases of the Smart-PI algorithm."""
    HYSTERESIS = "Hysteresis"  # Learning phase with ON/OFF control
    STABLE = "Stable"          # PI control with reliable model
    CALIBRATION = "Calibration" # Forced calibration cycle in progress

class SmartPICalibrationPhase(str, Enum):
    """Phases of the Smart-PI forced calibration."""
    IDLE = "Idle"
    COOL_DOWN = "CoolDown"
    HEAT_UP = "HeatUp"
    COOL_DOWN_FINAL = "CoolDownFinal"

class SmartPICalibrationResult(str, Enum):
    """Resolution status of a Smart-PI forced calibration cycle."""
    PENDING = "Pending"
    SUCCESS = "Success"
    CANCELLED = "Cancelled"

# ########################################################################
#                      SAFETY-FIRST GOVERNANCE ENUMS                   #
# ########################################################################

class GovernanceRegime(str, Enum):
    """Physical regime detected during a calculation step."""
    WARMUP = "warmup"                  # Hysteresis / bootstrap phase
    EXCITED_STABLE = "excited_stable"  # Normal PI regulation, significant error
    NEAR_BAND = "near_band"            # Close to setpoint, weak signal
    DEAD_BAND = "dead_band"            # In dead band, no action
    HOLD = "hold"                      # Integrator hold active
    PERTURBED = "perturbed"            # External disturbance (window, shedding)
    DEGRADED = "degraded"              # Sensor absent, deadtime unknown
    SATURATED = "saturated"            # Command at 0% or 100%


class FreezeReason(str, Enum):
    """Diagnostic reason why adaptation was frozen."""
    NONE = "none"
    # Structural
    REGIME_TRANSITION = "regime_transition"  # Cycle not homogeneous
    CYCLE_INVALID = "cycle_invalid"
    # Physical / external
    EVENT_POLLUTED = "event_polluted"
    SENSOR_INVALID = "sensor_invalid"
    DEADTIME_UNRELIABLE = "deadtime_unreliable"
    BOOT_GUARD = "boot_guard"
    # Regime-specific
    DEAD_BAND = "dead_band"
    NEAR_BAND = "near_band"
    WARMUP = "warmup"
    HOLD = "hold"
    PERTURBED = "perturbed"
    SATURATION = "saturation"
    SYSTEM_INEFFICIENT = "system_inefficient"


class GovernanceDecision(str, Enum):
    """Decision level for parameter adaptation."""
    ADAPT_ON = "adapt_on"                # Calculation and update allowed
    FREEZE = "freeze"                    # Keep previous values
    HARD_FREEZE = "hard_freeze"          # Absolute prohibition of update
    SOFT_FREEZE_DOWN = "soft_freeze_down" # Only decrease allowed


# Governance matrix: regime -> {domain: (decision, freeze_reason)}
# Domains: 'thermal' (a/b learning), 'gains' (Kp/Ki adaptation)
GOVERNANCE_MATRIX = {
    GovernanceRegime.WARMUP: {
        "thermal": (GovernanceDecision.ADAPT_ON, FreezeReason.NONE),
        "gains": (GovernanceDecision.FREEZE, FreezeReason.WARMUP),
    },
    GovernanceRegime.EXCITED_STABLE: {
        "thermal": (GovernanceDecision.ADAPT_ON, FreezeReason.NONE),
        "gains": (GovernanceDecision.ADAPT_ON, FreezeReason.NONE),
    },
    GovernanceRegime.NEAR_BAND: {
        "thermal": (GovernanceDecision.ADAPT_ON, FreezeReason.NONE),
        "gains": (GovernanceDecision.ADAPT_ON, FreezeReason.NONE),
    },
    GovernanceRegime.DEAD_BAND: {
        "thermal": (GovernanceDecision.HARD_FREEZE, FreezeReason.DEAD_BAND),
        "gains": (GovernanceDecision.HARD_FREEZE, FreezeReason.DEAD_BAND),
    },
    GovernanceRegime.SATURATED: {
        "thermal": (GovernanceDecision.ADAPT_ON, FreezeReason.NONE),  # Allow learning
        "gains": (GovernanceDecision.FREEZE, FreezeReason.SATURATION),  # Keep gains frozen
    },
    GovernanceRegime.HOLD: {
        "thermal": (GovernanceDecision.HARD_FREEZE, FreezeReason.HOLD),
        "gains": (GovernanceDecision.SOFT_FREEZE_DOWN, FreezeReason.HOLD),
    },
    GovernanceRegime.PERTURBED: {
        "thermal": (GovernanceDecision.HARD_FREEZE, FreezeReason.PERTURBED),
        "gains": (GovernanceDecision.HARD_FREEZE, FreezeReason.PERTURBED),
    },
    GovernanceRegime.DEGRADED: {
        "thermal": (GovernanceDecision.HARD_FREEZE, FreezeReason.SENSOR_INVALID),
        "gains": (GovernanceDecision.HARD_FREEZE, FreezeReason.SENSOR_INVALID),
    },
}

# ------------------------------
# Default controller parameters
# ------------------------------

# Safe fallback gains when model is unreliable
KP_SAFE = 0.55
KI_SAFE = 0.010

# Allowed ranges for computed gains
KP_MIN = 0.10
KP_MAX = 5.0
KI_MIN = 0.001
KI_MAX = 0.050

# IMC/SIMC gain tuning constants
GAIN_LAMBDA_DEADTIME_FACTOR = 3.0
GAIN_LAMBDA_VALVE_DEADTIME_FACTOR = 4.0
GAIN_LAMBDA_CYCLE_FACTOR = 2.0
GAIN_LAMBDA_MIN_MIN = 5.0


# Anti-windup / integrator behavior
INTEGRAL_LEAK = 0.995  # leak factor per cycle when inside deadband
MAX_STEP_PER_MINUTE = 0.25  # max output change per minute (rate limit)

# Setpoint step boost: faster rate limit when setpoint changes significantly
# This allows quick power ramp-up when user increases setpoint
SETPOINT_BOOST_THRESHOLD = 0.3   # min setpoint change (°C) to trigger boost
SETPOINT_BOOST_ERROR_MIN = 0.3   # min error (°C) to keep boost active
SETPOINT_BOOST_RATE = 0.50       # boosted rate limit (/min) vs 0.15 normal

# Setpoint change handling (mode change vs adjustment)
# - Large change (>= threshold): mode change (eco ↔ comfort) -> reset PI state
# - Small change (< threshold): integral preserved, servo via P + SP filter + FF
SETPOINT_MODE_DELTA_C = 0.5      # °C threshold for mode change detection
OVERSHOOT_I_CLAMP_EPS_C = 0.04  # Guard band below setpoint where integral cannot increase (°C)

# Tracking anti-windup (back-calculation) tuned for slow thermal systems
AW_TRACK_TAU_S = 120.0        # tracking time constant in seconds (typ. 60-180s)
AW_TRACK_MAX_DELTA_I = 5.0    # safety clamp on integral correction per cycle

# Skip cycles after resume from interruption (window, etc.)
SKIP_CYCLES_AFTER_RESUME = 1
LEARNING_PAUSE_RESUME_MIN = 20  # Pause learning after resume (window close, etc.) to allow stabilization. NB: Corrected duplication in original file comments

# Periodic recalculation interval (seconds) for SmartPI
# This ensures the rate-limit progresses even when temperature sensors don't update frequently
SMARTPI_RECALC_INTERVAL_SEC = 60

# Max continuous pause duration (minutes) before explicitly purging PI memory
PROLONGED_PAUSE_MEMORY_EXPIRATION_MIN = 5760.0  # 96 hours (4 days)

# --- Hysteresis Mode (during learning phase) ---
HYST_UPPER_C = 0.5  # ON -> OFF threshold (°C above setpoint)
HYST_LOWER_C = 0.3  # OFF -> ON threshold (°C below setpoint)

# Default deadband around setpoint (°C)
DEFAULT_DEADBAND_C = 0.05

# Absolute hysteresis for deadband exit (reduces oscillations at boundary)
# Enter deadband at |e| < deadband_c, exit only when |e| > deadband_c + hysteresis
# Using absolute value (not multiplicative) ensures consistent behavior across
# different deadband configurations and typical sensor noise levels.
DEADBAND_HYSTERESIS = 0.025

# --- Asymmetric Deadband / Near-band (HEAT only) ---
# Intent (thermal "rule of thumb"):
# - Make the "quiet zone" a bit wider when slightly below the setpoint (e>0) so the controller
#   does not wait too long before restarting after a setpoint decrease.
# - Make the zone tighter above the setpoint (e<0) to reduce overshoot/hunting.
# Guardrails: asymmetry is applied only in HEAT; COOL keeps symmetric logic.

# Deadband (°C) and its hysteresis (°C)
DEADBAND_BELOW_C = 0.06
DEADBAND_ABOVE_C = 0.04
DEADBAND_HYST_BELOW_C = 0.02
DEADBAND_HYST_ABOVE_C = 0.02

# Near-band asymmetry:
# - below setpoint: use configured near_band_deg (self.near_band_deg)
# - above setpoint: scale it down with a factor
NEAR_BAND_ABOVE_FACTOR = 0.40
NEAR_BAND_HYSTERESIS_C = 0.05

class TrajectoryPhase(str, Enum):
    """Trajectory state for setpoint shaping."""
    IDLE = "idle"
    TRACKING = "tracking"
    RELEASE = "release"


class ServoPhase(str, Enum):
    """Legacy compatibility enum for pre-trajectory tests and persisted state."""
    NONE = TrajectoryPhase.IDLE.value
    BOOST = TrajectoryPhase.TRACKING.value
    LANDING = TrajectoryPhase.TRACKING.value


# Trajectory generator
TRAJECTORY_ENABLE_ERROR_THRESHOLD = 0.3  # °C — signed raw error needed to arm the trajectory
TRAJECTORY_COMPLETE_EPS_C = 0.05         # °C — convergence epsilon to finish tracking
TRAJECTORY_MIN_P_ERROR_RATIO = 0.25      # Fraction of the raw signed error kept on the P path during braking
TRAJECTORY_SETPOINT_RHO = 0.70           # Fraction of available model slope used after a setpoint change
TRAJECTORY_DISTURBANCE_RHO = 0.50        # Fraction of available model slope used after a disturbance recovery
TRAJECTORY_BRAKE_GAIN = 1.20             # Safety factor applied to the predicted inertial rise before braking
TRAJECTORY_BRAKE_RELEASE_HYST_C = 0.10   # Extra signed-error margin to keep an active trajectory in tracking
TRAJECTORY_I_RUN_SCALE = 0.20            # Reduce free integral growth while the late-braking trajectory is still tracking
TRAJECTORY_RELEASE_TAU_FACTOR = 0.5      # Make the return to the raw target faster than braking while staying smooth
TRAJECTORY_BUMPLESS_MAX_U_DELTA = 0.03   # Maximum allowed proportional command step when releasing trajectory
# Setpoint landing cap (HEAT-only, internal linear command space [0, 1])
LANDING_SAFETY_MARGIN_C = 0.05
LANDING_ENABLE_ERROR_THRESHOLD_C = 0.40
LANDING_RELEASE_SLOPE_H = 0.12
LANDING_RELEASE_TIME_TO_DEADTIME_RATIO = 1.0
LANDING_RELEASE_TIME_TO_TARGET_EPS_MIN = 1e-6
LANDING_MIN_HORIZON_MIN = 1e-6
LANDING_U_EPS = 1e-6
LANDING_TEMPERATURE_COMPARISON_EPS_C = 1e-9
LANDING_NON_CONSTRAINING_PERSISTENCE = 3
LANDING_TRACKING_RELEASE_PERSISTENCE = 3
INTEGRAL_GUARD_RELEASE_ERROR_RATIO = 2.0 # Release positive-I guard only once the residual error is close to deadband scale
INTEGRAL_GUARD_RELEASE_SLOPE_RATIO = 0.35 # Release positive-I guard when the signed recovery slope has collapsed enough
INTEGRAL_GUARD_RELEASE_SLOPE_ABS_H = 0.12 # Absolute signed-slope floor (°C/h) below which stabilization may be accepted
INTEGRAL_GUARD_RELEASE_PERSISTENCE = 3    # Consecutive evaluations required before re-enabling positive integral growth
# Error filter time constant
ERROR_FILTER_TAU = 25.0 # Minutes (matches alpha ~0.35 at 10min)


# --- Robust learning / gating constants ---
# Window sizes
B_POINTS_MAX = 40        # OFF samples for b (tau)
A_POINTS_MAX = 25        # ON samples for a
RESIDUAL_HIST_MAX = 60   # Residual history for MAD estimation

# Robust gating
RESIDUAL_GATE_K = 4.5   # |r| > k * sigma_r  -> freeze learning

# Intercept coherence checks (dimensionless ratios)
INTERCEPT_SIGMA_FACTOR = 2.0   # |c| <= factor * sigma_r
INTERCEPT_SCALE_FACTOR = 0.30  # |c| <= factor * median(|y|)

# Tau stability check
B_STABILITY_MAD_RATIO_MAX = 0.60   # MAD(b) / median(b)
LEARN_BOOTSTRAP_COUNT = 10      # Number of learn cycles before applying strict residual gating

# --- SmartPI Robust Learning Constants ---
# Median+MAD Strategy Constants
AB_HISTORY_SIZE = 31      # Keep last 31 (ODD) values
AB_MIN_SAMPLES_B = 8      # Min samples for b publication (OFF phase)
AB_MIN_SAMPLES_A = 6      # Min samples for a publication (ON phase)
AB_MIN_SAMPLES_A_CONVERGED = 6  # Min samples for a publication once b converged
AB_MAD_SIGMA_MULT = 3.0   # Outlier rejection threshold (sigma)

AB_MAD_K = 1.4826         # Sigma scaling factor for MAD
AB_VAL_TOLERANCE = 1e-12  # Small epsilon
LEARN_SAMPLE_MAX = 240          # Max samples history (e.g. 4h @ 1min)
LEARN_Q_HIST_MAX = 200          # History for quantization estimation
DT_DERIVATIVE_MIN_ABS = 0.05    # Min absolute dT (°C) amplitude guard
OLS_MIN_JUMPS = 3               # Min temperature level changes for OLS validity
OLS_T_MIN = 2.5                 # Min t-statistic for slope significance
LEARN_QUALITY_THRESHOLD = 0.25  # Min QI quality to accept learning
QUANTIZATION_ROUND_TO = 0.001   # Rounding / binning for quantization detection

# Sequential gate a->b
AB_B_CONVERGENCE_MIN_SAMPLES: int = 11
AB_B_CONVERGENCE_MAD_RATIO: float = 0.30
AB_B_CONVERGENCE_RANGE_RATIO: float = 0.10
AB_B_CONVERGENCE_MIN_BHIST: int = 5
AB_A_SOFT_GATE_MIN_B: int = 8

# --- ABEstimator weighted-median aggregation parameters ---
AB_WMED_PLATEAU_N: int = 11          # Most-recent N points assigned weight 1.0 (plateau)
AB_WMED_ALPHA: float = 1.0           # Weight factor at the start of the tail
AB_WMED_R: float = 0.85              # Geometric decay factor for tail weights
AB_MIN_POINTS_FOR_PUBLISH_A: int = 6  # Below this count: freeze a to default value
AB_MIN_POINTS_FOR_PUBLISH_B: int = 8  # Below this count: freeze b to default value
AB_MIN_POINTS_FOR_PUBLISH: int = AB_MIN_POINTS_FOR_PUBLISH_B

# --- ABEstimator persistent drift detection ---
AB_DRIFT_BUFFER_MAXLEN: int = 8
AB_DRIFT_MIN_COUNT: int = 6
AB_DRIFT_MAX_BUFFER_MAD_FACTOR: float = 1.5
AB_DRIFT_MIN_SHIFT_FACTOR: float = 3.0
AB_DRIFT_RECENTER_MAX_CYCLES: int = 5
AB_DRIFT_RECENTER_ALPHA: float = 0.25
AB_DRIFT_RECENTER_STEP_MAX_FACTOR: float = 2.0
AB_DRIFT_MAD_FLOOR_ABS: float = 1e-4
AB_DRIFT_SEQ_GAP_MAX: int = 3

# --- SmartPI Learning Window Constants ---
# Absolute timeout for a learning window (both A and B).
# The window extends as long as the OLS slope is not yet robust, up to this limit.
DT_MAX_MIN = 240
MIN_ABS_DT = 0.03      # °C  (reference value; not used as a gate in learning_window)
DELTA_MIN = 0.2        # °C (Matches DELTA_MIN_ON)
U_OFF_MAX = 0.05
U_ON_MIN = 0.20
DELTA_MIN_OFF = 0.5        # °C
DELTA_MIN_ON = 0.2         # °C
AB_B_MIN_PASSIVE_GRADIENT_C = 2.0
AB_B_OUTDOOR_TARGET_MARGIN_C = 2.0
# Backward-compatible names retained for existing integrations and tests.
AB_B_HEAT_MIN_LOSS_GRADIENT_C = AB_B_MIN_PASSIVE_GRADIENT_C
AB_B_HEAT_OUTDOOR_BELOW_TARGET_MARGIN_C = AB_B_OUTDOOR_TARGET_MARGIN_C

# Power coefficient of variation gate (Welford-based)
U_CV_MAX = 0.30            # Maximum accepted CV of power over the learning window
U_CV_MIN_MEAN = 0.05       # Minimum mean(u) to compute CV (avoids division by ~0)


# --- SmartPI Near Band Defaults ---
DEFAULT_NEAR_BAND_DEG = 0.40
NEAR_BAND_TAPER_KP_MIN = 0.75
NEAR_BAND_TAPER_KI_MIN = 0.45


# --- Forced Calibration Constants ---
CALIBRATION_TIMEOUT_MIN = 600  # 10 hours timeout

# --- AutoCalibTrigger Enums ---

class AutoCalibState(str, Enum):
    """State machine states for AutoCalibTrigger."""
    IDLE = "idle"
    WAITING_SNAPSHOT = "waiting_snapshot"
    MONITORING = "monitoring"
    TRIGGERED = "triggered"
    POST_CALIB_CHECK = "post_calib_check"


class AutoCalibWaitingReason(str, Enum):
    """Reason for remaining in waiting_snapshot state."""
    NONE = "none"
    DEADTIME_COOL_PENDING = "deadtime_cool_pending"
    FALLBACK_7D_COUNTDOWN = "fallback_7d_countdown"


# --- AutoCalibTrigger Constants ---
AUTOCALIB_SNAPSHOT_PERIOD_H = 120          # Rolling snapshot period: 5 days
AUTOCALIB_DT_COOL_FALLBACK_DAYS = 7       # Days before fallback if cool deadtime never reliable
AUTOCALIB_COOLDOWN_H = 24                  # Minimum hours between calibrations
AUTOCALIB_A_MAD_THRESHOLD = 0.25          # MAD/med threshold for 'a' stagnation
AUTOCALIB_B_MAD_THRESHOLD = 0.30          # MAD/med threshold for 'b' stagnation
AUTOCALIB_TEXT_GRADIENT_C = 5.0           # Min Tin-Text gradient to check deadtime_cool stagnation
AUTOCALIB_MAX_RETRIES = 3                  # Max retries before declaring model degraded
AUTOCALIB_RETRY_DELAY_H = 6               # Hours between retries
AUTOCALIB_EXIT_NEW_OBS_MIN = 1            # Minimum new observations (a/b) for positive exit

# --- FFv2 Governance Enums ---

class ABConfidenceState(str, Enum):
    """Confidence state for a,b model parameters."""
    AB_BOOTSTRAP = "ab_bootstrap"
    AB_OK = "ab_ok"
    AB_DEGRADED = "ab_degraded"
    AB_BAD = "ab_bad"


# AB confidence thresholds keep full confidence stricter than bootstrap exit.
AB_CONFIDENCE_MIN_SAMPLES_A = 11
AB_CONFIDENCE_MIN_SAMPLES_B = 11

# --- FFv2 Normative Constants ---

# Trim slow correction
FF_TRIM_RHO = 0.15        # Max trim authority relative to u_ff_ab (dimensionless ratio)
FF_TRIM_LAMBDA = 0.05     # Trim EMA learning rate per admissible episode
FF_TRIM_EPSILON = 0.02    # Min u_ff_ab denominator for relative budget (avoids div-by-~0)
FF_TRIM_REBOOT_FREEZE_CYCLES = 3  # Freeze trim learning for a few cycles after reboot
FF_TRIM_MAX_ERROR_C = 0.3         # Max |error| (°C) across an admissible thermal window
FF_TRIM_MAX_SLOPE_H = 0.6        # Max |mean slope| (°C/h) for an admissible thermal window
FF_TRIM_PERSISTENCE = 3          # Same-direction independent windows required before applying trim
FF_TRIM_BUFFER_SIZE = 5          # Rolling sample count used for median trim correction
FF_TRIM_DELTA_EPSILON = 0.001    # Ignore power corrections below actuator precision scale
FF_TRIM_PI_STABILITY_EPSILON = 0.01  # Max PI output range across a near-band trim window
FF_TRIM_MIN_WINDOW_MIN = 30.0
FF_TRIM_MIN_WINDOW_CYCLES = 6
FF_TRIM_DEADTIME_WINDOW_FACTOR = 2.0
FF_TRIM_MIN_DISTINCT_MEASUREMENTS = 3
FF_TRIM_MIN_POWER_COVERAGE = 0.995
FF_TRIM_PERIODIC_MIN_DISTINCT_MEASUREMENTS = 5
FF_TRIM_PERIODIC_MIN_POWER_RANGE = 0.01
FF_TRIM_PERIODIC_NEGLIGIBLE_LIMIT_S = 5.0
FF_TRIM_TRANSFER_MAX_ERROR_C = 0.10
FF_TRIM_TRANSFER_MAX_SLOPE_H = 0.10
FF_TRIM_TRANSFER_MAX_P_POWER = 0.01
FF_TRIM_TRANSFER_MAX_POWER_RANGE = 0.005
FF_TRIM_TRANSFER_MAX_DELIVERY_RESIDUAL = 0.005
FF_TRIM_TRANSFER_COMMAND_EPSILON = 0.001

# Deadband output shaping
DEADBAND_EDGE_PERSISTENCE = 2

# AB confidence & fallback
AB_BAD_PERSIST_CYCLES = 3           # Cycles in AB_BAD before fallback activates

# --- FF3 conservative pseudo-MPC constants ---
FF3_DELTA_U = 0.05
FF3_MAX_AUTHORITY = 0.20
FF3_NEARBAND_GAIN = 0.50
FF3_MIN_HORIZON_CYCLES = 2
FF3_RESPONSE_LOOKAHEAD_CYCLES = 2
FF3_MAX_HORIZON_CYCLES = 8
FF3_ACTION_SENSITIVITY_EPS_C = 1e-4
FF3_COST_TRACKING_WEIGHT = 1.0
FF3_COST_TERMINAL_WEIGHT = 2.0
FF3_COST_OVERSHOOT_WEIGHT = 4.0
FF3_COST_DU_WEIGHT = 0.05
FF3_SCORE_EPS_COST = 1e-4
FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR = 0.5

# --- Adaptive T_int Filter ---
ENABLE_ADAPTIVE_TINT_FILTER = True
TINT_LP_ALPHA = 0.2              # EMA smoothing factor (0..1)
TINT_SIGMA_WINDOW = 30           # Rolling window size for sigma estimation
TINT_SIGMA_MIN = 0.02            # Floor on sigma (degC)
TINT_ADAPTIVE_K = 3.0            # Threshold multiplier: publish when |delta| > k * sigma

# --- Known model inputs (zone dependencies) ---
# Smallest effective loss coefficient (1/min) for which known inputs are composed.
KNOWN_INPUTS_B_EFF_MIN = 1e-5
# A known direct heat input shifts the reference temperature by q / b_eff;
# the shift is bounded so that a weak loss estimate cannot amplify it unbounded.
# The bound must exceed the largest setpoint-to-outdoor difference: below it the
# feed-forward counts less known heat than there is (b cancels out of it).
KNOWN_HEAT_MAX_SHIFT_C = 40.0

# --- Neighbour coupling (zone dependencies) ---
# Smallest zone-to-neighbour temperature difference that carries information on k.
COUPLING_K_MIN_DT = 0.5
# Upper bound of a credible exchange coefficient (1/min).
COUPLING_K_MAX = 0.02
COUPLING_MIN_SAMPLES = 8
# A coefficient is published only when its accepted values agree: MAD / median.
COUPLING_MAX_DISPERSION = 0.35
COUPLING_HISTORY_SIZE = 31
COUPLING_LS_WINDOW = 31
# Always-connected neighbours need longer buffers: b and each k can only be told
# apart once their temperature differences have varied independently.
COUPLING_JOINT_WINDOW = 96
# A joint fit needs this many windows: a few hours of rows share the same errors
# of SmartPI's own a and dead time and would agree on a wrong k.
COUPLING_JOINT_MIN_ROWS = 24
# New windows between two fits added to the history, so that agreeing fits do
# not merely repeat the same data.
COUPLING_JOINT_FIT_SPACING = 4
# A joint fit separates b from each k when every coefficient's standard error is
# at most this fraction of its value. Unlike a condition number, the standard
# error falls as windows accumulate.
COUPLING_JOINT_MAX_REL_SE = 0.15
# Above this condition number, door connections open together cannot be told apart.
COUPLING_LS_COND_MAX = 30.0
# Measurement windows are longer than SmartPI's own: with 0.1 degC sensors a
# few minutes hold a single reading step.
COUPLING_WINDOW_S = 30 * 60.0
# Learning is skipped for this long after a door opens or closes.
DOOR_SETTLING_S = 10 * 60.0

# --- Second heat source: split between the lead and the source ---
# Default parameters of the rules (fractions of full power).
SPLIT_DEFAULT_RATIO = 1.0
SPLIT_DEFAULT_ALONE = 0.5
SPLIT_DEFAULT_BAND = 0.2
# Allocation loop: an error held for SPLIT_SETTLE_FACTOR source settle times
# (delay plus lag), and never less than SPLIT_MIN_TIME_S, moves the source by
# 100 %. The lead's demand is averaged over SPLIT_AVERAGE_FRACTION of that time,
# never less than SPLIT_AVERAGE_MIN_S. Chosen on an ideal lead with fast and
# slow, weak and strong sources: overshoot within 3 %, no swinging.
SPLIT_SETTLE_FACTOR = 4.0
SPLIT_MIN_TIME_S = 60 * 60.0
SPLIT_AVERAGE_FRACTION = 0.1
SPLIT_AVERAGE_MIN_S = 15 * 60.0
# The source never covers its full range faster than it settles, nor than this.
SPLIT_MIN_FULL_RANGE_S = 10 * 60.0
# Range of the lead's demand fed to the loop (before its 0..1 limits).
SPLIT_DEMAND_MIN = -1.0
SPLIT_DEMAND_MAX = 2.0
# Recommendation: a source settling in an hour or more is slow; a lead slower
# than its source by this factor has its role reversed.
SPLIT_SLOW_SOURCE_S = 60 * 60.0
SPLIT_ROLES_REVERSED_FACTOR = 1.5

# --- Water input (supply water temperature from Smart Boiler) ---
# Heating types of a SmartPI thermostat. A water type turns the supply water into
# the emitter power factor phi = (max(T_supply - T_room, 0) / (T_ref - 20)) ** n;
# the electric types do not use the water. Exponents and reference waters are
# provisional (radiator 1.3 and 60 degC, floor 1.1 and 45 degC).
HEATING_RADIATOR = "radiator"
HEATING_FLOOR_WATER = "floor_water"
HEATING_FLOOR_ELECTRIC = "floor_electric"
HEATING_ELECTRIC_FAST = "electric_fast"
HEATING_TYPES = (HEATING_RADIATOR, HEATING_FLOOR_WATER, HEATING_FLOOR_ELECTRIC, HEATING_ELECTRIC_FAST)
WATER_EMITTERS: dict[str, tuple[float, float]] = {
    HEATING_RADIATOR: (1.3, 60.0),
    HEATING_FLOOR_WATER: (1.1, 45.0),
}
WATER_ROOM_REFERENCE_C = 20.0
# The supply is averaged over this time: burner cycling makes the raw value jump.
WATER_AVERAGE_S = 15 * 60.0
# Long-term average of the factor, used while the water is momentarily unknown.
WATER_LONG_AVERAGE_S = 3 * 86400.0
# Below this factor the water brings next to no heat (an idle boiler that fires on
# demand, water near the room): the integral is held, and the command is computed
# with at least this factor so that the zone can still call for heat.
WATER_NO_HEAT = 0.05
# Upper bound of the factor: the controller's power range is 0..1, so water hotter
# than the reference is taken as the reference (never less heat than without the input).
WATER_FACTOR_MAX = 1.0
# No reading at all (or an unknown reason) for this long after data were seen counts
# as a lasting gap: Smart Boiler removed, or the thermostat no longer one of its zones.
WATER_NO_DATA_LASTING_S = 24 * 3600.0

# --- A boiler that is not called (documentation/en/water_input.md, section 11) ---
# A boiler started this soon after the zone's call answers it (VT's activation delay
# is at most 10 min); not called for longer, the zone is held: no heat comes.
BOILER_CALL_GRACE_S = 15 * 60.0

# --- Second heat source: its heat as a known input of the lead ---
# x_f below this: the source's heat counts as absent (learning is not gated).
SOURCE_X_OFF = 0.05
# The source_heat gate pauses SmartPI's learning from this x_f; a neighbour's
# window counts for the source's column only when its weighted x_f reaches it.
SOURCE_X_MIN = 0.2
# Largest credible a_f (degC/min at full power).
SOURCE_A_F_MAX = 0.5
# A joint fit separates a_f only when x_f varies at least this much (standard deviation).
SOURCE_X_MIN_STD = 0.1
# A newly published coefficient enters the control law over this time, so that
# the controller's integral, which had absorbed its effect, follows smoothly.
KNOWN_INPUT_FADE_S = 60 * 60.0
# A lead command older than this is stale: the source applies 0.
SOURCE_COMMAND_MAX_AGE_S = 15 * 60.0
# Horizon points of the source's predicted heat (minutes).
SOURCE_TRAJECTORY_MIN = (15.0, 30.0, 60.0, 120.0)
# While SmartPI learns, the source works too, in blocks alternating between
# SOURCE_LEARN_HIGH and SOURCE_LEARN_LOW, each lasting twice the source's
# response time and at least SOURCE_LEARN_BLOCK_MIN_S, so that the zone fit can
# tell both sources apart. A zone colder than its setpoint by SOURCE_LEARN_COLD_C
# gets the source at full power; one warmer by SOURCE_LEARN_WARM_C gets none.
SOURCE_LEARN_HIGH = 1.0
SOURCE_LEARN_LOW = 0.0
SOURCE_LEARN_BLOCK_MIN_S = 2 * 3600.0
SOURCE_LEARN_COLD_C = 0.5
SOURCE_LEARN_WARM_C = 0.3
# Joint fit of the zone (zone_fit.py): least squares of a, b, a_f and the
# neighbours' k on the measurement windows, for each candidate delay of the lead
# and each candidate time scale of the source. Published from
# ZONE_FIT_MIN_WINDOWS windows when the relative standard errors of a, b and a_f
# are within ZONE_FIT_MAX_REL_SE; the sums fade over ZONE_FIT_MEMORY_WINDOWS; a
# new best candidate replaces the one in use when its squared error is
# ZONE_FIT_SWITCH_GAIN lower.
ZONE_FIT_MIN_WINDOWS = 36.0
ZONE_FIT_MEMORY_WINDOWS = 288.0
ZONE_FIT_MAX_REL_SE = 0.2
ZONE_FIT_SWITCH_GAIN = 0.2
# Candidate delays of the lead (minutes): SmartPI's own dead time is too long
# with slow rises and 0.1 degC sensors, which kept the fit from publishing.
ZONE_FIT_LEAD_DELAYS_MIN = (0.0, 3.0, 6.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0)
# Candidate time scales of the source: its emitter's delay and lag stretched
# together by these factors (separately they cannot be told apart in 30-min
# windows); a source without delay and lag gets lags of SOURCE_FAST_TAU_MIN.
SOURCE_SCALE_MULTIPLIERS = (0.4, 0.55, 0.7, 0.85, 1.0, 1.2, 1.4, 1.7, 2.0, 2.5)
SOURCE_FAST_TAU_MIN = (0.0, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0)
# SmartPI waits for the fit to learn while the source heats; if the fit is not
# published this long after learning started, SmartPI learns on its own.
ZONE_FIT_TIMEOUT_S = 48 * 3600.0
# A gap between calculations this long breaks a window of the fit and does not
# count on the learning clock (Home Assistant stopped, a restart).
ZONE_FIT_MAX_GAP_S = 15 * 60.0
# While SmartPI holds the zone fit's a and b, its count of learned a samples is
# at least this: the near band then follows the model, as after SmartPI's own learning.
ZONE_FIT_MODEL_SAMPLES_A = 10
# Predictive brake: the zone is predicted over the source's response time (at
# least SOURCE_PREDICTION_MIN_S, in steps of SOURCE_PREDICTION_STEP_S) with the
# fitted model; the source's command is capped at the largest that keeps the
# predicted zone at its setpoint (SPLIT_PREDICTED_SHARE of the predicted error
# corrected), found in SOURCE_BRAKE_STEPS halvings. A source that moves the
# predicted zone less than SOURCE_SENSITIVITY_MIN_C is not braked; the same
# margin is the brake's tolerance.
SOURCE_PREDICTION_STEP_S = 60.0
SOURCE_PREDICTION_MIN_S = 30 * 60.0
SOURCE_SENSITIVITY_MIN_C = 0.01
SPLIT_PREDICTED_SHARE = 1.0
SOURCE_BRAKE_STEPS = 8
# A zone colder than its setpoint by more than this recovers: the lead's
# feed-forward leaves the source's heat out, until the zone is predicted to reach
# the band within the lead's delay plus one cycle.
SOURCE_BOOST_ERROR_C = 0.2
# The allocation loop follows the lead's steady demand (feed-forward plus
# integral), not its answer to the current error: a setpoint change does not
# move the source. When the lead is stuck at a limit, the loop gets the end of
# its demand range instead: at 0 % with the zone warmer than the setpoint by
# SPLIT_LIMIT_ERROR_C (too much heat, even while SmartPI cuts its output), or at
# 100 % with the zone that much colder for longer than the source takes to
# settle (the source as a reserve).
SPLIT_LIMIT_ERROR_C = 0.2
SPLIT_LEAD_STOPPED = 0.01
SPLIT_LEAD_FULL = 0.99
# The source does not move up while the zone is warmer than its setpoint by
# more than this, nor down while it is that much colder: inside SmartPI's
# deadband the lead's demand stops reacting, so the zone's error decides.
SPLIT_HOLD_ERROR_C = 0.05
# Besides the allocation, the source answers the zone's error in proportion
# (command per degC, divided by its power relative to the lead's): two devices
# then correct a lasting error together, as two independent controllers would,
# while the rule still sets the split once the error is gone. Chosen on a heated
# floor and on a second radiator with a 0.1 degC sensor (2 holds both best).
SPLIT_ERROR_GAIN = 2.0
