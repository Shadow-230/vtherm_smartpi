"""What the scenarios share: the role, SmartPI's timing, the thermostats' diagnostics, waits, the
log, the simulator, options flows and restarts.

Waits follow SmartPI's own timing, read from the staged plugin (``smartpi/const.py``,
``smartpi/source_model.py``: both import only the standard library), as the in-process tests do:
a scenario waits for what the plugin does after its own delays, plus a margin, never a guess.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from bench import BenchApi, RestTransport
from flows import Form, Menu, run_flow
from roles import CYCLE_MIN, ROLES, SIM, Role, Thermostat, temperature_sensor
from stages import SMARTPI, VT

STAGE = pathlib.Path(__file__).resolve().parents[3]
PLUGIN = STAGE / "custom_components" / "vtherm_smartpi"


def _load(name: str, path: pathlib.Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SPC = _load("qas_smartpi_const", PLUGIN / "smartpi" / "const.py")
SOURCE_MODEL = _load("qas_smartpi_source_model", PLUGIN / "smartpi" / "source_model.py")

RECALC_S = float(SPC.SMARTPI_RECALC_INTERVAL_SEC)
CYCLE_S = CYCLE_MIN * 60.0
DOOR_SETTLING_S = float(SPC.DOOR_SETTLING_S)
BOILER_GRACE_S = float(SPC.BOILER_CALL_GRACE_S)
SOURCE_SILENT_S = float(SPC.SOURCE_COMMAND_MAX_AGE_S)
VT_WINDOW_DELAY_S = 30.0
MARGIN_S = 2 * RECALC_S + 15.0


def settle_s(heating_type: str | None) -> float:
    """An emitter's settling time as SmartPI takes it: its delay and three lags
    (smartpi/boiler_call.py ``settle_s``), from the plugin's own table."""
    table = SOURCE_MODEL.EMITTER_RESPONSE_MIN
    lag_min, tau_min = table.get(heating_type or "", table[SOURCE_MODEL.EMITTER_RADIATOR])
    return (lag_min + 3.0 * tau_min) * 60.0


def boiler_grace_s(thermostat: Thermostat) -> float:
    """The time a call has before it counts as not answered: the grace, plus the cycle for a
    switched device (VTherm counts it only while it is on)."""
    return BOILER_GRACE_S + (0.0 if thermostat.kind == "valve" else CYCLE_S)


class Context:
    role: Role = ROLES["base"]


CONTEXT = Context()


def role() -> Role:
    return CONTEXT.role


def api(r: Any) -> BenchApi:
    return BenchApi(r.ha)


# --- SmartPI's diagnostics --------------------------------------------------------------------


async def climate_uid(r: Any, climate: str) -> str:
    for entry in await api(r).entity_registry():
        if entry["entity_id"] == climate:
            return entry["unique_id"]
    raise LookupError(f"{climate} is not in the entity registry")


async def diag_entity(r: Any, climate: str) -> str:
    """SmartPI's diagnostic sensor of a thermostat (unique id smartpi_diag_<climate's>)."""
    uid = f"smartpi_diag_{await climate_uid(r, climate)}"
    for entry in await api(r).entity_registry():
        if entry["platform"] == SMARTPI and entry["unique_id"] == uid:
            return entry["entity_id"]
    raise LookupError(f"no SmartPI diagnostic sensor for {climate}")


async def live(r: Any, climate: str) -> dict[str, Any]:
    state = await r.ha.state(await diag_entity(r, climate))
    if state is None:
        return {}
    return (state.get("attributes") or {}).get("live") or {}


