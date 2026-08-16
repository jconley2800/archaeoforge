from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .blender_runner import find_blender, run_blender
from .compile_scene import compile_scene
from .config import default_config, write_config
from .db import (
    connect,
    import_claim_catalog,
    import_source_catalog,
    list_claims,
    list_reviews,
    list_sources,
    set_claim_status,
)
from .extract import extract_project
from .georef import run_georeference
from .image_finish import (
    HistoricalSpatialValidationError,
    finish_render,
    prepare_finish_request,
    register_finished_render,
)
from .ingest import ingest_project
from .models import FinishMode, ReviewStatus
from .openai_client import project_openai_api_key
from .project import ProjectPaths, load_config, resolve_project
from .report import export_chatgpt_handoff, export_evidence, generate_report
from .validate import validate_project

app = typer.Typer(
    name="archaeoforge",
    help="Evidence-controlled archaeological reconstruction automation.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


def _json(data: Any) -> None:
    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


def _project(path: Path) -> ProjectPaths:
    return resolve_project(path)


def _run_image_finish_stage(
    project: ProjectPaths,
    config: Any,
    *,
    skip_ai: bool,
    skip_blender: bool,
    no_render: bool,
    blender_rendered: bool,
) -> dict[str, Any]:
    beauty_render = project.renders_dir / "beauty.png"
    if not config.ai.finish_enabled:
        return {"skipped": True, "reason": "ai.finish_enabled is false"}
    if skip_ai:
        return {"skipped": True, "reason": "--skip-ai"}
    if skip_blender:
        return {"skipped": True, "reason": "--skip-blender"}
    if no_render:
        return {"skipped": True, "reason": "--no-render"}
    if not blender_rendered:
        return {"skipped": True, "reason": "Blender render did not complete"}
    if not beauty_render.is_file():
        return {"skipped": True, "reason": "beauty render not found"}
    if config.ai.finish_backend == "interactive_handoff":
        request_path = prepare_finish_request(
            project,
            base_image=beauty_render,
            mode=config.ai.finish_mode,
        )
        return {
            "status": "pending_external_finish",
            "backend": "interactive_handoff",
            "request": str(request_path),
        }
    return finish_render(
        project,
        base_image=beauty_render,
        mode=config.ai.finish_mode,
    )


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="New project directory")],
    title: Annotated[str, typer.Option("--title")] = "Untitled Reconstruction",
    place_name: Annotated[str, typer.Option("--place")] = "Unknown place",
    target_year: Annotated[int, typer.Option("--year", help="BCE is negative; CE is positive")] = -570,
    label: Annotated[str, typer.Option("--label")] = "approximately 570 BCE",
    force: Annotated[bool, typer.Option("--force")] = False,
    overwrite_existing: Annotated[
        bool,
        typer.Option(
            "--overwrite-existing",
            help="Replace existing project scaffold files. Requires --force.",
        ),
    ] = False,
) -> None:
    expanded_root = path.expanduser()
    if expanded_root.is_symlink():
        raise typer.BadParameter(f"Refusing to initialize through a symlinked project root: {expanded_root}")
    root = expanded_root.resolve()
    if overwrite_existing and not force:
        raise typer.BadParameter("--overwrite-existing requires --force")
    if root.exists() and any(root.iterdir()) and not force:
        raise typer.BadParameter(f"Directory is not empty: {root}. Use --force to add project scaffolding.")

    scaffold_paths = (
        root / "project.yaml",
        root / "data" / "features.geojson",
        root / "data" / "source_catalog.csv",
        root / "data" / "evidence_seed.csv",
        root / "prompts" / "finish.txt",
    )
    managed_directories = tuple(
        root / directory for directory in ("sources", "data", "assets", "prompts", "outputs")
    )
    symlinked_paths = [
        candidate for candidate in (*managed_directories, *scaffold_paths) if candidate.is_symlink()
    ]
    if symlinked_paths:
        relative_paths = ", ".join(str(candidate.relative_to(root)) for candidate in symlinked_paths)
        raise typer.BadParameter(
            "Refusing to initialize through symlinked project paths: "
            f"{relative_paths}. Replace the links with project-local directories and files first."
        )

    existing_scaffold = [candidate for candidate in scaffold_paths if candidate.exists()]
    if existing_scaffold and not overwrite_existing:
        relative_paths = ", ".join(str(candidate.relative_to(root)) for candidate in existing_scaffold)
        raise typer.BadParameter(
            "Refusing to overwrite existing project files: "
            f"{relative_paths}. Use --force --overwrite-existing to replace them."
        )

    for directory in managed_directories:
        directory.mkdir(parents=True, exist_ok=True)
    config = default_config(root.name.replace("_", "-"), title, place_name, target_year, label)
    write_config(config, root / "project.yaml")
    feature_collection = {"type": "FeatureCollection", "features": []}
    (root / "data" / "features.geojson").write_text(
        json.dumps(feature_collection, indent=2), encoding="utf-8"
    )
    (root / "data" / "source_catalog.csv").write_text(
        "id,relative_path,title,authors,publication_year,source_type,url,license,sha256,size_bytes,mime_type,local_copy,notes\n",
        encoding="utf-8",
    )
    (root / "data" / "evidence_seed.csv").write_text(
        "id,source_id,subject,property,claim,value_text,value_number,unit,locator,quotation,evidence_basis,evidence_class,confidence,date_start,date_end,uncertainty,alternative_group,tags,review_status,created_by,model_used,response_id,source_sha256_at_creation\n",
        encoding="utf-8",
    )
    (root / "prompts" / "finish.txt").write_text(
        "Preserve the supplied geometry, camera, silhouettes, openings, wall paths, roads, water, and object placement exactly. Improve only surfaces, atmosphere, lighting, vegetation variation, and small-scale human activity.\n",
        encoding="utf-8",
    )
    project = resolve_project(root)
    connection = connect(project)
    connection.close()
    console.print(f"Created ArchaeoForge project: {root}")


