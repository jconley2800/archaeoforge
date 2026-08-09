from __future__ import annotations

import csv
from pathlib import Path

import pytest

from archaeoforge.db import connect, get_source, import_source_catalog, list_pages
from archaeoforge.georef import build_georef_commands, run_georeference
from archaeoforge.ingest import ingest_project
from archaeoforge.util import sha256_file


def test_ingest_hashes_and_indexes_text(project_factory):
    project = project_factory()
    result = ingest_project(project)
    assert result["failed"] == 0
    connection = connect(project)
    try:
        source = get_source(connection, "SRC-TEST")
        pages = list_pages(connection, "SRC-TEST")
    finally:
        connection.close()
    assert source is not None
    assert source["sha256"] == sha256_file(project.sources_dir / "source.txt")
    assert "Measured wall width" in pages[0]["text_content"]


def _write_gcps(path: Path, count: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["enabled", "pixel_x", "pixel_y", "map_x", "map_y"])
        writer.writeheader()
        for index in range(count):
            writer.writerow(
                {
                    "enabled": "true",
                    "pixel_x": index * 10,
                    "pixel_y": index * 12,
                    "map_x": 448000 + index * 5,
                    "map_y": 3595000 + index * 5,
                }
            )


def test_georef_dry_run_builds_gdal_commands(project_factory, tmp_path):
    project = project_factory()
    image = tmp_path / "plan.png"
    image.write_bytes(b"not executed")
    gcps = tmp_path / "gcps.csv"
    _write_gcps(gcps, 3)
    output = tmp_path / "plan-georef.tif"
    result = run_georeference(
        project,
        source_image=image,
        gcps_path=gcps,
        output_raster=output,
        dry_run=True,
    )
    assert result["executed"] is False
    assert result["commands"][0].startswith("gdal_translate")
    assert "-order 1" in result["commands"][1]


def test_polynomial2_requires_six_gcps(project_factory, tmp_path):
    project = project_factory()
    gcps = tmp_path / "gcps.csv"
    _write_gcps(gcps, 5)
    with pytest.raises(ValueError, match="at least 6"):
        build_georef_commands(
            project,
            source_image=tmp_path / "plan.png",
            gcps_path=gcps,
            output_raster=tmp_path / "out.tif",
            transform="polynomial2",
        )


def test_catalog_reimport_does_not_downgrade_local_hash(project_factory, tmp_path):
    project = project_factory()
    connection = connect(project)
    try:
        before = get_source(connection, "SRC-TEST")
        catalog = tmp_path / "catalog.csv"
        catalog.write_text(
            "id,relative_path,title,authors,publication_year,source_type,url,license,sha256,size_bytes,mime_type,local_copy,notes\n"
            "SRC-TEST,,Catalog title,,,other,,,,,,false,Catalog metadata only\n",
            encoding="utf-8",
        )
        import_source_catalog(connection, catalog)
        after = get_source(connection, "SRC-TEST")
    finally:
        connection.close()
    assert before is not None and after is not None
    assert after["sha256"] == before["sha256"]
    assert after["local_copy"] == 1
