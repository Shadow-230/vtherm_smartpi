"""The handler reads configured dependencies from Home Assistant states."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_DEPENDENCIES,
    CONF_TARGET_VTHERM,
    DEFAULT_OPTIONS,
    DOMAIN,
    SHARED_SETTINGS,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.settings import own_entry

from fakes.fake_thermostat_runtime import FakeThermostatRuntime

VT_DOMAIN = "versatile_thermostat"


def _hall_attributes(temperature=21.5, window="off"):
    return {
        "current_temperature": temperature,
        "safety_manager": {"safety_state": "off"},
        "window_manager": {"window_state": window, "window_auto_state": "off"},
    }


def _set_options(hass, thermostat, options: dict) -> None:
    """The thermostat's own SmartPI entry (with its own shared settings) holds ``options``."""
    full = {
        CONF_TARGET_VTHERM: thermostat.unique_id,
        **{key: DEFAULT_OPTIONS[key] for key in SHARED_SETTINGS},
        **options,
    }
    entry = own_entry(hass, thermostat.unique_id)
    if entry is None:
        MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}-{thermostat.unique_id}",
            data={CONF_TARGET_VTHERM: thermostat.unique_id},
            options=full,
        ).add_to_hass(hass)
    else:
        hass.config_entries.async_update_entry(entry, options=full)


def _handler(hass, dependencies) -> tuple[SmartPIHandler, FakeThermostatRuntime]:
    thermostat = FakeThermostatRuntime()
    thermostat.hass = hass
    _set_options(hass, thermostat, {CONF_SMART_PI_DEPENDENCIES: dependencies})
    handler = SmartPIHandler(thermostat)
    handler.init_algorithm()
    return handler, thermostat


def _readings(thermostat):
    return thermostat.prop_algorithm._known_inputs_provider()


def _neighbour(thermostat):
    (observation,) = _readings(thermostat).neighbours
    return observation


async def test_without_dependencies_no_provider_is_registered(hass) -> None:
    _, thermostat = _handler(hass, [])

    assert thermostat.prop_algorithm._known_inputs_provider is None


async def test_neighbour_is_read_through_its_registry_id(hass) -> None:
    """The link survives an entity id change and follows the door sensor."""
    registry = er.async_get(hass)
    hall = registry.async_get_or_create("climate", VT_DOMAIN, "vt-hall", suggested_object_id="hall")
    door = registry.async_get_or_create("binary_sensor", "test", "door-1", suggested_object_id="hall_door")
    hass.states.async_set("climate.hall", "heat", _hall_attributes(window="on"))
    hass.states.async_set("binary_sensor.hall_door", "off")
    _, thermostat = _handler(
        hass,
        [
            {
                "id": "n1",
                "kind": "neighbour",
                "entity": hall.id,
                "mode": "door",
                "door_sensors": [door.id],
                "neighbour_window_action": "nothing",
            }
        ],
    )

    observation = _neighbour(thermostat)
    assert observation.item.item_id == "n1"
    assert observation.reading.entity_id == "climate.hall"
    assert observation.reading.temperature == 21.5
    assert observation.reading.window_open
    assert observation.reading.usable
    assert not observation.door_open
    assert not observation.connected

    hass.states.async_set("binary_sensor.hall_door", "on")
    assert _neighbour(thermostat).connected

    registry.async_update_entity("climate.hall", new_entity_id="climate.stair_hall")
    hass.states.async_set("climate.stair_hall", "heat", _hall_attributes(temperature=19.0))
    observation = _neighbour(thermostat)
    assert observation.reading.entity_id == "climate.stair_hall"
    assert observation.reading.temperature == 19.0


async def test_neighbour_appearing_later_becomes_available(hass) -> None:
    """A neighbour that starts after the zone is unavailable until its state exists."""
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create("sensor", "test", "hall-temp", suggested_object_id="hall_temperature")
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": sensor.id, "mode": "always"}],
    )
    assert not _neighbour(thermostat).reading.usable

    hass.states.async_set("sensor.hall_temperature", "18.2")
    observation = _neighbour(thermostat)
    assert observation.reading.usable
    assert observation.connected
    assert observation.reading.temperature == 18.2


async def test_unknown_registry_id_has_no_influence(hass) -> None:
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "always"}],
    )
    observation = _neighbour(thermostat)

    assert observation.reading.entity_id is None
    assert not observation.reading.usable
    assert not observation.connected


