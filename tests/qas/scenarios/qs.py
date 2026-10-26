"""QS: the instance as the setup left it (every role)."""

from __future__ import annotations

from typing import Any

from roles import ROLES
from stages import SMARTPI, VT

from .common import api, live, log_lines, path, role, system_log, wait_loaded
from .registry import scenario


@scenario(
    "QS0",
    tuple(ROLES),
    5,
    "test_qas_setup_flows.py, test_published_diagnostics_schema.py",
    minimum=True,
)
async def QS0(r: Any) -> None:
    """After the setup's restart every SmartPI and VTherm entry is loaded, neither logs a warning or an error, the only SmartPI entry is its defaults, and each thermostat has SmartPI's diagnostics with the user's learning setting on and the learning pause below the central boiler threshold published."""
    entries = await api(r).entries()
    for domain in (SMARTPI, VT):
        for e in (e for e in entries if e["domain"] == domain):
            r.check(e.get("state") == "loaded", f"{domain} entry {e['title']!r}: {e.get('state')}")
    titles = sorted(e["title"] for e in entries if e["domain"] == SMARTPI)
    r.check(titles == sorted(role().smartpi_titles), f"SmartPI entries: {titles}")
    climates = [t.climate for t in role().thermostats]
    r.check(await wait_loaded(r, climates, 300.0), f"thermostats and diagnostics loaded: {climates}")
    for climate in climates:
        data = await live(r, climate)
        r.check(bool(path(data, "control.phase")), f"{climate}: diagnostics published, phase {path(data, 'control.phase')}")
        r.check(path(data, "learning.enabled") is True, f"{climate}: learning.enabled {path(data, 'learning.enabled')}")
        r.check(
            isinstance(path(data, "learning.boiler_paused"), bool),
            f"{climate}: learning.boiler_paused published: {path(data, 'learning.boiler_paused')}",
        )
    lines = log_lines(await system_log(r))
    r.check(not lines, f"warnings and errors of SmartPI and VTherm since the restart: {len(lines)}")
    for line in lines[:12]:
        r.note(line)
