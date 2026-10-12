"""Tests for learning the heat exchange with neighbours from synthetic zone data.

A zone following ``dT/dt = a*u(t - L) - b*(T - T_ext) - sum k_j*(T - T_j)`` is
integrated in small steps and sampled once a minute, as SmartPI's calculations
would see it.
"""

from __future__ import annotations

import math

import pytest

from custom_components.vtherm_smartpi.smartpi.const import COUPLING_JOINT_MIN_ROWS, COUPLING_K_MAX
from custom_components.vtherm_smartpi.smartpi.coupling_estimator import (
    CouplingEstimator,
    CouplingTick,
)

A, B, L_S = 0.03, 0.001, 600.0


def _feed(
    estimator: CouplingEstimator,
    hours: float,
    neighbours: dict,
    always: frozenset = frozenset(),
    ext=lambda t: 5.0,
    power=lambda t: 0.5,
    allowed=lambda t: True,
    start_temp: float = 20.0,
) -> float:
    """Integrate the zone and feed one tick per minute; return the final temperature.

    ``neighbours`` maps an id to (k, temperature function, connected function).
    """
    temp, t = start_temp, 0.0
    steps_per_minute = 6
    for minute in range(int(hours * 60)):
        t = minute * 60.0
        connected = {key: temp_fn(t) for key, (_k, temp_fn, on) in neighbours.items() if on(t)}
        estimator.update(
            CouplingTick(
                now_s=t,
                zone_temp=temp,
                ext_temp=ext(t),
                power=power(t),
                a=A,
                b=B,
                deadtime_s=L_S,
                connected=connected,
                learning_allowed=allowed(t),
                always=always & set(connected),
            )
        )
        for step in range(steps_per_minute):
            ts = t + step * 10.0
            rate = A * power(ts - L_S) - B * (temp - ext(ts))
            for key, (k, temp_fn, on) in neighbours.items():
                if on(ts):
                    rate -= k * (temp - temp_fn(ts))
            temp += rate * 10.0 / 60.0
    return temp


def _always_on(_t: float) -> bool:
    return True


def test_door_neighbour_k_is_learned() -> None:
    estimator = CouplingEstimator()
    _feed(estimator, 12.0, {"hall": (0.002, lambda t: 15.0, _always_on)})

    assert estimator.k("hall") == pytest.approx(0.002, rel=0.03)
    assert not estimator.is_indistinguishable("hall")


