"""Unit tests of the water input (smartpi/water_input.py)."""

import pytest

from custom_components.vtherm_smartpi.smartpi.const import (
    HEATING_ELECTRIC_FAST,
    HEATING_FLOOR_ELECTRIC,
    HEATING_FLOOR_WATER,
    HEATING_RADIATOR,
    WATER_AVERAGE_S,
    WATER_FACTOR_MAX,
)
from custom_components.vtherm_smartpi.smartpi.water_input import (
    SOURCE_AVERAGE,
    SOURCE_DHW,
    SOURCE_LASTING,
    SOURCE_LIVE,
    SOURCE_NONE,
    SOURCE_UNUSED,
    WaterInput,
    WaterReading,
    emitter_factor,
)


def live(supply_c, heat_reason=None):
    return WaterReading(supply_c=supply_c, heat_available=heat_reason is None, heat_reason=heat_reason)


def test_emitter_factor_follows_the_emitter_law():
    # Reference water gives 1; half the drive gives 0.5 ** n.
    assert emitter_factor(HEATING_RADIATOR, 60.0, 20.0) == pytest.approx(1.0)
    assert emitter_factor(HEATING_RADIATOR, 40.0, 20.0) == pytest.approx(0.5**1.3)
    assert emitter_factor(HEATING_FLOOR_WATER, 45.0, 20.0) == pytest.approx(1.0)
    assert emitter_factor(HEATING_FLOOR_WATER, 32.5, 20.0) == pytest.approx(0.5**1.1)
    # Water colder than the room brings nothing; very hot water is capped.
    assert emitter_factor(HEATING_RADIATOR, 18.0, 20.0) == 0.0
    assert emitter_factor(HEATING_RADIATOR, 300.0, 20.0) == WATER_FACTOR_MAX


@pytest.mark.parametrize("heating_type", [HEATING_FLOOR_ELECTRIC, HEATING_ELECTRIC_FAST, None])
def test_electric_and_unset_types_ignore_the_water(heating_type):
    water = WaterInput(heating_type)
    state = water.update(live(40.0), 20.0, 0.0)
    assert not water.uses_water
    assert state.factor == 1.0
    assert state.source == SOURCE_UNUSED
    assert state.published_factor is None


def test_no_data_since_start_gives_one():
    water = WaterInput(HEATING_RADIATOR)
    state = water.update(None, 20.0, 0.0)
    assert state.factor == 1.0
    assert state.source == SOURCE_NONE
    assert state.published_factor is None
    assert not state.changed_for_good


def test_live_supply_is_averaged_over_fifteen_minutes():
    water = WaterInput(HEATING_RADIATOR)
    assert water.update(live(60.0), 20.0, 0.0).factor == pytest.approx(1.0)
    # A step to 40 degC: after one average time the supply has moved ~63 % of the way.
    state = None
    for k in range(1, 16):
        state = water.update(live(40.0), 20.0, k * WATER_AVERAGE_S / 15)
    assert state.source == SOURCE_LIVE
    assert state.supply_avg_c == pytest.approx(60.0 - 20.0 * (1.0 - 2.718281828 ** -1), abs=0.3)
    assert 0.5**1.3 < state.factor < 1.0
    assert state.published_factor == pytest.approx(state.factor)


