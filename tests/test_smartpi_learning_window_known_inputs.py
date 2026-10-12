"""The learning window judges the response direction after removing known model inputs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import GovernanceDecision, FreezeReason
from custom_components.vtherm_smartpi.smartpi.learning_window import LearningWindowManager


def _run_off_window(known_rate: float) -> str:
    """Feed an OFF window whose temperature rises only because of a known input."""
    window = LearningWindowManager("test")
    estimator = MagicMock(learn_skip_count=0, learn_last_reason="")
    dt_est = MagicMock(deadtime_heat_reliable=False, deadtime_cool_reliable=False, tin_history=[])
    governance = MagicMock()
    governance.decide_update.return_value = (GovernanceDecision.ADAPT_ON, FreezeReason.NONE)

    for minute in range(6):
        window.update(
            dt_min=1.0,
            current_temp=20.0 + 0.02 * minute,
            ext_temp=5.0,
            u_active=0.0,
            setpoint_changed=False,
            estimator=estimator,
            dt_est=dt_est,
            governance=governance,
            learning_resume_ts=None,
            now=1000.0 + 60.0 * minute,
            in_deadband=False,
            in_near_band=False,
            t_heat_episode_start=None,
            t_cool_episode_start=None,
            hvac_mode=VThermHvacMode_HEAT,
            known_rate=known_rate,
        )
    return estimator.learn_last_reason


def test_raw_rise_without_known_inputs_slides_the_window() -> None:
    assert _run_off_window(known_rate=0.0).startswith("skip: B flywheel")


@pytest.mark.parametrize("known_rate", [0.05, 0.1])
def test_known_inputs_explain_the_rise_and_keep_the_window(known_rate) -> None:
    """The zone cools once the known input is removed, so the passive window stays open."""
    assert not _run_off_window(known_rate).startswith("skip: B flywheel")
