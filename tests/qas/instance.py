"""One test instance through Home Assistant's API, for the setup and the scenarios.

The bench runs the pack from its own directory; its ``lib/`` (``ha_client``, ``harness``) is
found under ``TESTBED_ROOT`` or the current directory (``bench_lib``). ``Instance`` wraps the
bench's client; a test in-process gives the same methods over Home Assistant itself.

``run_flow`` drives a config flow through a list of steps: ``("menu", choice)`` picks a menu
entry, ``("form", step_id, answers)`` submits the form with what it shows and the answers. A step
that is not the one expected, an answer the form does not have, a refusal, or a flow that does
not create its entry stops it with ``FlowError``: a setup never creates something else quietly.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

Step = tuple[Any, ...]


class FlowError(RuntimeError):
    """A config flow went another way than its steps."""


class Flows(Protocol):
    async def flow_start(self, handler: str) -> dict[str, Any]: ...

    async def flow_submit(self, flow_id: str, data: Mapping[str, Any]) -> dict[str, Any]: ...

    async def flow_abort(self, flow_id: str) -> None: ...


def _shown(result: Mapping[str, Any]) -> dict[str, Any]:
    """What a form shows: each field's suggested value, or its default."""
    shown: dict[str, Any] = {}
    for field in result.get("data_schema") or []:
        value = (field.get("description") or {}).get("suggested_value", field.get("default"))
        if value is not None:
            shown[field["name"]] = value
    return shown


def _where(result: Mapping[str, Any]) -> str:
    return f"{result.get('type')} {result.get('step_id') or result.get('reason') or ''}".strip()


async def run_flow(flows: Flows, handler: str, steps: Sequence[Step]) -> dict[str, Any]:
    """Run a config flow of ``handler`` through ``steps``; it must create its entry."""
    result = await flows.flow_start(handler)
    try:
        for step in steps:
            kind, name = step[0], step[1]
            if result.get("type") != kind or (kind == "form" and result.get("step_id") != name):
                raise FlowError(f"{handler}: expected {kind} {name}, found {_where(result)}")
            if kind == "menu":
                if name not in (result.get("menu_options") or []):
                    raise FlowError(f"{handler}: no {name} in the menu {result.get('menu_options')}")
                result = await flows.flow_submit(result["flow_id"], {"next_step_id": name})
                continue
            answers = step[2] if len(step) > 2 else {}
            fields = {field["name"] for field in result.get("data_schema") or []}
            unknown = sorted(set(answers) - fields)
            if unknown:
                raise FlowError(f"{handler}: the form {name} has no {unknown}")
            flow_id = result["flow_id"]
            result = await flows.flow_submit(flow_id, _shown(result) | dict(answers))
            if result.get("errors"):
                result.setdefault("flow_id", flow_id)
                raise FlowError(f"{handler}: the form {name} refused {result['errors']}")
        if result.get("type") != "create_entry":
            raise FlowError(f"{handler}: ended at {_where(result)}, not at the entry's creation")
        return result
    except FlowError:
        if result.get("type") in ("form", "menu") and result.get("flow_id"):
            await flows.flow_abort(result["flow_id"])
        raise


def bench_lib() -> None:
    """Put the bench's ``lib/`` on the path."""
    root = pathlib.Path(os.environ.get("TESTBED_ROOT") or os.getcwd())
    lib = root / "lib"
    if not (lib / "harness.py").is_file():
        raise SystemExit(f"no bench lib/ under {root}: run from the bench, or set TESTBED_ROOT")
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))


class Instance:
    """A test instance, through the bench's client (``ha_client.HA``)."""

    def __init__(self, ha: Any) -> None:
        self.ha = ha

    async def _rest(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        status, data = await self.ha.rest(method, path, dict(body) if body is not None else None)
        if status >= 300 and not (status == 400 and isinstance(data, dict) and data.get("errors")):
            raise RuntimeError(f"{method} {path}: HTTP {status} {str(data)[:200]}")
        return data

    async def flow_start(self, handler: str) -> dict[str, Any]:
        return await self._rest("POST", "/api/config/config_entries/flow", {"handler": handler, "show_advanced_options": True})

    async def flow_submit(self, flow_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        return await self._rest("POST", f"/api/config/config_entries/flow/{flow_id}", data)

    async def flow_abort(self, flow_id: str) -> None:
        await self.ha.rest("DELETE", f"/api/config/config_entries/flow/{flow_id}")

    async def entries(self, domain: str) -> list[dict[str, Any]]:
        """The config entries of a domain: entry_id, title, state."""
        data = await self._rest("GET", "/api/config/config_entries/entry")
        return [e for e in data if e["domain"] == domain]

    async def entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        for entry in await self.ha.ws_cmd({"type": "config/entity_registry/list"}):
            if entry.get("platform") == platform and entry.get("unique_id") == unique_id and entry["entity_id"].startswith(f"{domain}."):
                return entry["entity_id"]
        return None

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        return await self.ha.state(entity_id)

    async def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        await self._rest("POST", f"/api/services/{domain}/{service}", data)

    async def wait_state(self, entity_id: str, within_s: float) -> bool:
        deadline = time.time() + within_s
        while time.time() < deadline:
            if await self.ha.state(entity_id) is not None:
                return True
            await asyncio.sleep(2.0)
        return False

    async def restart(self, within_s: float = 600.0) -> float:
        """Restart Home Assistant and reconnect; the seconds it took."""
        start = time.time()
        try:
            await self.ha.rest("POST", "/api/services/homeassistant/restart", {})
        except Exception:  # noqa: BLE001 — the connection may drop while it restarts
            pass
        await asyncio.sleep(20.0)
        while time.time() - start < within_s:
            try:
                status, _ = await self.ha.rest("GET", "/api/")
                if status == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(5.0)
        else:
            raise RuntimeError("Home Assistant did not come back after its restart")
        await self.ha.__aexit__(None, None, None)
        await self.ha.__aenter__()
        return time.time() - start