def test_burner_cycling_is_smoothed():
    water = WaterInput(HEATING_RADIATOR)
    factors = []
    for k in range(240):
        supply = 50.0 + (5.0 if (k // 9) % 2 else -5.0)  # +-5 degC, 18 min period
        factors.append(water.update(live(supply), 20.0, k * 60.0).factor)
    tail = factors[120:]
    assert max(tail) - min(tail) < 0.12
    raw_spread = emitter_factor(HEATING_RADIATOR, 55.0, 20.0) - emitter_factor(HEATING_RADIATOR, 45.0, 20.0)
    assert max(tail) - min(tail) < 0.5 * raw_spread


def test_hot_water_priority_gives_no_heat_and_does_not_feed_the_averages():
    water = WaterInput(HEATING_RADIATOR)
    for k in range(30):
        water.update(live(50.0), 20.0, k * 60.0)
    before = water.state.supply_avg_c
    state = water.update(live(70.0, heat_reason="dhw_active"), 20.0, 1800.0)
    assert state.factor == 0.0
    assert state.no_heat
    assert state.source == SOURCE_DHW
    assert state.published_factor == 0.0
    assert water.state.supply_avg_c == before
    # Hot water also wins when the supply itself is not known.
    state = water.update(WaterReading(supply_reason="flow_stale", heat_reason="dhw_active"), 20.0, 1900.0)
    assert state.factor == 0.0 and state.no_heat


def test_water_near_the_room_holds_the_integral_but_not_the_command():
    # An idle boiler that fires on demand: the zone must still be able to call for heat.
    water = WaterInput(HEATING_RADIATOR)
    state = water.update(live(21.0), 20.0, 0.0)
    assert state.low_heat
    assert not state.no_heat
    assert state.source == SOURCE_LIVE


def test_water_hotter_than_the_reference_counts_as_the_reference():
    water = WaterInput(HEATING_RADIATOR)
    assert water.update(live(75.0), 15.0, 0.0).factor == 1.0


def test_the_long_term_average_describes_the_water_while_heating():
    water = WaterInput(HEATING_RADIATOR)
    for k in range(60):
        water.update(live(50.0), 20.0, k * 600.0)
    heating = water._long_avg
    # Days of an idle boiler (no heat available, cold water) do not drag it down.
    for k in range(60, 60 + 5 * 144):
        water.update(WaterReading(supply_c=16.0, heat_available=False, heat_reason="flame_off"), 20.0, k * 600.0)
    assert water._long_avg == pytest.approx(heating)
    # Without a heat signal, water bringing next to no heat is left out too.
    for k in range(800, 800 + 144):
        water.update(WaterReading(supply_c=20.5), 20.0, k * 600.0)
    assert water._long_avg == pytest.approx(heating)


def test_the_long_term_average_follows_days_of_another_water():
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(60.0), 20.0, 0.0)
    for k in range(1, 3 * 144):
        water.update(live(40.0), 20.0, k * 600.0)
    # Three days is one time constant: most of the way from 1 to 0.5 ** 1.3.
    target = 0.5**1.3
    assert target < water._long_avg < target + 0.45 * (1.0 - target)


@pytest.mark.parametrize("reading", [
    WaterReading(supply_reason="flow_stale"),
    WaterReading(supply_reason="flow_unknown"),
    WaterReading(supply_reason="a_reason_added_later"),
    None,
])
def test_momentary_gap_uses_the_long_term_average(reading):
    water = WaterInput(HEATING_RADIATOR)
    for k in range(60):
        water.update(live(40.0), 20.0, k * 60.0)
    expected = water._long_avg
    state = water.update(reading, 20.0, 3600.0)
    assert state.source == SOURCE_AVERAGE
    assert state.factor == pytest.approx(expected)
    assert 0.0 < state.factor < 1.0
    assert not state.changed_for_good


@pytest.mark.parametrize("reason", ["not_mapped", "circuit_not_measured", "several_circuits"])
def test_lasting_gap_gives_one_and_is_a_change_for_good(reason):
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(40.0), 20.0, 0.0)
    state = water.update(WaterReading(supply_reason=reason, heat_reason=reason), 20.0, 60.0)
    assert state.factor == 1.0
    assert state.source == SOURCE_LASTING
    assert state.published_factor is None
    assert state.changed_for_good
    # Only once.
    assert not water.update(WaterReading(supply_reason=reason), 20.0, 120.0).changed_for_good


def test_no_data_for_a_day_after_data_were_seen_counts_as_lasting():
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(45.0), 20.0, 0.0)
    for reading in (None, WaterReading(supply_reason="a_reason_added_later")):
        assert water.update(reading, 20.0, 3600.0).source == SOURCE_AVERAGE
    state = water.update(None, 20.0, 3600.0 + 24 * 3600.0)
    assert state.source == SOURCE_LASTING
    assert state.factor == 1.0
    assert state.changed_for_good
    # Data back: a change for good again.
    assert water.update(live(45.0), 20.0, 30 * 3600.0).changed_for_good


def test_the_no_data_gap_goes_on_across_restarts():
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(45.0), 20.0, 0.0)
    now = 0.0
    for _ in range(3):
        # 10 h without data, then a restart (the monotonic clock starts again).
        for hour in range(1, 11):
            state = water.update(None, 20.0, now + hour * 3600.0)
        saved = water.save_state()
        water = WaterInput(HEATING_RADIATOR)
        water.load_state(saved)
        now = 0.0
    assert water.update(None, 20.0, 3600.0).source == SOURCE_LASTING


def test_an_unknown_room_temperature_is_not_a_gap():
    water = WaterInput(HEATING_RADIATOR)
    live_state = water.update(live(45.0), 20.0, 0.0)
    for hour in range(1, 30):
        state = water.update(live(45.0), None, hour * 3600.0)
    assert state.source == SOURCE_LIVE
    assert state.factor == pytest.approx(live_state.factor)
    assert not state.changed_for_good


