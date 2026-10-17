"""Edge cases of the zone's joint fit.

They check that the fit publishes nothing when the additional source never
ran or its heat barely varied under a noisy sensor, that a sample with a
temperature that is not a number is refused, and that a saved state which does
not match its own sums or cannot be read is dropped instead of resumed.
"""

from __future__ import annotations

import math
import random

import pytest

from custom_components.vtherm_smartpi.smartpi.const import ZONE_FIT_MIN_WINDOWS
from custom_components.vtherm_smartpi.smartpi.source_model import (
    SourceHeat,
    SourceResponse,
)
from custom_components.vtherm_smartpi.smartpi.zone_fit import ZoneFit

A, B, A_F = 0.03, 0.001, 0.025
RESPONSE = SourceResponse(600.0, 1800.0)
STEP_S = 10.0


def _lead(t_s: float) -> float:
    """The main source: 10-min cycles whose duty follows a slow wave."""
    duty = 0.5 + 0.4 * math.sin(2.0 * math.pi * t_s / (9.0 * 3600.0))
    return 1.0 if (t_s % 600.0) < duty * 600.0 else 0.0


def _delayed_power(start_s: float, end_s: float, delay_s: float) -> float:
    steps = max(1, round((end_s - start_s) / STEP_S))
    return sum(_lead(start_s - delay_s + (i + 0.5) * STEP_S) for i in range(steps)) / steps


def _run(hours: float, source, noise_c: float = 0.0, neighbour: bool = False) -> tuple[ZoneFit, list[bool]]:
    """A simulated zone with one source candidate and no delay, sampled once a minute.

    ``source`` gives the additional source's command; ``noise_c`` adds a seeded
    sensor noise of up to that many degrees. Return the fit and what each sample returned.
    """
    rng = random.Random(1)
    heat = SourceHeat(RESPONSE)
    fit = ZoneFit(1, 0, (0.0,))
    if neighbour:
        fit.set_neighbours(("hall",))
    temp, last, t_s, changed = 19.0, None, 0.0, []
    while t_s < hours * 3600.0:
        command = source(t_s)
        if command != last:
            heat.record(t_s, command)
            last = command
        ext = 2.0 + 3.0 * math.sin(2.0 * math.pi * t_s / 86400.0)
        if int(t_s) % 60 == 0:
            measured = temp + rng.uniform(-noise_c, noise_c)
            exchanges = {"hall": 22.0 - temp} if neighbour else None
            changed.append(fit.add_sample(t_s, measured, ext, (heat.value(t_s),), _delayed_power, exchanges))
        rate = A * _lead(t_s) - B * (temp - ext) + A_F * heat.value(t_s)
        if neighbour:
            rate += 0.002 * (22.0 - temp)
        temp += rate * STEP_S / 60.0
        t_s += STEP_S
    return fit, changed


def _blocks(t_s: float) -> float:
    return 1.0 if int(t_s // (5 * 3600.0)) % 2 == 0 else 0.0


def test_a_source_that_never_ran_leaves_the_fit_unpublished() -> None:
    """Its column is zero: the least squares cannot be solved for any candidate."""
    fit, changed = _run(20.0, lambda _t: 0.0)

    assert fit.windows >= ZONE_FIT_MIN_WINDOWS
    assert not any(changed)
    assert fit.result is None
    assert fit.current is None


def test_a_source_held_nearly_steady_under_a_noisy_sensor_is_not_published() -> None:
    """Its heat barely varies: a_f's standard error stays above ZONE_FIT_MAX_REL_SE.

    The same zone and noise with the source in blocks of 5 h is published.
    """
    steady, changed = _run(20.0, lambda t: 0.1 if t < 10 * 3600.0 else 0.2, noise_c=0.15)
    blocks, _ = _run(20.0, _blocks, noise_c=0.15)

    assert steady.windows >= ZONE_FIT_MIN_WINDOWS
    assert not any(changed)
    assert steady.result is None
    assert blocks.result is not None


@pytest.mark.parametrize(
    ("zone", "ext", "x_f"),
    [(float("nan"), 5.0, 0.0), (20.0, float("inf"), 0.0), (20.0, 5.0, float("nan"))],
    ids=["zone", "outdoor", "source_heat"],
)
def test_a_sample_that_is_not_a_number_is_refused(zone, ext, x_f) -> None:
    fit = ZoneFit(1, 0, (0.0,))
    fit.add_sample(0.0, 20.0, 5.0, (0.0,), _delayed_power)

    assert not fit.add_sample(60.0, zone, ext, (x_f,), _delayed_power)
    assert [sample[0] for sample in fit._samples] == [0.0]


def _saved(neighbour: bool = False) -> dict:
    """The state of a fit after a few windows (not published yet)."""
    fit, _ = _run(3.0, _blocks, neighbour=neighbour)
    state = fit.save_state()
    assert state is not None
    return state


def test_a_state_whose_neighbours_do_not_match_its_sums_is_dropped() -> None:
    """Sums with a neighbour's column, saved without the neighbour's name."""
    state = _saved(neighbour=True)
    del state["neighbours"]

    fit = ZoneFit(1, 0, (0.0,))
    fit.load_state(state)

    assert fit.windows == 0.0
    assert fit.save_state() is None


def _without(key):
    def change(state):
        del state[key]

    return change


def _set(key, value):
    def change(state):
        state[key] = value

    return change


@pytest.mark.parametrize(
    "change",
    [
        _without("yty"),
        _set("n", "many"),
        _set("xty", None),
        _set("current", "best"),
        _set("result", [0.03, 0.001]),
        _set("n", float("nan")),
        _set("yty", float("inf")),
    ],
    ids=["missing_sum", "unreadable_count", "unreadable_sums", "unreadable_current", "short_result", "nan_count", "inf_sum"],
)
def test_a_state_that_cannot_be_read_is_dropped(change) -> None:
    state = _saved()
    change(state)

    fit = ZoneFit(1, 0, (0.0,))
    fit.load_state(state)

    assert fit.windows == 0.0
    assert fit.result is None
    assert fit.current is None
