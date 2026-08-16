"""End-to-end checks against a real Blender.

These are skipped when no Blender is available, which is the case in CI. They exist because
the defects they cover are invisible to every host-side test: a silently ignored world
setting and a camera that leaves the reconstruction as a speck in one corner both produce a
perfectly valid PNG.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from archaeoforge.blender_runner import blender_script_path

FLATPAK_BLENDER = Path.home() / ".local/share/flatpak/exports/bin/org.blender.Blender"


def _find_blender() -> str | None:
    explicit = os.environ.get("ARCHAEOFORGE_BLENDER")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("blender")
    if found:
        return found
    if FLATPAK_BLENDER.exists():
        return str(FLATPAK_BLENDER)
    return None


BLENDER = _find_blender()
requires_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")


@pytest.fixture
def scratch_root():
    """A working directory a sandboxed Blender can actually read.

    The Flatpak build of Blender gets a private /tmp, so pytest's tmp_path is invisible to
    it. Everything under the user's cache directory is reachable by both builds.
    """
    base = Path.home() / ".cache" / "archaeoforge" / "tests"
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _manifest(**blender_overrides) -> dict:
    blender = {
        "engine": "BLENDER_EEVEE",
        "resolution_x": 480,
        "resolution_y": 270,
        "samples": 8,
        "render_passes": False,
        "save_blend": False,
        "render_mode": "realistic",
        "camera": {"auto_frame": True, "azimuth_degrees": 152.0, "elevation_degrees": 22.0, "lens_mm": 40.0},
        "sun": {"elevation_degrees": 38.0, "azimuth_degrees": 232.0, "energy": 2.6},
        "sky": {"procedural_sky": True, "strength": 0.7},
        "exposure": -1.0,
    }
    blender.update(blender_overrides)
    return {
        "manifest_schema": 1,
        "mode": "preview",
        "input_fingerprint": "test",
        "project": {"id": "render-test"},
        "blender": blender,
        "features": [
            {
                "id": "GROUND",
                "template": "terrain",
                "evidence_class": "C",
                "confidence": 0.5,
                "review_status": "draft",
                "evidence_ids": [],
                "provenance": [],
                "params": {"material": "sand", "height": 0.3},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-400, -400], [400, -400], [400, 400], [-400, 400], [-400, -400]]],
                },
            },
            {
                "id": "TOWER",
                "template": "building",
                "evidence_class": "B",
                "confidence": 0.8,
                "review_status": "draft",
                "evidence_ids": [],
                "provenance": [],
                "params": {"material": "mudbrick", "width": 60.0, "length": 60.0, "height": 45.0},
                "geometry": {"type": "Point", "coordinates": [-80.0, 40.0]},
            },
        ],
    }


def _run(root: Path, manifest: dict, *, render: bool = True) -> str:
    (root / "outputs" / "renders").mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    command = [
        BLENDER,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(blender_script_path()),
        "--",
        "--project",
        str(root),
        "--manifest",
        str(manifest_path),
    ]
    if render:
        command.append("--render")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-4000:]
    return completed.stdout


@requires_blender
def test_render_frames_the_site_and_lights_it_with_a_sky(scratch_root):
    output = _run(scratch_root, _manifest())
    assert "ARCHAEOFORGE CAMERA auto_frame" in output

    image = Image.open(scratch_root / "outputs" / "renders" / "beauty.png").convert("RGB")
    width, height = image.size
    raw = image.tobytes()
    pixels = [tuple(raw[index : index + 3]) for index in range(0, len(raw), 3)]

    # The default Blender world renders a neutral grey. Setting World.color does not change
    # that, because every world Blender creates is node based, so a grey corner here means
    # the sky node tree was not built.
    sky = image.getpixel((2, 2))
    assert sky[2] > sky[0] + 8, f"top-left pixel {sky} is not sky coloured"

    # A subject that occupies almost nothing is the framing failure this replaces.
    def is_sky(pixel: tuple[int, int, int]) -> bool:
        return pixel[2] > pixel[0] + 8

    subject = sum(0 if is_sky(pixel) else 1 for pixel in pixels)
    coverage = subject / (width * height)
    assert 0.2 < coverage < 0.95, f"site covers {coverage:.1%} of the frame"


@requires_blender
def test_manual_camera_is_still_honoured(scratch_root):
    manifest = _manifest()
    manifest["blender"]["camera"] = {
        "auto_frame": False,
        "location": [400.0, -500.0, 300.0],
        "target": [0.0, 0.0, 20.0],
        "lens_mm": 40.0,
    }
    output = _run(scratch_root, manifest)
    assert "ARCHAEOFORGE CAMERA auto_frame" not in output
    assert (scratch_root / "outputs" / "renders" / "beauty.png").exists()


@requires_blender
def test_render_passes_are_written_without_a_trailing_underscore(scratch_root):
    passes = scratch_root / "outputs" / "renders" / "passes"
    passes.mkdir(parents=True, exist_ok=True)
    (passes / "object_index.exr").write_bytes(b"stale pass from an older Blender run")
    _run(scratch_root, _manifest(render_passes=True))
    written = sorted(path.name for path in passes.glob("*.exr"))
    assert written == ["cryptomatte_object.exr", "depth.exr", "diffuse.exr", "normal.exr"]


@requires_blender
def test_object_indices_are_distinct_and_the_map_is_persisted(scratch_root):
    _run(scratch_root, _manifest(save_blend=True), render=False)

    expected_map = {"GROUND": 1, "TOWER": 2}
    map_path = scratch_root / "outputs" / "exports" / "object_index_map.json"
    assert json.loads(map_path.read_text(encoding="utf-8")) == expected_map

    blend_path = scratch_root / "outputs" / f"{scratch_root.name}.blend"
    probe = (
        "import bpy,json; "
        "scene_map=json.loads(bpy.context.scene['archaeoforge_object_index_map']); "
        "objects=[(obj['archaeoforge_feature_id'],obj.pass_index) for obj in bpy.data.objects "
        "if 'archaeoforge_feature_id' in obj]; "
        "print('ARCHAEOFORGE_INDEX_PROBE '+json.dumps({'scene_map':scene_map,'objects':objects}))"
    )
    completed = subprocess.run(
        [BLENDER, "--background", str(blend_path), "--python-exit-code", "1", "--python-expr", probe],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-4000:]
    line = next(line for line in completed.stdout.splitlines() if line.startswith("ARCHAEOFORGE_INDEX_PROBE "))
    snapshot = json.loads(line.removeprefix("ARCHAEOFORGE_INDEX_PROBE "))

    assert snapshot["scene_map"] == expected_map
    assert len({pass_index for _, pass_index in snapshot["objects"]}) > 1
    assert all(expected_map[feature_id] == pass_index for feature_id, pass_index in snapshot["objects"])


@requires_blender
def test_sphinx_template_builds_an_east_facing_full_length_guide(scratch_root):
    manifest = _manifest(save_blend=True)
    manifest["features"] = [
        {
            "id": "SPHINX",
            "template": "sphinx",
            "evidence_class": "C",
            "confidence": 0.65,
            "review_status": "needs_review",
            "evidence_ids": ["EVID-SPHINX"],
            "provenance": [],
            # These are the legacy box-envelope names. The native template must retain
            # 73.5 m east-west by 19 m north-south without transposing the footprint.
            "params": {
                "material": "limestone",
                "width": 73.5,
                "length": 19.0,
                "height": 20.0,
                "rotation_degrees": 0.0,
            },
            "geometry": {"type": "Point", "coordinates": [100.0, -50.0]},
        }
    ]
    _run(scratch_root, manifest, render=False)

    blend_path = scratch_root / "outputs" / f"{scratch_root.name}.blend"
    probe = (
        "import bpy,json; "
        "objects={obj.name:{'location':[round(v,4) for v in obj.location],"
        "'dimensions':[round(v,4) for v in obj.dimensions],"
        "'feature_part':obj['archaeoforge_feature_part'],"
        "'template':obj['archaeoforge_template'],"
        "'recognizability':obj['archaeoforge_template_recognizability']} for obj in bpy.data.objects "
        "if obj.get('archaeoforge_feature_id')=='SPHINX'}; "
        "print('ARCHAEOFORGE_SPHINX_PROBE '+json.dumps(objects,sort_keys=True))"
    )
    completed = subprocess.run(
        [BLENDER, "--background", str(blend_path), "--python-exit-code", "1", "--python-expr", probe],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-4000:]
    line = next(line for line in completed.stdout.splitlines() if line.startswith("ARCHAEOFORGE_SPHINX_PROBE "))
    objects = json.loads(line.removeprefix("ARCHAEOFORGE_SPHINX_PROBE "))

    assert set(objects) == {
        "SPHINX_body",
        "SPHINX_chest",
        "SPHINX_forepaw_north",
        "SPHINX_forepaw_south",
        "SPHINX_headdress",
        "SPHINX_headdress_lappet_north",
        "SPHINX_headdress_lappet_south",
        "SPHINX_head",
        "SPHINX_muzzle",
        "SPHINX_nose",
    }
    assert objects["SPHINX_nose"]["location"][0] > objects["SPHINX_muzzle"]["location"][0]
    assert objects["SPHINX_muzzle"]["location"][0] > objects["SPHINX_head"]["location"][0]
    assert objects["SPHINX_head"]["location"][0] > objects["SPHINX_body"]["location"][0]
    assert objects["SPHINX_forepaw_north"]["location"][1] > -50.0
    assert objects["SPHINX_forepaw_south"]["location"][1] < -50.0
    assert objects["SPHINX_body"]["dimensions"] == pytest.approx([49.98, 16.34, 7.2], abs=0.01)
    west_edge = objects["SPHINX_body"]["location"][0] - objects["SPHINX_body"]["dimensions"][0] / 2
    east_edge = (
        objects["SPHINX_forepaw_north"]["location"][0]
        + objects["SPHINX_forepaw_north"]["dimensions"][0] / 2
    )
    assert (west_edge, east_edge) == pytest.approx((63.25, 136.75), abs=0.01)
    assert sorted(item["feature_part"] for item in objects.values()) == list(range(1, 11))
    assert {item["template"] for item in objects.values()} == {"sphinx"}
    assert {item["recognizability"] for item in objects.values()} == {"identity_specific"}


@requires_blender
def test_unavailable_colour_management_look_is_reported(scratch_root):
    output = _run(scratch_root, _manifest(look="Definitely Not A Look"))
    assert "colour-management look" in output
