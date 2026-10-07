"""Entity Registry lifecycle and predictable-ID validation."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CLAIM_UNIQUE_ID_SEPARATOR,
    DIAGNOSTIC_UNIQUE_ID_SUFFIX,
    DOMAIN,
)
from .model import Requester
from .schema import claim_entity_id, diagnostic_entity_id


def claim_unique_id(config_entry_id: str, requester_id: str) -> str:
    """Return a stable claim unique ID independent of display names."""

    return f"{config_entry_id}{CLAIM_UNIQUE_ID_SEPARATOR}{requester_id}"


def diagnostic_unique_id(config_entry_id: str) -> str:
    """Return the diagnostic entity unique ID."""

    return f"{config_entry_id}{DIAGNOSTIC_UNIQUE_ID_SUFFIX}"


def find_entity_id_conflict(
    hass: HomeAssistant,
    target_entity_id: str,
    requesters: tuple[Requester, ...],
    diagnostic_enabled: bool,
    *,
    config_entry_id: str | None = None,
) -> str | None:
    """Return the first exact generated-ID conflict, if any.

    Existing entities owned by the same stable unique ID are allowed. This
    preserves user-customized entity IDs and avoids rewriting the registry.
    """

    registry = er.async_get(hass)
    domain = target_entity_id.partition(".")[0]
    candidates: list[tuple[str, str, str | None]] = [
        (
            claim_entity_id(target_entity_id, requester.id),
            domain,
            claim_unique_id(config_entry_id, requester.id)
            if config_entry_id
            else None,
        )
        for requester in requesters
    ]
    if diagnostic_enabled:
        candidates.append(
            (
                diagnostic_entity_id(target_entity_id),
                "sensor",
                diagnostic_unique_id(config_entry_id) if config_entry_id else None,
            )
        )

    for expected_entity_id, entity_domain, unique_id in candidates:
        if unique_id and registry.async_get_entity_id(
            entity_domain, DOMAIN, unique_id
        ):
            continue
        if registry.async_get(expected_entity_id) is not None:
            return expected_entity_id
        if hass.states.get(expected_entity_id) is not None:
            return expected_entity_id
    return None


def existing_requester_ids(
    hass: HomeAssistant,
    config_entry_id: str,
    target_domain: str,
    requesters: tuple[Requester, ...],
) -> frozenset[str]:
    """Return requesters whose registry entity already exists."""

    registry = er.async_get(hass)
    return frozenset(
        requester.id
        for requester in requesters
        if registry.async_get_entity_id(
            target_domain,
            DOMAIN,
            claim_unique_id(config_entry_id, requester.id),
        )
        is not None
    )


def remove_stale_entities(
    hass: HomeAssistant,
    config_entry_id: str,
    requesters: tuple[Requester, ...],
    diagnostic_enabled: bool,
) -> None:
    """Remove registry entities no longer represented by config data."""

    desired_unique_ids = {
        claim_unique_id(config_entry_id, requester.id) for requester in requesters
    }
    if diagnostic_enabled:
        desired_unique_ids.add(diagnostic_unique_id(config_entry_id))

    registry = er.async_get(hass)
    stale_entity_ids = [
        entity.entity_id
        for entity in registry.entities.values()
        if entity.config_entry_id == config_entry_id
        and entity.platform == DOMAIN
        and entity.unique_id not in desired_unique_ids
    ]
    for entity_id in stale_entity_ids:
        registry.async_remove(entity_id)


def verify_new_entity_ids(
    hass: HomeAssistant,
    config_entry_id: str,
    target_entity_id: str,
    requesters: tuple[Requester, ...],
    preexisting_requester_ids: Iterable[str],
    *,
    diagnostic_enabled: bool,
    diagnostic_preexisting: bool,
) -> tuple[str, str | None] | None:
    """Detect a setup-time race that caused HA to add a numeric suffix."""

    registry = er.async_get(hass)
    target_domain = target_entity_id.partition(".")[0]
    preexisting = set(preexisting_requester_ids)
    for requester in requesters:
        if requester.id in preexisting:
            continue
        actual = registry.async_get_entity_id(
            target_domain,
            DOMAIN,
            claim_unique_id(config_entry_id, requester.id),
        )
        expected = claim_entity_id(target_entity_id, requester.id)
        if actual != expected:
            return expected, actual

    if diagnostic_enabled and not diagnostic_preexisting:
        actual = registry.async_get_entity_id(
            "sensor", DOMAIN, diagnostic_unique_id(config_entry_id)
        )
        expected = diagnostic_entity_id(target_entity_id)
        if actual != expected:
            return expected, actual
    return None
