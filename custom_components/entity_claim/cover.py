"""Cover-domain Claim Entity adapter."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up Cover Claim Entities."""

    runtime: EntityClaimRuntime = entry.runtime_data
    async_add_entities(
        ClaimCoverEntity(
            runtime.controller,
            entry.entry_id,
            requester,
            runtime.source_name,
            runtime.source_device,
            restore_state=requester.id in runtime.restore_requester_ids,
        )
        for requester in runtime.requesters
    )


class ClaimCoverEntity(ClaimEntityMixin, RestoreEntity, CoverEntity):
    """An open/close Claim with optional ordered position semantics."""

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Expose only open, close, and an explicitly supported position."""

        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if self._controller.profile.supports_intermediate:
            features |= CoverEntityFeature.SET_POSITION
        return features

    @property
    def current_cover_position(self) -> int | None:
        """Return this requester's desired position when supported."""

        if not self._controller.profile.supports_intermediate:
            return None
        return self.claim_value

    @property
    def is_closed(self) -> bool:
        """Return whether this requester asks for the closed endpoint."""

        return self.claim_value == 0

    @property
    def is_opening(self) -> bool:
        """Claim Entities change desired input immediately."""

        return False

    @property
    def is_closing(self) -> bool:
        """Claim Entities change desired input immediately."""

        return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Request the fully open endpoint."""

        self._reject_extra_parameters(kwargs)
        await self.async_set_claim_value(self._controller.profile.maximum)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Request the fully closed endpoint."""

        self._reject_extra_parameters(kwargs)
        await self.async_set_claim_value(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set this requester's ordered position Claim."""

        if not self._controller.profile.supports_intermediate:
            raise ServiceValidationError(
                "The Source cover does not support position"
            )
        position = kwargs.pop(ATTR_POSITION, None)
        self._reject_extra_parameters(kwargs)
        if position is None:
            raise ServiceValidationError("Cover Claim position is required")
        await self.async_set_claim_value(position)

    @staticmethod
    def _reject_extra_parameters(kwargs: dict[str, Any]) -> None:
        """Reject Source features without defined Claim semantics."""

        if any(value is not None for value in kwargs.values()):
            raise ServiceValidationError(
                "Cover Claim does not support speed, tilt, or transition parameters"
            )

    def _restored_claim_value(self, restored: State) -> int:
        """Restore open/closed plus position from the Claim Entity state."""

        if restored.state == CoverState.CLOSED:
            return 0
        if self._controller.profile.supports_intermediate:
            position = restored.attributes.get(ATTR_CURRENT_POSITION)
            return self._controller.profile.normalize(
                self._controller.profile.maximum
                if position is None
                else position
            )
        return self._controller.profile.maximum
