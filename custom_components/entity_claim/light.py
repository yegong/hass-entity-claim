"""Light-domain Claim Entity adapter."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
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
    """A light claim exposing ON/OFF and defined brightness semantics."""

    _attr_supported_features = LightEntityFeature(0)

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Expose brightness only when the Source supports it."""

        if self._controller.profile.structured:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the Claim Entity's current logical color mode."""

        if not self.is_on:
            return None
        return (
            ColorMode.BRIGHTNESS
            if self._controller.profile.structured
            else ColorMode.ONOFF
        )

    @property
    def brightness(self) -> int | None:
        """Return this requester's requested brightness."""

        if not self._controller.profile.structured or not self.is_on:
            return None
        return self.claim_value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set an ON endpoint, optionally with an exact brightness."""

        brightness = kwargs.pop(ATTR_BRIGHTNESS, None)
        if any(value is not None for value in kwargs.values()):
            raise ServiceValidationError(
                "Light Claim supports only brightness in turn_on"
            )
        if brightness is not None and not self._controller.profile.structured:
            raise ServiceValidationError(
                "The Source light does not support brightness"
            )
        await self.async_set_claim_value(
            self._controller.profile.maximum
            if brightness is None
            else brightness
        )

    def _restored_claim_value(self, restored: State) -> int:
        """Restore ON/OFF plus brightness from the Claim Entity state."""

        if restored.state != STATE_ON:
            return 0
        if not self._controller.profile.structured:
            return self._controller.profile.maximum
        brightness = restored.attributes.get(ATTR_BRIGHTNESS)
        return self._controller.profile.normalize(
            self._controller.profile.maximum
            if brightness is None
            else brightness
        )

