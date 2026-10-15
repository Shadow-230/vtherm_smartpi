"""Edge cases of the allocation loop.

They check that a loop which has not seen a lead demand yet reports no error,
and that its error is measured once the first demand arrives.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.source_model import SourceResponse
from custom_components.vtherm_smartpi.smartpi.split import (
    RULE_LEAD_BAND,
    AllocationLoop,
    AllocationTiming,
    SplitRule,
)


def test_a_loop_without_a_lead_demand_yet_has_no_error() -> None:
    loop = AllocationLoop(SplitRule(RULE_LEAD_BAND, band=0.2), AllocationTiming.for_source(SourceResponse(300.0, 1200.0)), 0.7)

    assert loop.error() == 0.0

    loop.update(0.0, 0.9)
    assert loop.error() == pytest.approx(0.9 - 0.2)
    assert loop.command == 0.7