async def test_state_of_removed_items_is_dropped_at_startup(hass) -> None:
    handler, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "none"}],
    )
    state = thermostat.prop_algorithm.dependency_state
    state.for_item("n1")["k"] = 0.001
    state.for_item("gone")["k"] = 0.002

    await handler.async_added_to_hass()

    assert set(state.items) == {"n1"}


async def test_zone_presets_are_read_from_the_thermostat_state(hass) -> None:
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "none"}],
    )
    hass.states.async_set(
        thermostat.entity_id,
        "heat",
        {
            "preset_temperatures": {"frost_temp": 7.0, "eco_temp": 17.5, "boost_temp": "n/a"},
            "min_temp": 7.0,
            "max_temp": 30.0,
        },
    )

    readings = _readings(thermostat)
    assert readings.presets == {"frost_temp": 7.0, "eco_temp": 17.5}
    assert readings.temp_range == (7.0, 30.0)


def _source_item(entity: str) -> dict:
    return {
        "id": "s1",
        "kind": "source",
        "entity": entity,
        "split_rule": "lead_band",
        "lead_band_percent": 20,
        "lead_emitter": "radiator",
        "source_emitter": "floor_water",
    }


async def test_the_source_is_attached_through_its_registry_id(hass, monkeypatch) -> None:
    """The lead finds its source by registry id, publishes its command, and reads its cycles."""
    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    handler, thermostat = _handler(hass, [_source_item(floor.id)])
    link = handler._source_link()

    assert link.lead_of("vt-floor") == thermostat.unique_id
    assert thermostat.prop_algorithm.dependency_model.has_source

    thermostat.vtherm_hvac_mode = "heat"
    monkeypatch.setattr(type(thermostat.prop_algorithm), "source_command", property(lambda _: 0.4))
    handler._publish_source_command()
    now_s = __import__("time").monotonic()
    assert link.command_for("vt-floor", now_s, 900.0, "heat") == 0.4
    assert not link.lead_silent("vt-floor", now_s + 899.0, 900.0)
    observation = _readings(thermostat).source
    assert (observation.item_id, observation.active, observation.cycles) == ("s1", False, ())

    link.set_active("vt-floor", True, __import__("time").monotonic())
    observation = _readings(thermostat).source
    assert observation.active
    assert observation.entity_id == "climate.floor"


async def test_a_source_that_cannot_be_found_or_already_works_elsewhere_is_not_attached(hass) -> None:
    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    missing, _ = _handler(hass, [_source_item("0123456789abcdef")])
    assert missing._source_unique_id is None

    first, first_thermostat = _handler(hass, [_source_item(floor.id)])
    second, _ = _handler(hass, [_source_item(floor.id)])
    second._thermostat.unique_id = "other-lead"
    second._attach_source()

    assert second._source_unique_id is None
    assert first._source_link().lead_of("vt-floor") == first_thermostat.unique_id


