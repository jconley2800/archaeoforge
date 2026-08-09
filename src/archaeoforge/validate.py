from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.validation import explain_validity

from .compile_scene import EVIDENCE_OPTIONAL_TEMPLATES, _applies_to_year, _as_dict, _as_list, load_features
from .db import connect, list_claims, list_sources
from .models import EvidenceClass, ReviewStatus, ValidationIssue
from .project import ProjectPaths, load_config
from .util import sha256_file, utc_now


def _issue(
    severity: str,
    code: str,
    message: str,
    object_type: str,
    object_id: str = "",
    remediation: str = "",
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        object_type=object_type,
        object_id=object_id,
        remediation=remediation,
    )


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _water_solid_overlap_issue(
    water_id: str,
    water: Any,
    solid_id: str,
    solid: Any,
) -> ValidationIssue | None:
    try:
        overlap = water.intersection(solid)
    except Exception as exc:
        return _issue(
            "warning",
            "WATER_SOLID_OVERLAP_CHECK_FAILED",
            f"Could not check water feature {water_id} against solid feature {solid_id}: {exc}",
            "geometry",
            solid_id,
            "Repair the geometries and repeat validation; this pair was not checked for overlap.",
        )
    if overlap.is_empty or getattr(overlap, "area", 0.0) <= 1.0:
        return None
    return _issue(
        "warning",
        "WATER_SOLID_OVERLAP",
        f"Water feature {water_id} overlaps solid feature {solid_id}.",
        "geometry",
        solid_id,
        "Review whether the overlap is intentional, such as a bridge or quay.",
    )