@app.command()
def seed(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    p = _project(project)
    connection = connect(p)
    try:
        source_count = import_source_catalog(connection, p.data_dir / "source_catalog.csv")
    finally:
        connection.close()
    ingestion = ingest_project(p)
    connection = connect(p)
    try:
        claim_count = import_claim_catalog(connection, p.data_dir / "evidence_seed.csv")
    finally:
        connection.close()
    _json({"sources_catalogued": source_count, "ingestion": ingestion, "claims_imported": claim_count})


@app.command()
def ingest(
    project: Annotated[Path, typer.Argument()] = Path("."),
    render_visual_pages: Annotated[bool, typer.Option("--render-visual-pages")] = False,
) -> None:
    _json(ingest_project(_project(project), render_visual_pages=render_visual_pages))


@app.command()
def extract(
    project: Annotated[Path, typer.Argument()] = Path("."),
    source_id: Annotated[str | None, typer.Option("--source-id")] = None,
) -> None:
    _json(extract_project(_project(project), source_id=source_id))


@app.command("claims")
def show_claims(
    project: Annotated[Path, typer.Argument()] = Path("."),
    status: Annotated[ReviewStatus | None, typer.Option("--status")] = None,
    source_id: Annotated[str | None, typer.Option("--source-id")] = None,
) -> None:
    p = _project(project)
    connection = connect(p)
    try:
        rows = list_claims(connection, statuses=[status] if status else None, source_id=source_id)
    finally:
        connection.close()
    table = Table(title=f"Evidence claims: {p.root.name}")
    for column in ("ID", "Class", "Confidence", "Status", "Subject", "Property", "Locator"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["id"],
            row["evidence_class"],
            f"{row['confidence']:.2f}",
            row["review_status"],
            row["subject"],
            row["property_name"],
            row["locator"],
        )
    console.print(table)


@app.command()
def review(
    claim_id: Annotated[str, typer.Argument()],
    status: Annotated[ReviewStatus, typer.Argument()],
    project: Annotated[Path, typer.Option("--project", "-p")] = Path("."),
    reviewer: Annotated[str, typer.Option("--reviewer")] = "unspecified",
    notes: Annotated[str, typer.Option("--notes")] = "",
) -> None:
    p = _project(project)
    connection = connect(p)
    try:
        changed = set_claim_status(connection, claim_id, status, reviewer=reviewer, notes=notes)
    finally:
        connection.close()
    if not changed:
        raise typer.BadParameter(f"Unknown claim ID: {claim_id}")
    console.print(f"{claim_id}: {status.value}")


@app.command("export-evidence")
def export_evidence_command(
    project: Annotated[Path, typer.Argument()] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    p = _project(project)
    destination = output.expanduser().resolve() if output else None
    console.print(str(export_evidence(p, destination)))


@app.command("export-chatgpt")
def export_chatgpt_command(
    project: Annotated[Path, typer.Argument()] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    p = _project(project)
    destination = output.expanduser().resolve() if output else None
    console.print(str(export_chatgpt_handoff(p, destination)))


@app.command()
def validate(
    project: Annotated[Path, typer.Argument()] = Path("."),
    preview: Annotated[bool, typer.Option("--preview")] = False,
) -> None:
    report = validate_project(_project(project), preview=preview)
    _json(report)
    if not report["valid"]:
        raise typer.Exit(2)


@app.command("compile")
def compile_command(
    project: Annotated[Path, typer.Argument()] = Path("."),
    preview: Annotated[bool, typer.Option("--preview")] = False,
) -> None:
    manifest = compile_scene(_project(project), preview=preview)
    _json(manifest["statistics"] | {"input_fingerprint": manifest["input_fingerprint"]})


@app.command()
def build(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    _json(run_blender(_project(project), render=False))


@app.command()
def render(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    _json(run_blender(_project(project), render=True))


@app.command()
def georef(
    source_image: Annotated[Path, typer.Argument()],
    gcps: Annotated[Path, typer.Argument()],
    output: Annotated[Path, typer.Argument()],
    project: Annotated[Path, typer.Option("--project", "-p")] = Path("."),
    transform: Annotated[str | None, typer.Option("--transform")] = None,
    target_crs: Annotated[str | None, typer.Option("--target-crs")] = None,
    resampling: Annotated[str | None, typer.Option("--resampling")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    p = _project(project)
    _json(
        run_georeference(
            p,
            source_image=source_image.expanduser().resolve(),
            gcps_path=gcps.expanduser().resolve(),
            output_raster=output.expanduser().resolve(),
            transform=transform,
            target_crs=target_crs,
            resampling=resampling,
            dry_run=dry_run,
        )
    )


@app.command()
def report(
    project: Annotated[Path, typer.Argument()] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    p = _project(project)
    destination = output.expanduser().resolve() if output else None
    console.print(str(generate_report(p, destination)))


@app.command()
def finish(
    base_image: Annotated[Path, typer.Argument()],
    project: Annotated[Path, typer.Option("--project", "-p")] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    prompt: Annotated[Path | None, typer.Option("--prompt")] = None,
    audit: Annotated[
        bool | None,
        typer.Option("--audit/--no-audit", help="Override ai.geometry_audit_enabled."),
    ] = None,
    mode: Annotated[
        FinishMode | None,
        typer.Option("--mode", help="Override ai.finish_mode."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an explicit existing output.")] = False,
) -> None:
    p = _project(project)
    try:
        result = finish_render(
            p,
            base_image=base_image.expanduser().resolve(),
            destination=output.expanduser().resolve() if output else None,
            prompt_path=prompt.expanduser().resolve() if prompt else None,
            audit=audit,
            overwrite=force,
            mode=mode,
        )
    except HistoricalSpatialValidationError as exc:
        _json({"status": "validation_blocked", "reason": str(exc)})
        raise typer.Exit(code=2) from exc
    _json(result)


@app.command("prepare-finish")
def prepare_finish(
    base_image: Annotated[Path, typer.Argument(help="Base or edit-target image")],
    project: Annotated[Path, typer.Option("--project", "-p")] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Requested final PNG path")] = None,
    prompt: Annotated[Path | None, typer.Option("--prompt")] = None,
    request: Annotated[Path | None, typer.Option("--request", help="Finish-request JSON path")] = None,
    reference_image: Annotated[
        list[Path] | None,
        typer.Option(
            "--reference-image",
            help="Hash-bind an additional historical-scene supporting image; repeat as needed.",
        ),
    ] = None,
    mode: Annotated[
        FinishMode | None,
        typer.Option("--mode", help="Override ai.finish_mode."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace an existing valid finish-request JSON file."),
    ] = False,
) -> None:
    """Prepare a hash-bound request for Codex or another interactive image editor."""
    p = _project(project)
    request_path = prepare_finish_request(
        p,
        base_image=base_image.expanduser().resolve(),
        destination=output.expanduser().resolve() if output else None,
        prompt_path=prompt.expanduser().resolve() if prompt else None,
        request_path=request.expanduser().resolve() if request else None,
        overwrite_request=force,
        mode=mode,
        reference_images=[path.expanduser().resolve() for path in reference_image or []],
    )
    _json({"status": "ready_for_interactive_generation", "request": str(request_path)})


@app.command("register-finish")
def register_finish(
    generated_image: Annotated[Path, typer.Argument(help="PNG returned by the interactive image editor")],
    project: Annotated[Path, typer.Option("--project", "-p")] = Path("."),
    request: Annotated[Path | None, typer.Option("--request")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    provider: Annotated[str, typer.Option("--provider")] = "codex_builtin_imagegen",
    model: Annotated[str, typer.Option("--model")] = "gpt-image-2",
    audit: Annotated[
        bool | None,
        typer.Option("--audit/--no-audit", help="Override the audit preference in project.yaml."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing requested output.")] = False,
    normalize_size: Annotated[
        bool,
        typer.Option(
            "--normalize-size",
            help="Resize a same-aspect result to the exact requested dimensions and record the transform.",
        ),
    ] = False,
    manual_recommendation: Annotated[
        str | None,
        typer.Option("--manual-recommendation", help="Record accept, review, or reject."),
    ] = None,
    spatial_recommendation: Annotated[
        str | None,
        typer.Option(
            "--spatial-recommendation",
            help="Record accept, review, or reject for the complete historical spatial contract.",
        ),
    ] = None,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "",
    review_notes: Annotated[str, typer.Option("--review-notes")] = "",
) -> None:
    """Verify an interactive result and record its non-authoritative provenance."""
    p = _project(project)
    try:
        result = register_finished_render(
            p,
            generated_image=generated_image.expanduser().resolve(),
            request_path=request.expanduser().resolve() if request else None,
            destination=output.expanduser().resolve() if output else None,
            provider=provider,
            model=model,
            audit=audit,
            overwrite=force,
            normalize_size=normalize_size,
            manual_recommendation=manual_recommendation,
            spatial_recommendation=spatial_recommendation,
            reviewer=reviewer,
            review_notes=review_notes,
        )
    except HistoricalSpatialValidationError as exc:
        _json({"status": "validation_blocked", "reason": str(exc)})
        raise typer.Exit(code=2) from exc
    _json(result)


@app.command()
def status(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    p = _project(project)
    connection = connect(p)
    try:
        sources = list_sources(connection)
        claims = list_claims(connection)
        reviews = list_reviews(connection)
    finally:
        connection.close()
    by_status: dict[str, int] = {}
    for claim in claims:
        by_status[claim["review_status"]] = by_status.get(claim["review_status"], 0) + 1
    result = {
        "project": load_config(p).project.model_dump(mode="json"),
        "sources": len(sources),
        "local_sources": sum(bool(source["local_copy"]) for source in sources),
        "claims": len(claims),
        "claims_by_status": by_status,
        "reviews": len(reviews),
        "scene_manifest_exists": p.scene_manifest.exists(),
        "validation_report_exists": p.validation_report.exists(),
        "html_report_exists": p.html_report.exists(),
        "blend_file_exists": p.blend_file.exists(),
    }
    _json(result)


@app.command()
def doctor(project: Annotated[Path | None, typer.Argument()] = None) -> None:
    selected_project = _project(project) if project is not None else None
    api_key = (
        project_openai_api_key(selected_project, required=False)
        if selected_project is not None
        else os.getenv("OPENAI_API_KEY")
    )
    checks: dict[str, Any] = {
        "python": {"version": platform.python_version(), "supported": sys.version_info >= (3, 11)},
        "gdal_translate": shutil.which("gdal_translate"),
        "gdalwarp": shutil.which("gdalwarp"),
        "qgis": shutil.which("qgis"),
        "OPENAI_API_KEY": "set" if api_key else "not set",
    }
    if selected_project is not None:
        checks["blender"] = find_blender(selected_project)
        checks["project"] = str(selected_project.root)
    else:
        checks["blender"] = shutil.which("blender")
    _json(checks)


@app.command()
def run(
    project: Annotated[Path, typer.Argument()] = Path("."),
    preview: Annotated[bool, typer.Option("--preview")] = False,
    skip_ai: Annotated[bool, typer.Option("--skip-ai")] = False,
    skip_blender: Annotated[bool, typer.Option("--skip-blender")] = False,
    no_render: Annotated[bool, typer.Option("--no-render")] = False,
    render_visual_pages: Annotated[bool, typer.Option("--render-visual-pages")] = False,
) -> None:
    p = _project(project)
    stages: dict[str, Any] = {}

    connection = connect(p)
    try:
        stages["source_catalog"] = import_source_catalog(connection, p.data_dir / "source_catalog.csv")
    finally:
        connection.close()
    stages["ingestion"] = ingest_project(p, render_visual_pages=render_visual_pages)
    connection = connect(p)
    try:
        stages["evidence_seed"] = import_claim_catalog(connection, p.data_dir / "evidence_seed.csv")
    finally:
        connection.close()

    config = load_config(p)
    if config.ai.enabled and not skip_ai:
        stages["ai_extraction"] = extract_project(p)
    else:
        stages["ai_extraction"] = {"skipped": True}

    validation_result = validate_project(p, preview=preview)
    stages["validation"] = validation_result["counts"] | {"valid": validation_result["valid"]}
    if not validation_result["valid"]:
        stages["stopped"] = "validation errors"
        _json(stages)
        raise typer.Exit(2)

    manifest = compile_scene(p, preview=preview)
    stages["compile"] = manifest["statistics"] | {"input_fingerprint": manifest["input_fingerprint"]}
    stages["evidence_export"] = str(export_evidence(p))
    stages["report"] = str(generate_report(p))
    stages["chatgpt_export"] = str(export_chatgpt_handoff(p))

    blender_rendered = False
    if skip_blender:
        stages["blender"] = {"skipped": True, "reason": "--skip-blender"}
    elif find_blender(p) is None:
        stages["blender"] = {"skipped": True, "reason": "Blender executable not found"}
    else:
        stages["blender"] = run_blender(p, render=not no_render)
        blender_rendered = bool(stages["blender"].get("rendered"))

    stages["image_finish"] = _run_image_finish_stage(
        p,
        config,
        skip_ai=skip_ai,
        skip_blender=skip_blender,
        no_render=no_render,
        blender_rendered=blender_rendered,
    )

    _json(stages)


if __name__ == "__main__":
    app()
