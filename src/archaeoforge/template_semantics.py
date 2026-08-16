"""Conservative semantic recognizability classes for Blender feature templates.

The class describes what the native geometry communicates before any image
finisher sees it. Unknown templates deliberately fall back to a generic
envelope so a readiness check cannot infer semantic detail from a name alone.
"""

from __future__ import annotations

from typing import Literal

TemplateRecognizability = Literal["generic_envelope", "type_specific", "identity_specific"]

_RECOGNIZABILITY_RANK: dict[TemplateRecognizability, int] = {
    "generic_envelope": 0,
    "type_specific": 1,
    "identity_specific": 2,
}

IDENTITY_SPECIFIC_TEMPLATES = frozenset({"sphinx"})
TYPE_SPECIFIC_TEMPLATES = frozenset(
    {
        "canal",
        "city_wall",
        "gate",
        "palm",
        "processional",
        "pyramid",
        "residential_cluster",
        "river",
        "road",
        "tree",
        "wall",
        "water",
        "ziggurat",
    }
)


def template_recognizability(template: object) -> TemplateRecognizability:
    """Return how specifically a native template communicates feature identity."""
    normalized = str(template or "").strip().lower()
    if normalized in IDENTITY_SPECIFIC_TEMPLATES:
        return "identity_specific"
    if normalized in TYPE_SPECIFIC_TEMPLATES:
        return "type_specific"
    return "generic_envelope"


def meets_minimum_recognizability(
    template: object,
    minimum: Literal["type_specific", "identity_specific"],
) -> bool:
    """Whether a native template communicates at least the declared semantic level."""
    return _RECOGNIZABILITY_RANK[template_recognizability(template)] >= _RECOGNIZABILITY_RANK[minimum]
