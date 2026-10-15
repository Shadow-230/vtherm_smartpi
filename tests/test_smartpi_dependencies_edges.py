"""Edge cases of reading and validating the stored dependency options.

They check that entries of the list which are not mappings are skipped, that a
single door sensor stored as text is read as a one-sensor list, and that a
lead band outside 0..100 % is reported even when the other split parameters
are valid.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.dependencies import (
    ERROR_INVALID_SPLIT_PARAMETER,
    KIND_NEIGHBOUR,
    KIND_SOURCE,
    MODE_DOOR,
    parse_dependencies,
    validate_dependencies,
)

NEIGHBOUR = {"id": "n1", "kind": KIND_NEIGHBOUR, "entity": "reg-kitchen", "mode": "always"}


def test_entries_that_are_not_mappings_are_skipped() -> None:
    items = parse_dependencies(["neighbour", 3, None, ["id", "n2"], NEIGHBOUR])

    assert [item.item_id for item in items] == ["n1"]


def test_a_single_door_sensor_stored_as_text_is_read_as_one_sensor() -> None:
    (item,) = parse_dependencies([dict(NEIGHBOUR, mode=MODE_DOOR, door_sensors="reg-door")])

    assert item.door_sensors == ("reg-door",)
    assert validate_dependencies([item], "reg-own") == []


@pytest.mark.parametrize("band", [150, -5, "20", True, float("nan")])
def test_a_lead_band_outside_its_range_is_invalid(band) -> None:
    source = {
        "id": "s1",
        "kind": KIND_SOURCE,
        "entity": "reg-floor",
        "split_rule": "lead_band",
        "split_ratio": 100,
        "source_alone_percent": 50,
        "lead_band_percent": band,
        "lead_emitter": "radiator",
        "source_emitter": "floor_water",
    }

    issues = validate_dependencies(parse_dependencies([source]), "reg-own")

    assert [(issue.code, issue.item_id) for issue in issues] == [(ERROR_INVALID_SPLIT_PARAMETER, "s1")]
