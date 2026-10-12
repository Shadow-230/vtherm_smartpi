"""Outdoor conditions for SmartPI simulations.

Outdoor temperature profiles: constant, daily cycle and step. Forecasts and
further weather factors are meant to plug into the same ``Weather`` interface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Weather:
    """Outdoor temperature as a function of elapsed simulation time."""

    base_c: float = 5.0
    daily_amplitude_c: float = 0.0
    coldest_hour: float = 5.0
    step_at_h: float | None = None
    step_c: float = 0.0

    def outdoor_c(self, t_s: float) -> float:
        hours = t_s / 3600.0
        value = self.base_c
        if self.daily_amplitude_c:
            phase = 2.0 * math.pi * (hours - self.coldest_hour) / 24.0
            value -= self.daily_amplitude_c * math.cos(phase)
        if self.step_at_h is not None and hours >= self.step_at_h:
            value += self.step_c
        return value
