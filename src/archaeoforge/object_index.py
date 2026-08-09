from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

MAX_OBJECT_PASS_INDEX = 32767


def build_object_index_map(features: Iterable[Mapping[str, object]]) -> dict[str, int]:
    """Return a deterministic Blender pass index for every manifest feature."""
    feature_ids: list[str] = []
    for feature in features:
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not feature_id:
            raise ValueError("Every manifest feature must have a non-empty string ID.")
        feature_ids.append(feature_id)

    duplicates = sorted(feature_id for feature_id, count in Counter(feature_ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"Manifest feature IDs must be unique; duplicates: {', '.join(duplicates)}")

    ordered_ids = sorted(feature_ids)
    if len(ordered_ids) > MAX_OBJECT_PASS_INDEX:
        raise ValueError(
            f"Manifest has {len(ordered_ids)} features, but Blender object pass indices support at most "
            f"{MAX_OBJECT_PASS_INDEX} non-background values."
        )

    return {feature_id: index for index, feature_id in enumerate(ordered_ids, start=1)}
