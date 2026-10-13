"""Tests for the link between a lead thermostat and its second source, and the source settings."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.dependencies import KIND_SOURCE, DependencyItem
from custom_components.vtherm_smartpi.smartpi.source_link import SourceCycle, SourceLink
from custom_components.vtherm_smartpi.smartpi.source_model import (
    EMITTER_FLOOR_WATER,
    EMITTER_RADIATOR,
    SourceResponse,
)
from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings
from custom_components.vtherm_smartpi.smartpi.split import RULE_LEAD_BAND, RULE_SOURCE_FIRST


def test_a_source_follows_one_lead_only() -> None:
    link = SourceLink()

    assert link.attach("lead", "floor")
    assert not link.attach("other", "floor")
    assert link.lead_of("floor") == "lead"
    assert link.source_of("lead") == "floor"

    assert link.attach("lead", "radiator")
    assert link.lead_of("floor") is None
    link.detach_lead("lead")
    assert link.lead_of("radiator") is None


def test_the_source_applies_only_a_fresh_command_for_its_mode() -> None:
    link = SourceLink()
    link.attach("lead", "floor")
    link.publish_command("lead", 0.6, now_s=100.0, hvac_mode="heat")

    assert link.command_for("floor", 200.0, max_age_s=900.0, hvac_mode="heat") == 0.6
    assert link.command_for("floor", 1100.0, max_age_s=900.0, hvac_mode="heat") == 0.0
    assert link.command_for("floor", 200.0, max_age_s=900.0, hvac_mode="cool") == 0.0
    assert link.command_for("unknown", 200.0, max_age_s=900.0, hvac_mode="heat") == 0.0

    link.detach_lead("lead")
    link.attach("other", "floor")
    assert link.command_for("floor", 200.0, max_age_s=900.0, hvac_mode="heat") == 0.0


def test_cycles_and_activity_are_kept_only_for_attached_sources() -> None:
    link = SourceLink()
    cycle = SourceCycle(start_s=10.0, on_s=300.0, duration_s=600.0, power=0.5, switched=True)
    link.report_cycle("floor", cycle)
    link.set_active("floor", True, 0.0)
    assert link.cycles_since("floor", 0.0) == []
    assert not link.is_active("floor", 0.0, 900.0)

    link.attach("lead", "floor")
    link.report_cycle("floor", cycle)
    link.report_cycle("floor", SourceCycle(610.0, 0.0, 600.0, 0.0, True))
    link.set_active("floor", True, 100.0)

    assert [c.start_s for c in link.cycles_since("floor", 10.0)] == [610.0]
    assert link.is_active("floor", 900.0, 900.0)


def test_a_source_that_stops_reporting_is_no_longer_active() -> None:
    """A source gone without a word (removed, crashed) stops counting after the maximum age."""
    link = SourceLink()
    link.attach("lead", "floor")
    link.set_active("floor", True, 100.0)

    assert link.is_active("floor", 1000.0, 900.0)
    assert not link.is_active("floor", 1001.0, 900.0)
    link.set_active("floor", True, 1001.0)
    link.set_active("floor", False, 1002.0)
    assert not link.is_active("floor", 1003.0, 900.0)


def test_a_lead_that_publishes_nothing_for_a_while_is_silent() -> None:
    """Counted from the attachment until the first command; a lead switched off still speaks."""
    link = SourceLink()
    link.attach("lead", "floor", now_s=100.0)

    assert not link.lead_silent("floor", 1000.0, max_age_s=900.0)
    assert link.lead_silent("floor", 1001.0, max_age_s=900.0)
    link.publish_command("lead", 0.0, now_s=1100.0, hvac_mode="off")
    assert not link.lead_silent("floor", 2000.0, max_age_s=900.0)
    assert link.command_for("floor", 2000.0, max_age_s=900.0, hvac_mode="heat") == 0.0
    assert link.lead_silent("floor", 2001.0, max_age_s=900.0)
    assert not link.lead_silent("unknown", 5000.0, max_age_s=900.0)

    # Attaching again to the same lead keeps the clock; a new lead starts it over.
    link.attach("lead", "floor", now_s=3000.0)
    assert link.lead_silent("floor", 3000.0, max_age_s=900.0)
    link.detach_lead("lead")
    link.attach("other", "floor", now_s=3000.0)
    assert not link.lead_silent("floor", 3000.0, max_age_s=900.0)


def test_the_source_s_power_cap_is_kept_while_it_is_attached() -> None:
    link = SourceLink()
    link.set_power_cap("floor", 0.4)
    assert link.power_cap("floor") == 1.0

    link.attach("lead", "floor")
    link.set_power_cap("floor", 0.4)
    assert link.power_cap("floor") == 0.4
    link.set_power_cap("floor", 1.7)
    assert link.power_cap("floor") == 1.0
    link.detach_source("floor")
    assert link.power_cap("floor") == 1.0


def _item(**extra) -> DependencyItem:
    return DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg-floor", extra=extra)


def test_settings_turn_percent_and_emitters_into_the_model() -> None:
    settings = SourceSettings.from_item(
        _item(
            split_rule=RULE_SOURCE_FIRST,
            source_alone_percent=60,
            lead_emitter=EMITTER_RADIATOR,
            source_emitter=EMITTER_FLOOR_WATER,
        )
    )

    assert settings.rule.rule == RULE_SOURCE_FIRST
    assert settings.rule.alone == pytest.approx(0.6)
    assert settings.source == SourceResponse.for_emitter(EMITTER_FLOOR_WATER)
    assert settings.lead == SourceResponse.for_emitter(EMITTER_RADIATOR)


def test_settings_that_do_not_validate_fall_back_to_defaults() -> None:
    settings = SourceSettings.from_item(_item(split_rule="half", split_ratio=-5, source_emitter="custom"))

    assert settings.rule.rule == RULE_LEAD_BAND
    assert settings.rule.ratio == 1.0
    assert settings.source_emitter == EMITTER_RADIATOR
