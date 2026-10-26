"""Fan-domain Claim Entity adapter."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    FanEntity,
    FanEntityFeature,
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
    """A fan claim exposing ON/OFF and defined percentage semantics."""

    @property
    def supported_features(self) -> FanEntityFeature:
        """Expose percentage only when the Source supports speed control."""

        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._controller.profile.structured:
            features |= FanEntityFeature.SET_SPEED
        return features

    @property
    def percentage(self) -> int | None:
        """Return this requester's requested percentage."""

        if not self._controller.profile.structured:
            return None
        return self.claim_value

    @property
    def speed_count(self) -> int:
        """Approximate the Source percentage granularity."""

        step = max(1, round(self._controller.profile.step))
        return max(1, round(100 / step))

    async def async_set_percentage(self, percentage: int) -> None:
        """Set this requester's ordered percentage Claim."""

        if not self._controller.profile.structured:
            raise ServiceValidationError(
                "The Source fan does not support percentage"
            )
        await self.async_set_claim_value(percentage)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set an ON endpoint, optionally with an exact percentage."""

        if preset_mode is not None or any(
            value is not None for value in kwargs.values()
        ):
            raise ServiceValidationError(
                "Fan Claim supports only percentage in turn_on"
            )
        if percentage is not None and not self._controller.profile.structured:
            raise ServiceValidationError(
                "The Source fan does not support percentage"
            )
        await self.async_set_claim_value(
            self._controller.profile.maximum
            if percentage is None
            else percentage
        )

    def _restored_claim_value(self, restored: State) -> int:
        """Restore ON/OFF plus percentage from the Claim Entity state."""

        if restored.state != STATE_ON:
            return 0
        if not self._controller.profile.structured:
            return self._controller.profile.maximum
        percentage = restored.attributes.get(ATTR_PERCENTAGE)
        return self._controller.profile.normalize(
            self._controller.profile.maximum
            if percentage is None
            else percentage
        )
