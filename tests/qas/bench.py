"""The bench's side of the pack: its ``lib/`` and the API of one test instance.

The bench (``/home/ai/projects/ha-testbed``, its docs/test-pack.md) runs the pack from its own
directory: ``HA_INSTANCE=N python <stage>/tests/qas/run.py …``. Its ``lib/`` (``ha_client``,
``harness``) is found under ``TESTBED_ROOT``, or the current directory. ``lib/ha_client`` reads the
bench's ``local.env`` when it is imported, so it is imported only here, when a run starts.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time
from collections.abc import Mapping
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
for path in (HERE, HERE / "setup"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def bench_lib() -> pathlib.Path:
    root = pathlib.Path(os.environ.get("TESTBED_ROOT") or os.getcwd())
    lib = root / "lib"
    if not (lib / "harness.py").is_file():
        raise SystemExit(f"no bench lib/ under {root}: run from the bench, or set TESTBED_ROOT")
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    return lib


def instance() -> int:
    return int(os.environ.get("HA_INSTANCE", "1"))


class RestTransport:
    """Config and options flows through Home Assistant's REST API."""

    def __init__(self, ha: Any) -> None:
        self.ha = ha

    @staticmethod
    def _base(kind: str) -> str:
        return "/api/config/config_entries/" + ("flow" if kind == "config" else "options/flow")

    async def start(self, kind: str, handler: str) -> dict[str, Any]:
        status, body = await self.ha.rest(
            "POST", self._base(kind), {"handler": handler, "show_advanced_options": True}
        )
        if status >= 300:
            raise RuntimeError(f"{kind} flow {handler}: HTTP {status} {str(body)[:200]}")
        return body

    async def submit(self, kind: str, flow_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        status, body = await self.ha.rest("POST", f"{self._base(kind)}/{flow_id}", dict(data))
        if status == 400 and isinstance(body, dict):
            return {"type": "form", "flow_id": flow_id, "step_id": "?", "errors": body.get("errors") or body}
        if status >= 300:
            raise RuntimeError(f"{kind} flow step: HTTP {status} {str(body)[:200]}")
        return body

    async def abort(self, kind: str, flow_id: str) -> None:
        await self.ha.rest("DELETE", f"{self._base(kind)}/{flow_id}")


class BenchApi:
    """One test instance, for the setup stages and the scenarios."""

    def __init__(self, ha: Any) -> None:
        self.ha = ha
        self.transport = RestTransport(ha)

    async def entries(self) -> list[dict[str, Any]]:
        _status, body = await self.ha.rest("GET", "/api/config/config_entries/entry")
        return [
            {"entry_id": e["entry_id"], "domain": e["domain"], "title": e["title"], "state": e.get("state")}
            for e in body
        ]

    async def entity_registry(self) -> list[dict[str, Any]]:
        entities = await self.ha.ws_cmd({"type": "config/entity_registry/list"})
        devices = {d["id"]: d for d in await self.ha.ws_cmd({"type": "config/device_registry/list"})}
        out = []
        for e in entities:
            device = devices.get(e.get("device_id") or "")
            out.append(
                {
                    "entity_id": e["entity_id"],
                    "id": e.get("id"),
                    "unique_id": e.get("unique_id"),
                    "platform": e.get("platform"),
                    "area_id": e.get("area_id") or (device or {}).get("area_id"),
                    "device_id": e.get("device_id"),
                    "config_entry_id": e.get("config_entry_id"),
                    "disabled_by": e.get("disabled_by"),
                }
            )
        return out

    async def areas(self) -> list[dict[str, Any]]:
        return [
            {"area_id": a["area_id"], "name": a["name"]}
            for a in await self.ha.ws_cmd({"type": "config/area_registry/list"})
        ]

    async def create_area(self, name: str) -> str:
        return (await self.ha.ws_cmd({"type": "config/area_registry/create", "name": name}))["area_id"]

    async def set_area(self, entity_id: str, area_id: str, device_id: str | None) -> None:
        if device_id:
            await self.ha.ws_cmd(
                {"type": "config/device_registry/update", "device_id": device_id, "area_id": area_id}
            )
        else:
            await self.ha.ws_cmd(
                {"type": "config/entity_registry/update", "entity_id": entity_id, "area_id": area_id}
            )

    async def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        status, body = await self.ha.rest("POST", f"/api/services/{domain}/{service}", dict(data))
        if status >= 300:
            raise RuntimeError(f"{domain}.{service}: HTTP {status} {str(body)[:200]}")

    async def respond(self, domain: str, service: str, data: Mapping[str, Any]) -> Any:
        """A service call that returns a response."""
        status, body = await self.ha.rest(
            "POST", f"/api/services/{domain}/{service}?return_response", dict(data)
        )
        if status >= 300:
            raise RuntimeError(f"{domain}.{service}: HTTP {status} {str(body)[:200]}")
        return body.get("service_response") if isinstance(body, dict) else body

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        return await self.ha.state(entity_id)

    async def settle(self, entity_ids: list[str], within_s: float) -> list[str]:
        deadline = time.time() + within_s
        while True:
            missing = [e for e in entity_ids if await self.ha.state(e) is None]
            if not missing or time.time() >= deadline:
                return missing
            await asyncio.sleep(2.0)

    def log(self, text: str) -> None:
        print(text, flush=True)

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
                status, _body = await self.ha.rest("GET", "/api/")
                if status == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(5.0)
        else:
            raise RuntimeError("Home Assistant did not come back after its restart")
        try:
            await self.ha.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        await self.ha.__aenter__()
        return time.time() - start
