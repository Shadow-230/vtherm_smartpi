"""What the scenarios share: the role, SmartPI's timing, the thermostats' diagnostics, waits, the
log, the simulator and the thermostats.

Waits follow SmartPI's own timing, read from the staged plugin (``smartpi/const.py``, which
imports only the standard library): a scenario waits for what the plugin does after its own
delays, plus a margin, never a guess.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from bench import BenchApi
from roles import CYCLE_MIN, ROLES, SIM, Role
from stages import SMARTPI

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

RECALC_S = float(SPC.SMARTPI_RECALC_INTERVAL_SEC)
CYCLE_S = CYCLE_MIN * 60.0
MARGIN_S = 2 * RECALC_S + 15.0


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
        try:
            last = await live(r, climate)
        except LookupError:
            last = {}
        if last and predicate(last):
            return time.time() - start, last
        await asyncio.sleep(poll_s)
    return None, last


async def wait_loaded(r: Any, climates: Sequence[str], within_s: float = 300.0) -> bool:
    """The thermostats and their SmartPI diagnostics are there (after a restart or a reload)."""
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
        out.append(f"{entry.get('level')} {name}: {message[:200]}  (x{entry.get('count', 1)})")
    return out


# --- thermostats and the simulator ------------------------------------------------------------


async def set_temperature(r: Any, climate: str, value: float) -> None:
    await api(r).call("climate", "set_temperature", {"entity_id": climate, "temperature": value})


async def set_hvac(r: Any, climate: str, mode: str) -> None:
    await api(r).call("climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": mode})


async def sim(r: Any, service: str, **data: Any) -> None:
    await api(r).call(SIM, service, data)


async def put_back_thermostats(r: Any) -> None:
    """Every thermostat heating, at its base setpoint."""
    for thermostat in role().thermostats:
        state = await r.ha.state(thermostat.climate)
        if state is None:
            continue
        if state["state"] != "heat":
            await set_hvac(r, thermostat.climate, "heat")
        if state["attributes"].get("temperature") != thermostat.setpoint:
            await set_temperature(r, thermostat.climate, thermostat.setpoint)


async def reset(r: Any) -> None:
    """Between scenarios: the simulator's outdoors back to the configuration (temperatures,
    heaters and boiler go on), the thermostats heating at their base setpoint."""
    await sim(r, "reset", rooms=False)
    await put_back_thermostats(r)