def test_varying_power_is_shifted_by_the_dead_time() -> None:
    estimator = CouplingEstimator()
    _feed(
        estimator,
        12.0,
        {"hall": (0.002, lambda t: 15.0, _always_on)},
        power=lambda t: 0.8 if (t // 1500) % 2 else 0.2,
    )

    assert estimator.k("hall") == pytest.approx(0.002, rel=0.05)


def test_nothing_is_learned_while_learning_is_not_allowed() -> None:
    estimator = CouplingEstimator()
    _feed(estimator, 12.0, {"hall": (0.002, lambda t: 15.0, _always_on)}, allowed=lambda t: False)

    assert estimator.k("hall") is None
    assert estimator.samples("hall") == 0


def test_a_small_temperature_difference_carries_no_sample() -> None:
    estimator = CouplingEstimator()
    _feed(estimator, 12.0, {"hall": (0.002, lambda t: 20.2, _always_on)})

    assert estimator.samples("hall") == 0


def test_implausible_values_are_rejected() -> None:
    estimator = CouplingEstimator()
    _feed(estimator, 12.0, {"hall": (COUPLING_K_MAX * 3, lambda t: 15.0, _always_on)})

    assert estimator.k("hall") is None


def test_two_door_neighbours_open_together_are_separated() -> None:
    estimator = CouplingEstimator()
    _feed(
        estimator,
        20.0,
        {
            "hall": (0.002, lambda t: 15.0 + 3.0 * math.sin(t / 5000.0), _always_on),
            "kitchen": (0.001, lambda t: 24.0 + 2.0 * math.cos(t / 7000.0), _always_on),
        },
    )

    assert estimator.k("hall") == pytest.approx(0.002, rel=0.05)
    assert estimator.k("kitchen") == pytest.approx(0.001, rel=0.05)
    assert not estimator.is_indistinguishable("hall")


def test_door_neighbours_that_cannot_be_told_apart_share_one_coefficient() -> None:
    estimator = CouplingEstimator()
    _feed(
        estimator,
        20.0,
        {
            "hall": (0.002, lambda t: 15.0, _always_on),
            "stairs": (0.002, lambda t: 15.0, _always_on),
        },
    )

    assert estimator.is_indistinguishable("hall")
    assert estimator.k("hall") == pytest.approx(0.002, rel=0.05)
    assert estimator.k("stairs") == estimator.k("hall")


def test_always_neighbour_is_fitted_with_b_when_separable() -> None:
    """A neighbour whose temperature moves independently of the outdoors is separable."""
    estimator = CouplingEstimator()
    _feed(
        estimator,
        48.0,
        {"hall": (0.002, lambda t: 15.0 + 4.0 * math.sin(t / 9000.0), _always_on)},
        always=frozenset({"hall"}),
        ext=lambda t: 5.0 + 3.0 * math.cos(t / 43200.0 * math.pi),
    )

    assert estimator.k("hall") == pytest.approx(0.002, rel=0.05)
    assert estimator.loss_coefficient() == pytest.approx(B, rel=0.05)


def test_always_neighbour_that_cannot_be_separated_is_not_published() -> None:
    """Constant neighbour and outdoor temperatures leave b and k inseparable: nothing is published."""
    estimator = CouplingEstimator()
    _feed(
        estimator,
        48.0,
        {"hall": (0.002, lambda t: 15.0, _always_on)},
        always=frozenset({"hall"}),
    )

    assert estimator.k("hall") is None
    assert estimator.loss_coefficient() is None
    assert estimator.is_indistinguishable("hall")


def test_state_round_trip_and_forgetting() -> None:
    estimator = CouplingEstimator()
    _feed(estimator, 12.0, {"hall": (0.002, lambda t: 15.0, _always_on)})
    learned = estimator.k("hall")

    restored = CouplingEstimator()
    restored.load_item("hall", estimator.save_item("hall"))
    restored.load_shared(estimator.save_shared())
    assert restored.k("hall") == learned

    restored.forget_except(set())
    assert restored.k("hall") is None


def test_always_fit_waits_for_a_day_of_windows() -> None:
    """A few hours of rows share the same errors of SmartPI's model: no k is published from them."""
    estimator = CouplingEstimator()
    _feed(
        estimator,
        20.0,
        {"hall": (0.002, lambda t: 15.0 + 4.0 * math.sin(t / 9000.0), _always_on)},
        always=frozenset({"hall"}),
        ext=lambda t: 5.0 + 3.0 * math.cos(t / 43200.0 * math.pi),
    )

    assert estimator.samples("hall") >= COUPLING_JOINT_MIN_ROWS
    assert estimator.k("hall") is None
    assert estimator.loss_coefficient() is None


def _feed_switched_cycles(
    estimator: CouplingEstimator, duties: tuple[float, ...], hours: float = 12.0
) -> None:
    """A switched heater with a door neighbour at 15 degC (k = 0.002).

    Each 10-minute cycle heats at full power first, with the duties repeating in
    turn; the cycles start 4 minutes off the calculations. The commanded power
    is the duty, and each completed cycle reports what it realized.
    """
    cycle_s, offset_s, k, neighbour_c = 600.0, 240.0, 0.002, 15.0

    def duty(t: float) -> float:
        return duties[int((t + offset_s) // cycle_s) % len(duties)]

    def heat(t: float) -> float:
        return 1.0 if (t + offset_s) % cycle_s < duty(t) * cycle_s else 0.0

    temp = 20.0
    for minute in range(int(hours * 60)):
        t = minute * 60.0
        if t > 0.0 and (t + offset_s) % cycle_s == 0.0:
            estimator.record_realized_power(t - cycle_s, t, duty(t - cycle_s), switched=True)
        estimator.update(
            CouplingTick(
                now_s=t,
                zone_temp=temp,
                ext_temp=5.0,
                power=duty(t),
                a=A,
                b=B,
                deadtime_s=L_S,
                connected={"hall": neighbour_c},
                learning_allowed=True,
            )
        )
        for step in range(6):
            ts = t + step * 10.0
            rate = A * heat(ts - L_S) - B * (temp - 5.0) - k * (temp - neighbour_c)
            temp += rate * 10.0 / 60.0


@pytest.mark.parametrize(
    "duties",
    [(0.6,), (0.9, 0.1, 0.5)],
    ids=["temperature_ripple", "duty_changing_every_cycle"],
)
def test_switched_cycles_are_read_exactly(duties) -> None:
    """The slope and the averages use the same weights, and the power follows each cycle's on-time.

    With the averages taken uniformly under a least-squares slope, the ripple of
    the first case biases k by about 6 %; with the commanded power instead of
    the realized one, the second case is about 10 % off.
    """
    estimator = CouplingEstimator()
    _feed_switched_cycles(estimator, duties)

    assert estimator.k("hall") == pytest.approx(0.002, rel=0.01)


def test_saved_rows_that_cannot_be_read_are_skipped() -> None:
    estimator = CouplingEstimator()
    estimator.load_shared(
        {
            "ls_rows": [
                ["x", {"hall": 1.0}],
                [0.001, {"hall": "y"}],
                [float("nan"), {"hall": 1.0}],
                [0.001, {}],
                [0.001, {"hall": 1.0, "kitchen": 2.0}],
            ],
            "joint_rows": "not a list",
            "joint_b": [0.001, "z", float("inf")],
        }
    )

    assert estimator.samples("hall") == 1
    assert estimator.save_shared()["joint_b"] == [0.001]