async def test_the_zone_reads_both_heating_types_from_the_entries(hass) -> None:
    """The source item's emitters follow the heating types set in the two thermostats' entries."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.vtherm_smartpi.const import (
        CONF_SMART_PI_HEATING_TYPE,
        CONF_TARGET_VTHERM,
        DEFAULT_OPTIONS,
        DOMAIN,
    )

    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-vt-floor",
        data={CONF_TARGET_VTHERM: "vt-floor", **DEFAULT_OPTIONS},
        options={CONF_TARGET_VTHERM: "vt-floor", **DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: "floor_electric"},
    ).add_to_hass(hass)
    stale = {**_source_item(floor.id), "lead_emitter": "floor_water", "source_emitter": "radiator"}
    thermostat = FakeThermostatRuntime()
    thermostat.hass = hass
    _set_options(hass, thermostat, {CONF_SMART_PI_DEPENDENCIES: [stale], CONF_SMART_PI_HEATING_TYPE: "electric_fast"})
    handler = SmartPIHandler(thermostat)
    handler.init_algorithm()

    settings = handler._source_settings
    assert (settings.lead_emitter, settings.source_emitter) == ("electric_fast", "floor_electric")

    custom = {**_source_item(floor.id), "source_emitter": "custom", "source_lag_min": 40, "source_tau_min": 200}
    _set_options(hass, thermostat, {CONF_SMART_PI_DEPENDENCIES: [custom]})
    handler.init_algorithm()
    assert handler._source_settings.source_emitter == "custom"
    assert handler._source_settings.source.lag_s == 2400.0


async def test_a_reset_asked_by_the_zone_form_is_applied_once(hass, monkeypatch) -> None:
    from custom_components.vtherm_smartpi.const import CONF_SMART_PI_RESET_TOKEN

    handler, thermostat = _handler(hass, [])
    algo = thermostat.prop_algorithm
    resets = []
    monkeypatch.setattr(algo, "reset_learning", lambda: resets.append(1))
    _set_options(hass, thermostat, {CONF_SMART_PI_RESET_TOKEN: "t1"})

    handler._apply_reset_request()
    handler._apply_reset_request()
    assert resets == [1]
    assert algo.save_state()["dependency_reset_token"] == "t1"

    _set_options(hass, thermostat, {CONF_SMART_PI_RESET_TOKEN: "t2"})
    handler._apply_reset_request()
    assert resets == [1, 1]


async def test_a_source_without_a_heating_type_of_its_own_keeps_the_zone_s(hass) -> None:
    """The defaults entry holds no heating type (it is set for one thermostat only): a source
    whose entry sets none keeps the emitter the zone form stored."""
    from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE

    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, data={**DEFAULT_OPTIONS, CONF_SMART_PI_HEATING_TYPE: "floor_water"}
    ).add_to_hass(hass)
    handler, _ = _handler(hass, [{**_source_item(floor.id), "source_emitter": "radiator"}])
    assert handler._source_settings.source_emitter == "radiator"


async def test_the_reset_token_survives_a_restart(hass, monkeypatch) -> None:
    from custom_components.vtherm_smartpi.const import CONF_SMART_PI_RESET_TOKEN

    handler, thermostat = _handler(hass, [])
    _set_options(hass, thermostat, {CONF_SMART_PI_RESET_TOKEN: "t1"})
    handler._apply_reset_request()
    saved = thermostat.prop_algorithm.save_state()

    restarted, again = _handler(hass, [])
    _set_options(hass, again, {CONF_SMART_PI_RESET_TOKEN: "t1"})
    again.prop_algorithm.load_state(saved)
    resets = []
    monkeypatch.setattr(again.prop_algorithm, "reset_learning", lambda: resets.append(1))
    restarted._apply_reset_request()
    assert resets == []


async def test_a_master_without_these_two_thermostats_is_reported(hass, caplog) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    other = registry.async_get_or_create("climate", VT_DOMAIN, "vt-other", suggested_object_id="other")
    vt_entry = MockConfigEntry(domain="test_vtherm", data={"underlying_entity_ids": [other.entity_id, floor.entity_id]})
    vt_entry.add_to_hass(hass)
    master = registry.async_get_or_create("climate", VT_DOMAIN, "vt-master", suggested_object_id="master", config_entry=vt_entry)

    _handler(hass, [{**_source_item(floor.id), "zone_master": master.id}])
    assert "no longer has exactly this thermostat" in caplog.text


def _zone_as_vtherm_builds_it(hass, devices: list[str]) -> SmartPIHandler:
    """The zone's main source, built as VTherm builds it at a reload of its entry.

    VTherm calls ``init_algorithm`` from the thermostat's constructor (``post_init``) when the
    SmartPI factory is registered: Home Assistant has not given the entity its ``entity_id`` yet,
    only the unique id is known. A full restart does not show it: SmartPI depends on VTherm, so
    VTherm builds its thermostats before SmartPI registers the factory; VTherm then creates the
    handler in ``async_startup``, after Home Assistant has started, when the entity has its id.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    registry = er.async_get(hass)
    registry.async_get_or_create("climate", VT_DOMAIN, "fake-smartpi", suggested_object_id="fake_smartpi")
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    registry.async_get_or_create("climate", VT_DOMAIN, "vt-other", suggested_object_id="other")
    vt_entry = MockConfigEntry(domain="test_vtherm", data={"underlying_entity_ids": devices})
    vt_entry.add_to_hass(hass)
    master = registry.async_get_or_create("climate", VT_DOMAIN, "vt-master", suggested_object_id="master", config_entry=vt_entry)

    thermostat = FakeThermostatRuntime()
    thermostat.hass = hass
    thermostat.entity_id = None
    _set_options(hass, thermostat, {CONF_SMART_PI_DEPENDENCIES: [{**_source_item(floor.id), "zone_master": master.id}]})
    handler = SmartPIHandler(thermostat)
    handler.init_algorithm()
    thermostat.entity_id = "climate.fake_smartpi"
    return handler


async def test_a_master_with_exactly_these_two_thermostats_is_not_reported(hass, caplog) -> None:
    """At a reload the thermostat has no entity id yet: its devices are compared by unique id."""
    _zone_as_vtherm_builds_it(hass, ["climate.floor", "climate.fake_smartpi"])
    assert "no longer has exactly this thermostat" not in caplog.text


