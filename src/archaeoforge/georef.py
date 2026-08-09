from __future__ import annotations

import csv
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .project import ProjectPaths, load_config

MIN_GCPS = {"affine": 3, "polynomial2": 6, "tps": 3}


def read_gcps(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        raise FileNotFoundError(path)
    gcps: list[dict[str, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            enabled = str(row.get("enabled", "true")).strip().lower()
            if enabled in {"0", "false", "no", "n"}:
                continue
            try:
                gcps.append(
                    {
                        "pixel_x": float(row["pixel_x"]),
                        "pixel_y": float(row["pixel_y"]),
                        "map_x": float(row["map_x"]),
                        "map_y": float(row["map_y"]),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid GCP row {row_number}: {exc}") from exc
    return gcps


def build_georef_commands(
    project: ProjectPaths,
    *,
    source_image: Path,
    gcps_path: Path,
    output_raster: Path,
    transform: str | None = None,
    target_crs: str | None = None,
    resampling: str | None = None,
) -> list[list[str]]:
    config = load_config(project)
    transform = transform or config.gis.georeference_transform
    target_crs = target_crs or config.gis.target_crs
    resampling = resampling or config.gis.resampling
    if transform not in MIN_GCPS:
        raise ValueError(f"Unsupported transform: {transform}")
    gcps = read_gcps(gcps_path)
    required = MIN_GCPS[transform]
    if len(gcps) < required:
        raise ValueError(f"{transform} requires at least {required} enabled GCPs; found {len(gcps)}.")

    output_raster.parent.mkdir(parents=True, exist_ok=True)
    intermediate = output_raster.with_suffix(".gcps.vrt")
    translate = ["gdal_translate", "-of", "VRT"]
    for point in gcps:
        translate.extend(
            [
                "-gcp",
                str(point["pixel_x"]),
                str(point["pixel_y"]),
                str(point["map_x"]),
                str(point["map_y"]),
            ]
        )
    translate.extend([str(source_image), str(intermediate)])

    warp = ["gdalwarp", "-overwrite", "-r", resampling, "-t_srs", target_crs]
    if transform == "tps":
        warp.append("-tps")
    elif transform == "polynomial2":
        warp.extend(["-order", "2"])
    else:
        warp.extend(["-order", "1"])
    warp.extend([str(intermediate), str(output_raster)])
    return [translate, warp]


def run_georeference(
    project: ProjectPaths,
    *,
    source_image: Path,
    gcps_path: Path,
    output_raster: Path,
    transform: str | None = None,
    target_crs: str | None = None,
    resampling: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    commands = build_georef_commands(
        project,
        source_image=source_image,
        gcps_path=gcps_path,
        output_raster=output_raster,
        transform=transform,
        target_crs=target_crs,
        resampling=resampling,
    )
    printable = [shlex.join(command) for command in commands]
    if dry_run:
        return {"executed": False, "commands": printable, "output": str(output_raster)}
    missing = [command[0] for command in commands if shutil.which(command[0]) is None]
    if missing:
        raise RuntimeError(f"Missing GDAL command(s): {', '.join(sorted(set(missing)))}")
    for command in commands:
        subprocess.run(command, check=True)
    return {"executed": True, "commands": printable, "output": str(output_raster)}
