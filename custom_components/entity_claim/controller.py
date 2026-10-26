"""Home Assistant runtime adapter for the Entity Claim domain model."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
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
from homeassistant.helpers.storage import Store

from .const import (
    CONTROL_STORAGE_VERSION,
    COVER_MOVEMENT_GUARD_SECONDS,
    COVER_MOVEMENT_TTL_SECONDS,
    DOMAIN,
    PENDING_COMMAND_TTL_SECONDS,
)
from .model import (
    AggregationPolicy,
    ClaimKind,
    ClaimProfile,
    ClaimState,
    ControlPolicy,
    ControlState,
    Requester,
    ScopedSession,
    is_conflicting_external_change,
    is_expected_position_progress,
    matches_pending_command,
    values_match,
)

_LOGGER = logging.getLogger(__name__)

UpdateCallback = Callable[[], None]
TaskCreator = Callable[[Coroutine[Any, Any, None], str], object]
CancelCallback = Callable[[], None]


class PendingPurpose(StrEnum):
    """Why a Source command is currently in flight."""

    APPLY = "apply"
    ACQUIRE = "acquire"
    RESTORE = "restore"


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
    purpose: PendingPurpose


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
        control_policy: ControlPolicy,
        override_duration: timedelta,
        config_entry_id: str,
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
        self._control_policy = control_policy
        self._override_duration = override_duration
        self._override_until: datetime | None = None
        self._override_reason: str | None = None
        self._cancel_override_timer: CancelCallback | None = None
        self._pending_command: PendingCommand | None = None
        self._cancel_pending_timer: CancelCallback | None = None
        self._recent_command_contexts: dict[str, datetime] = {}
        self._scoped = ScopedSession()
        self._store = Store[dict[str, Any]](
            hass,
            CONTROL_STORAGE_VERSION,
            f"{DOMAIN}.{config_entry_id}.control",
        )
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
    def control_policy(self) -> ControlPolicy:
        """Return the configured reconciliation authority policy."""

        return self._control_policy

    @property
    def control_desired(self) -> int:
        """Return the desired value currently authorized for execution."""

        if self._control_policy is not ControlPolicy.SCOPED:
            return self.desired
        if self._scoped.restoring and self._scoped.baseline is not None:
            return self._scoped.baseline
        participants = tuple(self._scoped.participants or ())
        return self.state.aggregate_for(participants)

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
    def control_state(self) -> ControlState:
        """Return the normalized reconciliation lifecycle state."""

        if self._control_policy is ControlPolicy.OVERRIDE and self.override_active:
            return ControlState.PASSIVE
        if self._control_policy is ControlPolicy.SCOPED:
            if self._scoped.restoring:
                return ControlState.RESTORING
            if not self._scoped.participants:
                return ControlState.PASSIVE
            if not self._scoped.owned:
                return ControlState.APPLYING

        desired = self.control_desired
        pending_active = (
            self._pending_command is not None
            and datetime.now(UTC) <= self._pending_command.expires_at
        )
        if (
            self.source_moving
            or pending_active
            or not values_match(
                desired,
                self.actual_value,
                tolerance=self.profile.tolerance,
            )
        ):
            return ControlState.APPLYING
        return ControlState.ACTIVE

    @property
    def override_active(self) -> bool:
        """Return whether automatic reconciliation is currently suppressed."""

        return (
            self._control_policy is ControlPolicy.OVERRIDE
            and self._override_until is not None
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

    async def async_load(self) -> None:
        """Restore an uninterrupted scoped control session, if one exists."""

        if self._control_policy is not ControlPolicy.SCOPED:
            await self._store.async_remove()
            return
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        if stored and (
            stored.get("aggregation_policy") != self.state.policy.value
            or stored.get("claim_kind") != self.profile.kind.value
        ):
            await self._store.async_remove()
            return
        baseline = stored.get("baseline")
        participants = stored.get("participants")
        if not isinstance(baseline, int) or not isinstance(participants, list):
            return
        known = {requester.id for requester in self.requesters}
        restored_participants = [
            requester_id
            for requester_id in participants
            if isinstance(requester_id, str) and requester_id in known
        ]
        self._scoped.baseline = self.profile.normalize(baseline)
        self._scoped.participants = restored_participants
        self._scoped.owned = bool(stored.get("owned", True))
        self._scoped.restoring = bool(stored.get("restoring", False))

    async def async_start(self) -> None:
        """Start source observation after all claim entities have restored."""

        if self._ready:
            return
        self._ready = True
        self._unsubscribe_target = async_track_state_change_event(
            self.hass, [self.target_entity_id], self._handle_target_change
        )
        if self._control_policy is ControlPolicy.SCOPED:
            self._scoped.participants = [
                requester_id
                for requester_id in self._scoped.participants
                if self.state.get(requester_id) != self.state.neutral
            ]
            if self._scoped.owned and not self._scoped.participants:
                self._scoped.restoring = self._scoped.baseline is not None
            elif not self._scoped.participants:
                self._scoped.clear()
        self._notify()
        await self.async_reconcile()

    async def async_stop(self) -> None:
        """Stop source observation."""

        self._ready = False
        if self._control_policy is ControlPolicy.SCOPED:
            await self._store.async_save(self._scoped_storage_data())
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

        changed = self.state.set(requester_id, value)
        if changed:
            self._notify()
        if not reconcile or not self._ready:
            return
        if self._control_policy is ControlPolicy.SCOPED:
            await self._async_update_scoped_claim(requester_id)
            return
        if changed:
            await self.async_reconcile()

    async def _async_update_scoped_claim(self, requester_id: str) -> None:
        """Join, release, or start one scoped control session participant."""

        participants = self._scoped.participants
        asserted = self.state.get(requester_id) != self.state.neutral
        resumed_restoration = self._scoped.restoring and asserted

        if resumed_restoration:
            self._scoped.restoring = False
            self._scoped.owned = True

        if participants:
            if asserted and requester_id not in participants:
                self._scoped.join(requester_id)
            elif not asserted and requester_id in participants:
                self._scoped.release(requester_id)
        elif asserted:
            self._scoped.join(requester_id)
            if not resumed_restoration:
                self._scoped.owned = False
                self._scoped.restoring = False
                self._scoped.baseline = None

        if not participants:
            if self._scoped.baseline is not None:
                self._scoped.restoring = True
            else:
                self._scoped.clear()

        self._persist_scoped_session()
        self._notify()
        await self.async_reconcile()

    async def async_reconcile(self) -> None:
        """Apply the latest aggregated desired value to the Source."""

        if not self._ready or self.override_active:
            return
        async with self._reconcile_lock:
            if self.override_active:
                return
            if self._control_policy is ControlPolicy.SCOPED:
                await self._async_reconcile_scoped()
                return
            await self._async_apply_desired(
                self.desired, PendingPurpose.APPLY
            )

    async def _async_reconcile_scoped(self) -> None:
        """Apply or restore one uninterrupted scoped control session."""

        participants = self._scoped.participants
        if self._scoped.restoring:
            if self._scoped.baseline is None:
                self._clear_scoped_session()
                return
            await self._async_apply_desired(
                self._scoped.baseline,
                PendingPurpose.RESTORE,
            )
            return
        if not participants:
            return

        actual = self.actual_value
        if self._scoped.baseline is None:
            if actual is None or self.source_moving:
                return
            self._scoped.baseline = actual

        desired = self.control_desired
        if (
            not self._scoped.owned
            and not self.source_moving
            and values_match(
                desired, actual, tolerance=self.profile.tolerance
            )
            and self._active_pending_command() is None
        ):
            # The Claim did not visibly affect the Source, so it does not
            # establish a control session that may later restore anything.
            self._clear_scoped_session()
            return
        await self._async_apply_desired(
            desired,
            (
                PendingPurpose.APPLY
                if self._scoped.owned
                else PendingPurpose.ACQUIRE
            ),
        )

    async def _async_apply_desired(
        self, desired: int, purpose: PendingPurpose
    ) -> None:
        """Issue the smallest command needed for one authorized target."""

        actual = self.actual_value
        moving = self.source_moving
        pending = self._active_pending_command()
        if actual is None and not moving:
            return

        matches = values_match(
            desired, actual, tolerance=self.profile.tolerance
        )
        if pending is not None and pending.desired == desired:
            return

        superseded_pending = (
            pending is not None and pending.desired != desired
        )
        if matches and not superseded_pending:
            if purpose is PendingPurpose.RESTORE:
                self._clear_scoped_session()
            return
        if moving and pending is None:
            # Never fight an uncorrelated Cover movement. A scoped candidate
            # captures its baseline only after that movement becomes stable.
            return

        stop_cover = (
            matches and moving and superseded_pending and self._cover_can_stop()
        )
        self._clear_pending_command()
        service, data = self._source_command(desired, stop_cover=stop_cover)
        context = Context()
        self._remember_pending_command(
            desired, actual, context.id, purpose=purpose
        )
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
            if purpose is PendingPurpose.ACQUIRE:
                self._clear_scoped_session()
            _LOGGER.exception(
                "Unable to reconcile %s to %s",
                self.target_entity_id,
                desired,
            )
        finally:
            self._notify()

    def _source_command(
        self, desired: int, *, stop_cover: bool = False
    ) -> tuple[str, dict[str, Any]]:
        """Build the smallest domain action for the desired value."""

        data: dict[str, Any] = {ATTR_ENTITY_ID: self.target_entity_id}
        if self.target_domain == "cover":
            if stop_cover:
                return SERVICE_STOP_COVER, data
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
        """Classify a Source change, update authority, then reconcile."""

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
        pending = self._active_pending_command()
        self_induced, movement_in_progress = self._classify_pending_change(
            event, actual, moving
        )

        source_recovered = (
            self._source_recovery_pending
            or self._is_unavailable_state(old_state)
        )
        if not moving and actual is not None:
            self._source_recovery_pending = False

        if self._control_policy is ControlPolicy.SCOPED:
            observable = self._is_observable_source_change(old_state, new_state)
            if (
                not self_induced
                and not source_recovered
                and observable
            ):
                self._relinquish_scoped_session()
                return
            if moving or movement_in_progress or actual is None:
                self._notify()
                return
            if (
                self_induced
                and pending is not None
                and values_match(
                    pending.desired,
                    actual,
                    tolerance=self.profile.tolerance,
                )
            ):
                self._complete_scoped_command(pending)
            if self._scoped.participants or self._scoped.restoring:
                self._task_creator(
                    self.async_reconcile(),
                    f"Reconcile scoped claims for {self.target_entity_id}",
                )
            self._notify()
            return

        # opening/closing and expected intermediate position reports are
        # observations, not external control intent.
        if moving or movement_in_progress:
            self._notify()
            return
        if actual is None:
            return

        if (
            self._control_policy is ControlPolicy.OVERRIDE
            and is_conflicting_external_change(
                self.desired,
                actual,
                self_induced=self_induced,
                source_recovered=source_recovered,
                tolerance=self.profile.tolerance,
            )
        ):
            self._start_or_refresh_override()
            return

        if not self.override_active:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims for {self.target_entity_id}",
            )

    def _is_observable_source_change(
        self, old_state: State | None, new_state: State | None
    ) -> bool:
        """Return whether a controlled Source value visibly changed."""

        if self._is_unavailable_state(old_state) or self._is_unavailable_state(
            new_state
        ):
            return False
        if self._is_moving_state(old_state) != self._is_moving_state(new_state):
            return True
        old_value = self._source_value(old_state)
        new_value = self._source_value(new_state)
        if old_value is None or new_value is None:
            return False
        return not values_match(
            old_value,
            new_value,
            tolerance=self.profile.tolerance,
        )

    @callback
    def _complete_scoped_command(self, pending: PendingCommand) -> None:
        """Advance a scoped session after one visible command succeeds."""

        if pending.purpose is PendingPurpose.RESTORE:
            self._clear_scoped_session()
            return
        if not self._scoped.participants or self._scoped.restoring:
            return
        self._scoped.owned = True
        self._persist_scoped_session()

    @callback
    def _relinquish_scoped_session(self) -> None:
        """Give up the entire causal chain after an external operation."""

        self._clear_pending_command()
        self._scoped.clear()
        self._persist_scoped_session()
        self._notify()

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

    def _cover_can_stop(self) -> bool:
        """Return whether the Source Cover exposes an internal stop action."""

        source = self.actual_state
        if self.target_domain != "cover" or source is None:
            return False
        features = CoverEntityFeature(
            int(source.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
        )
        return CoverEntityFeature.STOP in features

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
        self,
        desired: int,
        actual: int | None,
        context_id: str,
        *,
        purpose: PendingPurpose,
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
            purpose=purpose,
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
        pending = self._pending_command
        self._cancel_pending_timer = None
        self._pending_command = None
        self._recent_command_contexts[pending.context_id] = pending.expires_at
        self._notify()
        if self._ready and not self.override_active:
            self._task_creator(
                self.async_reconcile(),
                f"Reconcile claims after command timeout for {self.target_entity_id}",
            )

    @callback
    def _clear_scoped_session(self) -> None:
        """Finish or abandon a scoped session and become passive."""

        self._clear_pending_command()
        self._scoped.clear()
        self._persist_scoped_session()
        self._notify()

    def _scoped_storage_data(self) -> dict[str, Any]:
        """Return the durable portion of scoped control provenance."""

        if (
            not self._scoped.owned
            and not self._scoped.restoring
        ) or self._scoped.baseline is None:
            return {}
        return {
            "baseline": self._scoped.baseline,
            "participants": list(self._scoped.participants or ()),
            "owned": self._scoped.owned,
            "restoring": self._scoped.restoring,
            "aggregation_policy": self.state.policy.value,
            "claim_kind": self.profile.kind.value,
        }

    @callback
    def _persist_scoped_session(self) -> None:
        """Persist control provenance without blocking state handling."""

        if self._control_policy is ControlPolicy.SCOPED:
            self._store.async_delay_save(self._scoped_storage_data, 1)

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
