"""Edge cases of the neighbour exchange learning.

They check what the estimator keeps or refuses at the borders of its normal
work: door fits whose coefficients come out negative or cannot be computed, a
heat source that never ran during the door windows, an always-connected
exchange fitted beyond its plausible bound, histories whose values disagree or
have no spread, saved rows and cycles that cannot be used, and forgetting the
items that are no longer configured.
"""

from __future__ import annotations

import math

import pytest

from custom_components.vtherm_smartpi.smartpi.const import (
    COUPLING_JOINT_MIN_ROWS,
    COUPLING_K_MAX,
)
from custom_components.vtherm_smartpi.smartpi.coupling_estimator import (
    CouplingEstimator,
    CouplingTick,
)

A, B, L_S = 0.03, 0.001, 600.0


def _tick(now_s: float, zone: float, ext: float, power: float, connected: dict, **extra) -> CouplingTick:
    return CouplingTick(
        now_s=now_s,
        zone_temp=zone,
        ext_temp=ext,
        power=power,
        a=A,
        b=B,
        deadtime_s=L_S,
        connected=connected,
        learning_allowed=True,
        **extra,
    )


def _steady(estimator: CouplingEstimator, windows: int, neighbours: dict, heats: dict | None = None) -> None:
    """A zone held at 20 degC (outdoors at 5 degC), one calculation a minute.

    ``neighbours`` maps an id to (k, temperature); the power balances the losses
    and the exchanges, so each 30-min window measures them exactly.
    """
    zone, ext = 20.0, 5.0
    power = (B * (zone - ext) + sum(k * (zone - temp) for k, temp in neighbours.values())) / A
    connected = {key: temp for key, (_k, temp) in neighbours.items()}
    for minute in range(30 * windows + 1):
        estimator.update(_tick(minute * 60.0, zone, ext, power, connected, heats=heats or {}))


def _simulate(estimator: CouplingEstimator, hours: float, k: float, power, ext, hall) -> None:
    """An always-connected neighbour: the zone is integrated in 10-s steps, sampled once a minute."""
    temp = 20.0
    for minute in range(int(hours * 60)):
        t = minute * 60.0
        estimator.update(_tick(t, temp, ext(t), power(t), {"hall": hall(t)}, always=frozenset({"hall"})))
        for step in range(6):
            ts = t + step * 10.0
            temp += (A * power(ts - L_S) - B * (temp - ext(ts)) - k * (temp - hall(ts))) * 10.0 / 60.0


def _door_rows(k_hall: float, k_kitchen: float) -> list:
    """Saved door rows of two neighbours open together, whose regressors are well apart."""
    rows = []
    for x_hall, x_kitchen in [(-5.0, -1.0), (-1.0, -5.0)] * 4:
        rows.append([k_hall * x_hall + k_kitchen * x_kitchen, {"hall": x_hall, "kitchen": x_kitchen}])
    return rows


def test_door_fit_with_every_coefficient_negative_publishes_nothing() -> None:
    """The zone gained heat from colder neighbours (an unmodelled gain): no k is kept."""
    estimator = CouplingEstimator()
    estimator.load_shared({"ls_rows": _door_rows(-0.002, -0.001)})

    assert estimator.samples("hall") == 8
    assert estimator.k("hall") is None
    assert estimator.k("kitchen") is None
    assert not estimator.is_indistinguishable("hall")


def test_door_fit_drops_a_negative_coefficient_and_refits_the_others() -> None:
    estimator = CouplingEstimator()
    estimator.load_shared({"ls_rows": _door_rows(0.002, -0.001)})

    # Alone, hall's least squares are r.x_hall / x_hall.x_hall = (0.002*104 - 0.001*40) / 104.
    assert estimator.k("hall") == pytest.approx((0.002 * 104 - 0.001 * 40) / 104)
    assert estimator.k("kitchen") is None


def test_doors_whose_exchanges_cancel_get_no_common_coefficient() -> None:
    """One neighbour as much warmer as the other is colder: no net exchange to share a k from."""
    estimator = CouplingEstimator()
    _steady(estimator, 8, {"hall": (0.002, 22.0), "kitchen": (0.002, 18.0)})

    assert estimator.samples("hall") == 8
    assert estimator.k("hall") is None
    assert estimator.k("kitchen") is None
    assert not estimator.is_indistinguishable("hall")


