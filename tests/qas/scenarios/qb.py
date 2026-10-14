"""QB: the fork against the author's 0.4.0 — twins over 48 h, the way forward from 0.4.0 and the
way back (instances 1 and 7).

QB2 and QB3 span a deploy that the bench's testing session makes between their two steps:

    instance 7 on 0.4.0:  QB2a  →  deploy qas  →  QB2  →  QB3a  →  deploy 0.4.0  →  QB3

Measurements that another step or another instance needs are kept in the bench's results
directory (``common.keep``); ``tests/qas/compare.py`` reads QB1's from instances 1 and 7.
"""

from __future__ import annotations

from typing import Any

from flows import Form, Menu

from .common import (
    api,
    entry,
    keep,
    kept,
    live,
    log_lines,
    options_flow,
    path,
    role,
    room_temperature,
    sample,
    stored_options,
    system_log,
    wait_loaded,
)
from .registry import scenario

LIVING = "climate.living"
ATTIC_SENSOR = "sensor.smartpi_room_sim_attic_temperature"
FIXTURE = "learned_room"
MODEL_KEYS = ("model.a", "model.b", "learning.accepted_updates_a", "learning.accepted_updates_b")


async def _hold_learning(r: Any, enabled: bool) -> None:
    """Pause learning until the next step reads the model, so that it does not move meanwhile
    (the pause is saved, and survives the deploy's restart); resume it afterwards."""
    await api(r).call("vtherm_smartpi", "set_smartpi_learning", {"entity_id": LIVING, "learning_enabled": enabled})


async def _twin_run(r: Any, hours: float, name: str) -> None:
    setpoint = role().thermostats[0].setpoint

    async def error() -> float | None:
        temperature = await room_temperature(r, "living")
        return None if temperature is None else setpoint - temperature

    samples = await sample(r, hours * 3600.0, 120.0, error)
    late = [v for t, v in samples if v is not None and t >= hours * 3600.0 / 2.0]
    data = await live(r, LIVING)
    measured = {
        "hours": hours,
        "a": path(data, "model.a"),
        "b": path(data, "model.b"),
        "phase": path(data, "control.phase"),
        "mean_error_second_half": sum(late) / len(late) if late else None,
    }
    where = keep(name, measured)
    r.note(f"kept for tests/qas/compare.py: {where.name} {measured}")
    r.check(measured["mean_error_second_half"] is not None, "the room was measured")
    errors = log_lines(await system_log(r), levels=("ERROR",))
    r.check(not errors, f"no error over the run: {errors[:3]}")


@scenario("QB1", ("base", "ref-0.4.0"), 48 * 60, "test_smartpi_regression_no_dependencies.py, test_smartpi_sim_baseline.py")
async def QB1(r: Any) -> None:
    """Over 48 h the twin room learns and regulates; the instance keeps a, b and its mean error from 24 to 48 h, and compare.py qb1 passes when the qas instance's a and b are within ±10 % of 0.4.0's and its mean error is no more than 0.03 °C above."""
    await _twin_run(r, 48.0, "qb1")


@scenario("QB1s", ("base", "ref-0.4.0"), 45, "test_smartpi_sim_baseline.py", minimum=True, short_of=("QB1",))
async def QB1s(r: Any) -> None:
    """For 45 minutes the twin room regulates and nothing logs an error, and the instance keeps what compare.py qb1s reads (both instances regulating, phases named)."""
    await _twin_run(r, 0.75, "qb1s")


@scenario("QB2a", ("ref-0.4.0",), 10, "test_algo_persistence.py", minimum=True)
async def QB2a(r: Any) -> None:
    """On 0.4.0, before the upgrade: SmartPI's state is the learned fixture, saved by 0.4.0 itself with learning paused, and its model is kept for QB2."""
    await _hold_learning(r, False)
    await api(r).call("smartpi_qas_helpers", "seed_state", {"entity_id": LIVING, "fixture": FIXTURE})
    data = await live(r, LIVING)
    saved = {key: path(data, key) for key in MODEL_KEYS}
    r.check(saved["model.a"] is not None and saved["model.b"] is not None, f"the seeded model: {saved}")
    keep("qb2", saved)


