"""Home Assistant runtime adapter for the Entity Claim domain model."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import PENDING_COMMAND_TTL_SECONDS
from .model import (
    AggregationPolicy,
    ClaimState,
    ReconcileAction,
    Requester,
    is_conflicting_external_change,
    matches_pending_command,
    reconcile_action,
)

_LOGGER = logging.getLogger(__name__)

UpdateCallback = Callable[[], None]
TaskCreator = Callable[[Coroutine[Any, Any, None], str], object]
CancelCallback = Callable[[], None]


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """A recent source command awaiting its resulting state change."""

    desired: bool
    issued_at: datetime
    context_id: str


@dataclass(slots=True)
class EntityClaimRuntime:
    """Runtime data attached to one config entry."""

    controller: EntityClaimController
    requesters: tuple[Requester, ...]
    target_domain: str
    source_name: str
    source_device: AnyDeviceEntry | None
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
        *,
        respect_source_changes: bool,
        override_duration: timedelta,
    ) -> None:
        self.hass = hass
        self.target_entity_id = target_entity_id
        self.requesters = requesters
        self.state = ClaimState(requesters, policy)
        self._task_creator = task_creator
        self._callbacks: set[UpdateCallback] = set()
        self._reconcile_lock = asyncio.Lock()
        self._respect_source_changes = respect_source_changes
        self._override_duration = override_duration
        self._override_until: datetime | None = None
        self._override_reason: str | None = None
        self._cancel_override_timer: CancelCallback | None = None
        self._pending_command: PendingCommand | None = None
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

    @property
    def respect_source_changes(self) -> bool:
        """Return whether conflicting external changes start an override."""

        return self._respect_source_changes

    @property
    def override_active(self) -> bool:
        """Return whether automatic reconciliation is currently suppressed."""

        return (
            self._override_until is not None
            and datetime.now(UTC) < self._override_until
        )

    @property
    def override_until(self) -> datetime | None:
        """Return the current override deadline, if any."""

        return self._override_until if self.override_active else None

    @property
    def override_reason(self) -> str | None:
        """Return the current override reason, if active."""

        return self._override_reason if self.override_active else None

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
        if self._cancel_override_timer is not None:
            self._cancel_override_timer()
            self._cancel_override_timer = None
        self._pending_command = None

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

        if not self._ready or self.override_active:
            return
        async with self._reconcile_lock:
            # Override may have started while this coroutine waited for a
            # previous source command to finish.
            if self.override_active:
                return
            action = reconcile_action(self.desired, self.actual_boolean)
            if action is None:
                return
            service = (
                SERVICE_TURN_ON
                if action is ReconcileAction.TURN_ON
                else SERVICE_TURN_OFF
            )
            domain = self.target_entity_id.partition(".")[0]
            desired = self.desired
            context = Context()
            self._pending_command = PendingCommand(
                desired=desired,
                issued_at=datetime.now(UTC),
                context_id=context.id,
            )
            try:
                await self.hass.services.async_call(
                    domain,
                    service,
                    {ATTR_ENTITY_ID: self.target_entity_id},
                    blocking=True,
                    context=context,
                )
            except HomeAssistantError:
                self._pending_command = None
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
        """Classify a source change, update override, then reconcile."""

        self._notify()
        if not self._ready:
            return

        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        actual = self._boolean_state(new_state)
        if actual is None:
            # unknown/unavailable does not express external intent. Reconcile
            # is deferred until an explicit state is reported again.
            return

        source_recovered = self._boolean_state(old_state) is None
        self_induced = self._consume_matching_pending(event, actual)
        if self._respect_source_changes and is_conflicting_external_change(
            self.desired,
            actual,
            self_induced=self_induced,
            source_recovered=source_recovered,
        ):
            self._start_or_refresh_override()
            return

        if not self.override_active:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims for {self.target_entity_id}",
            )

    @staticmethod
    def _boolean_state(state: State | None) -> bool | None:
        """Map an HA state to explicit Boolean state."""

        if state is None:
            return None
        if state.state == STATE_ON:
            return True
        if state.state == STATE_OFF:
            return False
        return None

    @callback
    def _consume_matching_pending(
        self, event: Event[Any], actual: bool
    ) -> bool:
        """Return whether a state change matches our pending source command."""

        pending = self._pending_command
        if pending is None:
            return False

        age = datetime.now(UTC) - pending.issued_at
        if age > timedelta(seconds=PENDING_COMMAND_TTL_SECONDS):
            self._pending_command = None
            return False

        # Context is authoritative. Matching desired state is a constrained,
        # short-lived fallback for device integrations which discard Context.
        context = event.context
        if not matches_pending_command(
            actual,
            pending.desired,
            context.id,
            context.parent_id,
            pending.context_id,
        ):
            return False

        self._pending_command = None
        return True

    @callback
    def _start_or_refresh_override(self) -> None:
        """Suppress reconciliation until duration after the latest conflict."""

        now = datetime.now(UTC)
        self._override_until = now + self._override_duration
        self._override_reason = "external_source_change"
        if self._cancel_override_timer is not None:
            self._cancel_override_timer()
        self._cancel_override_timer = async_call_later(
            self.hass,
            self._override_duration.total_seconds(),
            self._handle_override_expired,
        )
        self._notify()

    @callback
    def _handle_override_expired(self, _now: datetime) -> None:
        """Release control suppression and reconcile the latest desired state."""

        self._cancel_override_timer = None
        self._override_until = None
        self._override_reason = None
        self._notify()
        if self._ready:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims after override for {self.target_entity_id}",
            )

    @callback
    def _notify(self) -> None:
        """Notify all entity adapters that derived state changed."""

        for update_callback in tuple(self._callbacks):
            update_callback()
