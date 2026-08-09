from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from .db import connect, list_claims, list_sources
from .models import ReviewStatus
from .project import ProjectPaths, load_config
from .util import json_dumps, sha256_file, sha256_text, utc_now

CLASS_RANK = {"A": 1, "B": 2, "C": 3, "D": 4}
EVIDENCE_OPTIONAL_TEMPLATES = {"terrain", "context", "sky", "water_context"}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON list: {value}") from exc
            if not isinstance(parsed, list):
                raise ValueError(f"Expected a JSON list, received: {value}")
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return [str(value).strip()]


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("Feature params must decode to a JSON object.")
        return parsed
    raise ValueError("Feature params must be an object or a JSON object string.")


def _applies_to_year(item: dict[str, Any], target_year: int) -> bool:
    start = item.get("date_start")
    end = item.get("date_end")
    if start is not None and target_year < int(start):
        return False
    if end is not None and target_year > int(end):
        return False
    return True


def _feature_id(feature: dict[str, Any], index: int) -> str:
    properties = feature.get("properties") or {}
    return str(properties.get("id") or properties.get("feature_id") or f"FEATURE-{index:04d}")


def load_features(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError(f"{path} must be a GeoJSON FeatureCollection.")
    return payload["features"]


def _source_snapshot(project: ProjectPaths, sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for source in sources:
        current_sha = source.get("sha256") or ""
        relative = source.get("relative_path")
        if relative:
            local = (project.root / relative).resolve()
            try:
                local.relative_to(project.root.resolve())
                if local.exists():
                    current_sha = sha256_file(local)
            except ValueError:
                current_sha = "OUTSIDE_PROJECT_ROOT"
        snapshots.append(
            {
                "id": source["id"],
                "title": source["title"],
                "authors": source.get("authors", ""),
                "publication_year": source.get("publication_year"),
                "source_type": source.get("source_type", "other"),
                "url": source.get("url", ""),
                "license": source.get("license", ""),
                "relative_path": relative,
                "registered_sha256": source.get("sha256", ""),
                "current_sha256": current_sha,
                "local_copy": bool(source.get("local_copy")),
                "notes": source.get("notes", ""),
            }
        )
    return snapshots


def _worst_class(classes: list[str]) -> str:
    available = [value for value in classes if value in CLASS_RANK]
    return max(available, key=lambda value: CLASS_RANK[value]) if available else "D"


def compile_scene(
    project: ProjectPaths,
    *,
    preview: bool = False,
    features_path: Path | None = None,
    destination: Path | None = None,
) -> dict[str, Any]:
    """Compile reviewed claims and GeoJSON features into a Blender-neutral scene manifest."""
    config = load_config(project)
    features_path = features_path or project.data_dir / "features.geojson"
    destination = destination or project.scene_manifest
    raw_features = load_features(features_path)

    connection = connect(project)
    try:
        claims = list_claims(connection)
        sources = list_sources(connection)
    finally:
        connection.close()

    claim_by_id = {claim["id"]: claim for claim in claims}
    source_by_id = {source["id"]: source for source in sources}
    source_snapshot = _source_snapshot(project, sources)
    source_snapshot_by_id = {source["id"]: source for source in source_snapshot}
    allowed_statuses = {
        status.value if isinstance(status, ReviewStatus) else str(status)
        for status in (
            config.evidence_policy.preview_statuses if preview else config.evidence_policy.authoritative_statuses
        )
    }

    compiled: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    target_year = config.project.target_year

    for index, feature in enumerate(raw_features, start=1):
        feature_id = _feature_id(feature, index)
        properties = dict(feature.get("properties") or {})
        template = str(properties.get("template") or "building").strip().lower()
        feature_status = str(properties.get("review_status") or properties.get("status") or "draft")
        evidence_ids = _as_list(properties.get("evidence_ids"))
        reasons: list[str] = []

        if feature_status not in allowed_statuses:
            reasons.append(f"feature status {feature_status!r} is not allowed")
        if not _applies_to_year(properties, target_year):
            reasons.append(f"feature does not apply to target year {target_year}")

        geometry: BaseGeometry | None = None
        try:
            geometry = shape(feature.get("geometry"))
            if geometry.is_empty:
                reasons.append("geometry is empty")
            if not geometry.is_valid:
                reasons.append("geometry is invalid")
        except Exception as exc:
            reasons.append(f"geometry cannot be parsed: {exc}")

        try:
            params = _as_dict(properties.get("params"))
        except Exception as exc:
            params = {}
            reasons.append(str(exc))

        if (
            config.evidence_policy.require_evidence_for_geometry
            and template not in EVIDENCE_OPTIONAL_TEMPLATES
            and not evidence_ids
        ):
            reasons.append("no evidence claim is linked")

        linked_claims: list[dict[str, Any]] = []
        alternatives: dict[str, list[str]] = {}
        for evidence_id in evidence_ids:
            claim = claim_by_id.get(evidence_id)
            if claim is None:
                reasons.append(f"linked claim {evidence_id} does not exist")
                continue
            linked_claims.append(claim)
            if claim["review_status"] not in allowed_statuses:
                reasons.append(
                    f"linked claim {evidence_id} has disallowed status {claim['review_status']}"
                )
            if not _applies_to_year(claim, target_year):
                reasons.append(f"linked claim {evidence_id} does not apply to target year {target_year}")

            source = source_by_id.get(claim["source_id"])
            if source is None:
                reasons.append(f"linked claim {evidence_id} references missing source {claim['source_id']}")
            else:
                registered_sha = source.get("sha256") or ""
                current_sha = source_snapshot_by_id.get(source["id"], {}).get("current_sha256", registered_sha)
                bound_sha = claim.get("source_sha256_at_creation") or ""
                if current_sha == "OUTSIDE_PROJECT_ROOT":
                    reasons.append(f"linked claim {evidence_id} source path escapes the project root")
                if registered_sha and current_sha and current_sha != registered_sha:
                    reasons.append(f"linked claim {evidence_id} source file differs from the registered checksum")
                if not preview and not bound_sha:
                    reasons.append(f"linked claim {evidence_id} has no source-version binding")
                if bound_sha and current_sha and bound_sha != current_sha:
                    reasons.append(f"linked claim {evidence_id} source checksum has changed")

            group = claim.get("alternative_group")
            if group:
                alternatives.setdefault(group, []).append(evidence_id)

        for group, members in alternatives.items():
            if len(members) > 1:
                reasons.append(
                    f"mutually exclusive alternatives in group {group} are combined: {', '.join(members)}"
                )

        if reasons:
            excluded.append({"id": feature_id, "reasons": sorted(set(reasons))})
            continue

        feature_class = str(properties.get("evidence_class") or "D").upper()
        claim_classes = [str(claim["evidence_class"]).upper() for claim in linked_claims]
        effective_class = (
            _worst_class([feature_class, *claim_classes])
            if config.evidence_policy.conservative_feature_classification
            else feature_class
        )
        confidence_values = [float(properties.get("confidence", 1.0))]
        confidence_values.extend(float(claim["confidence"]) for claim in linked_claims)
        effective_confidence = min(confidence_values)

        provenance = []
        for claim in linked_claims:
            source = source_by_id.get(claim["source_id"], {})
            provenance.append(
                {
                    "claim_id": claim["id"],
                    "source_id": claim["source_id"],
                    "source_title": source.get("title", ""),
                    "source_url": source.get("url", ""),
                    "source_sha256_at_claim_creation": claim.get("source_sha256_at_creation", ""),
                    "source_sha256_registered": source.get("sha256", ""),
                    "locator": claim.get("locator", ""),
                    "quotation": claim.get("quotation", ""),
                    "claim": claim.get("claim", ""),
                    "evidence_class": claim.get("evidence_class", "D"),
                    "confidence": claim.get("confidence", 0.0),
                    "review_status": claim.get("review_status", "draft"),
                }
            )

        passthrough = {
            key: value
            for key, value in properties.items()
            if key
            not in {
                "id", "feature_id", "name", "template", "status", "review_status", "evidence_ids",
                "evidence_class", "confidence", "date_start", "date_end", "params", "notes",
            }
        }
        compiled.append(
            {
                "id": feature_id,
                "name": str(properties.get("name") or feature_id),
                "template": template,
                "geometry": mapping(geometry) if geometry is not None else None,
                "params": params,
                "evidence_ids": evidence_ids,
                "evidence_class": effective_class,
                "confidence": effective_confidence,
                "review_status": feature_status,
                "date_start": properties.get("date_start"),
                "date_end": properties.get("date_end"),
                "notes": str(properties.get("notes") or ""),
                "provenance": provenance,
                "properties": passthrough,
            }
        )

    fingerprint_payload = {
        "schema_version": config.schema_version,
        "project": config.project.model_dump(mode="json"),
        "mode": "preview" if preview else "authoritative",
        "features_sha256": sha256_file(features_path) if features_path.exists() else "",
        "source_versions": [
            {"id": item["id"], "current_sha256": item["current_sha256"]} for item in source_snapshot
        ],
        "claims": [
            {
                "id": claim["id"],
                "review_status": claim["review_status"],
                "source_sha256_at_creation": claim.get("source_sha256_at_creation", ""),
            }
            for claim in claims
        ],
        "compiled_feature_ids": [item["id"] for item in compiled],
        "excluded": excluded,
    }
    input_fingerprint = sha256_text(json_dumps(fingerprint_payload))
    manifest = {
        "manifest_schema": 1,
        "generated_at": utc_now(),
        "input_fingerprint": input_fingerprint,
        "mode": "preview" if preview else "authoritative",
        "is_preview": preview,
        "project_root": str(project.root),
        "project": config.project.model_dump(mode="json"),
        "blender": config.blender.model_dump(mode="json"),
        "sources": source_snapshot,
        "statistics": {
            "raw_features": len(raw_features),
            "compiled_features": len(compiled),
            "excluded_features": len(excluded),
            "claims": len(claims),
            "sources": len(sources),
        },
        "features": compiled,
        "excluded_features": excluded,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
