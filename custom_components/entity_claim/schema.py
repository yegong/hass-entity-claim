"""Pure configuration parsing and deterministic identifier helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Final
import unicodedata

from .const import CONF_REQUESTER_ID, CONF_REQUESTER_NAME
from .model import Requester

_REQUESTER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_REQUESTER_ID_LENGTH: Final = 64
MAX_REQUESTER_NAME_LENGTH: Final = 100
MAX_ENTITY_ID_LENGTH: Final = 255


@dataclass(frozen=True, slots=True)
class RequesterParseError(ValueError):
    """A requester name could not be converted into a valid requester."""

    item: int
    reason: str

    def __str__(self) -> str:
        """Return a concise user-facing detail."""

        prefix = f"Requester {self.item}: " if self.item else ""
        return f"{prefix}{self.reason}"


def requester_id_from_name(name: str) -> str:
    """Generate a deterministic machine ID from a user-visible name."""

    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    requester_id = re.sub(r"[^a-z0-9]+", "_", ascii_name).strip("_")
    digest = sha256(name.casefold().encode()).hexdigest()[:10]
    if not requester_id:
        requester_id = f"requester_{digest}"
    elif not requester_id[0].isalpha():
        requester_id = f"requester_{requester_id}"
    if len(requester_id) > MAX_REQUESTER_ID_LENGTH:
        requester_id = (
            f"{requester_id[: MAX_REQUESTER_ID_LENGTH - len(digest) - 1]}_{digest}"
        )
    return requester_id


def parse_requesters(
    value: Sequence[str],
    previous: Sequence[Requester] = (),
) -> tuple[Requester, ...]:
    """Build requesters from a dynamic list of user-visible names.

    Existing IDs are matched by name first and then by unchanged row position.
    This keeps entity registry identities stable for normal rename, add, remove,
    and case-change operations without exposing machine IDs in the UI.
    """

    if isinstance(value, (str, bytes)):
        raise RequesterParseError(0, "expected a list of requester names")

    names: list[str] = []
    original_positions: list[int] = []
    for item_number, raw_name in enumerate(value, start=1):
        if not isinstance(raw_name, str):
            raise RequesterParseError(item_number, "name must be text")
        name = raw_name.strip()
        if not name:
            continue
        if len(name) > MAX_REQUESTER_NAME_LENGTH:
            raise RequesterParseError(
                item_number,
                f"name exceeds {MAX_REQUESTER_NAME_LENGTH} characters",
            )
        names.append(name)
        original_positions.append(item_number - 1)

    previous_by_name: dict[str, deque[Requester]] = defaultdict(deque)
    for requester in previous:
        previous_by_name[requester.name.casefold()].append(requester)

    assigned_ids: list[str | None] = [None] * len(names)
    available_previous_ids = {requester.id for requester in previous}

    # Preserve identity when rows are retained or only their letter case changes.
    for index, name in enumerate(names):
        matches = previous_by_name[name.casefold()]
        while matches and matches[0].id not in available_previous_ids:
            matches.popleft()
        if matches:
            requester = matches.popleft()
            assigned_ids[index] = requester.id
            available_previous_ids.remove(requester.id)

    # A changed name in the same row is a rename, not a new requester.
    for index, original_position in enumerate(original_positions):
        if assigned_ids[index] is not None or original_position >= len(previous):
            continue
        requester = previous[original_position]
        if requester.id in available_previous_ids:
            assigned_ids[index] = requester.id
            available_previous_ids.remove(requester.id)

    requesters: list[Requester] = []
    seen: set[str] = set()
    for item_number, (name, requester_id) in enumerate(
        zip(names, assigned_ids, strict=True), start=1
    ):
        requester_id = requester_id or requester_id_from_name(name)
        if not _REQUESTER_ID_PATTERN.fullmatch(requester_id):
            raise RequesterParseError(
                item_number, f"could not generate a valid ID from '{name}'"
            )
        if requester_id in seen:
            raise RequesterParseError(
                item_number,
                f"'{name}' generates the same ID as another requester",
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


def requesters_to_names(requesters: Sequence[Requester]) -> list[str]:
    """Return user-visible names for the dynamic config-flow list."""

    return [requester.name for requester in requesters]


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
