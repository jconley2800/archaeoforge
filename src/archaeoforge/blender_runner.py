from __future__ import annotations

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

from PIL import Image

from .project import ProjectPaths, load_config
from .template_semantics import template_recognizability
from .util import sha256_file


def find_blender(project: ProjectPaths) -> str | None:
    executable = load_config(project).blender.executable
    explicit = Path(executable).expanduser()
    if explicit.is_file():
        return str(explicit.resolve())
    return shutil.which(executable)


def blender_script_path() -> Path:
    return Path(str(files("archaeoforge.blender").joinpath("build_scene.py")))


def build_blender_command(project: ProjectPaths, *, render: bool = False) -> list[str]:
    executable = find_blender(project)
    if executable is None:
        raise RuntimeError(
            "Blender executable was not found. Install Blender or set blender.executable in project.yaml."
        )
    if not project.scene_manifest.exists():
        raise FileNotFoundError(f"Scene manifest not found: {project.scene_manifest}")
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(blender_script_path()),
        "--",
        "--project",
        str(project.root),
        "--manifest",
        str(project.scene_manifest),
    ]
    if render:
        command.append("--render")
    return command


def run_blender(project: ProjectPaths, *, render: bool = False) -> dict[str, Any]:
    command = build_blender_command(project, render=render)
    log_path = project.log_dir / ("blender-render.log" if render else "blender-build.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    if completed.returncode != 0:
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
        except OSError:
            pass
        raise RuntimeError(f"Blender failed with exit code {completed.returncode}.\n{tail}")
    manifest = json.loads(project.scene_manifest.read_text(encoding="utf-8"))
    result: dict[str, Any] = {
        "render_receipt_schema": 1,
        "command": command,
        "log": str(log_path),
        "blend_file": str(project.blend_file),
        "rendered": render,
        "manifest": {
            "path": project.scene_manifest.relative_to(project.root).as_posix(),
            "sha256": sha256_file(project.scene_manifest),
            "input_fingerprint": str(manifest.get("input_fingerprint", "")),
        },
        "feature_templates": {
            str(feature["id"]): {
                "template": str(feature.get("template", "building")),
                "recognizability": template_recognizability(feature.get("template", "building")),
            }
            for feature in manifest.get("features", [])
            if isinstance(feature, dict) and feature.get("id")
        },
    }
    if render:
        beauty = project.renders_dir / "beauty.png"
        if not beauty.is_file():
            raise RuntimeError("Blender exited successfully but did not write outputs/renders/beauty.png.")
        with Image.open(beauty) as image:
            result["beauty_image"] = {
                "path": beauty.relative_to(project.root).as_posix(),
                "sha256": sha256_file(beauty),
                "width": image.width,
                "height": image.height,
                "format": image.format,
            }
    project.blender_result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