def validate_project(
    project: ProjectPaths,
    *,
    preview: bool = False,
    features_path: Path | None = None,
    destination: Path | None = None,
) -> dict[str, Any]:
    config = load_config(project)
    features_path = features_path or project.data_dir / "features.geojson"
    destination = destination or project.validation_report
    issues: list[ValidationIssue] = []

    connection = connect(project)
    try:
        sources = list_sources(connection)
        claims = list_claims(connection)
        source_text = {
            source["id"]: "\n".join(
                row["text_content"]
                for row in connection.execute(
                    "SELECT text_content FROM source_pages WHERE source_id = ? ORDER BY page_number",
                    (source["id"],),
                )
            )
            for source in sources
        }
    finally:
        connection.close()

    source_by_id = {source["id"]: source for source in sources}
    claim_by_id = {claim["id"]: claim for claim in claims}
    allowed_statuses = {
        status.value if isinstance(status, ReviewStatus) else str(status)
        for status in (
            config.evidence_policy.preview_statuses if preview else config.evidence_policy.authoritative_statuses
        )
    }
    target_year = config.project.target_year

    for source in sources:
        source_id = source["id"]
        relative = source.get("relative_path")
        if not relative:
            severity = "error" if (not preview and config.evidence_policy.require_local_copy_for_authoritative) else "warning"
            issues.append(
                _issue(
                    severity,
                    "SOURCE_EXTERNAL_ONLY",
                    "Source has no local immutable copy in the project.",
                    "source",
                    source_id,
                    "Add the licensed source file under sources/ and ingest it, or retain this as preview-only evidence.",
                )
            )
            continue
        path = (project.root / relative).resolve()
        try:
            path.relative_to(project.root.resolve())
        except ValueError:
            issues.append(
                _issue(
                    "error",
                    "SOURCE_PATH_ESCAPE",
                    f"Source path resolves outside the project root: {relative}",
                    "source",
                    source_id,
                    "Move the source into the project sources directory.",
                )
            )
            continue
        if not path.exists():
            issues.append(
                _issue(
                    "error",
                    "SOURCE_MISSING",
                    f"Registered local source is missing: {relative}",
                    "source",
                    source_id,
                    "Restore the file or update the source record and re-ingest.",
                )
            )
            continue
        current_sha = sha256_file(path)
        if not source.get("sha256"):
            issues.append(
                _issue(
                    "error" if not preview else "warning",
                    "SOURCE_HASH_MISSING",
                    "Local source has no registered SHA-256 checksum.",
                    "source",
                    source_id,
                    "Run archaeoforge ingest.",
                )
            )
        elif current_sha != source["sha256"]:
            issues.append(
                _issue(
                    "error",
                    "SOURCE_HASH_CHANGED",
                    "Local source bytes no longer match the registered checksum.",
                    "source",
                    source_id,
                    "Treat the changed file as a new source version, re-ingest it, and review dependent claims.",
                )
            )

    locator_classes = {
        value.value if isinstance(value, EvidenceClass) else str(value)
        for value in config.evidence_policy.require_locator_for_classes
    }
    for claim in claims:
        claim_id = claim["id"]
        source = source_by_id.get(claim["source_id"])
        if source is None:
            issues.append(
                _issue(
                    "error", "CLAIM_SOURCE_MISSING", "Claim references an unknown source.", "claim", claim_id
                )
            )
            continue
        bound_sha = claim.get("source_sha256_at_creation") or ""
        registered_sha = source.get("sha256") or ""
        if not preview and not bound_sha:
            issues.append(
                _issue(
                    "error",
                    "CLAIM_UNBOUND_SOURCE_VERSION",
                    "Authoritative claim has no source checksum snapshot.",
                    "claim",
                    claim_id,
                    "Ingest the local source and recreate or explicitly rebind the reviewed claim.",
                )
            )
        if bound_sha and registered_sha and bound_sha != registered_sha:
            issues.append(
                _issue(
                    "error",
                    "CLAIM_SOURCE_VERSION_MISMATCH",
                    "Claim was created against a different source checksum.",
                    "claim",
                    claim_id,
                    "Review the new source version and create or approve an updated claim.",
                )
            )
        if claim["evidence_class"] in locator_classes and not str(claim.get("locator") or "").strip():
            issues.append(
                _issue(
                    "error" if not preview else "warning",
                    "CLAIM_LOCATOR_MISSING",
                    f"Class {claim['evidence_class']} claim lacks a page, figure, object, or scan locator.",
                    "claim",
                    claim_id,
                )
            )
        quotation = str(claim.get("quotation") or "").strip()
        indexed = source_text.get(claim["source_id"], "")
        if quotation and indexed and _normalise_text(quotation) not in _normalise_text(indexed):
            issues.append(
                _issue(
                    "warning",
                    "CLAIM_QUOTE_NOT_FOUND",
                    "The stored quotation was not found verbatim in indexed source text.",
                    "claim",
                    claim_id,
                    "Check OCR, punctuation, and the cited page image before approval.",
                )
            )
        if claim.get("date_start") is not None and claim.get("date_end") is not None:
            if int(claim["date_start"]) > int(claim["date_end"]):
                issues.append(
                    _issue("error", "CLAIM_DATE_ORDER", "Claim date range is reversed.", "claim", claim_id)
                )
        if claim["review_status"] == "rejected":
            issues.append(
                _issue("info", "CLAIM_REJECTED", "Claim is explicitly rejected.", "claim", claim_id)
            )

    try:
        features = load_features(features_path)
    except Exception as exc:
        features = []
        issues.append(
            _issue(
                "error",
                "FEATURE_FILE_INVALID",
                f"Cannot load feature file: {exc}",
                "project",
                str(features_path),
            )
        )

    ids: set[str] = set()
    geometry_fingerprints: dict[str, list[str]] = defaultdict(list)
    allowed_feature_count = 0
    solid_geometries: list[tuple[str, Any]] = []
    water_geometries: list[tuple[str, Any]] = []

    for index, feature in enumerate(features, start=1):
        properties = feature.get("properties") or {}
        feature_id = str(properties.get("id") or properties.get("feature_id") or f"FEATURE-{index:04d}")
        template = str(properties.get("template") or "building").strip().lower()
        status = str(properties.get("review_status") or properties.get("status") or "draft")
        evidence_ids = _as_list(properties.get("evidence_ids"))

        if feature_id in ids:
            issues.append(_issue("error", "FEATURE_ID_DUPLICATE", "Feature ID is duplicated.", "feature", feature_id))
        ids.add(feature_id)
        if status in allowed_statuses:
            allowed_feature_count += 1
        else:
            issues.append(
                _issue(
                    "info" if preview else "warning",
                    "FEATURE_STATUS_BLOCKED",
                    f"Feature status {status!r} is not allowed in this build mode.",
                    "feature",
                    feature_id,
                )
            )
        if not _applies_to_year(properties, target_year):
            issues.append(
                _issue(
                    "warning",
                    "FEATURE_OUTSIDE_TARGET_DATE",
                    f"Feature does not apply to target year {target_year}.",
                    "feature",
                    feature_id,
                )
            )
        if (
            config.evidence_policy.require_evidence_for_geometry
            and template not in EVIDENCE_OPTIONAL_TEMPLATES
            and not evidence_ids
        ):
            issues.append(
                _issue(
                    "error" if status in allowed_statuses else "warning",
                    "FEATURE_EVIDENCE_MISSING",
                    "Geometry has no linked evidence claim.",
                    "feature",
                    feature_id,
                )
            )

        alternative_groups: dict[str, list[str]] = defaultdict(list)
        for evidence_id in evidence_ids:
            claim = claim_by_id.get(evidence_id)
            if claim is None:
                issues.append(
                    _issue(
                        "error", "FEATURE_CLAIM_MISSING", f"Linked claim {evidence_id} does not exist.", "feature", feature_id
                    )
                )
                continue
            if status in allowed_statuses and claim["review_status"] not in allowed_statuses:
                issues.append(
                    _issue(
                        "error",
                        "FEATURE_CLAIM_STATUS_BLOCKED",
                        f"Linked claim {evidence_id} has status {claim['review_status']}.",
                        "feature",
                        feature_id,
                    )
                )
            if not _applies_to_year(claim, target_year):
                issues.append(
                    _issue(
                        "error",
                        "FEATURE_CLAIM_OUTSIDE_TARGET_DATE",
                        f"Linked claim {evidence_id} does not apply to target year {target_year}.",
                        "feature",
                        feature_id,
                    )
                )
            if claim.get("alternative_group"):
                alternative_groups[str(claim["alternative_group"])].append(evidence_id)
        for group, members in alternative_groups.items():
            if len(members) > 1:
                issues.append(
                    _issue(
                        "error",
                        "FEATURE_ALTERNATIVE_CONFLICT",
                        f"Feature combines mutually exclusive claims in {group}: {', '.join(members)}.",
                        "feature",
                        feature_id,
                    )
                )

        try:
            params = _as_dict(properties.get("params"))
            for key, value in params.items():
                if key in {"width", "length", "height", "depth", "radius", "wall_width", "stage_height"}:
                    if isinstance(value, (int, float)) and value <= 0:
                        issues.append(
                            _issue(
                                "error",
                                "FEATURE_DIMENSION_NONPOSITIVE",
                                f"Parameter {key} must be positive.",
                                "feature",
                                feature_id,
                            )
                        )
        except Exception as exc:
            issues.append(_issue("error", "FEATURE_PARAMS_INVALID", str(exc), "feature", feature_id))

        try:
            geometry = shape(feature.get("geometry"))
            if geometry.is_empty:
                issues.append(_issue("error", "GEOMETRY_EMPTY", "Geometry is empty.", "geometry", feature_id))
            if not geometry.is_valid:
                issues.append(
                    _issue(
                        "error",
                        "GEOMETRY_INVALID",
                        explain_validity(geometry),
                        "geometry",
                        feature_id,
                    )
                )
            if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                polygon_count = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
                if any(len(poly.interiors) for poly in polygon_count):
                    issues.append(
                        _issue(
                            "warning",
                            "GEOMETRY_HAS_HOLES",
                            "Polygon holes require explicit Blender template support.",
                            "geometry",
                            feature_id,
                        )
                    )
            fingerprint = geometry.wkb_hex
            geometry_fingerprints[fingerprint].append(feature_id)
            category = str(properties.get("category") or "").lower()
            if template in {"water", "river", "canal"} or category == "water":
                water_geometries.append((feature_id, geometry))
            elif geometry.geom_type in {"Polygon", "MultiPolygon"} and template not in EVIDENCE_OPTIONAL_TEMPLATES:
                solid_geometries.append((feature_id, geometry))
        except Exception as exc:
            issues.append(
                _issue("error", "GEOMETRY_PARSE_FAILED", f"Cannot parse geometry: {exc}", "geometry", feature_id)
            )

    if features and allowed_feature_count == 0:
        issues.append(
            _issue(
                "error",
                "NO_FEATURES_ALLOWED",
                "The feature file contains geometry, but no feature is eligible for this build mode.",
                "project",
                remediation="Approve features and their linked claims, or use preview mode for inspection.",
            )
        )

    for members in geometry_fingerprints.values():
        if len(members) > 1:
            issues.append(
                _issue(
                    "warning",
                    "GEOMETRY_DUPLICATE",
                    f"Identical geometries are used by: {', '.join(members)}.",
                    "geometry",
                    members[0],
                )
            )

    for water_id, water in water_geometries:
        for solid_id, solid in solid_geometries:
            overlap_issue = _water_solid_overlap_issue(water_id, water, solid_id, solid)
            if overlap_issue is not None:
                issues.append(overlap_issue)

    issue_payload = [issue.model_dump(mode="json") for issue in issues]
    counts = {
        severity: sum(1 for issue in issues if issue.severity == severity)
        for severity in ("error", "warning", "info")
    }
    report = {
        "generated_at": utc_now(),
        "mode": "preview" if preview else "authoritative",
        "project": config.project.model_dump(mode="json"),
        "features_path": str(features_path),
        "counts": counts,
        "eligible_features": allowed_feature_count,
        "total_features": len(features),
        "valid": counts["error"] == 0,
        "issues": issue_payload,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
