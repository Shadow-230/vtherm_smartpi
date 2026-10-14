"""Whether the boiler heats a calling zone: the learning mode it gives."""

import pytest

from custom_components.vtherm_smartpi.smartpi.boiler_call import (
    MODE_COOLING,
    MODE_HELD,
    MODE_WARMING,
    SOURCE_SMART_BOILER,
    SOURCE_VTHERM,
    BoilerCall,
    BoilerReading,
    settle_s,
)
from custom_components.vtherm_smartpi.smartpi.const import BOILER_CALL_GRACE_S

SETTLE = settle_s("radiator")
GRACE = BOILER_CALL_GRACE_S
ON = BoilerReading(True, None, SOURCE_VTHERM)
OFF = BoilerReading(False, "not_called", SOURCE_VTHERM)


def _call(reading=None, heating_type="radiator") -> BoilerCall:
    call = BoilerCall()
    call.configure(heating_type)
    call.set_reading(reading)
    return call


def test_the_settling_time_is_the_emitter_s_delay_and_three_lags():
    """Less than 5 % of the change left: the stored heat after the pump stops, the warm-up after a start."""
    assert settle_s("radiator") == pytest.approx((5 + 3 * 20) * 60.0)
    assert settle_s("floor_water") == pytest.approx((30 + 3 * 180) * 60.0)
    assert settle_s(None) == settle_s("radiator")


def test_without_a_reading_learning_is_as_usual():
    call = _call()
    assert call.update(calling=True, heating=True, now_s=0.0) is None
    assert call.diagnostics() is None


def test_a_boiler_answering_the_zone_s_call_within_the_grace_changes_nothing():
    """An on-demand boiler, or VT's activation delay: the zone's own step, as usual."""
    call = _call(OFF)
    assert call.update(calling=True, heating=True, now_s=0.0) is None
    assert call.update(calling=True, heating=True, now_s=GRACE - 1.0) is None
    call.set_reading(ON)
    assert call.update(calling=True, heating=True, now_s=GRACE) is None


def test_a_call_left_unanswered_holds_the_zone_until_the_boiler_starts_and_the_emitter_warms():
    call = _call(OFF)
    call.update(calling=True, heating=True, now_s=0.0)
    # The emitter still gives the heat it holds: learning waits.
    assert call.update(calling=True, heating=True, now_s=GRACE) == MODE_COOLING
    assert call.update(calling=True, heating=True, now_s=SETTLE - 1.0) == MODE_COOLING
    assert call.update(calling=True, heating=True, now_s=SETTLE) == MODE_HELD

    call.set_reading(ON)
    start = 2 * SETTLE
    assert call.update(calling=True, heating=True, now_s=start) == MODE_WARMING
    assert call.update(calling=True, heating=True, now_s=start + SETTLE - 1.0) == MODE_WARMING
    assert call.update(calling=True, heating=True, now_s=start + SETTLE) is None


def test_stopping_again_while_the_emitter_warms_cools_it_at_once():
    """No new grace after a hold: a short call does not let the stop count as heat."""
    call = _call(OFF)
    call.update(calling=True, heating=True, now_s=0.0)
    call.update(calling=True, heating=True, now_s=SETTLE)
    call.set_reading(ON)
    assert call.update(calling=True, heating=True, now_s=SETTLE + 60.0) == MODE_WARMING
    call.set_reading(OFF)
    assert call.update(calling=True, heating=True, now_s=SETTLE + 120.0) == MODE_COOLING
    assert call.update(calling=True, heating=True, now_s=2 * SETTLE + 120.0) == MODE_HELD


def test_a_switched_device_off_within_its_cycle_is_no_hold():
    """VT counts a switched device for the boiler only while it is on: a cycle's off part
    longer than the grace is no hold when the boiler answers each on part."""
    cycle = 30 * 60.0
    call = _call(OFF)
    now = 0.0
    for _ in range(4):
        call.set_reading(ON)
        assert call.update(calling=True, heating=True, now_s=now, grace_s=GRACE + cycle) is None
        call.set_reading(OFF)
        assert call.update(calling=True, heating=True, now_s=now + 9 * 60.0, grace_s=GRACE + cycle) is None
        now += cycle
    # Never answered: held after the cycle and the grace, then the emitter has cooled.
    call.set_reading(OFF)
    start = now
    assert call.update(calling=True, heating=True, now_s=start, grace_s=GRACE + cycle) is None
    assert call.update(calling=True, heating=True, now_s=start + GRACE + cycle, grace_s=GRACE + cycle) == MODE_HELD


def test_a_reading_lost_during_a_hold_warms_the_emitter_and_stays_in_the_diagnostics():
    call = _call(OFF)
    call.update(calling=True, heating=True, now_s=0.0)
    call.update(calling=True, heating=True, now_s=SETTLE)
    call.set_reading(None)
    assert call.update(calling=True, heating=True, now_s=SETTLE + 60.0) == MODE_WARMING
    assert call.diagnostics() == {"source": None, "called": None, "reason": None, "learning": MODE_WARMING}
    assert call.update(calling=True, heating=True, now_s=2 * SETTLE + 60.0) is None
    assert call.diagnostics() is None


def test_a_short_stop_of_a_working_boiler_changes_nothing():
    """A boiler cycling under the zone's call keeps its water warm."""
    call = _call(ON)
    call.update(calling=True, heating=True, now_s=0.0)
    call.set_reading(OFF)
    assert call.update(calling=True, heating=True, now_s=60.0) is None
    call.set_reading(ON)
    assert call.update(calling=True, heating=True, now_s=60.0 + GRACE / 2) is None


def test_the_zone_s_own_command_off_ends_any_hold():
    call = _call(OFF)
    call.update(calling=True, heating=True, now_s=0.0)
    call.update(calling=True, heating=True, now_s=SETTLE)
    assert call.update(calling=False, heating=True, now_s=SETTLE + 60.0) is None


def test_a_call_not_known_counts_as_given():
    call = _call(BoilerReading(None, "not_controlling", SOURCE_SMART_BOILER))
    assert call.update(calling=True, heating=True, now_s=0.0) is None
    assert call.update(calling=True, heating=True, now_s=2 * SETTLE) is None


def test_smart_boiler_below_its_threshold_holds_a_calling_zone_only():
    """``below_threshold`` with the command above 0 gives no heat; ``no_demand`` with the
    command at 0 is the zone cooling as usual."""
    below = _call(BoilerReading(False, "below_threshold", SOURCE_SMART_BOILER))
    below.update(calling=True, heating=True, now_s=0.0)
    assert below.update(calling=True, heating=True, now_s=SETTLE) == MODE_HELD
    assert _call(BoilerReading(False, "no_demand", SOURCE_SMART_BOILER)).update(
        calling=False, heating=True, now_s=SETTLE
    ) is None


def test_outside_heating_nothing_is_held():
    call = _call(OFF)
    call.update(calling=True, heating=False, now_s=0.0)
    assert call.update(calling=True, heating=False, now_s=SETTLE) is None


def test_the_diagnostics_show_the_source_the_call_the_learning_mode_and_the_demand():
    demand = {"demand_power_kw": 0.4, "power_threshold_kw": 0.6}
    call = _call(BoilerReading(False, "below_threshold", SOURCE_SMART_BOILER, demand))
    call.update(calling=True, heating=True, now_s=0.0)
    call.update(calling=True, heating=True, now_s=SETTLE)
    assert call.diagnostics() == {
        "source": SOURCE_SMART_BOILER,
        "called": False,
        "reason": "below_threshold",
        "learning": MODE_HELD,
        "demand": demand,
    }
