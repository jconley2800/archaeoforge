from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon

from archaeoforge.polyline import mitered_segment_polygons


def test_right_angle_segments_share_a_miter_without_overlap() -> None:
    segments = mitered_segment_polygons([[0, 0], [10, 0], [10, 10]], 2.0)

    assert len(segments) == 2
    assert segments[0][1:3] == segments[1][0:4:3]
    assert Polygon(segments[0]).intersection(Polygon(segments[1])).area == pytest.approx(0.0)
    assert segments[0][1] == pytest.approx([9.0, 1.0])
    assert segments[0][2] == pytest.approx([11.0, -1.0])


def test_straight_polyline_remains_two_abutting_rectangles() -> None:
    segments = mitered_segment_polygons([[0, 0], [5, 0], [10, 0]], 2.0)

    assert segments == [
        [[0.0, 1.0], [5.0, 1.0], [5.0, -1.0], [0.0, -1.0], [0.0, 1.0]],
        [[5.0, 1.0], [10.0, 1.0], [10.0, -1.0], [5.0, -1.0], [5.0, 1.0]],
    ]


def test_cyclic_square_has_four_nonoverlapping_mitered_segments() -> None:
    segments = mitered_segment_polygons(
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        2.0,
        cyclic=True,
    )

    assert len(segments) == 4
    for index, segment in enumerate(segments):
        following = segments[(index + 1) % len(segments)]
        assert segment[1:3] == following[0:4:3]
        assert Polygon(segment).intersection(Polygon(following)).area == pytest.approx(0.0)


def test_acute_corner_respects_the_miter_limit() -> None:
    segments = mitered_segment_polygons(
        [[0, 0], [10, 0], [0.1, 0.01]],
        2.0,
        miter_limit=2.0,
    )

    corner = (10.0, 0.0)
    for point in segments[0][1:3]:
        assert math.dist(point, corner) <= 2.0 + 1e-9


def test_duplicate_points_are_removed_but_a_uturn_is_rejected() -> None:
    assert len(mitered_segment_polygons([[0, 0], [0, 0], [10, 0]], 2.0)) == 1
    with pytest.raises(ValueError, match="180-degree"):
        mitered_segment_polygons([[0, 0], [10, 0], [0, 0]], 2.0)
