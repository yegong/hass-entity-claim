"""Optional diagnostic sensor for one Entity Claim config entry."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import EntityClaimController, EntityClaimRuntime
from .model import ClaimKind
from .registry import diagnostic_unique_id
from .schema import diagnostic_entity_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the explicitly enabled diagnostic sensor."""

    runtime: EntityClaimRuntime = entry.runtime_data
    async_add_entities(
        [
            ClaimDiagnosticSensor(
                runtime.controller,
                entry.entry_id,
                runtime.source_name,
                runtime.source_device,
            )
        ]
    )


class ClaimDiagnosticSensor(SensorEntity):
    """Explain claims, aggregation, and source synchronization."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "diagnostics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        controller: EntityClaimController,
        config_entry_id: str,
        source_name: str,
        source_device: AnyDeviceEntry | None,
    ) -> None:
        self._controller = controller
        self.device_entry = source_device
        self._attr_unique_id = diagnostic_unique_id(config_entry_id)
        self._attr_translation_placeholders = {"source_name": source_name}
        self.entity_id = diagnostic_entity_id(controller.target_entity_id)

    @property
    def native_value(self) -> str:
        """Return a compact synchronization status."""

        if (
            self._controller.actual_value is None
            and not self._controller.source_moving
        ):
            return "source_unavailable"
        if self._controller.override_active:
            return "manual_override"
        if self._controller.source_moving:
            return "source_moving"
        return "in_sync" if self._controller.in_sync else "out_of_sync"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return an explainable snapshot of aggregation state."""

        source = self._controller.actual_state
        override_until = self._controller.override_until
        claims = {
            requester.id: self._value_snapshot(
                self._controller.state.get(requester.id),
                name=requester.name,
            )
            for requester in self._controller.requesters
        }
        aggregated = self._value_snapshot(self._controller.desired)
        target = {"actual_state": source.state if source else None}
        if (
            self._controller.actual_value is not None
            and self._controller.profile.kind is not ClaimKind.BINARY
        ):
            target[self._controller.profile.kind.value] = (
                self._controller.actual_value
            )
        if self._controller.source_moving:
            target["moving"] = True

        return {
            "target_entity": self._controller.target_entity_id,
            "aggregation": {
                "state": self._controller.state.policy.value,
                "ordered_value": (
                    "max"
                    if self._controller.state.policy.value == "any"
                    else "min"
                ),
            },
            "claims": claims,
            "aggregated": aggregated,
            "target": target,
            "manual_override": {
                "enabled": self._controller.respect_source_changes,
                "active": self._controller.override_active,
                "until": (
                    override_until.isoformat() if override_until is not None else None
                ),
                "reason": self._controller.override_reason,
            },
            "reconciliation_suppressed": self._controller.override_active,
            "in_sync": self._controller.in_sync,
        }

    def _value_snapshot(
        self, value: int, *, name: str | None = None
    ) -> dict[str, str | int]:
        """Describe one claim-space value without mixing in Source state."""

        snapshot: dict[str, str | int] = {
            "state": STATE_ON if value > 0 else STATE_OFF
        }
        if name is not None:
            snapshot["name"] = name
        if self._controller.profile.kind is not ClaimKind.BINARY:
            snapshot[self._controller.profile.kind.value] = value
        return snapshot

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._controller.subscribe(self._handle_controller_update)
        )

    @callback
    def _handle_controller_update(self) -> None:
        """Publish the latest diagnostic snapshot."""

        self.async_write_ha_state()
