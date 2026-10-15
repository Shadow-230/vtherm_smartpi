"""QZ: what every run checks, on every instance (specs/2026-10-14-qas-test-plan.md, §5 "In every
run")."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from stages import SMARTPI

from .common import entry, diag_entity, log_lines, role, system_log, wait_loaded
from .registry import scenario

# The thermostat is rebuilt at each reload: a zone's master with exactly its two devices is not
# reported (they are compared by unique id, the entity id is not set yet at that moment).
MASTER_WARNING = "no longer has exactly this thermostat"
# A thermostat's own entry takes its diagnostic sensor over from the defaults entry: Home Assistant
# refuses no duplicate unique ID.
DUPLICATE_ID = "does not generate unique IDs"

# New interface texts of the fork: shown in Polish they must read as text, not as raw keys
# (Home Assistant falls back to English where a language has no text of its own).
TEXT_KEYS = (
    "component.vtherm_smartpi.options.step.thermostat_menu.menu_options.settings",
    "component.vtherm_smartpi.options.step.thermostat_menu.menu_options.zone",
    "component.vtherm_smartpi.options.step.thermostat_menu.menu_options.dependencies",
    "component.vtherm_smartpi.options.step.thermostat_menu.menu_options.save",
    "component.vtherm_smartpi.options.step.thermostat_menu.description",
    "component.vtherm_smartpi.options.step.settings.data.smart_pi_use_defaults",
    "component.vtherm_smartpi.options.step.model_reset.description",
    "component.vtherm_smartpi.options.step.neighbour_details.data.mode",
    "component.vtherm_smartpi.options.step.zone_master.title",
)


async def _updates(r: Any, entity_id: str, seconds: float) -> int:
    """How many times an entity is written in ``seconds``."""
    count, last = 0, None
    start = time.time()
    while time.time() - start < seconds:
        updated = (await r.ha.state(entity_id) or {}).get("last_updated")
        if updated is not None and updated != last:
            count += last is not None
            last = updated
        await asyncio.sleep(2.0)
    return count


@scenario("QZ1", ("base", "neighbours", "zone", "zone-ref", "zone-valve", "central-boiler", "ref-0.4.0", "electric", "water"), 25, "—", minimum=True)
async def QZ1(r: Any) -> None:
    """SmartPI logs no error and each of its warnings is listed, the recorder refuses none of the diagnostics' attributes, the forms read in Polish show text and not keys, and after three reloads of an entry its thermostat does not calculate twice, no zone's master is reported as changed and no diagnostic sensor is refused as a duplicate."""
    entries = await system_log(r)
    errors = log_lines(entries, loggers=("custom_components.vtherm_smartpi",), levels=("ERROR",))
    r.check(not errors, f"no error from SmartPI: {len(errors)}")
    for line in errors[:10] + log_lines(entries, loggers=("custom_components.vtherm_smartpi",), levels=("WARNING",))[:10]:
        r.note(line)
    too_large = log_lines(entries, loggers=("homeassistant.components.recorder",), levels=("WARNING", "ERROR"), contains="attributes")
    r.check(not too_large, f"the recorder keeps the diagnostics' attributes: {too_large[:2]}")

    if role().plugin == "qas":
        for category in ("options",):
            texts = await r.ha.ws_cmd(
                {"type": "frontend/get_translations", "language": "pl", "category": category, "integration": [SMARTPI]}
            )
            resources = (texts or {}).get("resources") or {}
            missing = [key for key in TEXT_KEYS if not str(resources.get(key) or "").strip()]
            r.check(not missing, f"Polish {category} texts present (in English where none is Polish): missing {missing}")

    thermostat = next(t for t in role().thermostats if t.smartpi)
    diag = await diag_entity(r, thermostat.climate)
    before = await _updates(r, diag, 300.0)
    found = await entry(r, SMARTPI, thermostat.name)
    since = time.time()
    for _ in range(3):
        await r.ha.rest("POST", f"/api/config/config_entries/entry/{found['entry_id']}/reload")
        await asyncio.sleep(20.0)
    r.check(await wait_loaded(r, [thermostat.climate]), f"{thermostat.name} is back after three reloads")
    after = await _updates(r, diag, 300.0)
    r.check(after <= 1.5 * before + 2, f"{thermostat.name}'s diagnostics written {before} times in 5 minutes before the reloads, {after} after")
    reloaded = await system_log(r)
    master = log_lines(reloaded, since, ("custom_components.vtherm_smartpi",), ("WARNING",), MASTER_WARNING)
    r.check(not master, f"no zone's master reported as changed after the reloads: {master[:2]}")
    duplicates = [
        line
        for line in log_lines(reloaded, since, ("homeassistant.components.sensor",), ("ERROR",), DUPLICATE_ID)
        if "smartpi_diag_" in line
    ]
    r.check(not duplicates, f"no SmartPI diagnostic sensor refused as a duplicate after the reloads: {duplicates[:2]}")
