"""Edge cases of resuming the second source's candidate responses.

They check that a saved response with a missing or unreadable delay or lag
keeps the candidate in use, while the saved heat is still resumed.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.source_candidates import SourceCandidates
from custom_components.vtherm_smartpi.smartpi.source_model import SourceResponse

FLOOR = SourceResponse(1800.0, 10800.0)


@pytest.mark.parametrize(
    "saved",
    [{"tau_s": 10800.0}, {"lag_s": None, "tau_s": 10800.0}, {"lag_s": "slow", "tau_s": 10800.0}],
    ids=["missing_delay", "empty_delay", "unreadable_delay"],
)
def test_an_unreadable_saved_response_keeps_the_candidate_in_use(saved) -> None:
    candidates = SourceCandidates(FLOOR)
    candidates.select(7)

    candidates.load_state(saved, {"x_f": 0.4}, 0.0)

    assert candidates.current_index == 7
    assert candidates.values(0.0) == pytest.approx((0.4,) * len(candidates.candidates))
