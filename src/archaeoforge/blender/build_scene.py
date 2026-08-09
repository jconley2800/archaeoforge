"""Dependency-free Blender scene builder for ArchaeoForge manifests.

Run through Blender, not normal Python:
blender --background --python build_scene.py -- --project /path/to/project --manifest scene_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# The camera solver is shared with the host-side test suite, which cannot import bpy. The
# script still ships inside the package, so its parent directories locate that module in a
# source checkout and in an installed wheel alike.
_PACKAGE_PARENT = str(Path(__file__).resolve().parents[2])
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)
try:
    from archaeoforge.framing import solve_camera, spherical_direction
    from archaeoforge.object_index import build_object_index_map
    from archaeoforge.polyline import mitered_segment_polygons
except ImportError as exc:  # pragma: no cover - only reachable if the file is copied out
    raise RuntimeError(
        "build_scene.py must stay inside the archaeoforge package so that "
        "its shared geometry helpers can be imported."
    ) from exc


CLASS_COLORS = {
    "A": (0.08, 0.55, 0.20, 1.0),
    "B": (0.08, 0.32, 0.85, 1.0),
    "C": (0.92, 0.48, 0.05, 1.0),
    "D": (0.75, 0.06, 0.08, 1.0),
}
MATERIAL_COLORS = {
    "sand": (0.48, 0.30, 0.14, 1.0),
    "mudbrick": (0.42, 0.24, 0.10, 1.0),
    "plaster": (0.68, 0.57, 0.39, 1.0),
    "baked_brick": (0.48, 0.19, 0.07, 1.0),
    "blue_glaze": (0.015, 0.09, 0.32, 1.0),
    "dark_bitumen": (0.025, 0.018, 0.012, 1.0),
    "road": (0.28, 0.16, 0.075, 1.0),
    "water": (0.025, 0.20, 0.28, 1.0),
    "vegetation": (0.10, 0.25, 0.055, 1.0),
    "wood": (0.19, 0.075, 0.025, 1.0),
}
KNOWN_RENDER_PASS_FILES = (
    "depth.exr",
    "normal.exr",
    "diffuse.exr",
    "object_index.exr",
    "cryptomatte_object.exr",
)


def parse_args() -> argparse.Namespace:
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--render", action="store_true")
    return parser.parse_args(args)


def reset_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def make_material(name: str, color: tuple[float, float, float, float], roughness: float = 0.72) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    principled = nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = roughness
        if "Specular IOR Level" in principled.inputs:
            principled.inputs["Specular IOR Level"].default_value = 0.28
        if name == "water":
            principled.inputs["Roughness"].default_value = 0.18
            principled.inputs["Metallic"].default_value = 0.0
            if "Transmission Weight" in principled.inputs:
                principled.inputs["Transmission Weight"].default_value = 0.18
    return material


def material_library(render_mode: str) -> dict[str, bpy.types.Material]:
    materials = {name: make_material(name, color) for name, color in MATERIAL_COLORS.items()}
    for evidence_class, color in CLASS_COLORS.items():
        materials[f"evidence_{evidence_class}"] = make_material(f"evidence_{evidence_class}", color, 0.58)
    materials["default"] = materials["mudbrick"]
    materials["render_mode"] = render_mode  # type: ignore[assignment]
    return materials


def choose_material(feature: dict, materials: dict[str, bpy.types.Material]) -> bpy.types.Material:
    if materials.get("render_mode") == "evidence":
        return materials.get(f"evidence_{feature.get('evidence_class', 'D')}", materials["default"])
    name = str(feature.get("params", {}).get("material") or "mudbrick")
    return materials.get(name, materials["default"])


def add_material(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    if hasattr(obj.data, "materials"):
        obj.data.materials.append(material)


def create_cube(
    name: str,
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: bpy.types.Material,
    rotation_z: float = 0.0,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location, rotation=(0.0, 0.0, rotation_z))
    obj = bpy.context.active_object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    add_material(obj, material)
    return obj


def create_cylinder(
    name: str,
    location: tuple[float, float, float],
    radius: float,
    depth: float,
    material: bpy.types.Material,
    vertices: int = 12,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location)
    obj = bpy.context.active_object
    obj.name = name
    add_material(obj, material)
    return obj


def create_polygon_prism(
    name: str,
    coords: list[list[float]],
    z0: float,
    height: float,
    material: bpy.types.Material,
) -> bpy.types.Object:
    if len(coords) > 1 and coords[0][:2] == coords[-1][:2]:
        coords = coords[:-1]
    curve = bpy.data.curves.new(name=f"{name}_curve", type="CURVE")
    curve.dimensions = "2D"
    curve.resolution_u = 1
    curve.fill_mode = "BOTH"
    curve.extrude = max(height, 0.02) / 2.0
    curve.resolution_u = 1
    spline = curve.splines.new("POLY")
    spline.points.add(len(coords) - 1)
    for point, coordinate in zip(spline.points, coords, strict=True):
        point.co = (float(coordinate[0]), float(coordinate[1]), 0.0, 1.0)
    spline.use_cyclic_u = True
    obj = bpy.data.objects.new(name, curve)
    obj.location.z = z0 + max(height, 0.02) / 2.0
    bpy.context.collection.objects.link(obj)
    add_material(obj, material)
    return obj


def create_segment_boxes(
    name: str,
    coords: list[list[float]],
    width: float,
    height: float,
    z0: float,
    material: bpy.types.Material,
    cyclic: bool = False,
    miter_limit: float = 4.0,
) -> list[bpy.types.Object]:
    objects: list[bpy.types.Object] = []
    for index, polygon in enumerate(
        mitered_segment_polygons(coords, width, cyclic=cyclic, miter_limit=miter_limit),
        start=1,
    ):
        objects.append(
            create_polygon_prism(
                f"{name}_segment_{index:03d}",
                polygon,
                z0,
                height,
                material,
            )
        )
    return objects


def geometry_parts(geometry: dict) -> list[tuple[str, list]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        return [("Point", coordinates)]
    if geometry_type == "LineString":
        return [("LineString", coordinates)]
    if geometry_type == "Polygon":
        return [("Polygon", coordinates[0])]
    if geometry_type == "MultiPoint":
        return [("Point", item) for item in coordinates]
    if geometry_type == "MultiLineString":
        return [("LineString", item) for item in coordinates]
    if geometry_type == "MultiPolygon":
        return [("Polygon", item[0]) for item in coordinates]
    raise ValueError(f"Unsupported geometry type: {geometry_type}")


def centroid_of_ring(coords: list[list[float]]) -> tuple[float, float]:
    if len(coords) > 1 and coords[0][:2] == coords[-1][:2]:
        coords = coords[:-1]
    return (
        sum(float(point[0]) for point in coords) / max(len(coords), 1),
        sum(float(point[1]) for point in coords) / max(len(coords), 1),
    )


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    if len(polygon) < 3:
        return False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def rect_inside_polygon(x: float, y: float, width: float, depth: float, polygon: list[list[float]]) -> bool:
    return all(
        point_in_polygon(cx, cy, polygon)
        for cx, cy in (
            (x - width / 2, y - depth / 2),
            (x + width / 2, y - depth / 2),
            (x + width / 2, y + depth / 2),
            (x - width / 2, y + depth / 2),
        )
    )


def deterministic_seed(feature_id: str, explicit: int | None = None) -> int:
    if explicit is not None:
        return int(explicit)
    return int(hashlib.sha256(feature_id.encode("utf-8")).hexdigest()[:16], 16)


def tag_objects(objects: list[bpy.types.Object], feature: dict, object_index_map: dict[str, int]) -> None:
    provenance_json = json.dumps(feature.get("provenance", []), ensure_ascii=False)
    evidence_ids = ";".join(feature.get("evidence_ids", []))
    for index, obj in enumerate(objects, start=1):
        obj["archaeoforge_feature_id"] = feature["id"]
        obj["archaeoforge_feature_part"] = index
        obj["archaeoforge_evidence_ids"] = evidence_ids
        obj["archaeoforge_evidence_class"] = feature.get("evidence_class", "D")
        obj["archaeoforge_confidence"] = float(feature.get("confidence", 0.0))
        obj["archaeoforge_review_status"] = feature.get("review_status", "draft")
        obj["archaeoforge_provenance_json"] = provenance_json
        obj["archaeoforge_notes"] = feature.get("notes", "")
        obj.pass_index = object_index_map[feature["id"]]


def build_flat_polygon(feature: dict, materials: dict, default_height: float) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    height = float(params.get("height", default_height))
    z0 = float(params.get("z", 0.0))
    material = choose_material(feature, materials)
    objects = []
    for index, (part_type, coords) in enumerate(geometry_parts(feature["geometry"]), start=1):
        if part_type == "Polygon":
            objects.append(create_polygon_prism(f"{feature['id']}_{index}", coords, z0, height, material))
    return objects


def build_line_feature(feature: dict, materials: dict, *, default_width: float, default_height: float) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    width = float(params.get("width", default_width))
    height = float(params.get("height", default_height))
    z0 = float(params.get("z", 0.0))
    material = choose_material(feature, materials)
    objects: list[bpy.types.Object] = []
    for index, (part_type, coords) in enumerate(geometry_parts(feature["geometry"]), start=1):
        if part_type == "LineString":
            objects.extend(
                create_segment_boxes(
                    f"{feature['id']}_{index}",
                    coords,
                    width,
                    height,
                    z0,
                    material,
                    cyclic=bool(params.get("cyclic", False)),
                    miter_limit=float(params.get("miter_limit", 4.0)),
                )
            )
    return objects


def build_point_box(feature: dict, materials: dict) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    material = choose_material(feature, materials)
    width = float(params.get("width", 10.0))
    length = float(params.get("length", width))
    height = float(params.get("height", 8.0))
    z0 = float(params.get("z", 0.0))
    rotation = math.radians(float(params.get("rotation_degrees", 0.0)))
    objects = []
    for index, (part_type, coords) in enumerate(geometry_parts(feature["geometry"]), start=1):
        if part_type == "Point":
            objects.append(
                create_cube(
                    f"{feature['id']}_{index}",
                    (float(coords[0]), float(coords[1]), z0 + height / 2),
                    (width, length, height),
                    material,
                    rotation_z=rotation,
                )
            )
    return objects


def build_ziggurat(feature: dict, materials: dict) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    material = choose_material(feature, materials)
    stage_count = int(params.get("stage_count", 7))
    base_size = float(params.get("base_size", params.get("base_width", 90.0)))
    stage_sizes = [float(value) for value in params.get("stage_sizes", [])]
    stage_heights = [float(value) for value in params.get("stage_heights", [])]
    recede = float(params.get("recede_per_stage", base_size * 0.10))
    default_height = float(params.get("stage_height", 8.0))
    rotation = math.radians(float(params.get("rotation_degrees", 0.0)))
    z0 = float(params.get("z", 0.0))
    objects: list[bpy.types.Object] = []
    for _, point in geometry_parts(feature["geometry"]):
        if not isinstance(point, list):
            continue
        x, y = float(point[0]), float(point[1])
        current_z = z0
        for index in range(stage_count):
            size = stage_sizes[index] if index < len(stage_sizes) else max(base_size - recede * index, base_size * 0.18)
            height = stage_heights[index] if index < len(stage_heights) else default_height
            obj = create_cube(
                f"{feature['id']}_stage_{index + 1:02d}",
                (x, y, current_z + height / 2),
                (size, size, height),
                material,
                rotation_z=rotation,
            )
            objects.append(obj)
            current_z += height
        shrine = params.get("summit_shrine") or {}
        if shrine:
            width = float(shrine.get("width", max(base_size * 0.18, 8.0)))
            length = float(shrine.get("length", width))
            height = float(shrine.get("height", 6.0))
            objects.append(
                create_cube(
                    f"{feature['id']}_summit_shrine",
                    (x, y, current_z + height / 2),
                    (width, length, height),
                    materials.get(str(shrine.get("material", "plaster")), material),
                    rotation_z=rotation,
                )
            )
        stairs = params.get("stairs") or {}
        if stairs:
            count = int(stairs.get("steps", 24))
            total_length = float(stairs.get("length", base_size * 0.62))
            width = float(stairs.get("width", base_size * 0.12))
            total_height = float(stairs.get("height", stage_heights[0] if stage_heights else default_height))
            for step in range(count):
                ratio = (step + 1) / count
                step_length = total_length / count
                # Rotate the step offset with the ziggurat; spinning each step box on its own
                # axis would leave the whole staircase attached to the unrotated south face.
                local_y = -base_size / 2 - total_length / 2 + step_length * (step + 0.5)
                sx = x - local_y * math.sin(rotation)
                sy = y + local_y * math.cos(rotation)
                objects.append(
                    create_cube(
                        f"{feature['id']}_stair_{step + 1:03d}",
                        (sx, sy, z0 + total_height * ratio / 2),
                        (width, step_length, total_height * ratio),
                        material,
                        rotation_z=rotation,
                    )
                )
    return objects


def build_gate(feature: dict, materials: dict) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    material = choose_material(feature, materials)
    rotation = math.radians(float(params.get("rotation_degrees", 0.0)))
    front_width = float(params.get("front_width", 28.0))
    front_depth = float(params.get("front_depth", 13.0))
    main_width = float(params.get("main_width", 22.0))
    main_depth = float(params.get("main_depth", 33.0))
    passage = float(params.get("passage_width", 5.5))
    separation = float(params.get("separation", 14.0))
    height = float(params.get("height", 17.0))
    z0 = float(params.get("z", 0.0))
    objects: list[bpy.types.Object] = []
    for _, point in geometry_parts(feature["geometry"]):
        x, y = float(point[0]), float(point[1])
        for prefix, center_y, total_width, depth, local_height in (
            ("front", y - separation / 2, front_width, front_depth, height * 0.84),
            ("main", y + separation / 2, main_width, main_depth, height),
        ):
            tower_width = max((total_width - passage) / 2.0, 1.0)
            for side, offset in (("west", -(passage + tower_width) / 2), ("east", (passage + tower_width) / 2)):
                local_x, local_y = offset, center_y - y
                world_x = x + local_x * math.cos(rotation) - local_y * math.sin(rotation)
                world_y = y + local_x * math.sin(rotation) + local_y * math.cos(rotation)
                objects.append(
                    create_cube(
                        f"{feature['id']}_{prefix}_{side}",
                        (world_x, world_y, z0 + local_height / 2),
                        (tower_width, depth, local_height),
                        material,
                        rotation_z=rotation,
                    )
                )
            lintel_height = local_height * 0.28
            objects.append(
                create_cube(
                    f"{feature['id']}_{prefix}_lintel",
                    (x, center_y, z0 + local_height - lintel_height / 2),
                    (passage, depth, lintel_height),
                    material,
                    rotation_z=rotation,
                )
            )
    return objects


def build_residential(feature: dict, materials: dict) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    material = choose_material(feature, materials)
    count = int(params.get("count", 80))
    min_width, max_width = [float(v) for v in params.get("width_range", [6.0, 15.0])]
    min_depth, max_depth = [float(v) for v in params.get("depth_range", [7.0, 18.0])]
    min_height, max_height = [float(v) for v in params.get("height_range", [3.5, 7.5])]
    spacing = float(params.get("spacing", 1.8))
    z0 = float(params.get("z", 0.0))
    seed = deterministic_seed(feature["id"], params.get("seed"))
    rng = random.Random(seed)
    objects: list[bpy.types.Object] = []

    for _, polygon in geometry_parts(feature["geometry"]):
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        bounds = (min(xs), min(ys), max(xs), max(ys))
        occupied: list[tuple[float, float, float, float]] = []
        attempts = 0
        while len(objects) < count and attempts < count * 120:
            attempts += 1
            width = rng.uniform(min_width, max_width)
            depth = rng.uniform(min_depth, max_depth)
            x = rng.uniform(bounds[0] + width / 2, bounds[2] - width / 2)
            y = rng.uniform(bounds[1] + depth / 2, bounds[3] - depth / 2)
            if not rect_inside_polygon(x, y, width, depth, polygon):
                continue
            rect = (x - width / 2 - spacing, y - depth / 2 - spacing, x + width / 2 + spacing, y + depth / 2 + spacing)
            if any(not (rect[2] < other[0] or rect[0] > other[2] or rect[3] < other[1] or rect[1] > other[3]) for other in occupied):
                continue
            occupied.append(rect)
            height = rng.uniform(min_height, max_height)
            obj = create_cube(
                f"{feature['id']}_house_{len(objects) + 1:04d}",
                (x, y, z0 + height / 2),
                (width, depth, height),
                material,
                rotation_z=0.0 if rng.random() < 0.8 else math.radians(90.0),
            )
            objects.append(obj)
    return objects


def build_tree(feature: dict, materials: dict) -> list[bpy.types.Object]:
    params = feature.get("params", {})
    trunk_height = float(params.get("height", 8.0))
    trunk_radius = float(params.get("radius", 0.35))
    crown_radius = float(params.get("crown_radius", 2.2))
    z0 = float(params.get("z", 0.0))
    objects: list[bpy.types.Object] = []
    for index, (_, point) in enumerate(geometry_parts(feature["geometry"]), start=1):
        x, y = float(point[0]), float(point[1])
        trunk = create_cylinder(
            f"{feature['id']}_trunk_{index}",
            (x, y, z0 + trunk_height / 2),
            trunk_radius,
            trunk_height,
            materials["wood"],
            vertices=10,
        )
        objects.append(trunk)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=crown_radius, location=(x, y, z0 + trunk_height))
        crown = bpy.context.active_object
        crown.name = f"{feature['id']}_crown_{index}"
        crown.scale.z = 0.45
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        add_material(crown, materials["vegetation"])
        objects.append(crown)
    return objects


def build_feature(feature: dict, materials: dict) -> list[bpy.types.Object]:
    template = feature.get("template", "building")
    if template in {"terrain", "context"}:
        return build_flat_polygon(feature, materials, 0.25)
    if template in {"water", "river"}:
        return build_flat_polygon(feature, materials, 0.08)
    if template in {"building", "palace", "temple", "platform"}:
        parts = geometry_parts(feature["geometry"])
        if any(part_type == "Polygon" for part_type, _ in parts):
            return build_flat_polygon(feature, materials, 8.0)
        return build_point_box(feature, materials)
    if template in {"wall", "city_wall"}:
        return build_line_feature(feature, materials, default_width=6.0, default_height=10.0)
    if template in {"road", "processional"}:
        return build_line_feature(feature, materials, default_width=12.0, default_height=0.18)
    if template == "canal":
        feature = dict(feature)
        feature["params"] = {"material": "water", "width": 8.0, "height": 0.08, **feature.get("params", {})}
        return build_line_feature(feature, materials, default_width=8.0, default_height=0.08)
    if template == "ziggurat":
        return build_ziggurat(feature, materials)
    if template == "gate":
        return build_gate(feature, materials)
    if template == "residential_cluster":
        return build_residential(feature, materials)
    if template in {"tree", "palm"}:
        return build_tree(feature, materials)
    return build_point_box(feature, materials)


def look_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


FRAMING_EXCLUDED_TEMPLATES = {"terrain", "context"}


def world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector] | None:
    """Axis-aligned world-space bounding box of the supplied objects."""
    bpy.context.view_layer.update()
    minimum: Vector | None = None
    maximum: Vector | None = None
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            if minimum is None or maximum is None:
                minimum = point.copy()
                maximum = point.copy()
                continue
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
    if minimum is None or maximum is None:
        return None
    return minimum, maximum


def sun_direction(elevation_degrees: float, azimuth_degrees: float) -> Vector:
    """Unit vector pointing from the site towards the sun."""
    return Vector(spherical_direction(elevation_degrees, azimuth_degrees))


def frame_camera(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    camera_data: bpy.types.Camera,
    bounds: tuple[Vector, Vector],
    camera_config: dict,
) -> tuple[Vector, Vector]:
    """Place the camera so the whole bounding box fits the frame with a margin."""
    minimum, maximum = bounds
    render = scene.render
    solution = solve_camera(
        tuple(minimum),
        tuple(maximum),
        azimuth_degrees=float(camera_config.get("azimuth_degrees", 145.0)),
        elevation_degrees=float(camera_config.get("elevation_degrees", 24.0)),
        margin=float(camera_config.get("margin", 1.06)),
        target_height_bias=float(camera_config.get("target_height_bias", 0.0)),
        lens_mm=camera_data.lens,
        orthographic=camera_data.type == "ORTHO",
        sensor_width=camera_data.sensor_width,
        sensor_height=camera_data.sensor_height,
        sensor_fit=camera_data.sensor_fit,
        resolution_x=render.resolution_x,
        resolution_y=render.resolution_y,
        pixel_aspect_x=render.pixel_aspect_x,
        pixel_aspect_y=render.pixel_aspect_y,
    )
    if camera_data.type == "ORTHO":
        camera_data.ortho_scale = solution.ortho_scale
    camera.location = Vector(solution.location)
    look_at(camera, solution.target)
    return Vector(solution.target), camera.location


def apply_camera_clipping(
    camera: bpy.types.Object,
    camera_data: bpy.types.Camera,
    bounds: tuple[Vector, Vector] | None,
) -> None:
    """Size the clip range to the scene.

    Blender's factory far plane is 1000 m and a site is routinely wider than that, so the
    default silently deletes the far half of the model with a razor-straight edge. It looks
    like a framing problem, and re-aiming the camera only clips more.
    """
    if bounds is None:
        reach = camera.location.length
    else:
        minimum, maximum = bounds
        reach = max(
            (Vector((x, y, z)) - camera.location).length
            for x in (minimum.x, maximum.x)
            for y in (minimum.y, maximum.y)
            for z in (minimum.z, maximum.z)
        )
    camera_data.clip_end = max(reach * 1.5, 1000.0)
    camera_data.clip_start = max(camera_data.clip_end / 100000.0, 0.1)


def setup_camera(
    scene: bpy.types.Scene,
    config: dict,
    bounds: tuple[Vector, Vector] | None = None,
    clipping_bounds: tuple[Vector, Vector] | None = None,
) -> None:
    camera_config = config.get("camera", {})
    camera_data = bpy.data.cameras.new("ArchaeoForge Camera")
    camera = bpy.data.objects.new("ArchaeoForge Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera_data.lens = float(camera_config.get("lens_mm", 48.0))
    if camera_config.get("orthographic"):
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = float(camera_config.get("ortho_scale", 600.0))
    scene.camera = camera

    if camera_config.get("auto_frame", True) and bounds is not None:
        target, location = frame_camera(scene, camera, camera_data, bounds, camera_config)
        apply_camera_clipping(camera, camera_data, clipping_bounds or bounds)
        print(
            "ARCHAEOFORGE CAMERA auto_frame target=({:.1f}, {:.1f}, {:.1f}) "
            "location=({:.1f}, {:.1f}, {:.1f}) clip_end={:.1f}".format(
                *target, *location, camera_data.clip_end
            )
        )
        return

    camera.location = tuple(camera_config.get("location", [320.0, -420.0, 260.0]))
    look_at(camera, tuple(camera_config.get("target", [0.0, 60.0, 20.0])))
    apply_camera_clipping(camera, camera_data, clipping_bounds or bounds)


def setup_world(scene: bpy.types.Scene, config: dict) -> None:
    """Build a node-based sky world.

    ``World.color`` only applies to worlds that do not use nodes, and every world Blender
    creates does use nodes, so setting it renders nothing. The background and all ambient
    fill therefore have to come from the node tree.
    """
    sky_config = config.get("sky", {})
    sun_config = config.get("sun", {})
    world = scene.world or bpy.data.worlds.new("ArchaeoForge World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputWorld")
    background = tree.nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = float(sky_config.get("strength", 1.0))
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])

    if sky_config.get("procedural_sky", True):
        sky = tree.nodes.new("ShaderNodeTexSky")
        if "MULTIPLE_SCATTERING" in {item.identifier for item in sky.bl_rna.properties["sky_type"].enum_items}:
            sky.sky_type = "MULTIPLE_SCATTERING"
        elif "NISHITA" in {item.identifier for item in sky.bl_rna.properties["sky_type"].enum_items}:
            sky.sky_type = "NISHITA"
        # The sun lamp supplies the key light; a second sun disc in the sky would double it.
        sky.sun_disc = False
        sky.sun_elevation = math.radians(float(sun_config.get("elevation_degrees", 42.0)))
        sky.sun_rotation = math.radians(float(sun_config.get("azimuth_degrees", 215.0)))
        sky.turbidity = float(sky_config.get("turbidity", 2.6))
        sky.ground_albedo = float(sky_config.get("ground_albedo", 0.32))
        sky.air_density = float(sky_config.get("air_density", 1.0))
        for attribute in ("aerosol_density", "dust_density"):
            if hasattr(sky, attribute):
                setattr(sky, attribute, float(sky_config.get("dust_density", 1.2)))
        tree.links.new(sky.outputs["Color"], background.inputs["Color"])
    else:
        background.inputs["Color"].default_value = tuple(
            sky_config.get("color", [0.28, 0.42, 0.62, 1.0])
        )


def setup_lighting(scene: bpy.types.Scene, config: dict) -> None:
    sun_config = config.get("sun", {})
    light_data = bpy.data.lights.new("ArchaeoForge Sun", type="SUN")
    light_data.energy = float(sun_config.get("energy", 3.6))
    light_data.angle = math.radians(float(sun_config.get("angle_degrees", 1.6)))
    sun = bpy.data.objects.new("ArchaeoForge Sun", light_data)
    bpy.context.collection.objects.link(sun)

    rotations = sun_config.get("rotation_degrees")
    if sun_config.get("elevation_degrees") is None and rotations:
        sun.rotation_euler = tuple(math.radians(float(value)) for value in rotations)
    else:
        # The sun lamp shines along its local -Z, so aim local +Z at the sun.
        direction = sun_direction(
            float(sun_config.get("elevation_degrees") or 42.0),
            float(sun_config.get("azimuth_degrees", 215.0)),
        )
        sun.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()

    setup_world(scene, config)


def setup_render(scene: bpy.types.Scene, blender_config: dict, project_root: Path) -> None:
    requested_engine = blender_config.get("engine", "BLENDER_EEVEE_NEXT")
    engine_candidates = {
        "BLENDER_EEVEE_NEXT": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"),
        "BLENDER_EEVEE": ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"),
    }.get(requested_engine, (requested_engine,))
    for engine in engine_candidates:
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    else:
        raise RuntimeError(f"Unsupported Blender render engine: {requested_engine}")
    scene.render.resolution_x = int(blender_config.get("resolution_x", 1024))
    scene.render.resolution_y = int(blender_config.get("resolution_y", 1024))
    scene.render.resolution_percentage = int(blender_config.get("resolution_percentage", 100))
    scene.render.film_transparent = bool(blender_config.get("transparent_background", False))
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = str(project_root / "outputs" / "renders" / "beauty.png")

    view_transform = str(blender_config.get("view_transform", "AgX"))
    try:
        scene.view_settings.view_transform = view_transform
    except TypeError:
        print(
            f"ARCHAEOFORGE WARNING view transform {view_transform!r} is unavailable in this Blender build; "
            f"keeping {scene.view_settings.view_transform!r}"
        )
    requested_look = blender_config.get("look")
    if requested_look:
        # The available looks depend on the active view transform, so this has to come after it,
        # and a bad name has to be reported rather than silently swallowed.
        try:
            scene.view_settings.look = requested_look
        except TypeError:
            available = [item.identifier for item in scene.view_settings.bl_rna.properties["look"].enum_items]
            print(
                f"ARCHAEOFORGE WARNING colour-management look {requested_look!r} is not available for view "
                f"transform {scene.view_settings.view_transform!r}; available: {available}"
            )
    scene.view_settings.exposure = float(blender_config.get("exposure", 0.0))

    samples = int(blender_config.get("samples", 64))
    if scene.render.engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = samples
    eevee = getattr(scene, "eevee", None)
    if eevee is not None and scene.render.engine.startswith("BLENDER_EEVEE"):
        if hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = samples
        if hasattr(eevee, "use_shadows"):
            eevee.use_shadows = bool(blender_config.get("shadows", True))
        # Without ray tracing EEVEE has no ambient occlusion or indirect bounce, so every
        # surface facing away from the sun collapses to the flat world colour.
        if hasattr(eevee, "use_raytracing"):
            eevee.use_raytracing = bool(blender_config.get("raytracing", True))
        if hasattr(eevee, "shadow_ray_count"):
            eevee.shadow_ray_count = int(blender_config.get("shadow_ray_count", 4))
        if hasattr(eevee, "shadow_step_count"):
            eevee.shadow_step_count = int(blender_config.get("shadow_step_count", 6))

    view_layer = scene.view_layers[0]
    view_layer.use_pass_z = True
    view_layer.use_pass_normal = True
    view_layer.use_pass_diffuse_color = True
    view_layer.use_pass_object_index = True
    if hasattr(view_layer, "use_pass_cryptomatte_object"):
        view_layer.use_pass_cryptomatte_object = True

    if blender_config.get("render_passes", True):
        modern_compositor = hasattr(scene, "compositing_node_group")
        if modern_compositor:
            existing_names = {group.name for group in bpy.data.node_groups}
            bpy.ops.node.new_compositing_node_group(name="ArchaeoForge Compositor")
            tree = next(group for group in bpy.data.node_groups if group.name not in existing_names)
            scene.compositing_node_group = tree
        else:
            scene.use_nodes = True
            tree = scene.node_tree
        tree.nodes.clear()
        render_layers = tree.nodes.new("CompositorNodeRLayers")
        composite = tree.nodes.new("NodeGroupOutput" if modern_compositor else "CompositorNodeComposite")
        tree.links.new(render_layers.outputs["Image"], composite.inputs["Image"])
        for socket_names, file_name in (
            (("Depth",), "depth"),
            (("Normal",), "normal"),
            (("DiffCol", "Diffuse Color"), "diffuse"),
            (("IndexOB", "Object Index"), "object_index"),
            (("CryptoObject00",), "cryptomatte_object"),
        ):
            render_socket = next((render_layers.outputs.get(name) for name in socket_names if name in render_layers.outputs), None)
            if render_socket is None:
                continue
            output = tree.nodes.new("CompositorNodeOutputFile")
            if modern_compositor:
                output.directory = str(project_root / "outputs" / "renders" / "passes")
                output.file_name = file_name
                socket_type = {
                    "NodeSocketFloat": "FLOAT",
                    "NodeSocketVector": "VECTOR",
                    "NodeSocketColor": "RGBA",
                }.get(render_socket.bl_idname, "RGBA")
                output.file_output_items.new(socket_type, render_socket.name)
                output_input = output.inputs[render_socket.name]
            else:
                output.base_path = str(project_root / "outputs" / "renders" / "passes")
                # The legacy file-output node appends the frame number to the slot path.
                output.file_slots[0].path = f"{file_name}_"
                output_input = output.inputs[0]
            output.format.file_format = "OPEN_EXR_MULTILAYER" if modern_compositor else "OPEN_EXR"
            output.format.color_depth = "32"
            tree.links.new(render_socket, output_input)


def clear_known_render_outputs(project_root: Path) -> Path:
    """Remove only reproducible pass files so unavailable passes cannot look current."""
    passes = project_root / "outputs" / "renders" / "passes"
    passes.mkdir(parents=True, exist_ok=True)
    for filename in KNOWN_RENDER_PASS_FILES:
        (passes / filename).unlink(missing_ok=True)
    return passes


def main() -> None:
    args = parse_args()
    project_root = Path(args.project).resolve()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    blender_config = manifest.get("blender", {})
    object_index_map = build_object_index_map(manifest.get("features", []))

    reset_scene()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    materials = material_library(blender_config.get("render_mode", "realistic"))

    evidence_collections: dict[str, bpy.types.Collection] = {}
    for evidence_class in ("A", "B", "C", "D"):
        collection = bpy.data.collections.new(f"Evidence Class {evidence_class}")
        scene.collection.children.link(collection)
        evidence_collections[evidence_class] = collection

    all_objects: list[bpy.types.Object] = []
    framing_objects: list[bpy.types.Object] = []
    for feature in manifest.get("features", []):
        try:
            objects = build_feature(feature, materials)
            tag_objects(objects, feature, object_index_map)
            all_objects.extend(objects)
            if str(feature.get("template", "")).lower() not in FRAMING_EXCLUDED_TEMPLATES:
                framing_objects.extend(objects)
            target_collection = evidence_collections.get(feature.get("evidence_class", "D"))
            if target_collection:
                for obj in objects:
                    for collection in list(obj.users_collection):
                        collection.objects.unlink(obj)
                    target_collection.objects.link(obj)
        except Exception as exc:
            print(f"ARCHAEOFORGE FEATURE ERROR {feature.get('id')}: {type(exc).__name__}: {exc}")
            raise

    camera_config = blender_config.get("camera", {})
    # Sheet terrain and context planes normally extend well past the site and would shrink
    # the reconstruction to a speck if they drove the framing. They still have to drive the
    # clip range, or the far half of the terrain is cut away.
    scene_bounds = world_bounds(all_objects)
    if camera_config.get("frame_includes_context", False):
        bounds = scene_bounds
    else:
        bounds = world_bounds(framing_objects) or scene_bounds

    setup_render(scene, blender_config, project_root)
    setup_camera(scene, blender_config, bounds, scene_bounds)
    setup_lighting(scene, blender_config)

    manifest_text = bpy.data.texts.new("ArchaeoForge Scene Manifest")
    manifest_text.write(json.dumps(manifest, indent=2, ensure_ascii=False))
    scene["archaeoforge_input_fingerprint"] = manifest.get("input_fingerprint", "")
    scene["archaeoforge_mode"] = manifest.get("mode", "unknown")
    scene["archaeoforge_project_id"] = manifest.get("project", {}).get("id", "")
    object_index_json = json.dumps(object_index_map, ensure_ascii=False, sort_keys=True)
    scene["archaeoforge_object_index_map"] = object_index_json
    object_index_path = project_root / "outputs" / "exports" / "object_index_map.json"
    object_index_path.parent.mkdir(parents=True, exist_ok=True)
    object_index_path.write_text(
        json.dumps(object_index_map, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    blend_path = project_root / "outputs" / f"{project_root.name}.blend"
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    if blender_config.get("save_blend", True):
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if args.render:
        clear_known_render_outputs(project_root)
        bpy.ops.render.render(write_still=True)
        if blender_config.get("save_blend", True):
            bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))


if __name__ == "__main__":
    main()
