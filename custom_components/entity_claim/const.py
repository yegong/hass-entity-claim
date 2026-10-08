"""Constants for the Entity Claim integration."""

from typing import Final

DOMAIN: Final = "entity_claim"

CONF_TARGET_ENTITY_ID: Final = "target_entity_id"
CONF_REQUESTERS: Final = "requesters"
CONF_AGGREGATION_POLICY: Final = "aggregation_policy"
CONF_DIAGNOSTIC_SENSOR_ENABLED: Final = "diagnostic_sensor_enabled"
CONF_RESPECT_SOURCE_CHANGES: Final = "respect_source_changes"
CONF_OVERRIDE_DURATION_MINUTES: Final = "override_duration_minutes"

CONF_REQUESTER_ID: Final = "id"
CONF_REQUESTER_NAME: Final = "name"

SUPPORTED_DOMAINS: Final = ("fan", "light", "switch")
DEFAULT_AGGREGATION_POLICY: Final = "any"
DEFAULT_DIAGNOSTIC_SENSOR_ENABLED: Final = False
DEFAULT_RESPECT_SOURCE_CHANGES: Final = True
DEFAULT_OVERRIDE_DURATION_MINUTES: Final = 15

# A pending command is retained briefly so integrations which preserve the
# service-call Context can correlate the resulting state change. The desired
# state is only a secondary fallback for integrations which drop Context.
PENDING_COMMAND_TTL_SECONDS: Final = 30

CLAIM_UNIQUE_ID_SEPARATOR: Final = ":claim:"
DIAGNOSTIC_UNIQUE_ID_SUFFIX: Final = ":diagnostic"
