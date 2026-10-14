"""Config and options flows driven step by step, the same way on the bench and in-process.

A flow is a script of steps: ``Menu(choice)`` picks a menu entry, ``Form(step, answers)`` fills
the form ``step`` with what it shows now (its defaults and suggested values) and the given
answers. Every step must be the one the script expects, a form's errors stop the flow, and the
flow must end by creating (or saving) the entry. So a setup script fails loudly where a form
changed, instead of creating something else.

``Transport`` is where the flows run: the bench's REST client (``setup/setup_role.py``), or Home
Assistant's flow managers in a test (``tests/test_qas_setup_flows.py``), which give the same
JSON as the REST API.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class FlowError(RuntimeError):
    """A flow went another way than its script."""


@dataclass(frozen=True)
class Menu:
    choice: str
    # a menu the flow shows only sometimes (the additional source of a zone heated by water)
    if_shown: bool = False


@dataclass(frozen=True)
class Form:
    step: str
    answers: Mapping[str, Any] = field(default_factory=dict)
    # answers a form may not have (an older plugin's form): skipped there, not an error
    optional: frozenset[str] = frozenset()
    # a step the flow shows only sometimes (a warning to confirm, a reset to choose)
    if_shown: bool = False


Step = Menu | Form


class Transport(Protocol):
    async def start(self, kind: str, handler: str) -> dict[str, Any]:
        """Start a flow: ``kind`` "config" (handler: a domain) or "options" (an entry id)."""

    async def submit(self, kind: str, flow_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Submit a step."""

    async def abort(self, kind: str, flow_id: str) -> None:
        """Abandon a flow."""


def summary(result: Mapping[str, Any]) -> str:
    kind = result.get("type")
    if kind == "menu":
        return f"menu {result.get('step_id')} {result.get('menu_options')}"
    if kind == "form":
        return (
            f"form {result.get('step_id')} errors={result.get('errors')}"
            f" fields={sorted(form_fields(result))}"
        )
    if kind == "abort":
        return f"abort {result.get('reason')}"
    return str(kind)


def form_defaults(result: Mapping[str, Any]) -> dict[str, Any]:
    """What a form shows now: each field's suggested value, or its default."""
    body: dict[str, Any] = {}
    for field_ in result.get("data_schema") or []:
        value = (field_.get("description") or {}).get("suggested_value", field_.get("default"))
        if value is not None:
            body[field_["name"]] = value
    return body


def form_fields(result: Mapping[str, Any]) -> set[str]:
    return {field_["name"] for field_ in result.get("data_schema") or []}


async def walk(
    transport: Transport,
    kind: str,
    handler: str,
    steps: Sequence[Step],
    *,
    refused_ok: bool = False,
    resume: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``steps`` and return where the flow is then, without ending it: a scenario reads a
    form's errors, defaults or an abort there. With ``refused_ok`` the last form may come back
    with errors (the flow stays open: ``transport.abort`` it). ``resume``: go on from a result
    an earlier ``walk`` returned, instead of starting a flow."""
    result = dict(resume) if resume is not None else await transport.start(kind, handler)
    for index, step in enumerate(steps):
        where = f"{kind} flow {handler}, step {index + 1} ({step})"
        if isinstance(step, Menu):
            if step.if_shown and result.get("type") != "menu":
                continue
            if result.get("type") != "menu" or step.choice not in (result.get("menu_options") or []):
                await _abort(transport, kind, result)
                raise FlowError(f"{where}: found {summary(result)}")
            result = await transport.submit(kind, result["flow_id"], {"next_step_id": step.choice})
            continue
        if result.get("type") != "form" or result.get("step_id") != step.step:
            if step.if_shown:
                continue
            await _abort(transport, kind, result)
            raise FlowError(f"{where}: found {summary(result)}")
        fields = form_fields(result)
        unknown = sorted(set(step.answers) - fields - set(step.optional))
        if unknown:
            await _abort(transport, kind, result)
            raise FlowError(f"{where}: the form has no {unknown}; it has {sorted(fields)}")
        body = form_defaults(result) | {k: v for k, v in step.answers.items() if k in fields}
        flow_id = result["flow_id"]
        result = await transport.submit(
            kind, flow_id, {k: v for k, v in body.items() if v is not None}
        )
        if result.get("type") == "form" and result.get("errors"):
            if refused_ok and index == len(steps) - 1:
                return {**result, "flow_id": result.get("flow_id") or flow_id}
            await _abort(transport, kind, {**result, "flow_id": result.get("flow_id") or flow_id})
            raise FlowError(f"{where}: refused {result['errors']}")
    return result


async def run_flow(
    transport: Transport,
    kind: str,
    handler: str,
    steps: Sequence[Step],
    *,
    end: str = "create_entry",
) -> dict[str, Any]:
    """Run ``steps`` and return the flow's last result, which must be of type ``end``
    ("create_entry", or "abort" for a flow whose point is an abort)."""
    result = await walk(transport, kind, handler, steps)
    if result.get("type") != end:
        await _abort(transport, kind, result)
        raise FlowError(f"{kind} flow {handler}: ended at {summary(result)}, not {end}")
    return result


async def _abort(transport: Transport, kind: str, result: Mapping[str, Any]) -> None:
    flow_id = result.get("flow_id")
    if flow_id and result.get("type") in ("form", "menu"):
        try:
            await transport.abort(kind, flow_id)
        except Exception:  # noqa: BLE001 — the flow's own error is the one to report
            pass
