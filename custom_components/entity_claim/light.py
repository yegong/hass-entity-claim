"""Light-domain Claim Entity adapter."""

from __future__ import annotations

from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
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
    """Set up light claim entities."""

    runtime: EntityClaimRuntime = entry.runtime_data
    async_add_entities(
        ClaimLightEntity(
            runtime.controller,
            entry.entry_id,
            requester,
            runtime.source_name,
            runtime.source_device,
            restore_state=requester.id in runtime.restore_requester_ids,
        )
        for requester in runtime.requesters
    )


class ClaimLightEntity(ClaimEntityMixin, RestoreEntity, LightEntity):
    """A light claim exposing only ON/OFF, without source capabilities."""

    _attr_supported_features = LightEntityFeature(0)
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

