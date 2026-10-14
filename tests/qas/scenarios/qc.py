"""QC: VTherm's central boiler and the boiler's call SmartPI reads from it (instance 6).

The bedroom (2000 W) cannot start the boiler alone: VTherm's power threshold is 2500 W and its
device-count threshold is off. The office (3000 W) starts it alone.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

from stages import VT

from .common import (
    CYCLE_S,
    MARGIN_S,
    RECALC_S,
    api,
    boiler_grace_s,
    live,
    log_lines,
    path,
    role,
    set_temperature,
    settle_s,
    system_log,
    thermostat,
    wait_live,
)
from .registry import scenario

BEDROOM = "climate.bedroom"
OFFICE = "climate.office"
OFF_SETPOINT = 7.0


async def _boiler_sensor(r: Any) -> str:
    for entry in await api(r).entity_registry():
        if entry["platform"] == VT and entry["unique_id"] == "central_boiler_state":
            return entry["entity_id"]
    raise LookupError("VTherm's central boiler binary sensor is missing")


async def _boiler_is(r: Any, sensor: str, on: bool, within_s: float) -> float | None:
    want = "on" if on else "off"
    return await r.ha.until(lambda: _state_is(r, sensor, want), within_s, 5.0)


async def _state_is(r: Any, entity_id: str, want: str) -> bool:
    state = await r.ha.state(entity_id)
    return bool(state) and state["state"] == want


@scenario("QC1", ("central-boiler",), 30, "test_smartpi_boiler_handler.py", minimum=True)
async def QC1(r: Any) -> None:
    """For a thermostat VTherm's central boiler serves, the boiler block reads source vtherm_central_boiler, and its called follows the central boiler's binary sensor."""
    sensor = await _boiler_sensor(r)
    await set_temperature(r, BEDROOM, OFF_SETPOINT)
    await set_temperature(r, OFFICE, 24.0)
    took = await _boiler_is(r, sensor, True, CYCLE_S + MARGIN_S)
    r.check(took is not None, f"the office alone starts the boiler: {sensor} on after {took}")
    agree = total = 0
    sources = set()
    start = time.time()
    while time.time() - start < 600.0:
        state = (await r.ha.state(sensor) or {}).get("state")
        block = path(await live(r, BEDROOM), "boiler") or {}
        sources.add(block.get("source"))
        if state in ("on", "off") and block.get("called") is not None:
            total += 1
            agree += (state == "on") == bool(block["called"])
        await asyncio.sleep(15.0)
    r.check(sources == {"vtherm_central_boiler"}, f"the bedroom's boiler source: {sorted(map(str, sources))}")
    r.check(total > 0 and agree / total >= 0.9, f"called follows {sensor} in {agree} of {total} samples")
    await set_temperature(r, OFFICE, OFF_SETPOINT)
    took = await _boiler_is(r, sensor, False, CYCLE_S + MARGIN_S)
    r.check(took is not None, f"the boiler stops with the office: off after {took}")
    took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler.called") is False, 2 * RECALC_S + 30.0, 10.0)
    r.check(took is not None, f"the bedroom reads it: called {path(data, 'boiler.called')}")


async def _alone_below_the_thresholds(r: Any, full: bool) -> None:
    bedroom = thermostat("Bedroom")
    grace = boiler_grace_s(bedroom)
    settle = settle_s(bedroom.heating_type)
    sensor = await _boiler_sensor(r)
    await set_temperature(r, OFFICE, OFF_SETPOINT)
    r.check(await _boiler_is(r, sensor, False, CYCLE_S + MARGIN_S) is not None, "the boiler is off to begin with")
    await set_temperature(r, BEDROOM, 23.0)
    took, data = await wait_live(r, BEDROOM, lambda d: (path(d, "power.current_cycle_percent") or 0) > 0, 3 * RECALC_S, 10.0)
    r.check(took is not None, f"the bedroom calls: {path(data, 'power.current_cycle_percent')} %")
    t0 = time.time()
    updates = path(data, "learning.accepted_updates_a") or 0
    took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler.learning") == "emitter_cooling", grace + MARGIN_S, 20.0)
    r.check(took is not None and time.time() - t0 >= grace - MARGIN_S, f"emitter_cooling {time.time() - t0:.0f} s after the call (grace {grace:.0f} s)")
    took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler.learning") == "held", settle - grace + 2 * MARGIN_S, 30.0)
    r.check(took is not None, f"held {time.time() - t0:.0f} s after the call (settle {settle:.0f} s)")
    await asyncio.sleep(10 * 60.0)
    data = await live(r, BEDROOM)
    r.check(path(data, "boiler.learning") == "held", f"still held after 10 minutes: {path(data, 'boiler.learning')}")
    held_updates = path(data, "learning.accepted_updates_a") or 0
    r.check(held_updates == updates, f"no a learned since the call: accepted updates of a {updates} → {held_updates}")
    await set_temperature(r, OFFICE, 24.0)
    r.check(await _boiler_is(r, sensor, True, CYCLE_S + MARGIN_S) is not None, "the office starts the boiler")
    took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler.learning") == "emitter_warming", 2 * RECALC_S + MARGIN_S, 10.0)
    r.check(took is not None, f"emitter_warming after the boiler's start: {path(data, 'boiler.learning')}")
    if not full:
        return
    t1 = time.time()
    took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler.learning") is None, settle + 2 * MARGIN_S, 30.0)
    r.check(took is not None, f"back to learning as usual {time.time() - t1:.0f} s after the start (settle {settle:.0f} s)")
    r.check((path(data, "learning.accepted_updates_a") or 0) >= held_updates, "learning goes on from where it was")


@scenario("QC2", ("central-boiler",), 150, "test_smartpi_boiler_call.py, test_smartpi_boiler_algo.py")
async def QC2(r: Any) -> None:
    """The small zone calling alone below the thresholds goes emitter_cooling after the grace, held after its emitter's settling time without learning a, emitter_warming once the big zone starts the boiler, and back to normal learning one settling time later."""
    await _alone_below_the_thresholds(r, full=True)


