"""Edge cases of composing the known inputs into the control model.

They check that a heat input at zero, or one that is not a number, is left out
of the composition: alone it leaves the raw model unchanged, and next to an
exchange it adds no heat and stays out of the signature, also where its
trajectory has come down to zero at the horizon.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.effective_model import (
    ExchangeInput,
    HeatInput,
    KnownInputs,
    compose,
)

B = 0.002
T_EXT = 0.0


@pytest.mark.parametrize("rate", [0.0, float("nan"), float("inf")], ids=["zero", "nan", "infinite"])
def test_a_heat_input_at_zero_or_not_a_number_is_left_out(rate) -> None:
    alone = compose(B, T_EXT, KnownInputs(heats=(HeatInput("source", rate),)))
    with_exchange = compose(
        B, T_EXT, KnownInputs(exchanges=(ExchangeInput("n1", 0.002, 20.0),), heats=(HeatInput("source", rate),))
    )

    assert not alone.active
    assert alone.b is B and alone.ext_temp is T_EXT
    assert with_exchange.signature == ("n1",)
    assert with_exchange.heat_rate == 0.0
    assert with_exchange.ext_temp == pytest.approx(10.0)


def test_a_heat_whose_trajectory_falls_to_zero_is_left_out_at_the_horizon() -> None:
    """A source switched off: its heat counts now, not once it has died away."""
    inputs = KnownInputs(heats=(HeatInput("source", 0.004, trajectory=((60.0, 0.0),)),))

    assert compose(B, T_EXT, inputs).ext_temp == pytest.approx(2.0)
    assert not compose(B, T_EXT, inputs, offset_min=90.0).active
