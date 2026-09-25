"""Tests for __init__.py's cleanup of entities the integration no longer provides."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eolia import async_remove_retired_entities, async_remove_unsupported_entities
from custom_components.eolia.const import DOMAIN

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="


def _add(registry, entry, platform_domain, unique_suffix, name):
    return registry.async_get_or_create(
        platform_domain,
        DOMAIN,
        f"{APPLIANCE_ID}_{unique_suffix}",
        suggested_object_id=name,
        config_entry=entry,
    )


async def test_the_retired_double_temp_switch_is_removed_and_nothing_else(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ghost = _add(registry, entry, "switch", "double_temp_enabled", "yurt_double_temperature_setting")
    keepers = [
        _add(registry, entry, "switch", "nanoex", "yurt_nanoex"),
        _add(registry, entry, "number", "double_temp_low", "yurt_double_temperature_low"),
        _add(registry, entry, "climate", "climate", "yurt"),
    ]

    removed = async_remove_retired_entities(hass, entry)

    assert removed == [ghost.entity_id]
    assert registry.async_get(ghost.entity_id) is None
    assert all(registry.async_get(e.entity_id) is not None for e in keepers)


async def test_cleanup_is_a_no_op_when_there_is_nothing_to_remove(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    _add(er.async_get(hass), entry, "switch", "nanoex", "yurt_nanoex")

    assert async_remove_retired_entities(hass, entry) == []


async def test_cleanup_only_touches_this_config_entry(hass):
    mine = MockConfigEntry(domain=DOMAIN)
    other = MockConfigEntry(domain="something_else")
    mine.add_to_hass(hass)
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    foreign = registry.async_get_or_create(
        "switch", "something_else", "x_double_temp_enabled", config_entry=other
    )

    assert async_remove_retired_entities(hass, mine) == []
    assert registry.async_get(foreign.entity_id) is not None


def _coordinator(flags):
    """Just enough of the coordinator: supports() answers True for anything unknown."""
    return SimpleNamespace(
        supports=lambda appliance_id, function_id: True if flags is None else flags.get(function_id, True)
    )


async def test_the_air_quality_switch_is_removed_on_a_model_without_the_feature(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    switch = _add(registry, entry, "switch", "airquality", "yurt_air_quality_monitoring")
    keeper = _add(registry, entry, "switch", "nanoex", "yurt_nanoex")

    removed = async_remove_unsupported_entities(hass, entry, _coordinator({"airquality": False}))

    assert removed == [switch.entity_id]
    assert registry.async_get(switch.entity_id) is None
    assert registry.async_get(keeper.entity_id) is not None


async def test_the_air_quality_switch_is_kept_on_a_model_that_has_it(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    switch = _add(registry, entry, "switch", "airquality", "yurt_air_quality_monitoring")

    assert async_remove_unsupported_entities(hass, entry, _coordinator({"airquality": True})) == []
    assert registry.async_get(switch.entity_id) is not None


async def test_unknown_capabilities_never_delete_anything(hass):
    # A failed /functions fetch leaves the flags unknown; supports() then says True.
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    switch = _add(registry, entry, "switch", "airquality", "yurt_air_quality_monitoring")

    assert async_remove_unsupported_entities(hass, entry, _coordinator(None)) == []
    assert registry.async_get(switch.entity_id) is not None
