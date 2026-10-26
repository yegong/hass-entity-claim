"""Home Assistant runtime adapter for the Entity Claim domain model."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    SERVICE_SET_PERCENTAGE,
)
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    COVER_MOVEMENT_GUARD_SECONDS,
    COVER_MOVEMENT_TTL_SECONDS,
    PENDING_COMMAND_TTL_SECONDS,
)
from .model import (
    AggregationPolicy,
    ClaimKind,
    ClaimProfile,
    ClaimState,
    Requester,
    is_conflicting_external_change,
    is_expected_position_progress,
    matches_pending_command,
    values_match,
)

_LOGGER = logging.getLogger(__name__)

UpdateCallback = Callable[[], None]
TaskCreator = Callable[[Coroutine[Any, Any, None], str], object]
CancelCallback = Callable[[], None]


@dataclass(slots=True)
class PendingCommand:
    """A recent source command awaiting its resulting state changes."""

    desired: int
    start: int | None
    last_actual: int | None
    issued_at: datetime
    context_id: str
    fallback_until: datetime
    expires_at: datetime


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
    """Own claim state, ordered aggregation, and source reconciliation."""

    def __init__(
        self,
        hass: HomeAssistant,
        target_entity_id: str,
        requesters: tuple[Requester, ...],
        policy: AggregationPolicy,
        profile: ClaimProfile,
        task_creator: TaskCreator,
        *,
        respect_source_changes: bool,
        override_duration: timedelta,
    ) -> None:
        self.hass = hass
        self.target_entity_id = target_entity_id
        self.target_domain = target_entity_id.partition(".")[0]
        self.requesters = requesters
        self.state = ClaimState(requesters, policy, profile)
        self.profile = profile
        self._task_creator = task_creator
        self._callbacks: set[UpdateCallback] = set()
        self._reconcile_lock = asyncio.Lock()
        self._respect_source_changes = respect_source_changes
        self._override_duration = override_duration
        self._override_until: datetime | None = None
        self._override_reason: str | None = None
        self._cancel_override_timer: CancelCallback | None = None
        self._pending_command: PendingCommand | None = None
        self._cancel_pending_timer: CancelCallback | None = None
        self._recent_command_contexts: dict[str, datetime] = {}
        self._source_recovery_pending = self._is_unavailable_state(
            hass.states.get(target_entity_id)
        )
        self._ready = False
        self._unsubscribe_target: Callable[[], None] | None = None

    @property
    def desired(self) -> int:
        """Return the aggregated desired value."""

        return self.state.desired

    @property
    def desired_boolean(self) -> bool:
        """Return the ON/OFF projection of the desired value."""

        return self.desired > 0

    @property
    def actual_state(self) -> State | None:
        """Return the canonical source state."""

        return self.hass.states.get(self.target_entity_id)

    @property
    def actual_value(self) -> int | None:
        """Return the Source value represented in the Claim profile."""

        return self._source_value(self.actual_state)

    @property
    def actual_boolean(self) -> bool | None:
        """Return the ON/OFF projection of the actual Source value."""

        actual = self.actual_value
        return None if actual is None else actual > 0

    @property
    def source_moving(self) -> bool:
        """Return whether a Cover is in a transient travel state."""

        return self._is_moving_state(self.actual_state)

    @property
    def in_sync(self) -> bool:
        """Return whether Source actual value satisfies desired value."""

        return values_match(
            self.desired,
            self.actual_value,
            tolerance=self.profile.tolerance,
        )

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
        self._clear_pending_command()
        self._recent_command_contexts.clear()

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
        value: int | float | bool,
        *,
        reconcile: bool = True,
    ) -> None:
        """Set one requester's claim without consulting Source actual state."""

        if not self.state.set(requester_id, value):
            return
        self._notify()
        if reconcile and self._ready:
            await self.async_reconcile()

    async def async_reconcile(self) -> None:
        """Apply the latest aggregated desired value to the Source."""

        if not self._ready or self.override_active:
            return
        async with self._reconcile_lock:
            if self.override_active:
                return

            desired = self.desired
            actual = self.actual_value
            if actual is None or values_match(
                desired, actual, tolerance=self.profile.tolerance
            ):
                return

            pending = self._active_pending_command()
            if pending is not None and pending.desired == desired:
                return
            if self.source_moving and pending is None:
                # Never fight a Cover while an uncorrelated movement is in
                # progress. Its explicit final state will be evaluated later.
                return

            self._clear_pending_command()
            service, data = self._source_command(desired)
            context = Context()
            self._remember_pending_command(desired, actual, context.id)
            try:
                await self.hass.services.async_call(
                    self.target_domain,
                    service,
                    data,
                    blocking=True,
                    context=context,
                )
            except HomeAssistantError:
                self._clear_pending_command()
                _LOGGER.exception(
                    "Unable to reconcile %s to %s",
                    self.target_entity_id,
                    desired,
                )
            finally:
                self._notify()

    def _source_command(self, desired: int) -> tuple[str, dict[str, Any]]:
        """Build the smallest domain action for the desired value."""

        data: dict[str, Any] = {ATTR_ENTITY_ID: self.target_entity_id}
        if self.target_domain == "cover":
            source = self.actual_state
            features = CoverEntityFeature(
                int(source.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
                if source is not None
                else 0
            )
            if desired == 0 and CoverEntityFeature.CLOSE in features:
                return SERVICE_CLOSE_COVER, data
            if (
                desired == self.profile.maximum
                and CoverEntityFeature.OPEN in features
            ):
                return SERVICE_OPEN_COVER, data
            data[ATTR_POSITION] = desired
            return SERVICE_SET_COVER_POSITION, data

        if desired == 0:
            return SERVICE_TURN_OFF, data
        if self.profile.kind is ClaimKind.BRIGHTNESS:
            data[ATTR_BRIGHTNESS] = desired
            return SERVICE_TURN_ON, data
        if self.profile.kind is ClaimKind.PERCENTAGE:
            data[ATTR_PERCENTAGE] = desired
            return SERVICE_SET_PERCENTAGE, data
        return SERVICE_TURN_ON, data

    @callback
    def _handle_target_change(self, event: Event[Any]) -> None:
        """Classify a Source change, update override, then reconcile."""

        self._notify()
        if not self._ready:
            return

        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if self._is_unavailable_state(new_state):
            self._source_recovery_pending = True
            return

        actual = self._source_value(new_state)
        moving = self._is_moving_state(new_state)
        self_induced, movement_in_progress = self._classify_pending_change(
            event, actual, moving
        )

        # opening/closing and expected intermediate position reports are
        # observations, not external control intent.
        if moving or movement_in_progress:
            self._notify()
            return
        if actual is None:
            return

        source_recovered = (
            self._source_recovery_pending
            or self._is_unavailable_state(old_state)
        )
        self._source_recovery_pending = False
        if self._respect_source_changes and is_conflicting_external_change(
            self.desired,
            actual,
            self_induced=self_induced,
            source_recovered=source_recovered,
            tolerance=self.profile.tolerance,
        ):
            self._start_or_refresh_override()
            return

        if not self.override_active:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims for {self.target_entity_id}",
            )

    def _source_value(self, state: State | None) -> int | None:
        """Map an HA Source state to the configured ordered value."""

        if self._is_unavailable_state(state):
            return None
        assert state is not None

        if self.target_domain == "cover":
            position = state.attributes.get(ATTR_CURRENT_POSITION)
            if position is not None and self.profile.supports_intermediate:
                return self.profile.normalize(position)
            if state.state == CoverState.CLOSED:
                return 0
            if state.state == CoverState.OPEN:
                return self.profile.maximum
            return None

        if state.state == STATE_OFF:
            return 0
        if state.state != STATE_ON:
            return None
        if self.profile.kind is ClaimKind.BRIGHTNESS:
            brightness = state.attributes.get(ATTR_BRIGHTNESS)
            return max(
                1,
                self.profile.normalize(
                    self.profile.maximum
                    if brightness is None
                    else brightness
                ),
            )
        if self.profile.kind is ClaimKind.PERCENTAGE:
            percentage = state.attributes.get(ATTR_PERCENTAGE)
            return max(
                1,
                self.profile.normalize(
                    self.profile.maximum
                    if percentage is None
                    else percentage
                ),
            )
        return self.profile.maximum

    @staticmethod
    def _is_unavailable_state(state: State | None) -> bool:
        """Return whether state contains no explicit Source observation."""

        return state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)

    def _is_moving_state(self, state: State | None) -> bool:
        """Return whether the Source is a Cover currently in motion."""

        return (
            self.target_domain == "cover"
            and state is not None
            and state.state in (CoverState.OPENING, CoverState.CLOSING)
        )

    @callback
    def _classify_pending_change(
        self,
        event: Event[Any],
        actual: int | None,
        moving: bool,
    ) -> tuple[bool, bool]:
        """Return ``(self_induced, movement_in_progress)`` for a change."""

        context = event.context
        pending = self._active_pending_command()
        if pending is None:
            return self._matches_recent_command_context(event), False

        context_matches = (
            context.id == pending.context_id
            or context.parent_id == pending.context_id
        )
        if not context_matches and self._matches_recent_command_context(event):
            return True, False

        if self.profile.kind is ClaimKind.POSITION:
            old_state: State | None = event.data.get("old_state")
            movement_stopped = (
                not moving and self._is_moving_state(old_state)
            )
            expected_progress = is_expected_position_progress(
                pending.start,
                pending.last_actual,
                actual,
                pending.desired,
                tolerance=self.profile.tolerance,
            )
            target_reached = values_match(
                pending.desired,
                actual,
                tolerance=self.profile.tolerance,
            )
            if target_reached and not moving:
                self._clear_pending_command()
                return True, False
            if expected_progress and (
                context_matches
                or moving
                or (
                    not movement_stopped
                    and datetime.now(UTC) <= pending.fallback_until
                )
            ):
                if actual is not None:
                    pending.last_actual = actual
                # Keep the no-Context guard sliding while reports continue to
                # make monotonic progress. A stationary or reversed update
                # after the quiet period is evaluated as an external change.
                pending.fallback_until = datetime.now(UTC) + timedelta(
                    seconds=COVER_MOVEMENT_GUARD_SECONDS
                )
                return True, True
            if context_matches:
                self._clear_pending_command()
                return True, False
            return False, False

        if actual is not None:
            target_reached = values_match(
                pending.desired,
                actual,
                tolerance=self.profile.tolerance,
            )
            if target_reached and matches_pending_command(
                actual,
                pending.desired,
                context.id,
                context.parent_id,
                pending.context_id,
                tolerance=self.profile.tolerance,
            ):
                self._clear_pending_command()
                return True, False
        if context_matches:
            return True, True
        return False, False

    def _active_pending_command(self) -> PendingCommand | None:
        """Return a non-expired pending command."""

        pending = self._pending_command
        if pending is None:
            return None
        if datetime.now(UTC) <= pending.expires_at:
            return pending
        self._clear_pending_command()
        return None

    @callback
    def _remember_pending_command(
        self, desired: int, actual: int | None, context_id: str
    ) -> None:
        """Track one command and schedule expiration of its protection."""

        now = datetime.now(UTC)
        movement = self.target_domain == "cover"
        ttl = (
            COVER_MOVEMENT_TTL_SECONDS
            if movement
            else PENDING_COMMAND_TTL_SECONDS
        )
        self._pending_command = PendingCommand(
            desired=desired,
            start=actual,
            last_actual=actual,
            issued_at=now,
            context_id=context_id,
            fallback_until=now
            + timedelta(
                seconds=(
                    COVER_MOVEMENT_GUARD_SECONDS if movement else ttl
                )
            ),
            expires_at=now + timedelta(seconds=ttl),
        )
        self._cancel_pending_timer = async_call_later(
            self.hass,
            ttl,
            lambda now: self._handle_pending_expired(now, context_id),
        )

    @callback
    def _clear_pending_command(self) -> None:
        """Forget the current command and cancel its expiration timer."""

        if self._pending_command is not None:
            self._recent_command_contexts[
                self._pending_command.context_id
            ] = self._pending_command.expires_at
        self._pending_command = None
        if self._cancel_pending_timer is not None:
            self._cancel_pending_timer()
            self._cancel_pending_timer = None

    def _matches_recent_command_context(self, event: Event[Any]) -> bool:
        """Recognize delayed state reports from a superseded command."""

        now = datetime.now(UTC)
        self._recent_command_contexts = {
            context_id: expires_at
            for context_id, expires_at in self._recent_command_contexts.items()
            if now <= expires_at
        }
        return (
            event.context.id in self._recent_command_contexts
            or event.context.parent_id in self._recent_command_contexts
        )

    @callback
    def _handle_pending_expired(
        self, _now: datetime, context_id: str
    ) -> None:
        """Release command protection and reassess the latest Source state."""

        if (
            self._pending_command is None
            or self._pending_command.context_id != context_id
        ):
            return
        self._cancel_pending_timer = None
        self._pending_command = None
        self._notify()
        if self._ready and not self.override_active:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims after command timeout for {self.target_entity_id}",
            )

    @callback
    def _start_or_refresh_override(self) -> None:
        """Suppress reconciliation until duration after the latest conflict."""

        self._clear_pending_command()
        now = datetime.now(UTC)
        self._override_until = now + self._override_duration
        self._override_reason = "external_source_change"
        if self._cancel_override_timer is not None:
            self._cancel_override_timer()
        expected_until = self._override_until
        self._cancel_override_timer = async_call_later(
            self.hass,
            self._override_duration.total_seconds(),
            lambda scheduled_now: self._handle_override_expired(
                scheduled_now, expected_until
            ),
        )
        self._notify()

    @callback
    def _handle_override_expired(
        self, _now: datetime, expected_until: datetime | None
    ) -> None:
        """Release control suppression and reconcile the latest desired value."""

        if self._override_until != expected_until:
            return
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
