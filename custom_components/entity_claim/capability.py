"""Map Home Assistant Source capabilities to the shared claim value model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import ATTR_PERCENTAGE_STEP, FanEntityFeature
from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    brightness_supported,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES

from .model import ClaimKind, ClaimProfile

LIGHT_MAX_BRIGHTNESS = 255
PERCENT_MAXIMUM = 100
COVER_POSITION_TOLERANCE = 2


def profile_for_source(
    domain: str, attributes: Mapping[str, Any]
) -> ClaimProfile:
    """Return only the Source capability with defined Claim semantics."""

    supported_features = int(attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
    if domain == "light" and brightness_supported(
        attributes.get(ATTR_SUPPORTED_COLOR_MODES)
    ):
        return ClaimProfile(ClaimKind.BRIGHTNESS, LIGHT_MAX_BRIGHTNESS)
    if domain == "fan" and supported_features & FanEntityFeature.SET_SPEED:
        step = float(attributes.get(ATTR_PERCENTAGE_STEP, 1) or 1)
        return ClaimProfile(
            ClaimKind.PERCENTAGE,
            PERCENT_MAXIMUM,
            tolerance=max(0, step),
            step=step,
        )
    if domain == "cover":
        supports_position = bool(
            supported_features & CoverEntityFeature.SET_POSITION
        )
        if not supports_position:
            return ClaimProfile(
                ClaimKind.BINARY,
                1,
                supports_intermediate=False,
            )
        return ClaimProfile(
            ClaimKind.POSITION,
            PERCENT_MAXIMUM,
            tolerance=COVER_POSITION_TOLERANCE,
        )
    return ClaimProfile(ClaimKind.BINARY, 1)


def aggregation_selector_key(profile: ClaimProfile) -> str:
    """Return the translated selector whose copy explains this capability."""

    return f"aggregation_policy_{profile.kind.value}"