def path(data: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    value: Any = data
    for key in dotted.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return default
        value = value[key]
    return value


async def wait_live(
    r: Any,
    climate: str,
    predicate: Callable[[dict[str, Any]], bool],
    within_s: float,
    poll_s: float = 15.0,
) -> tuple[float | None, dict[str, Any]]:
    """Seconds until ``predicate(live)`` holds, or None after ``within_s``; and the last live."""
    start = time.time()
    last: dict[str, Any] = {}
    while time.time() - start <= within_s:
        last = await live(r, climate)
        if last and predicate(last):
            return time.time() - start, last
        await asyncio.sleep(poll_s)
    return None, last


async def sample(
    r: Any, seconds: float, every_s: float, read: Callable[[], Awaitable[Any]]
) -> list[tuple[float, Any]]:
    """``read()`` every ``every_s`` for ``seconds``: [(seconds since the start, value)]."""
    out: list[tuple[float, Any]] = []
    start = time.time()
    while True:
        out.append((time.time() - start, await read()))
        if time.time() - start + every_s > seconds:
            return out
        await asyncio.sleep(every_s)


# --- the log ----------------------------------------------------------------------------------

PLUGIN_LOGGERS = ("custom_components.vtherm_smartpi", "custom_components.versatile_thermostat")


async def system_log(r: Any) -> list[dict[str, Any]]:
    return await r.ha.ws_cmd({"type": "system_log/list"})


def log_lines(
    entries: Sequence[Mapping[str, Any]],
    since: float = 0.0,
    loggers: Sequence[str] = PLUGIN_LOGGERS,
    levels: Sequence[str] = ("ERROR", "WARNING"),
    contains: str | None = None,
) -> list[str]:
    """The log entries of these loggers and levels whose last occurrence is after ``since``
    (seconds since the epoch), as one line each."""
    out = []
    for entry in entries:
        name = str(entry.get("name", ""))
        if loggers and not name.startswith(tuple(loggers)):
            continue
        if entry.get("level") not in levels:
            continue
        if float(entry.get("timestamp", 0.0)) < since:
            continue
        message = " | ".join(entry.get("message") or [])
        if contains is not None and contains not in message:
            continue
        out.append(f"{entry.get('level')} {name}: {message[:200]} (x{entry.get('count', 1)})")
    return out


# --- thermostats and the simulator ------------------------------------------------------------


async def set_temperature(r: Any, climate: str, value: float) -> None:
    await api(r).call("climate", "set_temperature", {"entity_id": climate, "temperature": value})


async def set_hvac(r: Any, climate: str, mode: str) -> None:
    await api(r).call("climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": mode})


async def sim(r: Any, service: str, **data: Any) -> None:
    await api(r).call(SIM, service, data)


async def room_temperature(r: Any, room: str) -> float | None:
    state = await r.ha.state(temperature_sensor(room))
    try:
        return float(state["state"]) if state else None
    except (TypeError, ValueError):
        return None


async def heat(r: Any, heater: str) -> float:
    state = await r.ha.state(f"sensor.{SIM}_{heater}_heat")
    return float(state["state"]) if state and state["state"] not in ("unknown", "unavailable") else 0.0


async def put_back_thermostats(r: Any) -> None:
    """Every thermostat not driven by a master: heating, at its base setpoint."""
    driven = role().masters_devices
    for thermostat in role().thermostats:
        if thermostat.climate in driven:
            continue
        state = await r.ha.state(thermostat.climate)
        if state is None:
            continue
        if state["state"] != "heat":
            await set_hvac(r, thermostat.climate, "heat")
        if state["attributes"].get("temperature") != thermostat.setpoint:
            await set_temperature(r, thermostat.climate, thermostat.setpoint)


async def reset(r: Any) -> None:
    """Between scenarios: the simulator's inputs back to the configuration (temperatures,
    heaters and boiler go on; the boiler's state and hot water back to normal), the helpers'
    heating provider registered with nothing forced, the thermostats at their base, every zone's
    main source heard."""
    await sim(r, "reset", rooms=False)
    if role().provider:
        # The helpers' heating provider registered again, with nothing forced.
        await api(r).call("smartpi_qas_helpers", "set_provider", {"registered": True, "supply_reason": "", "call_reason": ""})
    await put_back_thermostats(r)
    if role().zone is not None:
        try:
            await api(r).call("smartpi_qas_helpers", "silence_lead", {"entity_id": role().zone.main, "silent": False})
        except RuntimeError as err:  # no link yet (the zone is starting): nothing is silenced
            r.note(f"reset: the zone's link not there yet ({str(err)[:80]})")