async def test_a_master_with_another_device_is_reported_at_a_reload(hass, caplog) -> None:
    _zone_as_vtherm_builds_it(hass, ["climate.fake_smartpi", "climate.floor", "climate.other"])
    assert "no longer has exactly this thermostat" in caplog.text


async def test_a_master_without_the_source_is_reported_at_a_reload(hass, caplog) -> None:
    _zone_as_vtherm_builds_it(hass, ["climate.fake_smartpi", "climate.other"])
    assert "no longer has exactly this thermostat" in caplog.text


async def test_a_master_device_missing_from_the_registry_is_reported(hass, caplog) -> None:
    _zone_as_vtherm_builds_it(hass, ["climate.fake_smartpi", "climate.floor_removed"])
    assert "no longer has exactly this thermostat" in caplog.text


async def test_a_source_entry_change_reloads_its_zone_s_main_too(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.vtherm_smartpi import _zone_mains_of
    from custom_components.vtherm_smartpi.const import CONF_TARGET_VTHERM, DEFAULT_OPTIONS, DOMAIN

    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}-vt-radiator",
        data={CONF_TARGET_VTHERM: "vt-radiator", **DEFAULT_OPTIONS},
        options={CONF_TARGET_VTHERM: "vt-radiator", **DEFAULT_OPTIONS, CONF_SMART_PI_DEPENDENCIES: [_source_item(floor.id)]},
    ).add_to_hass(hass)

    assert _zone_mains_of(hass, "vt-floor") == ["vt-radiator"]
    assert _zone_mains_of(hass, "vt-radiator") == []


async def test_a_thermostat_range_that_is_not_a_range_is_not_used(hass) -> None:
    _, thermostat = _handler(
        hass,
        [{"id": "n1", "kind": "neighbour", "entity": "0123456789abcdef", "mode": "none"}],
    )
    hass.states.async_set(thermostat.entity_id, "heat", {"min_temp": 25.0, "max_temp": 20.0})

    assert _readings(thermostat).temp_range is None


async def test_a_second_source_turned_off_turns_its_device_off(hass) -> None:
    """Off while its device still runs: the device is turned off, nothing left on in the cycle."""
    handler, thermostat = _handler(hass, [])
    thermostat.vtherm_hvac_mode = "off"
    thermostat.is_device_active = True

    await handler._control_as_source(False)

    thermostat.async_underlying_entity_turn_off.assert_awaited_once()
    assert thermostat._on_time_sec == 0


async def test_a_source_that_cannot_be_resolved_has_no_heating_type(hass, monkeypatch) -> None:
    handler, _ = _handler(hass, [])

    def _fail(_registry_id):
        raise RuntimeError("registry not ready")

    monkeypatch.setattr(handler, "_resolve_unique_id", _fail)

    assert handler._source_heating_type("0123456789abcdef") is None


async def test_a_zone_whose_master_was_deleted_is_reported(hass, caplog) -> None:
    """The master thermostat removed from Home Assistant while the zone still names it."""
    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")

    _handler(hass, [{**_source_item(floor.id), "zone_master": "0123456789abcdef"}])

    assert "The zone's master thermostat cannot be found" in caplog.text


async def test_the_zone_s_recovery_follows_the_regulator_settings_unless_it_has_its_own(hass) -> None:
    """The thermostat's shared settings (here its own) give the recovery; the zone's own wins."""
    from custom_components.vtherm_smartpi.const import CONF_SMART_PI_ZONE_RECOVERY

    registry = er.async_get(hass)
    floor = registry.async_get_or_create("climate", VT_DOMAIN, "vt-floor", suggested_object_id="floor")
    thermostat = FakeThermostatRuntime()
    thermostat.hass = hass
    _set_options(hass, thermostat, {CONF_SMART_PI_DEPENDENCIES: [_source_item(floor.id)], CONF_SMART_PI_ZONE_RECOVERY: "economy"})
    handler = SmartPIHandler(thermostat)
    handler.init_algorithm()
    assert handler._source_settings.recovery == "economy"

    own = {**_source_item(floor.id), "recovery": "fast"}
    _set_options(hass, thermostat, {CONF_SMART_PI_DEPENDENCIES: [own], CONF_SMART_PI_ZONE_RECOVERY: "economy"})
    handler.init_algorithm()
    assert handler._source_settings.recovery == "fast"
