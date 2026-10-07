"""Home Assistant runtime adapter for the Entity Claim domain model."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .model import (
    AggregationPolicy,
    ClaimState,
    ReconcileAction,
    Requester,
    reconcile_action,
)

_LOGGER = logging.getLogger(__name__)

UpdateCallback = Callable[[], None]
TaskCreator = Callable[[Coroutine[Any, Any, None], str], object]


@dataclass(slots=True)
class EntityClaimRuntime:
    """Runtime data attached to one config entry."""

    controller: EntityClaimController
    requesters: tuple[Requester, ...]
    target_domain: str
    source_name: str
    restore_requester_ids: frozenset[str]
    diagnostic_enabled: bool


class EntityClaimController:
    """Own claim state, aggregation, and source reconciliation."""

    def __init__(
        self,
        hass: HomeAssistant,
        target_entity_id: str,
        requesters: tuple[Requester, ...],
        policy: AggregationPolicy,
        task_creator: TaskCreator,
    ) -> None:
        self.hass = hass
        self.target_entity_id = target_entity_id
        self.requesters = requesters
        self.state = ClaimState(requesters, policy)
        self._task_creator = task_creator
        self._callbacks: set[UpdateCallback] = set()
        self._reconcile_lock = asyncio.Lock()
        self._ready = False
        self._unsubscribe_target: Callable[[], None] | None = None

    @property
    def desired(self) -> bool:
        """Return the aggregated desired state."""

        return self.state.desired

    @property
    def actual_state(self) -> State | None:
        """Return the canonical source state."""

        return self.hass.states.get(self.target_entity_id)

    @property
    def actual_boolean(self) -> bool | None:
        """Return explicit Boolean source state, or None when unavailable."""

        source = self.actual_state
        if source is None:
            return None
        if source.state == STATE_ON:
            return True
        if source.state == STATE_OFF:
            return False
        return None

    @property
    def in_sync(self) -> bool:
        """Return whether source actual state equals desired state."""

        actual = self.actual_boolean
        return actual is not None and actual == self.desired

    async def async_start(self) -> None:
        """Start source observation after all claim entities have restored."""

        if self._ready:
            return
        self._ready = True
        self._unsubscribe_target = async_track_state_change_event(
            self.hass, [self.target_entity_id], self._handle_target_change
        )
        self._notify()
        await self.async_reconcile()

    async def async_stop(self) -> None:
        """Stop source observation."""

        self._ready = False
        if self._unsubscribe_target is not None:
            self._unsubscribe_target()
            self._unsubscribe_target = None

    @callback
    def subscribe(self, update_callback: UpdateCallback) -> Callable[[], None]:
        """Subscribe an entity to controller updates."""

        self._callbacks.add(update_callback)

        @callback
        def unsubscribe() -> None:
            self._callbacks.discard(update_callback)

        return unsubscribe

    async def async_set_claim(
        self,
        requester_id: str,
        claimed: bool,
        *,
        reconcile: bool = True,
    ) -> None:
        """Set one requester's claim without consulting source actual state."""

        if not self.state.set(requester_id, claimed):
            return
        self._notify()
        if reconcile and self._ready:
            await self.async_reconcile()

    async def async_reconcile(self) -> None:
        """Apply the aggregated desired state to the source when needed."""

        if not self._ready:
            return
        async with self._reconcile_lock:
            action = reconcile_action(self.desired, self.actual_boolean)
            if action is None:
                return
            service = (
                SERVICE_TURN_ON
                if action is ReconcileAction.TURN_ON
                else SERVICE_TURN_OFF
            )
            domain = self.target_entity_id.partition(".")[0]
            try:
                await self.hass.services.async_call(
                    domain,
                    service,
                    {ATTR_ENTITY_ID: self.target_entity_id},
                    blocking=True,
                )
            except HomeAssistantError:
                # The claim remains authoritative even if the physical source
                # cannot currently satisfy it.
                _LOGGER.exception(
                    "Unable to reconcile %s to %s",
                    self.target_entity_id,
                    STATE_ON if self.desired else STATE_OFF,
                )
            finally:
                self._notify()

    @callback
    def _handle_target_change(self, event: Event[Any]) -> None:
        """Reconcile an external or delayed source state change."""

        self._notify()
        if self._ready:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims for {self.target_entity_id}",
            )

    @callback
    def _notify(self) -> None:
        """Notify all entity adapters that derived state changed."""

        for update_callback in tuple(self._callbacks):
            update_callback()
