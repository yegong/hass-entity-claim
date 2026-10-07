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

        if self._controller.actual_boolean is None:
            return "source_unavailable"
        return "in_sync" if self._controller.in_sync else "out_of_sync"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return an explainable snapshot of aggregation state."""

        source = self._controller.actual_state
        return {
            "target_entity": self._controller.target_entity_id,
            "aggregation": {"state": self._controller.state.policy.value},
            "claims": {
                requester.id: {
                    "name": requester.name,
                    "state": (
                        STATE_ON
                        if self._controller.state.get(requester.id)
                        else STATE_OFF
                    ),
                }
                for requester in self._controller.requesters
            },
            "aggregated": {
                "state": STATE_ON if self._controller.desired else STATE_OFF
            },
            "target": {"actual_state": source.state if source else None},
            "in_sync": self._controller.in_sync,
        }

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
