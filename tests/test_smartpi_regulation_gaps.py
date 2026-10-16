"""Gaps in SmartPI's own regulation: the first own tick after one integrates nothing.

The author's change resets the time tracking after a missing sensor and a guard
cut, and resets the PI state after a restart into the other HVAC mode. The fork
adds a gap of its own: a thermostat working as another thermostat's additional
source (the source role) applies the main source's command and regulates
nothing, so the period in that role must not be integrated when it leaves it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_COOL, VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import AB_MIN_SAMPLES_A, AB_MIN_SAMPLES_B


def _regulating_algo() -> SmartPI:
    """A SmartPI out of its learning phase, with the PI integrating."""
    algo = SmartPI(
        hass=MagicMock(),
        cycle_min=10,
        minimal_activation_delay=0,
        minimal_deactivation_delay=0,
        name="Gaps",
        max_on_percent=1.0,
        deadband_c=0.0,
    )
    algo.Ki = 0.05
    algo.Kp = 0.5
    algo.est.a = 0.02
    algo.est.b = 0.002
    algo.est.a_meas_hist.extend([0.02] * AB_MIN_SAMPLES_A)
    algo.est.b_meas_hist.extend([0.002] * AB_MIN_SAMPLES_B)
    return algo


def test_leaving_the_source_role_does_not_integrate_the_role_period() -> None:
    """Two hours as an additional source leave the integral where it was on the first own tick."""
    algo = _regulating_algo()
    clock = [10_000.0]

    def tick() -> None:
        algo.calculate(target_temp=20.0, current_temp=19.0, ext_current_temp=5.0, hvac_mode=VThermHvacMode_HEAT)

    with patch("custom_components.vtherm_smartpi.algo.time.monotonic", side_effect=lambda: clock[0]):
        tick()
        clock[0] += 60.0
        tick()
        assert algo.ctl.last_i_mode == "I:RUN"

        algo.enter_source_role("climate.main")
        algo.set_source_command(0.6)
        for _ in range(24):
            clock[0] += 300.0
            tick()
        assert algo.on_percent == pytest.approx(0.6)
        algo.leave_source_role()

        before = algo.integral
        clock[0] += 60.0
        tick()

    assert algo.integral == pytest.approx(before)


def test_restart_into_the_other_mode_resets_pi_only_on_the_first_tick() -> None:
    """The persisted model mode resets the PI once; later ticks in the new mode keep their integral."""
    source = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Saved"
    )
    source.est.ensure_hvac_mode(VThermHvacMode_HEAT)
    source.integral = 5.0
    restored = SmartPI(
        hass=MagicMock(),
        cycle_min=10,
        minimal_activation_delay=0,
        minimal_deactivation_delay=0,
        name="Restored",
        saved_state=source.save_state(),
    )

    restored.calculate(target_temp=None, current_temp=None, hvac_mode=VThermHvacMode_COOL)
    assert restored.integral == 0.0

    restored.integral = 2.0
    restored.calculate(target_temp=None, current_temp=None, hvac_mode=VThermHvacMode_COOL)
    assert restored.integral == 2.0
