"""Tests for the split rules, the allocation loop and the recommendation by emitter type."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.source_model import (
    EMITTER_ELECTRIC_FAST,
    EMITTER_FLOOR_WATER,
    EMITTER_RADIATOR,
    SourceHeat,
    SourceResponse,
)
from custom_components.vtherm_smartpi.smartpi.split import (
    REASON_SIMILAR_SPEED,
    REASON_SLOW_SOURCE,
    RULE_LEAD_BAND,
    RULE_SOURCE_FIRST,
    RULE_TOGETHER,
    AllocationLoop,
    AllocationTiming,
    SplitRule,
    recommend,
)

A = 0.03


@pytest.mark.parametrize(
    ("rule", "source", "lead"),
    [
        (SplitRule(RULE_TOGETHER, ratio=2.0), 0.4, 0.2),
        (SplitRule(RULE_TOGETHER, ratio=2.0), 1.0, 0.5),
        (SplitRule(RULE_TOGETHER, ratio=0.5), 0.25, 0.5),
        (SplitRule(RULE_TOGETHER, ratio=0.5), 0.8, 1.0),
        (SplitRule(RULE_SOURCE_FIRST, alone=0.5), 0.3, 0.0),
        (SplitRule(RULE_SOURCE_FIRST, alone=0.5), 0.7, 0.4),
        (SplitRule(RULE_SOURCE_FIRST, alone=0.5), 1.0, 1.0),
        (SplitRule(RULE_SOURCE_FIRST, alone=1.0), 0.9, 0.0),
        (SplitRule(RULE_SOURCE_FIRST, alone=0.0), 0.6, 0.6),
        (SplitRule(RULE_LEAD_BAND, band=0.2), 0.7, 0.2),
    ],
)
def test_rule_curves(rule, source, lead) -> None:
    """Examples of the approved definitions (lead's target average for a source command)."""
    assert rule.lead_target(source) == pytest.approx(lead)


def test_invalid_rules_are_rejected() -> None:
    with pytest.raises(ValueError):
        SplitRule("first_come")
    with pytest.raises(ValueError):
        SplitRule(RULE_TOGETHER, ratio=0.0)


def _loop(rule: SplitRule, command: float = 0.5) -> AllocationLoop:
    timing = AllocationTiming(time_constant_s=3600.0, max_rate_per_s=1.0 / 600.0, average_s=60.0)
    return AllocationLoop(rule, timing, command)


def test_a_lead_working_more_than_the_curve_moves_the_source_up() -> None:
    loop = _loop(SplitRule(RULE_LEAD_BAND, band=0.2))
    loop.update(0.0, 0.6)

    assert loop.update(600.0, 0.6) > 0.5
    loop.reset(0.5)
    loop.update(0.0, 0.05)
    assert loop.update(600.0, 0.05) < 0.5


def test_the_source_moves_no_faster_than_its_limit_and_stays_in_range() -> None:
    timing = AllocationTiming(time_constant_s=60.0, max_rate_per_s=1.0 / 600.0, average_s=60.0)
    loop = AllocationLoop(SplitRule(RULE_LEAD_BAND, band=0.0), timing, command=0.9)
    loop.update(0.0, 1.0)

    assert loop.update(30.0, 1.0) == pytest.approx(0.9 + 30.0 / 600.0)
    assert loop.update(3600.0, 1.0) == 1.0
    loop.reset(0.05)
    loop.update(0.0, -0.5)
    assert loop.update(36000.0, -0.5) == 0.0


def test_a_lead_beyond_its_limits_moves_the_source_at_both_ends() -> None:
    """Above 100 % the source rises as a reserve; below 0 (too much heat) it comes down."""
    reserve = _loop(SplitRule(RULE_TOGETHER, ratio=0.5), command=0.5)
    reserve.update(0.0, 1.3)
    assert reserve.update(600.0, 1.3) > 0.5

    excess = _loop(SplitRule(RULE_SOURCE_FIRST, alone=0.5), command=0.4)
    excess.update(0.0, -0.3)
    assert excess.update(600.0, -0.3) < 0.4


def _closed_loop(rule: SplitRule, response: SourceResponse, a_f: float, demand: float, days: float = 8.0):
    """An ideal lead that covers what the source leaves; return the source and lead demand histories."""
    heat = SourceHeat(response)
    loop = AllocationLoop(rule, AllocationTiming.for_source(response, gain_ratio=a_f / A))
    sources, leads = [], []
    for minute in range(int(days * 24 * 60)):
        now_s = minute * 60.0
        lead = (demand - a_f * heat.value(now_s)) / A
        source = loop.update(now_s, lead)
        heat.record(now_s, source)
        sources.append(source)
        leads.append(lead)
    return sources, leads


@pytest.mark.parametrize(
    ("emitter", "a_f"),
    [
        (EMITTER_FLOOR_WATER, A),
        (EMITTER_FLOOR_WATER, 3 * A),
        (EMITTER_RADIATOR, A),
        (EMITTER_ELECTRIC_FAST, 2 * A),
        (EMITTER_ELECTRIC_FAST, 0.5 * A),
    ],
    ids=["floor", "strong_floor", "radiator", "strong_fast", "weak_fast"],
)
@pytest.mark.parametrize(
    ("rule", "demand"),
    [
        (SplitRule(RULE_LEAD_BAND, band=0.2), 0.024),
        (SplitRule(RULE_TOGETHER, ratio=1.0), 0.024),
        (SplitRule(RULE_SOURCE_FIRST, alone=0.5), 0.03),
    ],
    ids=["lead_band", "together", "source_first"],
)
def test_the_loop_settles_on_the_rule_without_pulling(emitter, a_f, rule, demand) -> None:
    """The source settles where the rule puts the lead, without swinging past it.

    A source too weak for the rule ends at 100 % and leaves the lead above its target.
    """
    sources, leads = _closed_loop(rule, SourceResponse.for_emitter(emitter), a_f, demand)
    final = sources[-1]
    lead = min(max(leads[-1], 0.0), 1.0)

    if final >= 1.0:
        assert lead >= rule.lead_target(final) - 0.02
    else:
        assert lead == pytest.approx(rule.lead_target(final), abs=0.02)
    assert A * lead + a_f * final == pytest.approx(demand, rel=0.02)
    assert max(sources) - final < 0.05
    assert max(sources[-1440:]) - min(sources[-1440:]) < 0.01


def test_recommendation_by_emitter() -> None:
    radiator = SourceResponse.for_emitter(EMITTER_RADIATOR)
    floor = SourceResponse.for_emitter(EMITTER_FLOOR_WATER)
    fast = SourceResponse.for_emitter(EMITTER_ELECTRIC_FAST)

    slow_source = recommend(radiator, floor)
    assert (slow_source.rule.rule, slow_source.reason, slow_source.roles_reversed) == (
        RULE_LEAD_BAND,
        REASON_SLOW_SOURCE,
        False,
    )
    similar = recommend(radiator, radiator)
    assert (similar.rule.rule, similar.reason, similar.roles_reversed) == (RULE_TOGETHER, REASON_SIMILAR_SPEED, False)
    assert recommend(floor, radiator).roles_reversed
    assert recommend(fast, radiator).rule.rule == RULE_TOGETHER
