"""Pure geometry helpers shared by Blender and the host-side test suite."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point2D = tuple[float, float]


def _subtract(left: Point2D, right: Point2D) -> Point2D:
    return left[0] - right[0], left[1] - right[1]


def _add(left: Point2D, right: Point2D) -> Point2D:
    return left[0] + right[0], left[1] + right[1]


def _scale(point: Point2D, value: float) -> Point2D:
    return point[0] * value, point[1] * value


def _cross(left: Point2D, right: Point2D) -> float:
    return left[0] * right[1] - left[1] * right[0]


def _length(vector: Point2D) -> float:
    return math.hypot(*vector)


def _unit(vector: Point2D) -> Point2D:
    length = _length(vector)
    if length <= 1e-9:
        raise ValueError("Polyline contains a zero-length segment.")
    return vector[0] / length, vector[1] / length


def _normal(direction: Point2D) -> Point2D:
    return -direction[1], direction[0]


def _join_point(
    vertex: Point2D,
    previous_direction: Point2D,
    next_direction: Point2D,
    *,
    side: float,
    half_width: float,
    miter_limit: float,
) -> Point2D:
    previous_normal = _normal(previous_direction)
    next_normal = _normal(next_direction)
    previous_offset = _add(vertex, _scale(previous_normal, side * half_width))
    next_offset = _add(vertex, _scale(next_normal, side * half_width))
    denominator = _cross(previous_direction, next_direction)

    if abs(denominator) <= 1e-9:
        dot = (
            previous_direction[0] * next_direction[0]
            + previous_direction[1] * next_direction[1]
        )
        if dot < 0.0:
            raise ValueError("Polyline reverses direction at a 180-degree corner.")
        return next_offset

    distance_on_previous = _cross(
        _subtract(next_offset, previous_offset),
        next_direction,
    ) / denominator
    intersection = _add(previous_offset, _scale(previous_direction, distance_on_previous))
    miter = _subtract(intersection, vertex)
    miter_length = _length(miter)
    maximum = half_width * miter_limit
    if miter_length > maximum:
        intersection = _add(vertex, _scale(miter, maximum / miter_length))
    return intersection


def mitered_segment_polygons(
    coordinates: Sequence[Sequence[float]],
    width: float,
    *,
    cyclic: bool = False,
    miter_limit: float = 4.0,
) -> list[list[list[float]]]:
    """Return one abutting quadrilateral per nonzero polyline segment.

    Adjacent segment prisms share their complete end edge. This eliminates the corner
    overlaps produced by centering an independent rectangular box on every segment.
    """
    if width <= 0.0:
        raise ValueError("Polyline width must be positive.")
    if miter_limit < 1.0:
        raise ValueError("Miter limit must be at least 1.0.")

    points: list[Point2D] = []
    for coordinate in coordinates:
        if len(coordinate) < 2:
            raise ValueError("Polyline coordinates must contain X and Y values.")
        point = float(coordinate[0]), float(coordinate[1])
        if not points or _length(_subtract(point, points[-1])) > 1e-9:
            points.append(point)
    if cyclic and len(points) > 1 and _length(_subtract(points[0], points[-1])) <= 1e-9:
        points.pop()

    minimum_points = 3 if cyclic else 2
    if len(points) < minimum_points:
        return []

    segment_count = len(points) if cyclic else len(points) - 1
    directions = [
        _unit(_subtract(points[(index + 1) % len(points)], points[index]))
        for index in range(segment_count)
    ]
    half_width = width / 2.0
    left: list[Point2D] = []
    right: list[Point2D] = []

    for index, vertex in enumerate(points):
        if not cyclic and index == 0:
            normal = _normal(directions[0])
            left.append(_add(vertex, _scale(normal, half_width)))
            right.append(_add(vertex, _scale(normal, -half_width)))
            continue
        if not cyclic and index == len(points) - 1:
            normal = _normal(directions[-1])
            left.append(_add(vertex, _scale(normal, half_width)))
            right.append(_add(vertex, _scale(normal, -half_width)))
            continue

        previous_direction = directions[(index - 1) % segment_count]
        next_direction = directions[index % segment_count]
        left.append(
            _join_point(
                vertex,
                previous_direction,
                next_direction,
                side=1.0,
                half_width=half_width,
                miter_limit=miter_limit,
            )
        )
        right.append(
            _join_point(
                vertex,
                previous_direction,
                next_direction,
                side=-1.0,
                half_width=half_width,
                miter_limit=miter_limit,
            )
        )

    polygons: list[list[list[float]]] = []
    for index in range(segment_count):
        next_index = (index + 1) % len(points)
        polygon = [
            [*left[index]],
            [*left[next_index]],
            [*right[next_index]],
            [*right[index]],
            [*left[index]],
        ]
        polygons.append(polygon)
    return polygons
