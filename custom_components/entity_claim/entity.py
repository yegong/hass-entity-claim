"""Shared Boolean Claim Entity adapter."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_ON
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import AnyDeviceEntry

from .controller import EntityClaimController
from .model import Requester
from .registry import claim_unique_id
from .schema import claim_entity_id


class ClaimEntityMixin:
    """Map a domain entity's ON/OFF API to one persistent logical claim."""

    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = False
    _attr_has_entity_name = True
    _attr_translation_key = "claim"

    def __init__(
        self,
        controller: EntityClaimController,
        config_entry_id: str,
        requester: Requester,
        source_name: str,
        source_device: AnyDeviceEntry | None,
        *,
        restore_state: bool,
    ) -> None:
        self._controller = controller
        self._requester = requester
        self._restore_state = restore_state
        self.device_entry = source_device
        self._attr_unique_id = claim_unique_id(config_entry_id, requester.id)
        self._attr_translation_placeholders = {
            "source_name": source_name,
            "requester_name": requester.name,
        }
        # HA treats a preset entity_id as an exact suggested object ID on first
        # registration. The config flow has already checked this full ID.
        self.entity_id = claim_entity_id(controller.target_entity_id, requester.id)

    @property
    def is_on(self) -> bool:
        """Return only this requester's desired claim state."""

        return self._controller.state.get(self._requester.id)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose stable claim identity and target for observability."""

        return {
            "target_entity": self._controller.target_entity_id,
            "requester_id": self._requester.id,
            "requester_name": self._requester.name,
            "aggregation_policy": self._controller.state.policy.value,
        }

    async def async_added_to_hass(self) -> None:
        """Restore the claim only when this requester existed before setup."""

        await super().async_added_to_hass()  # type: ignore[misc]
        if self._restore_state:
            restored = await self.async_get_last_state()  # type: ignore[attr-defined]
            if restored is not None:
                await self._controller.async_set_claim(
                    self._requester.id,
                    restored.state == STATE_ON,
                    reconcile=False,
                )
        self.async_on_remove(  # type: ignore[attr-defined]
            self._controller.subscribe(self._handle_controller_update)
        )

    async def async_turn_on(self, *args: Any, **kwargs: Any) -> None:
        """Assert this requester's claim."""

        if any(value is not None for value in args) or any(
            value is not None for value in kwargs.values()
        ):
            raise ServiceValidationError(
                "Entity Claim currently supports only Boolean turn_on semantics"
            )
        await self._controller.async_set_claim(self._requester.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Release this requester's claim."""

        if any(value is not None for value in kwargs.values()):
            raise ServiceValidationError(
                "Entity Claim currently supports only Boolean turn_off semantics"
            )
        await self._controller.async_set_claim(self._requester.id, False)

    @callback
    def _handle_controller_update(self) -> None:
        """Publish a changed claim or aggregation attribute."""

        self.async_write_ha_state()  # type: ignore[attr-defined]
