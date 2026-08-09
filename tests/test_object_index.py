from __future__ import annotations

import pytest

from archaeoforge.object_index import MAX_OBJECT_PASS_INDEX, build_object_index_map


def test_object_index_map_is_dense_and_stable_across_manifest_order() -> None:
    features = [{"id": "ZIGGURAT"}, {"id": "GROUND"}, {"id": "GATE"}]

    expected = {"GATE": 1, "GROUND": 2, "ZIGGURAT": 3}
    assert build_object_index_map(features) == expected
    assert build_object_index_map(reversed(features)) == expected


def test_object_index_map_rejects_more_indices_than_blender_supports() -> None:
    features = ({"id": f"FEATURE-{index:05d}"} for index in range(MAX_OBJECT_PASS_INDEX + 1))

    with pytest.raises(ValueError, match=r"32768 features.*at most 32767"):
        build_object_index_map(features)


def test_object_index_map_rejects_duplicate_feature_ids() -> None:
    with pytest.raises(ValueError, match=r"must be unique.*WALL"):
        build_object_index_map([{"id": "WALL"}, {"id": "WALL"}])
