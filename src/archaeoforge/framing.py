"""Camera framing solver.

Pure standard library on purpose: the Blender scene builder imports this module from
inside Blender's own Python, which has none of the project's third-party dependencies.

Angles follow a surveying convention rather than a Blender one. Azimuth is a compass
bearing in degrees where 0 is north (+Y) and 90 is east (+X); elevation is degrees above
the horizon. +Z is up and units are metres.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import NamedTuple

Vec3 = tuple[float, float, float]


class CameraSolution(NamedTuple):
    target: Vec3
    location: Vec3
    distance: float
    ortho_scale: float


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, factor: float) -> Vec3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalise(a: Vec3) -> Vec3:
    length = math.sqrt(_dot(a, a))
    if length < 1e-12:
        raise ValueError("Cannot normalise a zero-length vector.")
    return _scale(a, 1.0 / length)


def spherical_direction(elevation_degrees: float, azimuth_degrees: float) -> Vec3:
    """Unit vector pointing away from the site along a compass bearing and elevation."""
    elevation = math.radians(elevation_degrees)
    azimuth = math.radians(azimuth_degrees)
    return (
        math.sin(azimuth) * math.cos(elevation),
        math.cos(azimuth) * math.cos(elevation),
        math.sin(elevation),
    )


def camera_basis(offset: Vec3) -> tuple[Vec3, Vec3, Vec3]:
    """Right, up and forward axes for a camera sitting at ``offset`` from its target."""
    forward = _normalise(_scale(offset, -1.0))
    right = _cross(forward, (0.0, 0.0, 1.0))
    if math.sqrt(_dot(right, right)) < 1e-9:
        right = (1.0, 0.0, 0.0)
    right = _normalise(right)
    up = _normalise(_cross(right, forward))
    return right, up, forward


def half_angle_tangents(
    *,
    lens_mm: float,
    sensor_width: float = 36.0,
    sensor_height: float = 24.0,
    sensor_fit: str = "AUTO",
    resolution_x: int = 1920,
    resolution_y: int = 1080,
    pixel_aspect_x: float = 1.0,
    pixel_aspect_y: float = 1.0,
) -> tuple[float, float]:
    """Tangents of the horizontal and vertical half field of view.

    Mirrors Blender's ``BKE_camera_sensor_size``: under ``AUTO`` the sensor *width* is the
    size of whichever image dimension is larger, and ``sensor_height`` only applies to an
    explicitly vertical fit.
    """
    if lens_mm <= 0.0:
        raise ValueError("lens_mm must be positive.")
    width = max(resolution_x * pixel_aspect_x, 1e-9)
    height = max(resolution_y * pixel_aspect_y, 1e-9)
    fit = sensor_fit.upper()
    if fit == "AUTO":
        fits_horizontally = width >= height
        sensor_size = sensor_width
    else:
        fits_horizontally = fit != "VERTICAL"
        sensor_size = sensor_width if fits_horizontally else sensor_height
    half = (sensor_size / 2.0) / lens_mm
    if fits_horizontally:
        return half, half * height / width
    return half * width / height, half


def bounds_corners(minimum: Vec3, maximum: Vec3) -> list[Vec3]:
    return [
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]


def frame_coordinates(
    point: Vec3,
    *,
    location: Vec3,
    right: Vec3,
    up: Vec3,
    forward: Vec3,
    tan_h: float,
    tan_v: float,
) -> tuple[float, float, float]:
    """Normalised device coordinates of ``point``, plus its depth in front of the camera.

    A point is inside the frame when both coordinates are within -1..1 and depth is positive.
    """
    relative = _sub(point, location)
    depth = _dot(relative, forward)
    if depth <= 1e-9:
        return (math.inf, math.inf, depth)
    return (
        _dot(relative, right) / (depth * tan_h),
        _dot(relative, up) / (depth * tan_v),
        depth,
    )


def solve_camera(
    minimum: Vec3,
    maximum: Vec3,
    *,
    azimuth_degrees: float = 145.0,
    elevation_degrees: float = 24.0,
    margin: float = 1.06,
    target_height_bias: float = 0.0,
    lens_mm: float = 48.0,
    orthographic: bool = False,
    sensor_width: float = 36.0,
    sensor_height: float = 24.0,
    sensor_fit: str = "AUTO",
    resolution_x: int = 1920,
    resolution_y: int = 1080,
    pixel_aspect_x: float = 1.0,
    pixel_aspect_y: float = 1.0,
) -> CameraSolution:
    """Place a camera so an axis-aligned bounding box exactly fits the frame.

    The distance is solved from the eight box corners instead of estimated from a radius,
    so a wide flat site is framed as tightly as a compact tall one.
    """
    if any(maximum[axis] < minimum[axis] for axis in range(3)):
        raise ValueError("maximum must be component-wise greater than or equal to minimum.")
    margin = max(float(margin), 1.0)
    elevation = min(max(float(elevation_degrees), 1.0), 89.0)

    target = (
        (minimum[0] + maximum[0]) / 2.0,
        (minimum[1] + maximum[1]) / 2.0,
        (minimum[2] + maximum[2]) / 2.0 + (maximum[2] - minimum[2]) * float(target_height_bias),
    )
    offset = spherical_direction(elevation, azimuth_degrees)
    right, up, forward = camera_basis(offset)
    diagonal = math.sqrt(_dot(_sub(maximum, minimum), _sub(maximum, minimum)))
    relative = [_sub(corner, target) for corner in bounds_corners(minimum, maximum)]

    ortho_scale = 0.0
    if orthographic:
        width = max(resolution_x * pixel_aspect_x, 1e-9)
        height = max(resolution_y * pixel_aspect_y, 1e-9)
        aspect = width / height
        half_right = max(abs(_dot(corner, right)) for corner in relative)
        half_up = max(abs(_dot(corner, up)) for corner in relative)
        if aspect >= 1.0:
            ortho_scale = 2.0 * margin * max(half_right, half_up * aspect)
        else:
            ortho_scale = 2.0 * margin * max(half_up, half_right / aspect)
        deepest = max(_dot(corner, forward) for corner in relative)
        distance = max(deepest, 0.0) + diagonal
    else:
        tan_h, tan_v = half_angle_tangents(
            lens_mm=lens_mm,
            sensor_width=sensor_width,
            sensor_height=sensor_height,
            sensor_fit=sensor_fit,
            resolution_x=resolution_x,
            resolution_y=resolution_y,
            pixel_aspect_x=pixel_aspect_x,
            pixel_aspect_y=pixel_aspect_y,
        )
        tan_h = max(tan_h / margin, 1e-9)
        tan_v = max(tan_v / margin, 1e-9)
        distance = 0.0
        for corner in relative:
            along = _dot(corner, forward)
            required = max(abs(_dot(corner, right)) / tan_h, abs(_dot(corner, up)) / tan_v)
            distance = max(distance, required - along)
        distance = max(distance, diagonal * 0.05, 1.0)

    return CameraSolution(
        target=target,
        location=_add(target, _scale(offset, distance)),
        distance=distance,
        ortho_scale=ortho_scale,
    )


def points_bounds(points: Iterable[Vec3]) -> tuple[Vec3, Vec3] | None:
    minimum: list[float] | None = None
    maximum: list[float] | None = None
    for point in points:
        if minimum is None or maximum is None:
            minimum = [point[0], point[1], point[2]]
            maximum = [point[0], point[1], point[2]]
            continue
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])
    if minimum is None or maximum is None:
        return None
    return (minimum[0], minimum[1], minimum[2]), (maximum[0], maximum[1], maximum[2])
