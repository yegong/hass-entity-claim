"""Constants for the Entity Claim integration."""

from typing import Final

DOMAIN: Final = "entity_claim"

CONF_TARGET_ENTITY_ID: Final = "target_entity_id"
CONF_REQUESTERS: Final = "requesters"
CONF_AGGREGATION_POLICY: Final = "aggregation_policy"
CONF_DIAGNOSTIC_SENSOR_ENABLED: Final = "diagnostic_sensor_enabled"

CONF_REQUESTER_ID: Final = "id"
CONF_REQUESTER_NAME: Final = "name"

SUPPORTED_DOMAINS: Final = ("fan", "light", "switch")
DEFAULT_AGGREGATION_POLICY: Final = "any"
DEFAULT_DIAGNOSTIC_SENSOR_ENABLED: Final = False

CLAIM_UNIQUE_ID_SEPARATOR: Final = ":claim:"
DIAGNOSTIC_UNIQUE_ID_SUFFIX: Final = ":diagnostic"

