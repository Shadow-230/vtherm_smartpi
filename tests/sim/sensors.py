"""Sensors and the thermostat-side signal processing seen by SmartPI.

``QuantizedSensor`` publishes like a typical 0.1 degC sensor: only when its rounded
value changes. ``VThermSlope`` reproduces the temperature slope that Versatile
Thermostat hands to the algorithm: an exponential moving average of the
measurements (half-life 300 s, alpha capped at 0.5, two decimals) and a slope in
degC/h smoothed 0.2/0.8, with a placeholder point when no measurement arrived for
30 minutes.
"""

from __future__ import annotations

import math

EMA_HALFLIFE_S = 300.0
EMA_MAX_ALPHA = 0.5
SLOPE_PLACEHOLDER_AFTER_S = 30 * 60.0
MAX_SLOPE_C_PER_H = 120.0


class QuantizedSensor:
    """Sensor that rounds to its resolution and publishes on change only."""

    def __init__(self, resolution_c: float = 0.1) -> None:
        self.resolution_c = resolution_c
        self.value: float | None = None

    def sample(self, true_c: float) -> float | None:
        """Return the new published value, or None when nothing is published."""
        rounded = round(round(true_c / self.resolution_c) * self.resolution_c, 6)
        if rounded == self.value:
            return None
        self.value = rounded
        return rounded


class VThermSlope:
    """Temperature EMA and slope as computed by Versatile Thermostat."""

    def __init__(self) -> None:
        self.ema_c: float | None = None
        self._ema_raw_c: float | None = None
        self._ema_t_s: float | None = None
        self.slope_c_per_h: float | None = None
        self._slope_t_s: float | None = None
        self._slope_c: float | None = None

    def measure(self, value_c: float, t_s: float) -> None:
        """Feed a published measurement."""
        self._update_ema(value_c, t_s)
        self._add_point(self.ema_c, t_s, store_time=True)

    def check_age(self, t_s: float) -> None:
        """Add the placeholder point VTherm adds on each control cycle when data is old."""
        if self.ema_c is None:
            return
        if self._slope_t_s is None:
            self._add_point(self.ema_c, t_s, store_time=True)
        elif t_s - self._slope_t_s >= SLOPE_PLACEHOLDER_AFTER_S:
            self._add_point(self.ema_c, t_s, store_time=False)

    def _update_ema(self, value_c: float, t_s: float) -> None:
        """Update the average; like VTherm, keep it unrounded and publish it rounded."""
        if self._ema_raw_c is None or self._ema_t_s is None:
            self._ema_raw_c, self._ema_t_s = value_c, t_s
            self.ema_c = value_c
            return
        decay_s = t_s - self._ema_t_s
        if decay_s > 0.0:
            alpha = min(1.0 - math.exp(math.log(0.5) * decay_s / EMA_HALFLIFE_S), EMA_MAX_ALPHA)
            self._ema_raw_c = alpha * value_c + (1.0 - alpha) * self._ema_raw_c
            self._ema_t_s = t_s
        self.ema_c = round(self._ema_raw_c, 2)

    def _add_point(self, value_c: float, t_s: float, store_time: bool) -> None:
        if self._slope_t_s is None or self._slope_c is None:
            self._slope_t_s, self._slope_c = t_s, value_c
            return
        delta_s = t_s - self._slope_t_s
        if delta_s <= 0.0:
            return
        new_slope = (value_c - self._slope_c) / (delta_s / 3600.0)
        if abs(new_slope) > MAX_SLOPE_C_PER_H:
            return
        if self.slope_c_per_h is None:
            self.slope_c_per_h = round(new_slope, 2)
        else:
            self.slope_c_per_h = round(0.2 * self.slope_c_per_h + 0.8 * new_slope, 2)
        if store_time:
            self._slope_t_s = t_s
        self._slope_c = value_c