@scenario("QB2", ("ref-0.4.0",), 30, "test_algo_persistence.py", minimum=True)
async def QB2(r: Any) -> None:
    """After the upgrade from 0.4.0 to qas, the state 0.4.0 saved is loaded unchanged, no warning about its format is logged, nothing is reset, and the old entry's options menu works."""
    before = kept("qb2", 7)
    r.check(before is not None, "QB2a kept the 0.4.0 model on this instance")
    r.check(await wait_loaded(r, [LIVING], 600.0), "the thermostat runs on qas")
    data = await live(r, LIVING)
    for key in MODEL_KEYS:
        r.check(path(data, key) == (before or {}).get(key), f"{key}: 0.4.0 {(before or {}).get(key)} → qas {path(data, key)}")
    lines = log_lines(await system_log(r))
    r.check(not [line for line in lines if "format" in line or "unsupported" in line.lower()], f"no warning about the saved format: {lines[:3]}")
    r.check(not lines, f"no warning or error from SmartPI or VTherm since the upgrade's restart: {len(lines)}")
    await options_flow(r, role().thermostats[0].name, [Menu("settings"), Form("settings")])
    r.check(True, "the old entry's options open as the menu and save")
    await _hold_learning(r, True)
    r.note("put back: learning enabled")


@scenario("QB3a", ("ref-0.4.0",), 10, "—")
async def QB3a(r: Any) -> None:
    """On qas, before going back to 0.4.0: a neighbour (the attic's sensor) is added to the entry, learning is paused and the model is kept for QB3; this is left in place on purpose."""
    await options_flow(
        r,
        role().thermostats[0].name,
        [
            Menu("dependencies"),
            Menu("neighbour_add"),
            Form("neighbour_add", {"entity": ATTIC_SENSOR}),
            Form("neighbour_details", {"mode": "always", "door_sensors": []}),
            Form("neighbour_confirm", if_shown=True),
            Form("model_reset", {"reset_model": False}, if_shown=True),
        ],
    )
    stored = await stored_options(role().thermostats[0].name)
    r.check(bool(stored.get("smart_pi_dependencies")), "the neighbour is stored")
    await _hold_learning(r, False)
    data = await live(r, LIVING)
    keep("qb3", {key: path(data, key) for key in MODEL_KEYS})
    r.note("left in place for QB3 (on 0.4.0), which removes it by saving the options there; learning paused until then")


@scenario("QB3", ("ref-0.4.0",), 30, "—")
async def QB3(r: Any) -> None:
    """Back on 0.4.0 with a neighbour configured, the entry loads without errors and the state too; saving the options in 0.4.0 drops the fork's keys, as known."""
    before = kept("qb3", 7) or {}
    r.check(await wait_loaded(r, [LIVING], 600.0), "the thermostat runs on 0.4.0")
    title = role().thermostats[0].name
    found = await entry(r, "vtherm_smartpi", title)
    r.check(found.get("state") == "loaded", f"the entry with the fork's keys loads in 0.4.0: {found.get('state')}")
    data = await live(r, LIVING)
    for key in ("model.a", "model.b"):
        r.check(path(data, key) == before.get(key), f"{key}: qas {before.get(key)} → 0.4.0 {path(data, key)}")
    errors = log_lines(await system_log(r), levels=("ERROR",))
    r.check(not errors, f"no error since going back: {errors[:3]}")
    await options_flow(r, title, [Form("init")])
    stored = await stored_options(title)
    dropped = "smart_pi_dependencies" not in stored
    r.check(True, f"known: saving the options in 0.4.0 {'drops' if dropped else 'keeps'} the fork's keys")
    await _hold_learning(r, True)
    r.note("this also puts the instance back to its base (no neighbour); learning enabled again")
    if not dropped:
        r.note(f"still stored: smart_pi_dependencies = {stored.get('smart_pi_dependencies')}")


@scenario("QB2l", ("ref-0.4.0",), 5, "test_algo_persistence.py")
async def QB2l(r: Any) -> None:
    """On 0.4.0 after QB1, before the upgrade: the model 0.4.0 learned itself is kept for QB2 with learning paused, without seeding."""
    await _hold_learning(r, False)
    data = await live(r, LIVING)
    saved = {key: path(data, key) for key in MODEL_KEYS}
    r.check(saved["model.a"] is not None and saved["model.b"] is not None, f"0.4.0's learned model: {saved}")
    keep("qb2", saved)
