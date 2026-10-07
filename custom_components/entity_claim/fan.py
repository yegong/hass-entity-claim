"""Fan-domain Claim Entity adapter."""

from __future__ import annotations

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .controller import EntityClaimRuntime
from .entity import ClaimEntityMixin


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up fan claim entities."""

    runtime: EntityClaimRuntime = entry.runtime_data
    async_add_entities(
        ClaimFanEntity(
            runtime.controller,
            entry.entry_id,
            requester,
            runtime.source_name,
            runtime.source_device,
            restore_state=requester.id in runtime.restore_requester_ids,
        )
        for requester in runtime.requesters
    )


class ClaimFanEntity(ClaimEntityMixin, RestoreEntity, FanEntity):
    """A fan claim exposing only explicitly defined Boolean semantics."""

    _attr_supported_features = (
        FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )
