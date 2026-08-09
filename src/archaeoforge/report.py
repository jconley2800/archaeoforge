from __future__ import annotations

import json
import re
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .compile_scene import load_features
from .db import connect, export_claims_csv, list_claims, list_reviews, list_sources
from .project import ProjectPaths, load_config
from .util import utc_now

_REDACTED_LOCAL_PATH = "<redacted-local-path>"
_PROJECT_ROOT = "<project-root>"
_FILE_URI_RE = re.compile(r"(?i)\bfile://(?:localhost)?(?:/[^\s<>\"'`]+)+")
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\)[^\r\n,;|<>:\"'`\)\]\}]+"
)
_POSIX_PATH_RE = re.compile(
    r"(?<![\w:/<>])/(?!/)[^\r\n,;|<>:\"'`\)\]\}]*"
)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _redact_paths_in_text(value: str, project_root: Path) -> str:
    """Remove local absolute paths while leaving project-relative paths and URLs useful."""

    resolved_root = project_root.resolve()
    root_variants = {
        str(resolved_root),
        str(resolved_root).replace("/", "\\"),
    }
    try:
        root_variants.add(resolved_root.as_uri())
    except ValueError:
        pass
    for root in sorted(root_variants, key=len, reverse=True):
        if root:
            value = value.replace(root, _PROJECT_ROOT)

    home = Path.home().resolve()
    home_variants = {str(home), str(home).replace("/", "\\")}
    try:
        home_variants.add(home.as_uri())
    except ValueError:
        pass
    for local_home in sorted(home_variants, key=len, reverse=True):
        if local_home:
            value = value.replace(local_home, _REDACTED_LOCAL_PATH)

    value = _FILE_URI_RE.sub(_REDACTED_LOCAL_PATH, value)
    value = _WINDOWS_PATH_RE.sub(_REDACTED_LOCAL_PATH, value)
    return _POSIX_PATH_RE.sub(_REDACTED_LOCAL_PATH, value)


def _redact_local_paths(value: Any, project_root: Path) -> Any:
    """Recursively sanitize every user-controlled field in an exported handoff."""

    if isinstance(value, dict):
        return {
            (_redact_paths_in_text(key, project_root) if isinstance(key, str) else key):
            _redact_local_paths(item, project_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_local_paths(item, project_root) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_local_paths(item, project_root) for item in value)
    if isinstance(value, str):
        return _redact_paths_in_text(value, project_root)
    return value


def export_evidence(project: ProjectPaths, destination: Path | None = None) -> Path:
    destination = destination or project.exports_dir / "evidence_register.csv"
    connection = connect(project)
    try:
        export_claims_csv(connection, destination)
    finally:
        connection.close()
    return destination


def export_chatgpt_handoff(project: ProjectPaths, destination: Path | None = None) -> Path:
    destination = destination or project.exports_dir / "chatgpt_handoff.json"
    config = load_config(project)
    connection = connect(project)
    try:
        sources = list_sources(connection)
        claims = list_claims(connection)
        reviews = list_reviews(connection)
    finally:
        connection.close()

    for source in sources:
        source["metadata"] = json.loads(source.pop("metadata_json") or "{}")

    manifest = _load_json(project.scene_manifest, {})
    manifest.pop("project_root", None)
    validation = _load_json(project.validation_report, {})
    if "features_path" in validation:
        validation["features_path"] = "data/features.geojson"

    payload = {
        "handoff_schema": 1,
        "generated_at": utc_now(),
        "suggested_prompt": (
            "Analyze this ArchaeoForge handoff. Separate approved evidence from draft or "
            "needs-review claims, preserve evidence classes and uncertainty, and do not present "
            "preview geometry as established fact. Summarize the reconstruction, validation "
            "issues, contradictions, and the highest-priority evidence-review actions."
        ),
        "project_config": config.model_dump(mode="json"),
        "sources": sources,
        "evidence_claims": claims,
        "review_history": reviews,
        "validation": validation,
        "scene_manifest": manifest,
    }
    payload = _redact_local_paths(payload, project.root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def generate_report(project: ProjectPaths, destination: Path | None = None) -> Path:
    destination = destination or project.html_report
    config = load_config(project)
    connection = connect(project)
    try:
        sources = list_sources(connection)
        claims = list_claims(connection)
        reviews = list_reviews(connection)
    finally:
        connection.close()

    manifest = _load_json(project.scene_manifest, {})
    validation = _load_json(project.validation_report, {"counts": {}, "issues": [], "valid": None})
    try:
        raw_features = load_features(project.data_dir / "features.geojson")
    except Exception:
        raw_features = []

    claim_statuses = Counter(claim["review_status"] for claim in claims)
    evidence_classes = Counter(claim["evidence_class"] for claim in claims)
    review_by_claim: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        review_by_claim.setdefault(review["claim_id"], []).append(review)

    template_root = Path(str(files("archaeoforge.templates")))
    environment = Environment(
        loader=FileSystemLoader(template_root),
        autoescape=select_autoescape(
            enabled_extensions=("html", "htm", "xml"),
            default_for_string=True,
            default=True,
        ),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("report.html.j2")
    html = template.render(
        generated_at=utc_now(),
        config=config.model_dump(mode="json"),
        sources=sources,
        claims=claims,
        reviews=reviews,
        review_by_claim=review_by_claim,
        claim_statuses=dict(claim_statuses),
        evidence_classes=dict(evidence_classes),
        manifest=manifest,
        validation=validation,
        raw_feature_count=len(raw_features),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination
