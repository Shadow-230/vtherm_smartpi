"""Tests for the composition of known inputs into the control model."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.const import KNOWN_HEAT_MAX_SHIFT_C
from custom_components.vtherm_smartpi.smartpi.effective_model import (
    ExchangeInput,
    HeatInput,
    KnownInputs,
    compose,
)

B = 0.002
T_EXT = 0.0


@pytest.mark.parametrize("inputs", [None, KnownInputs(), KnownInputs(learning_gate="door_open")])
def test_without_inputs_the_raw_values_pass_through_unchanged(inputs) -> None:
    b, ext = 0.0021, 4.3
    model = compose(b, ext, inputs)

    assert model.b is b
    assert model.ext_temp is ext
    assert not model.active
    assert model.known_rate(20.0) == 0.0


def test_one_exchange_shifts_the_reference_towards_the_other_temperature() -> None:
    model = compose(B, T_EXT, KnownInputs(exchanges=(ExchangeInput("n1", 0.002, 20.0),)))

    assert model.active
    assert model.b == pytest.approx(0.004)
    assert model.ext_temp == pytest.approx(10.0)
    assert model.signature == ("n1",)


def test_the_composition_keeps_the_zone_dynamics() -> None:
    """b_eff * (T_ref - T) equals the sum of the individual flows at any temperature."""
    inputs = KnownInputs(
        exchanges=(ExchangeInput("n1", 0.001, 22.0), ExchangeInput("n2", 0.0005, 15.0)),
        heats=(HeatInput("sun", 0.003),),
    )
    model = compose(B, 2.0, inputs)

    for temperature in (10.0, 19.5, 25.0):
        direct = (
            B * (2.0 - temperature)
            + 0.001 * (22.0 - temperature)
            + 0.0005 * (15.0 - temperature)
            + 0.003
        )
        assert model.b * (model.ext_temp - temperature) == pytest.approx(direct)
        assert model.known_rate(temperature) == pytest.approx(direct - B * (2.0 - temperature))


def test_unavailable_outdoor_temperature_disables_the_composition() -> None:
    model = compose(B, None, KnownInputs(exchanges=(ExchangeInput("n1", 0.002, 20.0),)))

    assert not model.active
    assert model.ext_temp is None


@pytest.mark.parametrize(
    "exchange",
    [ExchangeInput("n1", 0.0, 20.0), ExchangeInput("n1", -0.001, 20.0), ExchangeInput("n1", 0.002, float("nan"))],
    ids=["zero_conductance", "negative_conductance", "non_finite_temperature"],
)
def test_unusable_exchanges_are_ignored(exchange) -> None:
    assert not compose(B, T_EXT, KnownInputs(exchanges=(exchange,))).active


@pytest.mark.parametrize(("ext", "neighbour"), [(0.0, 20.0), (35.0, 24.0)], ids=["heating", "cooling"])
def test_reference_lies_between_outdoor_and_neighbour(ext, neighbour) -> None:
    model = compose(B, ext, KnownInputs(exchanges=(ExchangeInput("n1", 0.001, neighbour),)))

    assert min(ext, neighbour) < model.ext_temp < max(ext, neighbour)


def test_feed_forward_ratio_grows_with_the_exchange() -> None:
    """FF1 holds the setpoint with k_ff = b/a against T_ref: a colder neighbour asks for more power."""
    a, setpoint = 0.03, 20.0
    plain = B / a * (setpoint - T_EXT)
    model = compose(B, T_EXT, KnownInputs(exchanges=(ExchangeInput("n1", 0.001, 15.0),)))

    assert model.b / a * (setpoint - model.ext_temp) == pytest.approx(plain + 0.001 / a * (setpoint - 15.0))


def test_known_heat_is_bounded_when_losses_are_small() -> None:
    model = compose(1e-5, 5.0, KnownInputs(heats=(HeatInput("source", 0.05),)))

    assert model.ext_temp == pytest.approx(5.0 + KNOWN_HEAT_MAX_SHIFT_C)


def test_known_heat_with_either_sign() -> None:
    warm = compose(B, T_EXT, KnownInputs(heats=(HeatInput("sun", 0.004),)))
    cold = compose(B, T_EXT, KnownInputs(heats=(HeatInput("sky", -0.004),)))

    assert warm.ext_temp == pytest.approx(2.0)
    assert cold.ext_temp == pytest.approx(-2.0)
    assert warm.b == cold.b == B


def test_without_a_trajectory_the_horizon_equals_now() -> None:
    inputs = KnownInputs(exchanges=(ExchangeInput("n1", 0.002, 20.0),), heats=(HeatInput("q", 0.001),))

    assert compose(B, T_EXT, inputs, offset_min=90.0) == compose(B, T_EXT, inputs)


def test_trajectories_are_interpolated_over_the_horizon() -> None:
    exchange = ExchangeInput("n1", 0.002, 20.0, trajectory=((60.0, 0.002, 14.0), (120.0, 0.0, 14.0)))
    heat = HeatInput("q", 0.0, trajectory=((60.0, 0.002),))

    assert exchange.at(30.0) == pytest.approx((0.002, 17.0))
    assert exchange.at(90.0) == pytest.approx((0.001, 14.0))
    assert exchange.at(500.0) == pytest.approx((0.0, 14.0))
    assert heat.at(30.0) == pytest.approx(0.001)
    assert not compose(B, T_EXT, KnownInputs(exchanges=(exchange,)), offset_min=200.0).active
