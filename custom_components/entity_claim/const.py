"""Constants for the Entity Claim integration."""

from typing import Final

DOMAIN: Final = "entity_claim"

CONF_TARGET_ENTITY_ID: Final = "target_entity_id"
CONF_REQUESTERS: Final = "requesters"
CONF_AGGREGATION_POLICY: Final = "aggregation_policy"
CONF_CONTROL_POLICY: Final = "control_policy"
CONF_DIAGNOSTIC_SENSOR_ENABLED: Final = "diagnostic_sensor_enabled"
# Legacy Config Entry key migrated to ``control_policy`` in version 2.
CONF_RESPECT_SOURCE_CHANGES: Final = "respect_source_changes"
CONF_OVERRIDE_DURATION_MINUTES: Final = "override_duration_minutes"

CONF_REQUESTER_ID: Final = "id"
CONF_REQUESTER_NAME: Final = "name"

SUPPORTED_DOMAINS: Final = ("cover", "fan", "light", "switch")
DEFAULT_AGGREGATION_POLICY: Final = "any"
DEFAULT_CONTROL_POLICY: Final = "override"
DEFAULT_DIAGNOSTIC_SENSOR_ENABLED: Final = False
DEFAULT_RESPECT_SOURCE_CHANGES: Final = True
DEFAULT_OVERRIDE_DURATION_MINUTES: Final = 15

# A pending command is retained briefly so integrations which preserve the
# service-call Context can correlate the resulting state change. The desired
# state is only a secondary fallback for integrations which drop Context.
PENDING_COMMAND_TTL_SECONDS: Final = 30

# Covers can report many intermediate positions after one command. Context is
# preferred, while this short guard protects integrations that drop Context
# before an explicit opening/closing state is visible. The longer lifetime
# prevents duplicate commands during normal travel.
COVER_MOVEMENT_GUARD_SECONDS: Final = 10
COVER_MOVEMENT_TTL_SECONDS: Final = 300

CONTROL_STORAGE_VERSION: Final = 1

CLAIM_UNIQUE_ID_SEPARATOR: Final = ":claim:"
DIAGNOSTIC_UNIQUE_ID_SUFFIX: Final = ":diagnostic"