@scenario("QC2s", ("central-boiler",), 95, "test_smartpi_boiler_call.py, test_smartpi_boiler_algo.py", minimum=True, short_of=("QC2", "QC4"))
async def QC2s(r: Any) -> None:
    """The small zone calling alone below the thresholds goes emitter_cooling after the grace and held after its emitter's settling time without learning a, then emitter_warming once the big zone starts the boiler."""
    await _alone_below_the_thresholds(r, full=False)


@scenario("QC3", ("central-boiler",), 20, "test_smartpi_boiler_handler.py", minimum=True)
async def QC3(r: Any) -> None:
    """With the central boiler's binary sensor gone (disabled), SmartPI reads no boiler's call and learns as before, logging no error; the sensor comes back afterwards."""
    sensor = await _boiler_sensor(r)
    since = time.time()
    try:
        await r.ha.ws_cmd({"type": "config/entity_registry/update", "entity_id": sensor, "disabled_by": "user"})
        gone = await r.ha.until(lambda: _absent(r, sensor), 180.0, 5.0)
        r.check(gone is not None, f"{sensor} gone after {gone}")
        took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler") is None, 3 * RECALC_S + MARGIN_S, 10.0)
        r.check(took is not None, f"no boiler block: {path(data, 'boiler')}")
        await asyncio.sleep(5 * 60.0)
        data = await live(r, BEDROOM)
        r.check(path(data, "boiler") is None and bool(path(data, "control.phase")), f"still regulating, phase {path(data, 'control.phase')}")
        errors = log_lines(await system_log(r), since=since, loggers=("custom_components.vtherm_smartpi",), levels=("ERROR",))
        r.check(not errors, f"no error from SmartPI: {errors[:3]}")
    finally:
        await r.ha.ws_cmd({"type": "config/entity_registry/update", "entity_id": sensor, "disabled_by": None})
        back = await r.ha.until(lambda: _present(r, sensor), 240.0, 5.0)
        r.check(back is not None, f"put back: {sensor} enabled and present after {back}")
        took, data = await wait_live(r, BEDROOM, lambda d: path(d, "boiler.source") == "vtherm_central_boiler", 3 * RECALC_S + MARGIN_S, 10.0)
        r.check(took is not None, "the bedroom reads the boiler's call again")


async def _absent(r: Any, entity_id: str) -> bool:
    state = await r.ha.state(entity_id)
    return state is None or state["state"] == "unavailable"


async def _present(r: Any, entity_id: str) -> bool:
    state = await r.ha.state(entity_id)
    return bool(state) and state["state"] in ("on", "off")


@scenario("QC4", ("central-boiler",), 48 * 60, "B1 (test_smartpi_sim_boiler_call.py)")
async def QC4(r: Any) -> None:
    """Every day from 2:00 to 8:00 the big zone stays off, so the small zone calls without the boiler; after 48 h its learned a is within 0.85–1.2 of its radiator's true a."""
    true_a = role().sim["heaters"]["bedroom_radiator"]["a"]
    office = thermostat("Office")
    start = time.time()
    last = None
    while time.time() - start < 48 * 3600.0:
        night = 2 <= dt.datetime.now().hour < 8
        if night != last:
            await set_temperature(r, OFFICE, OFF_SETPOINT if night else office.setpoint)
            r.note(f"{dt.datetime.now():%Y-%m-%d %H:%M}: the office {'off' if night else 'on'}")
            last = night
        await asyncio.sleep(300.0)
    data = await live(r, BEDROOM)
    a = path(data, "model.a")
    ratio = (a or 0.0) / true_a
    r.check(a is not None and 0.85 <= ratio <= 1.2, f"the bedroom's a {a} is {ratio:.2f} of the truth {true_a}")
    errors = log_lines(await system_log(r), levels=("ERROR",))
    r.check(not errors, f"no error over the run: {errors[:3]}")