def test_a_heat_source_that_never_ran_during_the_door_windows_leaves_k_unknown() -> None:
    """The source's column is zero: it cannot be told apart, and no common coefficient mixes it in."""
    estimator = CouplingEstimator()
    _steady(estimator, 8, {"hall": (0.002, 15.0)}, heats={"stove": 0.0})

    assert estimator.samples("hall") == 8
    assert estimator.save_item("hall") == {"k_history": []}
    assert estimator.k("hall") is None
    assert estimator.k("stove") is None
    assert not estimator.is_indistinguishable("hall")


def test_an_always_connected_exchange_fitted_beyond_its_bound_is_not_kept() -> None:
    """The buffer separates k from b, but k comes out above COUPLING_K_MAX: nothing enters the history."""
    estimator = CouplingEstimator()
    _simulate(
        estimator,
        48.0,
        0.025,
        power=lambda t: 1.0 if (t // 10800.0) % 2 else 0.0,
        ext=lambda t: 5.0 + 3.0 * math.cos(t / 43200.0 * math.pi),
        hall=lambda t: 15.0 + 4.0 * math.sin(t / 9000.0),
    )

    assert 0.025 > COUPLING_K_MAX
    assert estimator.samples("hall") >= COUPLING_JOINT_MIN_ROWS
    assert estimator.save_shared()["joint_k"] == {}
    assert estimator.k("hall") is None
    assert estimator.loss_coefficient() is None
    assert not estimator.is_indistinguishable("hall")


def test_a_history_whose_values_disagree_publishes_nothing() -> None:
    estimator = CouplingEstimator()
    estimator.load_item("hall", {"k_history": [0.001] * 4 + [0.004] * 4})

    assert estimator.k("hall") is None


def test_a_fitted_loss_whose_values_disagree_is_not_published() -> None:
    """The exchange is agreed, the loss fitted with it is not: the loss stays unknown."""
    estimator = CouplingEstimator()
    estimator.load_shared({"joint_k": {"hall": [0.0015] * 8}, "joint_b": [0.0005] * 4 + [0.002] * 4})

    assert estimator.k("hall") == pytest.approx(0.0015)
    assert estimator.loss_coefficient() is None


def test_a_history_without_spread_keeps_the_next_measurement() -> None:
    """With identical values there is no spread to judge an outlier by; with a spread it is refused."""
    flat = CouplingEstimator()
    flat.load_item("hall", {"k_history": [0.002] * 5})
    _steady(flat, 1, {"hall": (0.003, 15.0)})

    spread = CouplingEstimator()
    spread.load_item("hall", {"k_history": [0.0019, 0.00195, 0.002, 0.00205, 0.0021]})
    _steady(spread, 1, {"hall": (0.003, 15.0)})

    assert flat.save_item("hall")["k_history"] == [0.002] * 5 + [pytest.approx(0.003)]
    assert len(spread.save_item("hall")["k_history"]) == 5


def test_saved_rows_of_another_shape_are_skipped() -> None:
    estimator = CouplingEstimator()
    estimator.load_shared(
        {
            "ls_rows": [
                "not a row",
                [0.001],
                [0.001, {"hall": 1.0}, "extra"],
                (0.001, {"hall": 1.0}),
            ],
        }
    )

    assert estimator.samples("hall") == 1
    assert estimator.save_shared()["ls_rows"] == [[0.001, {"hall": 1.0}]]


def test_forgetting_keeps_the_configured_items_and_drops_the_others() -> None:
    estimator = CouplingEstimator()
    estimator.load_item("hall", {"k_history": [0.002] * 8})
    estimator.load_item("kitchen", {"k_history": [0.001] * 8})
    estimator.load_shared({"joint_k": {"hall": [0.0015] * 8, "attic": [0.001] * 8}, "joint_b": [0.001] * 8})

    estimator.forget_except({"hall"})

    assert estimator.k("hall") == pytest.approx(0.002)
    assert estimator.save_item("kitchen") == {"k_history": []}
    assert estimator.k("kitchen") is None
    assert estimator.save_shared()["joint_k"] == {"hall": [0.0015] * 8}


def test_an_empty_or_unreadable_cycle_report_is_ignored() -> None:
    """The commanded power stays in use where a reported cycle cannot be read."""
    estimator = CouplingEstimator()
    estimator.update(_tick(0.0, 20.0, 5.0, 0.4, {}))

    estimator.record_realized_power(600.0, 600.0, 1.0, switched=True)
    estimator.record_realized_power(0.0, 600.0, float("nan"))
    estimator.record_realized_power(0.0, 600.0, float("inf"))

    assert estimator._delayed_power(0.0, 600.0) == pytest.approx(0.4)

    estimator.record_realized_power(0.0, 600.0, 1.0)
    assert estimator._delayed_power(0.0, 600.0) == pytest.approx(1.0)
