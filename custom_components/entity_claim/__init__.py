"""Entity Claim integration setup.

Home Assistant imports intentionally live inside setup functions. This keeps
the pure domain modules importable for local tests without installing HA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one source entity and its independent claims."""

    from homeassistant.config_entries import ConfigEntryError, ConfigEntryNotReady
    from homeassistant.const import ATTR_FRIENDLY_NAME, Platform
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.device import async_entity_id_to_device

    from .const import (
        CONF_AGGREGATION_POLICY,
        CONF_DIAGNOSTIC_SENSOR_ENABLED,
        CONF_REQUESTERS,
        CONF_TARGET_ENTITY_ID,
        DOMAIN,
        SUPPORTED_DOMAINS,
    )
    from .controller import EntityClaimController, EntityClaimRuntime
    from .model import AggregationPolicy
    from .registry import (
        diagnostic_unique_id,
        existing_requester_ids,
        find_entity_id_conflict,
        inherit_source_entity_area,
        remove_stale_entities,
        verify_new_entity_ids,
    )
    from .schema import requesters_from_config, split_entity_id

    target_entity_id = entry.data[CONF_TARGET_ENTITY_ID]
    target_domain, target_object_id = split_entity_id(target_entity_id)
    if target_domain not in SUPPORTED_DOMAINS:
        raise ConfigEntryError(f"Unsupported target domain: {target_domain}")

    source_state = hass.states.get(target_entity_id)
    if source_state is None:
        raise ConfigEntryNotReady(f"Target entity is not available: {target_entity_id}")

    registry = er.async_get(hass)
    source_registry_entry = registry.async_get(target_entity_id)
    if source_registry_entry is not None and source_registry_entry.platform == DOMAIN:
        raise ConfigEntryError("A claim entity cannot be used as a source target")

    requesters = requesters_from_config(entry.data[CONF_REQUESTERS])
    diagnostic_enabled = bool(entry.data[CONF_DIAGNOSTIC_SENSOR_ENABLED])
    if conflict := find_entity_id_conflict(
        hass,
        target_entity_id,
        requesters,
        diagnostic_enabled,
        config_entry_id=entry.entry_id,
    ):
        raise ConfigEntryError(f"Claim entity ID is already in use: {conflict}")

    restore_ids = existing_requester_ids(
        hass, entry.entry_id, target_domain, requesters
    )
    diagnostic_preexisting = (
        registry.async_get_entity_id(
            "sensor", DOMAIN, diagnostic_unique_id(entry.entry_id)
        )
        is not None
    )
    remove_stale_entities(
        hass, entry.entry_id, requesters, diagnostic_enabled
    )

    source_name = str(
        source_state.attributes.get(ATTR_FRIENDLY_NAME)
        or target_object_id.replace("_", " ").title()
    )
    controller = EntityClaimController(
        hass,
        target_entity_id,
        requesters,
        AggregationPolicy(entry.data[CONF_AGGREGATION_POLICY]),
        lambda coroutine, name: entry.async_create_task(
            hass, coroutine, name
        ),
    )
    entry.runtime_data = EntityClaimRuntime(
        controller=controller,
        requesters=requesters,
        target_domain=target_domain,
        source_name=source_name,
        source_device=async_entity_id_to_device(hass, target_entity_id),
        restore_requester_ids=restore_ids,
        diagnostic_enabled=diagnostic_enabled,
    )

    platforms = [Platform(target_domain)]
    if diagnostic_enabled:
        platforms.append(Platform.SENSOR)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    if raced_entity_ids := verify_new_entity_ids(
        hass,
        entry.entry_id,
        target_entity_id,
        requesters,
        restore_ids,
        diagnostic_enabled=diagnostic_enabled,
        diagnostic_preexisting=diagnostic_preexisting,
    ):
        await hass.config_entries.async_unload_platforms(entry, platforms)
        raced_entity_id, incorrectly_registered_id = raced_entity_ids
        if incorrectly_registered_id is not None:
            registry.async_remove(incorrectly_registered_id)
        raise ConfigEntryError(
            f"Claim entity ID became occupied during setup: {raced_entity_id}"
        )

    inherit_source_entity_area(hass, entry.entry_id, target_entity_id)
    await controller.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and stop reconciliation."""

    from homeassistant.const import Platform

    runtime: Any = entry.runtime_data
    await runtime.controller.async_stop()
    platforms = [Platform(runtime.target_domain)]
    # Use the platforms from the loaded runtime, not newly updated entry data.
    # During reconfigure the entry is updated before its old runtime unloads.
    if runtime.diagnostic_enabled:
        platforms.append(Platform.SENSOR)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if not unload_ok:
        await runtime.controller.async_start()
    return unload_ok