# --- events, entries, options and restarts ---------------------------------------------------


async def recorded_events(r: Any, clear: bool = False) -> list[dict[str, Any]]:
    response = await api(r).respond("smartpi_qas_helpers", "recorded_events", {"clear": clear})
    return (response or {}).get("events", [])


async def entry(r: Any, domain: str, title: str) -> dict[str, Any]:
    for e in await api(r).entries():
        if e["domain"] == domain and e["title"] == title:
            return e
    raise LookupError(f"no {domain} entry {title!r}")


async def options_flow(
    r: Any, title: str, steps: Sequence[Form | Menu], end: str = "create_entry"
) -> dict[str, Any]:
    """A SmartPI entry's options flow, step by step (flows.run_flow), over REST."""
    found = await entry(r, SMARTPI, title)
    result = await run_flow(RestTransport(r.ha), "options", found["entry_id"], steps, end=end)
    await asyncio.sleep(15.0)  # the entry reloads
    return result


async def stored_options(title: str) -> dict[str, Any]:
    """An entry's stored options (the bench reads its .storage)."""
    from harness import Pack, stored_options as read  # noqa: PLC0415 — the bench's lib

    stored = await asyncio.to_thread(read, Pack(domain=SMARTPI, multi_entry=True))
    return stored.get(title, {})


async def restart(r: Any) -> float:
    took = await api(r).restart()
    r.ent.update(await r.ha.entities())
    r.note(f"Home Assistant restarted in {took:.0f} s")
    return took


async def wait_loaded(r: Any, climates: Sequence[str], within_s: float = 300.0) -> bool:
    """The thermostats and their SmartPI diagnostics are back (after a restart or a reload)."""
    start = time.time()
    while time.time() - start < within_s:
        try:
            ready = True
            for climate in climates:
                state = await r.ha.state(climate)
                diag = await r.ha.state(await diag_entity(r, climate))
                ready &= bool(state) and state["state"] not in ("unavailable", "unknown")
                ready &= bool(diag) and diag["state"] not in ("unavailable",)
            if ready:
                return True
        except LookupError:
            pass
        await asyncio.sleep(10.0)
    return False


def results_dir() -> pathlib.Path:
    """Where the runner writes its results (the bench's ``results/<domain>/``)."""
    from harness import Pack, results_file  # noqa: PLC0415

    return results_file(Pack(domain=SMARTPI)).parent


def keep(name: str, data: Mapping[str, Any]) -> pathlib.Path:
    """Keep measurements for a later step or another instance (QB1, QB2, QE5)."""
    from ha_client import N  # noqa: PLC0415

    out = results_dir() / f"{name}-instance{N}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": dt.datetime.now(dt.UTC).isoformat(), **data}, indent=1), encoding="utf-8")
    return out


def kept(name: str) -> dict[str, Any] | None:
    """What this instance kept earlier (QB2 and QB3 run on the instance that ran QB2a and QB3a)."""
    from ha_client import N  # noqa: PLC0415

    found = results_dir() / f"{name}-instance{N}.json"
    return json.loads(found.read_text(encoding="utf-8")) if found.is_file() else None


def thermostat(name: str) -> Thermostat:
    return role().thermostat(name)


__all__ = [
    "BOILER_GRACE_S",
    "CYCLE_S",
    "DOOR_SETTLING_S",
    "MARGIN_S",
    "RECALC_S",
    "SOURCE_SILENT_S",
    "VT",
    "VT_WINDOW_DELAY_S",
    "Form",
    "Menu",
]
