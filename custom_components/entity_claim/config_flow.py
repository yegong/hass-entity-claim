"""Config flow for Entity Claim."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_AGGREGATION_POLICY,
    CONF_DIAGNOSTIC_SENSOR_ENABLED,
    CONF_REQUESTERS,
    CONF_TARGET_ENTITY_ID,
    DEFAULT_AGGREGATION_POLICY,
    DEFAULT_DIAGNOSTIC_SENSOR_ENABLED,
    DOMAIN,
    SUPPORTED_DOMAINS,
)
from .model import AggregationPolicy, Requester
from .registry import find_entity_id_conflict
from .schema import (
    RequesterParseError,
    claim_entity_id,
    diagnostic_entity_id,
    parse_requesters,
    requesters_from_config,
    requesters_to_config,
    requesters_to_names,
    split_entity_id,
)


def _configuration_schema(
    defaults: dict[str, Any], *, include_target: bool
) -> probatio.Schema:
    """Build the user or reconfigure form schema."""

    schema: dict[Any, Any] = {}
    if include_target:
        schema[
            probatio.Required(
                CONF_TARGET_ENTITY_ID,
                default=defaults.get(CONF_TARGET_ENTITY_ID),
            )
        ] = EntitySelector(
            EntitySelectorConfig(domain=list(SUPPORTED_DOMAINS))
        )
    schema[
        probatio.Required(
            CONF_REQUESTERS,
            default=defaults.get(CONF_REQUESTERS, []),
        )
    ] = TextSelector(TextSelectorConfig(multiple=True))
    schema[
        probatio.Required(
            CONF_AGGREGATION_POLICY,
            default=defaults.get(
                CONF_AGGREGATION_POLICY, DEFAULT_AGGREGATION_POLICY
            ),
        )
    ] = SelectSelector(
        SelectSelectorConfig(
            options=[policy.value for policy in AggregationPolicy],
            translation_key="aggregation_policy",
        )
    )
    schema[
        probatio.Required(
            CONF_DIAGNOSTIC_SENSOR_ENABLED,
            default=defaults.get(
                CONF_DIAGNOSTIC_SENSOR_ENABLED,
                DEFAULT_DIAGNOSTIC_SENSOR_ENABLED,
            ),
        )
    ] = BooleanSelector()
    return probatio.Schema(schema)


def _target_title(hass: HomeAssistant, target_entity_id: str) -> str:
    """Return a stable and recognizable config-entry title."""

    state = hass.states.get(target_entity_id)
    if state is None:
        return target_entity_id
    return str(state.attributes.get(ATTR_FRIENDLY_NAME, target_entity_id))


class EntityClaimConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one independently managed source entity."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a config entry for one source entity."""

        errors: dict[str, str] = {}
        detail = ""
        defaults = user_input or {}
        if user_input is not None:
            registry = er.async_get(self.hass)
            target_entity_id = er.async_resolve_entity_id(
                registry, user_input[CONF_TARGET_ENTITY_ID]
            )
            if target_entity_id is None:
                errors[CONF_TARGET_ENTITY_ID] = "target_not_found"
            else:
                user_input[CONF_TARGET_ENTITY_ID] = target_entity_id
                requesters, validation_error, detail = self._validate_input(
                    target_entity_id,
                    user_input,
                )
                if validation_error:
                    errors[
                        CONF_REQUESTERS
                        if validation_error == "invalid_requesters"
                        else "base"
                    ] = validation_error
                else:
                    assert requesters is not None
                    await self.async_set_unique_id(target_entity_id)
                    self._abort_if_unique_id_configured()
                    data = self._entry_data(target_entity_id, user_input, requesters)
                    return self.async_create_entry(
                        title=_target_title(self.hass, target_entity_id),
                        data=data,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_configuration_schema(defaults, include_target=True),
            errors=errors,
            description_placeholders={"error_detail": detail},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add, remove, or rename requesters and change entry policy."""

        entry = self._get_reconfigure_entry()
        target_entity_id = entry.data[CONF_TARGET_ENTITY_ID]
        current_requesters = requesters_from_config(entry.data[CONF_REQUESTERS])
        defaults = user_input or {
            CONF_REQUESTERS: requesters_to_names(current_requesters),
            CONF_AGGREGATION_POLICY: entry.data[CONF_AGGREGATION_POLICY],
            CONF_DIAGNOSTIC_SENSOR_ENABLED: entry.data[
                CONF_DIAGNOSTIC_SENSOR_ENABLED
            ],
        }
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            requesters, validation_error, detail = self._validate_input(
                target_entity_id,
                user_input,
                config_entry_id=entry.entry_id,
                previous_requesters=current_requesters,
            )
            if validation_error:
                errors[
                    CONF_REQUESTERS
                    if validation_error == "invalid_requesters"
                    else "base"
                ] = validation_error
            else:
                assert requesters is not None
                await self.async_set_unique_id(target_entity_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=self._entry_data(
                        target_entity_id, user_input, requesters
                    ),
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_configuration_schema(defaults, include_target=False),
            errors=errors,
            description_placeholders={
                "target_entity_id": target_entity_id,
                "error_detail": detail,
            },
        )

    def _validate_input(
        self,
        target_entity_id: str,
        user_input: dict[str, Any],
        *,
        config_entry_id: str | None = None,
        previous_requesters: tuple[Requester, ...] = (),
    ) -> tuple[tuple[Requester, ...] | None, str | None, str]:
        """Validate target, requester syntax, and all exact entity IDs."""

        try:
            target_domain, _ = split_entity_id(target_entity_id)
        except ValueError as err:
            return None, "target_not_found", str(err)
        if target_domain not in SUPPORTED_DOMAINS:
            return None, "unsupported_domain", target_domain
        if self.hass.states.get(target_entity_id) is None:
            return None, "target_not_found", target_entity_id

        source_registry_entry = er.async_get(self.hass).async_get(target_entity_id)
        if (
            source_registry_entry is not None
            and source_registry_entry.platform == DOMAIN
        ):
            return None, "claim_cannot_be_target", target_entity_id

        try:
            requesters = parse_requesters(
                user_input[CONF_REQUESTERS], previous_requesters
            )
            # Validate length before doing registry lookups.
            for requester in requesters:
                claim_entity_id(target_entity_id, requester.id)
            if user_input[CONF_DIAGNOSTIC_SENSOR_ENABLED]:
                diagnostic_entity_id(target_entity_id)
        except (RequesterParseError, ValueError) as err:
            return None, "invalid_requesters", str(err)

        conflict = find_entity_id_conflict(
            self.hass,
            target_entity_id,
            requesters,
            bool(user_input[CONF_DIAGNOSTIC_SENSOR_ENABLED]),
            config_entry_id=config_entry_id,
        )
        if conflict:
            return None, "entity_id_conflict", conflict
        return requesters, None, ""

    @staticmethod
    def _entry_data(
        target_entity_id: str,
        user_input: dict[str, Any],
        requesters: tuple[Requester, ...],
    ) -> dict[str, Any]:
        """Normalize form values for stable config-entry storage."""

        return {
            CONF_TARGET_ENTITY_ID: target_entity_id,
            CONF_REQUESTERS: requesters_to_config(requesters),
            CONF_AGGREGATION_POLICY: user_input[CONF_AGGREGATION_POLICY],
            CONF_DIAGNOSTIC_SENSOR_ENABLED: bool(
                user_input[CONF_DIAGNOSTIC_SENSOR_ENABLED]
            ),
        }
