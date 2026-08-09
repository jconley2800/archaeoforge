from __future__ import annotations

import math

import pytest

from archaeoforge.framing import (
    bounds_corners,
    camera_basis,
    frame_coordinates,
    half_angle_tangents,
    points_bounds,
    solve_camera,
    spherical_direction,
)

SITE_MIN = (-285.0, -350.0, 0.0)
SITE_MAX = (310.0, 500.0, 64.0)


def _visibility(solution, minimum, maximum, **camera):
    offset = tuple(solution.location[axis] - solution.target[axis] for axis in range(3))
    right, up, forward = camera_basis(offset)
    tan_h, tan_v = half_angle_tangents(**camera)
    return [
        frame_coordinates(
            corner,
            location=solution.location,
            right=right,
            up=up,
            forward=forward,
            tan_h=tan_h,
            tan_v=tan_v,
        )
        for corner in bounds_corners(minimum, maximum)
    ]


def test_spherical_direction_uses_compass_bearings():
    east = spherical_direction(0.0, 90.0)
    assert east == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)
    north = spherical_direction(0.0, 0.0)
    assert north == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)
    overhead = spherical_direction(90.0, 0.0)
    assert overhead[2] == pytest.approx(1.0)


def test_auto_frame_fits_every_corner_inside_the_frame():
    camera = dict(
        lens_mm=40.0,
        resolution_x=1536,
        resolution_y=864,
    )
    solution = solve_camera(
        SITE_MIN,
        SITE_MAX,
        azimuth_degrees=152.0,
        elevation_degrees=22.0,
        margin=1.05,
        **camera,
    )
    coordinates = _visibility(solution, SITE_MIN, SITE_MAX, **camera)
    assert all(depth > 0.0 for _, _, depth in coordinates)
    assert all(abs(x) <= 1.0 and abs(y) <= 1.0 for x, y, _ in coordinates)


def test_auto_frame_is_tight_rather_than_merely_safe():
    """The site must fill the frame; the old hand-placed camera left it a speck."""
    camera = dict(lens_mm=40.0, resolution_x=1536, resolution_y=864)
    solution = solve_camera(SITE_MIN, SITE_MAX, margin=1.05, **camera)
    coordinates = _visibility(solution, SITE_MIN, SITE_MAX, **camera)
    extent = max(max(abs(x), abs(y)) for x, y, _ in coordinates)
    # 1/1.05 = 0.952: at least one corner sits on the requested margin.
    assert extent == pytest.approx(1.0 / 1.05, abs=0.02)


def test_wider_margin_moves_the_camera_further_back():
    tight = solve_camera(SITE_MIN, SITE_MAX, margin=1.0, lens_mm=40.0)
    loose = solve_camera(SITE_MIN, SITE_MAX, margin=1.4, lens_mm=40.0)
    assert loose.distance > tight.distance


def test_longer_lens_moves_the_camera_further_back():
    wide = solve_camera(SITE_MIN, SITE_MAX, lens_mm=24.0)
    long = solve_camera(SITE_MIN, SITE_MAX, lens_mm=85.0)
    assert long.distance > wide.distance * 2.0


def test_camera_elevation_places_it_above_the_site():
    solution = solve_camera(SITE_MIN, SITE_MAX, elevation_degrees=22.0, lens_mm=40.0)
    assert solution.location[2] > SITE_MAX[2]
    assert solution.target == pytest.approx((12.5, 75.0, 32.0))


def test_elevation_is_clamped_out_of_the_ground():
    """A negative elevation would bury the camera and aim it at the sky."""
    below = solve_camera(SITE_MIN, SITE_MAX, elevation_degrees=-40.0, lens_mm=40.0)
    clamped = solve_camera(SITE_MIN, SITE_MAX, elevation_degrees=1.0, lens_mm=40.0)
    assert below.location[2] > below.target[2]
    assert below.location == pytest.approx(clamped.location)


def test_portrait_and_landscape_sensor_fit_differ():
    landscape = half_angle_tangents(lens_mm=50.0, resolution_x=1920, resolution_y=1080)
    portrait = half_angle_tangents(lens_mm=50.0, resolution_x=1080, resolution_y=1920)
    assert landscape[0] > landscape[1]
    assert portrait[1] > portrait[0]
    assert landscape[0] == pytest.approx(portrait[1])


def test_orthographic_scale_covers_the_site():
    solution = solve_camera(
        SITE_MIN,
        SITE_MAX,
        orthographic=True,
        margin=1.1,
        resolution_x=1536,
        resolution_y=864,
    )
    assert solution.ortho_scale > 0.0
    offset = tuple(solution.location[axis] - solution.target[axis] for axis in range(3))
    right, up, _ = camera_basis(offset)
    aspect = 1536 / 864
    for corner in bounds_corners(SITE_MIN, SITE_MAX):
        relative = tuple(corner[axis] - solution.target[axis] for axis in range(3))
        horizontal = sum(relative[axis] * right[axis] for axis in range(3))
        vertical = sum(relative[axis] * up[axis] for axis in range(3))
        assert abs(horizontal) <= solution.ortho_scale / 2.0 + 1e-6
        assert abs(vertical) <= solution.ortho_scale / (2.0 * aspect) + 1e-6


def test_degenerate_bounds_do_not_divide_by_zero():
    solution = solve_camera((5.0, 5.0, 5.0), (5.0, 5.0, 5.0), lens_mm=50.0)
    assert solution.distance >= 1.0
    assert all(math.isfinite(value) for value in solution.location)


def test_points_bounds_and_empty_input():
    assert points_bounds([]) is None
    assert points_bounds([(1.0, -2.0, 3.0), (-4.0, 5.0, 0.0)]) == (
        (-4.0, -2.0, 0.0),
        (1.0, 5.0, 3.0),
    )