def test_a_known_momentary_reason_stays_momentary():
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(45.0), 20.0, 0.0)
    for hour in (1, 24, 48):
        state = water.update(WaterReading(supply_reason="flow_stale"), 20.0, hour * 3600.0)
        assert state.source == SOURCE_AVERAGE
        assert not state.changed_for_good


def test_data_appearing_is_a_change_for_good_but_not_lasting_from_the_start():
    water = WaterInput(HEATING_RADIATOR)
    assert not water.update(WaterReading(supply_reason="not_mapped"), 20.0, 0.0).changed_for_good
    assert water.update(live(45.0), 20.0, 60.0).changed_for_good
    assert not water.update(live(45.0), 20.0, 120.0).changed_for_good


def test_first_data_ever_are_a_change_for_good():
    water = WaterInput(HEATING_RADIATOR)
    water.update(None, 20.0, 0.0)
    assert water.update(live(45.0), 20.0, 60.0).changed_for_good


def test_state_round_trip_keeps_the_long_term_average_and_the_availability():
    water = WaterInput(HEATING_RADIATOR)
    for k in range(60):
        water.update(live(40.0), 20.0, k * 60.0)
    saved = water.save_state()
    restored = WaterInput(HEATING_RADIATOR)
    restored.load_state(saved)
    state = restored.update(WaterReading(supply_reason="flow_stale"), 20.0, 0.0)
    assert state.source == SOURCE_AVERAGE
    assert state.factor == pytest.approx(water._long_avg)
    # The data being used before the restart, a first reading is no change.
    fresh = WaterInput(HEATING_RADIATOR)
    fresh.load_state(saved)
    assert not fresh.update(live(40.0), 20.0, 0.0).changed_for_good


def test_the_state_names_the_inputs_it_belongs_to():
    water = WaterInput(HEATING_RADIATOR)
    water.inputs = "entities|sensor.supply"
    water.update(live(40.0), 20.0, 0.0)
    saved = water.save_state()
    assert saved["inputs"] == "entities|sensor.supply"
    restored = WaterInput(HEATING_RADIATOR)
    restored.load_state(saved)
    assert restored.inputs == "entities|sensor.supply"
    # A state saved before the field: the inputs are not known.
    old = {key: value for key, value in saved.items() if key != "inputs"}
    legacy = WaterInput(HEATING_RADIATOR)
    legacy.load_state(old)
    assert legacy.inputs is None and legacy.availability == "used"
    for bad in ("", 3):
        broken = WaterInput(HEATING_RADIATOR)
        broken.load_state({**old, "inputs": bad})
        assert broken.inputs is None


def test_forgetting_the_data_gives_one_at_once_without_a_change_for_good():
    water = WaterInput(HEATING_RADIATOR)
    for k in range(10):
        water.update(live(40.0), 20.0, k * 60.0)
    water.forget_data()
    assert water.availability == "none" and water.save_state()["long_average"] is None
    state = water.update(WaterReading(supply_reason="provider_missing"), 20.0, 600.0)
    assert (state.source, state.factor, state.changed_for_good) == ("none", 1.0, False)
    # The next data are a first appearance (a change for good the handler may settle).
    assert water.update(live(40.0), 20.0, 660.0).changed_for_good


def test_state_of_another_heating_type_is_ignored():
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(40.0), 20.0, 0.0)
    floor = WaterInput(HEATING_FLOOR_WATER)
    floor.load_state(water.save_state())
    assert floor._long_avg is None
    for bad in (None, [], {"heating_type": HEATING_RADIATOR, "long_average": "x", "availability": 3}):
        WaterInput(HEATING_RADIATOR).load_state(bad)


def test_a_state_loaded_before_the_heating_type_is_applied_when_it_is_set():
    source = WaterInput(HEATING_RADIATOR)
    source.update(live(40.0), 20.0, 0.0)
    water = WaterInput()
    water.load_state(source.save_state())
    water.configure(HEATING_RADIATOR)
    assert water._long_avg == pytest.approx(source._long_avg)
    assert water.availability == "used"


def test_changing_the_heating_type_drops_the_averages():
    water = WaterInput(HEATING_RADIATOR)
    water.update(live(40.0), 20.0, 0.0)
    water.configure(HEATING_FLOOR_WATER)
    assert water._long_avg is None and water._supply_avg is None
    assert water.update(None, 20.0, 60.0).source == SOURCE_NONE
