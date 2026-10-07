"""Pure configuration parsing and deterministic identifier helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Final

from .const import CONF_REQUESTER_ID, CONF_REQUESTER_NAME
from .model import Requester

_REQUESTER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_REQUESTER_ID_LENGTH: Final = 64
MAX_REQUESTER_NAME_LENGTH: Final = 100
MAX_ENTITY_ID_LENGTH: Final = 255


@dataclass(frozen=True, slots=True)
class RequesterParseError(ValueError):
    """A requester declaration could not be parsed."""

    line: int
    reason: str

    def __str__(self) -> str:
        """Return a concise user-facing detail."""

        prefix = f"Line {self.line}: " if self.line else ""
        return f"{prefix}{self.reason}"


def parse_requesters(value: str) -> tuple[Requester, ...]:
    """Parse one ``id: name`` requester per line."""

    requesters: list[Requester] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        requester_id, separator, name = line.partition(":")
        requester_id = requester_id.strip()
        name = name.strip()
        if not separator or not requester_id or not name:
            raise RequesterParseError(
                line_number, "expected the format 'requester_id: Requester name'"
            )
        if len(requester_id) > MAX_REQUESTER_ID_LENGTH:
            raise RequesterParseError(
                line_number,
                f"requester id exceeds {MAX_REQUESTER_ID_LENGTH} characters",
            )
        if not _REQUESTER_ID_PATTERN.fullmatch(requester_id):
            raise RequesterParseError(
                line_number,
                "requester id must start with a lowercase letter and contain "
                "only lowercase letters, digits, and underscores",
            )
        if len(name) > MAX_REQUESTER_NAME_LENGTH:
            raise RequesterParseError(
                line_number,
                f"requester name exceeds {MAX_REQUESTER_NAME_LENGTH} characters",
            )
        if requester_id in seen:
            raise RequesterParseError(
                line_number, f"duplicate requester id '{requester_id}'"
            )
        seen.add(requester_id)
        requesters.append(Requester(requester_id, name))
    return tuple(requesters)


def requesters_from_config(
    value: Sequence[Mapping[str, Any]],
) -> tuple[Requester, ...]:
    """Build requester objects from config-entry JSON data."""

    return tuple(
        Requester(str(item[CONF_REQUESTER_ID]), str(item[CONF_REQUESTER_NAME]))
        for item in value
    )


def requesters_to_config(
    requesters: tuple[Requester, ...],
) -> list[dict[str, str]]:
    """Serialize requesters into config-entry JSON data."""

    return [
        {CONF_REQUESTER_ID: requester.id, CONF_REQUESTER_NAME: requester.name}
        for requester in requesters
    ]


def requesters_to_text(requesters: tuple[Requester, ...]) -> str:
    """Serialize requesters for the multiline config-flow field."""

    return "\n".join(
        f"{requester.id}: {requester.name}" for requester in requesters
    )


def split_entity_id(entity_id: str) -> tuple[str, str]:
    """Split and minimally validate an entity ID."""

    domain, separator, object_id = entity_id.partition(".")
    if not separator or not domain or not object_id:
        raise ValueError(f"Invalid entity ID: {entity_id}")
    return domain, object_id


def claim_entity_id(target_entity_id: str, requester_id: str) -> str:
    """Return the exact, deterministic entity ID for a new claim."""

    domain, object_id = split_entity_id(target_entity_id)
    entity_id = f"{domain}.{object_id}_required_by_{requester_id}"
    if len(entity_id) > MAX_ENTITY_ID_LENGTH:
        raise ValueError(
            f"Generated entity ID exceeds {MAX_ENTITY_ID_LENGTH} characters: "
            f"{entity_id}"
        )
    return entity_id


def diagnostic_entity_id(target_entity_id: str) -> str:
    """Return the exact, deterministic diagnostic sensor entity ID."""

    _, object_id = split_entity_id(target_entity_id)
    entity_id = f"sensor.{object_id}_claim_diagnostics"
    if len(entity_id) > MAX_ENTITY_ID_LENGTH:
        raise ValueError(
            f"Generated entity ID exceeds {MAX_ENTITY_ID_LENGTH} characters: "
            f"{entity_id}"
        )
    return entity_id
