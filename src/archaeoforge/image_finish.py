from __future__ import annotations

import base64
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .models import (
    DriftAssessment,
    FinishMode,
    HistoricalSpatialAssessment,
    HistoricalSpatialContract,
)
from .openai_client import new_official_openai_client, project_openai_api_key
from .project import ProjectPaths, load_config
from .template_semantics import meets_minimum_recognizability, template_recognizability
from .util import sha256_file, sha256_text, utc_now

LEGACY_FINISH_REQUEST_SCHEMA = 1
MODE_AWARE_LEGACY_FINISH_REQUEST_SCHEMA = 2
SPATIAL_CONTRACT_LEGACY_FINISH_REQUEST_SCHEMA = 3
FINISH_REQUEST_SCHEMA = 4
FINISH_RECORD_SCHEMA = 3
CODEX_IMAGE_PROVIDER = "codex_builtin_imagegen"
CODEX_IMAGE_MODEL = "gpt-image-2"
GPT_IMAGE_2_INPUT_FIDELITY = "automatic_high"
_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_GEOMETRY_ACCEPT_SCORE = 0.98
_SPATIAL_ACCEPT_CONFIDENCE = 0.85

DEFAULT_FINISH_PROMPT = """Preserve the supplied camera, geometry, monument silhouettes, openings, stage counts,
wall alignments, street alignments, river position, relative scale, and object placement exactly. Do not redesign,
add, remove, enlarge, shrink, or relocate architecture. Improve only physically plausible materials, subtle surface
variation, atmospheric perspective, vegetation variation, distant human activity, and lighting. Do not add famous
structures that are absent from the base render. The base render is the authoritative spatial constraint.
"""

DEFAULT_HISTORICAL_SCENE_PROMPT = """Transform the schematic base render into a lifelike, inhabited historical
scene of {place_name} in {target_year_label}. Use its viewpoint and composition as visual guides while replacing
simplified forms with coherent, historically plausible architecture, landscape, atmosphere, vegetation, and human
activity. Preserve every relationship in the project's bound spatial contract even when its archaeological review
status remains draft or needs-review; evidence status does not grant permission to move selected reconstruction
anchors. Make all interpretive detail conservative and avoid anachronistic, fantasy, modern, or unsupported famous
structures.
"""

_FINISH_CONSTRAINTS = (
    "Change only surface treatment, atmosphere, lighting, vegetation variation, and distant low-detail activity.",
    "Preserve the camera, crop, perspective, geometry, silhouettes, openings, stage counts, wall and road paths, "
    "waterways, relative scale, and every structure's placement.",
    "Do not add, remove, relocate, enlarge, shrink, or redesign architecture.",
    "Do not add text, labels, borders, logos, or watermarks.",
    "Treat the result as a non-authoritative presentation layer, never as archaeological evidence.",
)

_BROAD_ANCHOR_POLICY_FIXED = "fixed"
_BROAD_ANCHOR_POLICY_GUIDE_ONLY = "guide_only"
_BROAD_ANCHOR_POLICIES = {
    _BROAD_ANCHOR_POLICY_FIXED,
    _BROAD_ANCHOR_POLICY_GUIDE_ONLY,
}

_HISTORICAL_SCENE_COMMON_CONSTRAINTS = (
    "You may replace schematic forms with coherent lifelike architecture and extend the surrounding environment "
    "with historically appropriate detail requested by the primary prompt.",
    "Do not add anachronistic, fantasy, modern, or unsupported famous structures.",
    "Do not add text, labels, borders, logos, or watermarks.",
    "Treat the result as an interpretive, non-authoritative presentation layer, never as archaeological evidence.",
)

_HISTORICAL_SCENE_SPATIAL_CONSTRAINTS = (
    "Treat the base render as a spatial and compositional guide, not as exact finished geometry.",
    "Preserve the viewpoint, crop, and every protected feature relationship in the bound spatial contract.",
    "A draft or needs-review evidence status describes uncertainty; it does not make a selected presentation "
    "anchor movable.",
    "Replace proxy form and materials without relocating protected anchors. Only feature IDs explicitly listed "
    "as mutable in the contract may move.",
    "If the primary request or a supporting image conflicts with the spatial contract, follow the contract.",
)

_HISTORICAL_AUDIT_SKIP_REASON = (
    "Strict geometry audit is not applicable to historical_scene finishes because that mode permits "
    "interpretive geometry changes; a separate protected-anchor audit is required before publication."
)


class HistoricalSpatialValidationError(RuntimeError):
    """Raised before publication when a historical finish has not passed its anchor contract."""


def _client(project: ProjectPaths) -> Any:
    return new_official_openai_client(project)


def _data_url(path: Path) -> str:
    mime = "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif path.suffix.lower() == ".webp":
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _atomic_write_text(destination: Path, value: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with (
            source.open("rb") as source_handle,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as destination_handle,
        ):
            temporary = Path(destination_handle.name)
            shutil.copyfileobj(source_handle, destination_handle)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_resize_png(source: Path, destination: Path, size: tuple[int, int]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        with Image.open(source) as image:
            image.resize(size, Image.Resampling.LANCZOS).save(temporary, format="PNG")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _save_image_response(response: Any, destination: Path) -> None:
    if not getattr(response, "data", None):
        raise RuntimeError("The image endpoint returned no image data.")
    item = response.data[0]
    encoded = getattr(item, "b64_json", None)
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            if encoded:
                handle.write(base64.b64decode(encoded, validate=True))
            else:
                raise RuntimeError("The GPT Image endpoint returned no base64 image data.")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _image_metadata(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format or "unknown"
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"Not a valid supported image: {path}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"Image has invalid dimensions: {path}")
    return {
        "sha256": sha256_file(path),
        "width": width,
        "height": height,
        "format": image_format.upper(),
    }


def _temporary_sibling(destination: Path, *, suffix: str = ".png") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=suffix,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    temporary.unlink()
    return temporary


def _assert_image_snapshot(path: Path, expected: dict[str, Any], *, label: str) -> dict[str, Any]:
    current = _image_metadata(path)
    fields = ("sha256", "width", "height", "format")
    if any(current[field] != expected.get(field) for field in fields):
        raise ValueError(f"{label} changed while the finish operation was running.")
    return current


def _api_response_metadata(response: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for field in ("created", "background", "output_format", "quality", "size"):
        value = getattr(response, field, None)
        if value is not None:
            metadata[field] = value if isinstance(value, (str, int, float, bool)) else str(value)
    usage = getattr(response, "usage", None)
    if usage is not None:
        if hasattr(usage, "model_dump"):
            metadata["usage"] = usage.model_dump(mode="json")
        elif isinstance(usage, dict):
            metadata["usage"] = usage
        else:
            metadata["usage"] = str(usage)
    data = getattr(response, "data", None)
    if data:
        revised_prompt = getattr(data[0], "revised_prompt", None)
        if revised_prompt:
            metadata["revised_prompt"] = revised_prompt
    return metadata


def _assessment_accepts_geometry(assessment: DriftAssessment | None) -> bool:
    return bool(
        assessment is not None
        and assessment.recommendation == "accept"
        and assessment.geometry_preservation_score >= _GEOMETRY_ACCEPT_SCORE
        and assessment.camera_preserved
        and assessment.major_silhouettes_preserved
        and assessment.object_placement_preserved
        and not assessment.detected_changes
    )


def _relative_path(project: ProjectPaths, path: Path, *, label: str) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(project.root).as_posix()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be inside the project directory: {resolved}") from exc


def _resolve_relative_path(project: ProjectPaths, value: str, *, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"{label} must be project-relative, not absolute: {value}")
    resolved = (project.root / relative).resolve()
    try:
        resolved.relative_to(project.root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project directory: {value}") from exc
    return resolved


def _resolve_artifact_path(
    project: ProjectPaths,
    path: Path,
    *,
    container: Path,
    suffix: str,
    label: str,
) -> Path:
    """Resolve a generated artifact into its dedicated project output directory."""
    resolved = path.expanduser().resolve()
    _relative_path(project, resolved, label=label)
    try:
        resolved.relative_to(container.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must be inside {container.resolve()}: {resolved}") from exc
    if resolved.suffix.lower() != suffix:
        raise ValueError(f"{label} must use the {suffix} extension: {resolved}")
    return resolved


def _resolve_finish_output_path(project: ProjectPaths, path: Path, *, label: str) -> Path:
    return _resolve_artifact_path(
        project,
        path,
        container=project.renders_dir,
        suffix=".png",
        label=label,
    )


def _resolve_finish_request_path(project: ProjectPaths, path: Path, *, label: str) -> Path:
    return _resolve_artifact_path(
        project,
        path,
        container=project.exports_dir,
        suffix=".json",
        label=label,
    )


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {label} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _load_finish_prompt(
    project: ProjectPaths,
    prompt_path: Path | None,
    mode: FinishMode | str = FinishMode.precise_object_edit,
) -> str:
    finish_mode = _coerce_finish_mode(mode)
    if prompt_path is not None:
        selected = prompt_path
        prompt = selected.read_text(encoding="utf-8")
    elif finish_mode is FinishMode.historical_scene:
        selected = project.prompts_dir / "finish_historical_scene.txt"
        if selected.exists():
            prompt = selected.read_text(encoding="utf-8")
        else:
            config = load_config(project)
            prompt = DEFAULT_HISTORICAL_SCENE_PROMPT.format(
                place_name=config.project.place_name,
                target_year_label=config.project.target_year_label,
            )
    else:
        selected = project.prompts_dir / "finish.txt"
        prompt = selected.read_text(encoding="utf-8") if selected.exists() else DEFAULT_FINISH_PROMPT
    prompt = prompt.strip()
    if not prompt:
        raise ValueError(f"Finishing prompt is empty: {selected}")
    return prompt


def _coerce_finish_mode(value: FinishMode | str | None) -> FinishMode:
    if value is None:
        return FinishMode.precise_object_edit
    try:
        return FinishMode(value)
    except ValueError as exc:
        supported = ", ".join(mode.value for mode in FinishMode)
        raise ValueError(f"Unsupported finish mode {value!r}; expected one of: {supported}.") from exc


def _request_finish_mode(request: dict[str, Any]) -> FinishMode:
    schema = request.get("finish_request_schema")
    if schema == LEGACY_FINISH_REQUEST_SCHEMA:
        if "finish_mode" in request:
            raise ValueError("Legacy finish request schema 1 must not carry finish_mode.")
        return FinishMode.precise_object_edit
    if schema in {
        MODE_AWARE_LEGACY_FINISH_REQUEST_SCHEMA,
        SPATIAL_CONTRACT_LEGACY_FINISH_REQUEST_SCHEMA,
        FINISH_REQUEST_SCHEMA,
    }:
        if "finish_mode" not in request:
            raise ValueError(f"Finish request schema {schema} must explicitly carry finish_mode.")
        return _coerce_finish_mode(request["finish_mode"])
    raise ValueError(f"Unsupported finish request schema: {schema!r}")


def _spatial_contract_prompt_block(spatial_contract: dict[str, Any]) -> str:
    constraints = spatial_contract.get("constraints", [])
    protected_features = spatial_contract.get("protected_features", [])
    if not constraints or not protected_features:
        raise ValueError("Historical-scene prompt construction requires a non-empty spatial contract.")
    feature_lines = []
    for feature in protected_features:
        feature_lines.append(
            "- {id} ({name}): geometry={geometry}; params={params}; evidence_status={status}.".format(
                id=feature["id"],
                name=feature.get("name") or feature["id"],
                geometry=json.dumps(feature.get("geometry"), ensure_ascii=False, sort_keys=True),
                params=json.dumps(feature.get("params", {}), ensure_ascii=False, sort_keys=True),
                status=feature.get("review_status", "unknown"),
            )
        )
    relationship_lines = [
        f"- [{constraint['id']}] {constraint['requirement']}"
        for constraint in constraints
        if constraint.get("required", True)
    ]
    mutable = spatial_contract.get("mutable_feature_ids", [])
    mutable_line = ", ".join(mutable) if mutable else "none"
    base_requirements = spatial_contract.get("base_render_requirements", [])
    base_requirement_lines = [
        "- [{id}] minimum={minimum}; features={features}; {requirement}".format(
            id=item["id"],
            minimum=item["minimum_recognizability"],
            features=", ".join(item["feature_ids"]),
            requirement=item["requirement"],
        )
        for item in base_requirements
    ]
    base_requirement_block = (
        "\nProtected semantic base-render requirements already satisfied:\n"
        + "\n".join(base_requirement_lines)
        if base_requirement_lines
        else ""
    )
    return (
        "NON-NEGOTIABLE SPATIAL CONTRACT\n"
        "Protected feature snapshots from the bound scene manifest:\n"
        + "\n".join(feature_lines)
        + "\nRequired visible relationships:\n"
        + "\n".join(relationship_lines)
        + base_requirement_block
        + f"\nExplicitly mutable feature IDs: {mutable_line}.\n"
        "Proxy form and materials may change; protected placement, ordering, topology, scale relationships, "
        "viewpoint, and crop may not. If the requested scene cannot satisfy every required relationship, do not "
        "regularize or improvise it."
    )


def _structured_finish_prompt(
    prompt: str,
    mode: FinishMode | str = FinishMode.precise_object_edit,
    *,
    spatial_contract: dict[str, Any] | None = None,
    supporting_reference_count: int = 0,
) -> str:
    finish_mode = _coerce_finish_mode(mode)
    if finish_mode is FinishMode.historical_scene:
        if spatial_contract is None:
            raise ValueError("Historical-scene prompt construction requires a spatial contract.")
        constraints = "\n".join(
            f"- {item}"
            for item in (*_HISTORICAL_SCENE_SPATIAL_CONSTRAINTS, *_HISTORICAL_SCENE_COMMON_CONSTRAINTS)
        )
        input_images = "Image 1 is the base render and spatial/compositional guide."
        if supporting_reference_count:
            last_index = supporting_reference_count + 1
            reference_label = (
                "Image 2 is" if supporting_reference_count == 1 else f"Images 2 through {last_index} are"
            )
            input_images += (
                f" {reference_label} hash-bound supporting references only; use them for the roles described in "
                "the primary request, not as additional edit targets."
            )
        return (
            "Use case: historical-scene\n"
            "Asset type: evidence-controlled interpretive archaeological scene visualization\n"
            f"{_spatial_contract_prompt_block(spatial_contract)}\n"
            f"Primary request: {prompt.strip()}\n"
            f"Input images: {input_images}\n"
            "Constraints:\n"
            f"{constraints}\n"
            "Avoid: anachronistic or fantasy architecture, changed viewpoint or crop, spatial regularization, "
            "text, and watermark"
        )

    constraints = "\n".join(f"- {item}" for item in _FINISH_CONSTRAINTS)
    return (
        "Use case: precise-object-edit\n"
        "Asset type: evidence-controlled archaeological reconstruction presentation render\n"
        f"Primary request: {prompt.strip()}\n"
        "Input images: Image 1 is the authoritative base render and spatial constraint.\n"
        "Constraints:\n"
        f"{constraints}\n"
        "Avoid: new architecture, changed geometry, changed camera or crop, fantasy elements, text, and watermark"
    )


def _manifest_supports_fixed_broad_anchors(manifest: dict[str, Any]) -> bool:
    features = manifest.get("features")
    if manifest.get("mode") != "authoritative" or not isinstance(features, list) or not features:
        return False
    for feature in features:
        if not isinstance(feature, dict) or feature.get("review_status") != "approved":
            return False
        provenance = feature.get("provenance")
        if not isinstance(provenance, list) or not provenance:
            return False
        if any(not isinstance(item, dict) or item.get("review_status") != "approved" for item in provenance):
            return False
    return True


def _manifest_broad_anchor_policy(manifest: dict[str, Any]) -> str:
    if _manifest_supports_fixed_broad_anchors(manifest):
        return _BROAD_ANCHOR_POLICY_FIXED
    return _BROAD_ANCHOR_POLICY_GUIDE_ONLY


def _manifest_binding(project: ProjectPaths) -> dict[str, Any]:
    if not project.scene_manifest.is_file():
        raise FileNotFoundError(
            f"Compiled scene manifest not found: {project.scene_manifest}. Run 'archaeoforge compile' first."
        )
    manifest = _read_json_object(project.scene_manifest, label="scene manifest")
    return {
        "path": _relative_path(project, project.scene_manifest, label="Scene manifest"),
        "sha256": sha256_file(project.scene_manifest),
        "input_fingerprint": str(manifest.get("input_fingerprint", "")),
        "mode": str(manifest.get("mode", "unknown")),
    }


def _historical_render_receipt_binding(project: ProjectPaths, *, base_image: Path) -> dict[str, Any]:
    """Prove that a historical base is the fresh Blender render of the bound manifest."""
    expected_base = (project.renders_dir / "beauty.png").resolve()
    if base_image != expected_base:
        raise ValueError(
            "historical_scene must use the current outputs/renders/beauty.png as Image 1. "
            "Generated candidates may be bound only as supporting appearance references, never as the spatial base."
        )
    if not project.blender_result.is_file():
        raise ValueError(
            "Historical-scene render receipt is missing. Recompile and rerender before preparing the finish."
        )
    receipt = _read_json_object(project.blender_result, label="Blender render receipt")
    if receipt.get("render_receipt_schema") != 1 or receipt.get("rendered") is not True:
        raise ValueError(
            "Historical-scene render receipt does not record a completed render. Recompile and rerender first."
        )

    current_manifest = _read_json_object(project.scene_manifest, label="scene manifest")
    expected_manifest = {
        "path": _relative_path(project, project.scene_manifest, label="Scene manifest"),
        "sha256": sha256_file(project.scene_manifest),
        "input_fingerprint": str(current_manifest.get("input_fingerprint", "")),
    }
    if receipt.get("manifest") != expected_manifest:
        raise ValueError(
            "Historical-scene render receipt was produced from a different manifest. Recompile and rerender first."
        )

    expected_templates = {
        str(feature["id"]): {
            "template": str(feature.get("template", "building")),
            "recognizability": template_recognizability(feature.get("template", "building")),
        }
        for feature in current_manifest.get("features", [])
        if isinstance(feature, dict) and feature.get("id")
    }
    if receipt.get("feature_templates") != expected_templates:
        raise ValueError(
            "Historical-scene render receipt template semantics do not match the current manifest. "
            "Recompile and rerender first."
        )

    current_base = _image_metadata(base_image)
    expected_beauty = {
        "path": _relative_path(project, base_image, label="Base image"),
        **current_base,
    }
    if receipt.get("beauty_image") != expected_beauty:
        raise ValueError(
            "Historical-scene beauty image changed after the recorded render. Recompile and rerender first."
        )
    return {
        "path": _relative_path(project, project.blender_result, label="Blender render receipt"),
        "sha256": sha256_file(project.blender_result),
        "render_receipt_schema": 1,
        "manifest": expected_manifest,
        "beauty_image": expected_beauty,
        "feature_templates": expected_templates,
    }


def _historical_spatial_contract_binding(project: ProjectPaths) -> dict[str, Any]:
    config = load_config(project)
    configured_path = config.ai.historical_scene_spatial_contract
    if not configured_path:
        raise ValueError(
            "historical_scene requires ai.historical_scene_spatial_contract; author a project-relative "
            "contract before preparing or publishing an interpretive finish."
        )
    contract_path = _resolve_relative_path(
        project,
        configured_path,
        label="Historical-scene spatial-contract path",
    )
    try:
        contract = HistoricalSpatialContract.model_validate(
            _read_json_object(contract_path, label="historical-scene spatial contract")
        )
    except Exception as exc:
        raise ValueError(f"Invalid historical-scene spatial contract: {contract_path}: {exc}") from exc

    manifest = _read_json_object(project.scene_manifest, label="scene manifest")
    features = manifest.get("features")
    if not isinstance(features, list):
        raise ValueError("Scene manifest does not contain a feature list for spatial-contract binding.")
    features_by_id: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("id"), str):
            raise ValueError("Scene manifest contains an invalid feature entry.")
        feature_id = feature["id"]
        if feature_id in features_by_id:
            raise ValueError(f"Scene manifest contains duplicate feature id: {feature_id}")
        features_by_id[feature_id] = feature

    referenced_ids = {
        feature_id for constraint in contract.constraints for feature_id in constraint.feature_ids
    }.union(
        feature_id
        for requirement in contract.base_render_requirements
        for feature_id in requirement.feature_ids
    ).union(contract.mutable_feature_ids)
    missing_ids = sorted(referenced_ids.difference(features_by_id))
    if missing_ids:
        raise ValueError(
            "Historical-scene spatial contract references feature ids absent from the compiled manifest: "
            + ", ".join(missing_ids)
        )

    all_manifest_evidence = {
        evidence_id
        for feature in features_by_id.values()
        for evidence_id in feature.get("evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    missing_evidence = sorted(
        {
            evidence_id for constraint in contract.constraints for evidence_id in constraint.evidence_ids
        }.difference(all_manifest_evidence)
    )
    if missing_evidence:
        raise ValueError(
            "Historical-scene spatial contract references evidence ids absent from the compiled manifest: "
            + ", ".join(missing_evidence)
        )

    protected_ids = sorted(
        {
            feature_id
            for constraint in contract.constraints
            if constraint.required
            for feature_id in constraint.feature_ids
        }
    )
    protected_id_set = set(protected_ids)
    readiness_unprotected = sorted(
        {
            feature_id
            for requirement in contract.base_render_requirements
            for feature_id in requirement.feature_ids
        }.difference(protected_id_set)
    )
    if readiness_unprotected:
        raise ValueError(
            "Historical base-render requirements must reference required protected features: "
            + ", ".join(readiness_unprotected)
        )

    readiness_failures: list[str] = []
    for requirement in contract.base_render_requirements:
        for feature_id in requirement.feature_ids:
            feature = features_by_id[feature_id]
            template = feature.get("template", "building")
            if not meets_minimum_recognizability(template, requirement.minimum_recognizability):
                readiness_failures.append(
                    f"{feature_id} uses template {template!r} ({template_recognizability(template)}) "
                    f"but {requirement.id} requires {requirement.minimum_recognizability}"
                )
    if readiness_failures:
        raise ValueError(
            "Historical-scene base render is not semantically ready: "
            + "; ".join(readiness_failures)
            + ". Replace generic envelopes with suitable native templates, recompile, and rerender before finishing."
        )

    protected_features = []
    for feature_id in protected_ids:
        feature = features_by_id[feature_id]
        protected_features.append(
            {
                key: feature.get(key)
                for key in (
                    "id",
                    "name",
                    "template",
                    "geometry",
                    "params",
                    "review_status",
                    "evidence_ids",
                )
            }
        )
    return {
        "path": _relative_path(project, contract_path, label="Historical-scene spatial contract"),
        "sha256": sha256_file(contract_path),
        "spatial_contract_schema": contract.spatial_contract_schema,
        "constraints": [constraint.model_dump(mode="json") for constraint in contract.constraints],
        "base_render_requirements": [
            requirement.model_dump(mode="json") for requirement in contract.base_render_requirements
        ],
        "mutable_feature_ids": list(contract.mutable_feature_ids),
        "protected_features": protected_features,
        "notes": contract.notes,
    }


def _supporting_reference_bindings(
    project: ProjectPaths,
    reference_images: list[Path] | None,
    *,
    base_image: Path,
) -> list[dict[str, Any]]:
    if len(reference_images or []) > 4:
        raise ValueError("A finish request may bind at most four supporting reference images.")
    bindings: list[dict[str, Any]] = []
    seen = {base_image}
    for image_index, reference_image in enumerate(reference_images or [], start=2):
        reference_image = reference_image.expanduser().resolve()
        if reference_image in seen:
            raise ValueError("Supporting reference images must be unique and differ from the base image.")
        seen.add(reference_image)
        relative = _relative_path(project, reference_image, label="Supporting reference image")
        bindings.append(
            {
                "image_index": image_index,
                "role": "supporting_reference",
                "path": relative,
                **_image_metadata(reference_image),
            }
        )
    return bindings


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for version in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-v{version}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not select an available output name beside {path}")


def _finish_record_path(destination: Path) -> Path:
    return destination.with_suffix(".provenance.json")


def _finish_audit_path(destination: Path) -> Path:
    return destination.with_suffix(".audit.json")


def _finish_artifact_paths(destination: Path) -> tuple[Path, Path, Path]:
    return destination, _finish_record_path(destination), _finish_audit_path(destination)


def _next_available_finish_path(path: Path) -> Path:
    if not any(candidate.exists() for candidate in _finish_artifact_paths(path)):
        return path
    for version in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-v{version}{path.suffix}")
        if not any(artifact.exists() for artifact in _finish_artifact_paths(candidate)):
            return candidate
    raise RuntimeError(f"Could not select an available finish-output name beside {path}")


def _existing_finish_artifacts(destination: Path) -> list[Path]:
    return [artifact for artifact in _finish_artifact_paths(destination) if artifact.exists()]


def _aspect_ratio(metadata: dict[str, Any]) -> float:
    return float(metadata["width"]) / float(metadata["height"])


def _size_metadata(value: str) -> dict[str, int]:
    width_text, height_text = value.lower().split("x", maxsplit=1)
    return {"width": int(width_text), "height": int(height_text)}


def _validate_api_image_size(model: str, size: str) -> None:
    dimensions = _size_metadata(size)
    width, height = dimensions["width"], dimensions["height"]
    if model in {"gpt-image-2", "gpt-image-2-2026-04-21"}:
        if width % 16 or height % 16:
            raise ValueError("GPT Image 2 width and height must both be divisible by 16.")
        ratio = width / height
        if not 1 / 3 <= ratio <= 3:
            raise ValueError("GPT Image 2 aspect ratio must be between 1:3 and 3:1.")
        total_pixels = width * height
        if total_pixels < 655_360:
            raise ValueError("GPT Image 2 output must contain at least 655,360 pixels.")
        if max(width, height) > 3840 or total_pixels > 8_294_400:
            raise ValueError("GPT Image 2 output cannot exceed 3840x2160-equivalent resolution.")
        return
    supported = {"1024x1024", "1536x1024", "1024x1536"}
    if size not in supported:
        raise ValueError(
            f"Image model {model!r} requires one of the supported standard sizes: "
            f"{', '.join(sorted(supported))}."
        )


def _assert_compatible_frame(base: dict[str, Any], finished: dict[str, Any]) -> None:
    if (finished["width"], finished["height"]) != (base["width"], base["height"]):
        raise ValueError(
            "Finished image dimensions differ from the authoritative base render: "
            f"{finished['width']}x{finished['height']} instead of {base['width']}x{base['height']}. "
            "Regenerate without changing the camera or crop."
        )


def _assert_compatible_aspect_ratio(base: dict[str, Any], finished: dict[str, Any]) -> None:
    relative_delta = abs(_aspect_ratio(finished) / _aspect_ratio(base) - 1.0)
    if relative_delta > 0.005:
        raise ValueError(
            "Finished image aspect ratio differs from the authoritative base render by "
            f"{relative_delta:.2%}; regenerate without changing the camera or crop."
        )


def _finish_request_id(request: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in request.items()
        if key not in {"request_id", "generated_at", "status", "suggested_codex_prompt"}
    }
    canonical = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def _suggested_codex_prompt(
    base_path: str,
    request_path: str,
    destination: str,
    mode: FinishMode | str = FinishMode.precise_object_edit,
    *,
    spatial_contract: dict[str, Any] | None = None,
    supporting_reference_paths: tuple[str, ...] = (),
) -> str:
    finish_mode = _coerce_finish_mode(mode)
    if finish_mode is FinishMode.historical_scene:
        if spatial_contract is None:
            raise ValueError("Historical-scene handoff requires a bound spatial contract.")
        supporting_instruction = ""
        if supporting_reference_paths:
            indexed_paths = ", ".join(
                f"Image {index}: {path}" for index, path in enumerate(supporting_reference_paths, start=2)
            )
            supporting_instruction = (
                f" Include only the request's hash-bound supporting references ({indexed_paths}); do not use "
                "unbound iterative images."
            )
        return (
            f"Use $imagegen to transform the local base image at {base_path} into a lifelike historical scene. "
            f"Follow the historical_scene prompt in {request_path} exactly. Its NON-NEGOTIABLE SPATIAL CONTRACT "
            "is hash-bound: preserve every protected anchor and required visible relationship even when its "
            "evidence status is draft or needs-review."
            f"{supporting_instruction} Keep the "
            f"returned PNG at its separate tool-generated candidate path; do not write the final destination "
            f"{destination} directly. Publish it only through the validation gate: archaeoforge register-finish "
            f"PATH_TO_CANDIDATE --project . --request {request_path}"
        )
    return (
        f"Use $imagegen to edit the local authoritative base image at {base_path}. Follow the prompt in "
        f"{request_path} exactly and preserve its camera and geometry. Keep the returned PNG at its separate "
        f"tool-generated candidate path; do not write the final destination {destination} directly. Publish it "
        "only through the validation gate: archaeoforge register-finish PATH_TO_CANDIDATE "
        f"--project . --request {request_path}"
    )


def prepare_finish_request(
    project: ProjectPaths,
    *,
    base_image: Path,
    destination: Path | None = None,
    prompt_path: Path | None = None,
    request_path: Path | None = None,
    overwrite_request: bool = False,
    mode: FinishMode | str | None = None,
    reference_images: list[Path] | None = None,
) -> Path:
    """Write a portable, hash-bound request for an interactive image-generation tool.

    Codex's built-in image generator is a session capability, not a Python API that this
    package can import. This request is the explicit handoff between those two trust zones.
    """
    config = load_config(project)
    finish_mode = _coerce_finish_mode(config.ai.finish_mode if mode is None else mode)
    base_image = base_image.expanduser().resolve()
    base_relative = _relative_path(project, base_image, label="Base image")
    base_metadata = _image_metadata(base_image)
    manifest = _manifest_binding(project)
    render_receipt = (
        _historical_render_receipt_binding(project, base_image=base_image)
        if finish_mode is FinishMode.historical_scene
        else None
    )
    spatial_contract = (
        _historical_spatial_contract_binding(project) if finish_mode is FinishMode.historical_scene else None
    )
    if reference_images and finish_mode is not FinishMode.historical_scene:
        raise ValueError("Supporting reference images are available only for historical_scene finishes.")
    supporting_references = _supporting_reference_bindings(
        project,
        reference_images,
        base_image=base_image,
    )

    if destination is None:
        destination = _next_available_finish_path(project.renders_dir / "finished.png")
    destination = _resolve_finish_output_path(
        project,
        destination,
        label="Finished image destination",
    )
    if destination == base_image:
        raise ValueError("Finished image destination cannot overwrite the authoritative base image.")
    destination_relative = _relative_path(project, destination, label="Finished image destination")
    if destination_relative in {str(reference["path"]) for reference in supporting_references}:
        raise ValueError("Finished image destination cannot overwrite a supporting reference image.")
    existing_finish_artifacts = _existing_finish_artifacts(destination)
    if existing_finish_artifacts:
        paths = ", ".join(str(path) for path in existing_finish_artifacts)
        raise FileExistsError(f"Finished image output set already exists: {paths}")

    raw_prompt = _load_finish_prompt(project, prompt_path, finish_mode)
    prompt = _structured_finish_prompt(
        raw_prompt,
        finish_mode,
        spatial_contract=spatial_contract,
        supporting_reference_count=len(supporting_references),
    )
    if request_path is None:
        request_path = _next_available_path(project.exports_dir / "image_finish_request.json")
    request_path = _resolve_finish_request_path(project, request_path, label="Finish request")
    if request_path.exists():
        if not overwrite_request:
            raise FileExistsError(f"Finish request already exists: {request_path}")
        existing_request = _read_json_object(request_path, label="existing finish request")
        try:
            _request_finish_mode(existing_request)
        except ValueError as exc:
            raise ValueError(
                "Refusing to replace an existing JSON file that is not an ArchaeoForge finish request: "
                f"{request_path}"
            ) from exc
    request_relative = _relative_path(project, request_path, label="Finish request")
    size = f"{base_metadata['width']}x{base_metadata['height']}"
    request = {
        "finish_request_schema": FINISH_REQUEST_SCHEMA,
        "finish_mode": finish_mode.value,
        "generated_at": utc_now(),
        "status": "ready_for_interactive_generation",
        "authority": {
            "authoritative": False,
            "statement": (
                "The generated image is a derived presentation layer. The evidence register and scene manifest "
                "remain authoritative."
            ),
        },
        "project": {
            "id": config.project.id,
            "title": config.project.title,
            "target_year": config.project.target_year,
            "target_year_label": config.project.target_year_label,
        },
        "manifest": manifest,
        "render_receipt": render_receipt,
        "spatial_contract": spatial_contract,
        "base_image": {"path": base_relative, **base_metadata},
        "reference_images": supporting_references,
        "desired_output": {
            "path": destination_relative,
            "format": "PNG",
            "width": base_metadata["width"],
            "height": base_metadata["height"],
        },
        "generation": {
            "operation": "edit",
            "interactive_provider": CODEX_IMAGE_PROVIDER,
            "interactive_model": CODEX_IMAGE_MODEL,
            "api_model": config.ai.image_model,
            "quality": config.ai.image_quality,
            "input_fidelity": GPT_IMAGE_2_INPUT_FIDELITY,
            "size": size,
        },
        "prompt": prompt,
        "prompt_sha256": sha256_text(prompt),
        "geometry_audit_enabled": (
            config.ai.geometry_audit_enabled if finish_mode is FinishMode.precise_object_edit else False
        ),
        "suggested_codex_prompt": _suggested_codex_prompt(
            base_relative,
            request_relative,
            destination_relative,
            finish_mode,
            spatial_contract=spatial_contract,
            supporting_reference_paths=tuple(str(reference["path"]) for reference in supporting_references),
        ),
    }
    request["request_id"] = _finish_request_id(request)
    _atomic_write_text(
        request_path,
        json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return request_path


def _validate_finish_request(project: ProjectPaths, request_path: Path) -> dict[str, Any]:
    request_path = _resolve_finish_request_path(project, request_path, label="Finish request")
    request = _read_json_object(request_path, label="finish request")
    request_schema = request.get("finish_request_schema")
    finish_mode = _request_finish_mode(request)
    if request.get("status") != "ready_for_interactive_generation":
        raise ValueError(f"Finish request is not ready for generation: {request.get('status')!r}")
    if not isinstance(request.get("geometry_audit_enabled"), bool):
        raise ValueError("Finish request geometry-audit policy must be true or false.")
    if request.get("request_id") != _finish_request_id(request):
        raise ValueError("Finish request content was changed after it was prepared.")
    if finish_mode is FinishMode.historical_scene and request["geometry_audit_enabled"]:
        raise ValueError("Historical-scene finish requests must disable strict geometry audit.")
    if finish_mode is FinishMode.historical_scene and request_schema != FINISH_REQUEST_SCHEMA:
        raise ValueError(
            "Legacy historical-scene finish requests do not bind a protected spatial contract; "
            "prepare a new request before registration."
        )
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or sha256_text(prompt) != request.get("prompt_sha256"):
        raise ValueError("Finish request prompt is missing or its checksum does not match.")

    manifest = request.get("manifest")
    spatial_contract = request.get("spatial_contract")
    render_receipt = request.get("render_receipt")
    base = request.get("base_image")
    supporting_references = request.get("reference_images", [])
    output = request.get("desired_output")
    request_project = request.get("project")
    if not all(isinstance(value, dict) for value in (manifest, base, output)):
        raise ValueError("Finish request is missing manifest, base-image, or output metadata.")
    if not isinstance(supporting_references, list):
        raise ValueError("Finish request supporting references must be a list.")
    if finish_mode is not FinishMode.historical_scene and supporting_references:
        raise ValueError("Supporting reference images are available only for historical_scene finishes.")
    if finish_mode is FinishMode.historical_scene:
        if not isinstance(spatial_contract, dict):
            raise ValueError("Historical-scene finish request is missing its spatial contract.")
        current_spatial_contract = _historical_spatial_contract_binding(project)
        if spatial_contract != current_spatial_contract:
            raise ValueError(
                "Historical-scene spatial contract or its protected manifest features changed after the finish "
                "request was prepared; prepare a new request."
            )
    elif spatial_contract is not None:
        raise ValueError("Precise-object-edit finish requests must not carry a historical spatial contract.")
    config = load_config(project)
    if not isinstance(request_project, dict) or request_project.get("id") != config.project.id:
        raise ValueError("Finish request project identity does not match the selected project.")

    manifest_path = _resolve_relative_path(project, str(manifest.get("path", "")), label="Manifest path")
    if manifest_path != project.scene_manifest.resolve():
        raise ValueError("Finish request must bind the project's compiled scene manifest.")
    if sha256_file(manifest_path) != manifest.get("sha256"):
        raise ValueError(
            "Scene manifest changed after the finish request was prepared; prepare a new request."
        )
    broad_anchor_policy = manifest.get("broad_anchor_policy")
    if broad_anchor_policy is not None:
        if broad_anchor_policy not in _BROAD_ANCHOR_POLICIES:
            raise ValueError("Finish request carries an unsupported broad-anchor policy.")
        current_manifest = _read_json_object(manifest_path, label="scene manifest")
        expected_policy = _manifest_broad_anchor_policy(current_manifest)
        if broad_anchor_policy != expected_policy:
            raise ValueError("Finish request broad-anchor policy does not match the bound scene manifest.")

    base_path = _resolve_relative_path(project, str(base.get("path", "")), label="Base-image path")
    current_base = _image_metadata(base_path)
    if current_base["sha256"] != base.get("sha256"):
        raise ValueError("Authoritative base image changed after the finish request was prepared.")
    for field in ("width", "height", "format"):
        if current_base[field] != base.get(field):
            raise ValueError(
                "Authoritative base image metadata changed after the finish request was prepared."
            )
    if finish_mode is FinishMode.historical_scene:
        if not isinstance(render_receipt, dict):
            raise ValueError("Historical-scene finish request is missing its Blender render receipt.")
        current_render_receipt = _historical_render_receipt_binding(project, base_image=base_path)
        if render_receipt != current_render_receipt:
            raise ValueError(
                "Historical-scene Blender render receipt changed after the finish request was prepared; "
                "prepare a new request."
            )
    elif render_receipt is not None:
        raise ValueError("Precise-object-edit finish requests must not carry a Blender render receipt.")

    reference_paths: list[Path] = []
    reference_path_values: list[str] = []
    for expected_index, reference in enumerate(supporting_references, start=2):
        if not isinstance(reference, dict):
            raise ValueError("Finish request supporting-reference entries must be objects.")
        if reference.get("image_index") != expected_index:
            raise ValueError("Finish request supporting-reference image indices must be consecutive from 2.")
        if reference.get("role") != "supporting_reference":
            raise ValueError("Finish request carries an unsupported reference-image role.")
        path_value = str(reference.get("path", ""))
        reference_path = _resolve_relative_path(
            project,
            path_value,
            label="Supporting-reference path",
        )
        if reference_path == base_path or reference_path in reference_paths:
            raise ValueError("Supporting reference images must be unique and differ from the base image.")
        current_reference = _image_metadata(reference_path)
        for field in ("sha256", "width", "height", "format"):
            if current_reference[field] != reference.get(field):
                raise ValueError("Supporting reference image changed after the finish request was prepared.")
        reference_paths.append(reference_path)
        reference_path_values.append(path_value)

    output_path = _resolve_relative_path(project, str(output.get("path", "")), label="Desired-output path")
    _resolve_finish_output_path(project, output_path, label="Desired-output path")
    if output_path == base_path:
        raise ValueError("Finished image destination cannot overwrite the authoritative base image.")
    if output_path in reference_paths:
        raise ValueError("Finished image destination cannot overwrite a supporting reference image.")
    if output.get("format") != "PNG":
        raise ValueError("Finish request output format must be PNG.")
    if (output.get("width"), output.get("height")) != (base.get("width"), base.get("height")):
        raise ValueError("Finish request output dimensions must match the authoritative base image.")
    expected_instruction = _suggested_codex_prompt(
        str(base.get("path", "")),
        _relative_path(project, request_path, label="Finish request"),
        str(output.get("path", "")),
        finish_mode,
        spatial_contract=spatial_contract,
        supporting_reference_paths=tuple(reference_path_values),
    )
    if request.get("suggested_codex_prompt") != expected_instruction:
        raise ValueError("Finish request handoff instruction was changed after it was prepared.")
    return request


def _write_finish_record(
    project: ProjectPaths,
    *,
    base_image: Path,
    finished_image: Path,
    prompt: str,
    provider: str,
    model: str,
    manifest: dict[str, Any],
    request_path: Path | None,
    request_id: str | None,
    assessment: DriftAssessment | None,
    finish_mode: FinishMode | str = FinishMode.precise_object_edit,
    render_receipt: dict[str, Any] | None = None,
    base_metadata: dict[str, Any] | None = None,
    finished_metadata: dict[str, Any] | None = None,
    audit_metadata: dict[str, Any] | None = None,
    audit_error: str | None = None,
    audit_status: str | None = None,
    audit_reason: str | None = None,
    source_artifact: dict[str, Any] | None = None,
    normalization: dict[str, Any] | None = None,
    manual_review: dict[str, Any] | None = None,
    generation_metadata: dict[str, Any] | None = None,
    supporting_references: list[dict[str, Any]] | None = None,
    spatial_contract: dict[str, Any] | None = None,
    spatial_assessment: HistoricalSpatialAssessment | None = None,
    spatial_audit_metadata: dict[str, Any] | None = None,
    spatial_audit_error: str | None = None,
    spatial_audit_status: str | None = None,
    spatial_review: dict[str, Any] | None = None,
) -> Path:
    finish_mode = _coerce_finish_mode(finish_mode)
    base_metadata = dict(base_metadata or _image_metadata(base_image))
    finished_metadata = dict(finished_metadata or _image_metadata(finished_image))
    generation = {"provider": provider, "model": model, "operation": "edit"}
    if generation_metadata:
        generation.update(generation_metadata)
    record = {
        "finish_record_schema": FINISH_RECORD_SCHEMA,
        "finish_mode": finish_mode.value,
        "recorded_at": utc_now(),
        "authority": {
            "authoritative": False,
            "statement": "This image is a derived presentation layer and is not archaeological evidence.",
        },
        "manifest": manifest,
        "render_receipt": render_receipt,
        "request_path": (
            _relative_path(project, request_path, label="Finish request")
            if request_path is not None
            else None
        ),
        "request_id": request_id,
        "base_image": {
            "path": _relative_path(project, base_image, label="Base image"),
            **base_metadata,
        },
        "reference_images": list(supporting_references or []),
        "finished_image": {
            "path": _relative_path(project, finished_image, label="Finished image"),
            **finished_metadata,
        },
        "source_artifact": source_artifact,
        "normalization": normalization,
        "generation": generation,
        "prompt": prompt,
        "prompt_sha256": sha256_text(prompt),
        "geometry_audit": assessment.model_dump(mode="json") if assessment else None,
        "geometry_audit_invocation": audit_metadata,
        "geometry_audit_status": (
            audit_status or ("complete" if assessment else ("failed" if audit_error else "skipped"))
        ),
        "geometry_audit_reason": audit_reason,
        "geometry_audit_error": audit_error,
        "historical_spatial_contract": spatial_contract,
        "historical_spatial_audit": (
            spatial_assessment.model_dump(mode="json") if spatial_assessment else None
        ),
        "historical_spatial_audit_invocation": spatial_audit_metadata,
        "historical_spatial_audit_status": spatial_audit_status,
        "historical_spatial_audit_error": spatial_audit_error,
        "historical_spatial_review": spatial_review,
        "manual_review": manual_review,
        "manual_review_required": True,
    }
    if finish_mode is FinishMode.precise_object_edit:
        if _assessment_accepts_geometry(assessment):
            record["manual_review_required"] = False
        if manual_review is not None:
            recommendation = manual_review.get("recommendation")
            if recommendation != "accept":
                record["manual_review_required"] = True
            elif assessment is None or _assessment_accepts_geometry(assessment):
                record["manual_review_required"] = False
    elif manual_review is not None:
        record["manual_review_required"] = manual_review.get("recommendation") != "accept"
    if normalization is not None:
        record["manual_review_required"] = True
    record["registration_state"] = (
        "registered_review_required" if record["manual_review_required"] else "accepted"
    )
    record_path = _finish_record_path(finished_image)
    _atomic_write_text(
        record_path,
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return record_path


def audit_geometry(
    project: ProjectPaths,
    *,
    base_image: Path,
    finished_image: Path,
    metadata_out: dict[str, Any] | None = None,
) -> DriftAssessment:
    config = load_config(project)
    client = _client(project)
    response = client.responses.parse(
        model=config.ai.extraction_model,
        reasoning={"effort": config.ai.reasoning_effort},
        input=[
            {
                "role": "system",
                "content": (
                    "You are a strict visual geometry auditor. Compare the original 3D render and the finished "
                    "image. Ignore material, lighting, smoke, dust, vegetation texture, and small people. Penalize "
                    "camera changes, shifted structures, changed silhouettes, added or removed architecture, altered "
                    "wall paths, changed ziggurat stage counts, and changed river or road positions."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Image 1 is the authoritative base. Image 2 is the AI finish.",
                    },
                    {"type": "input_image", "image_url": _data_url(base_image), "detail": "high"},
                    {"type": "input_image", "image_url": _data_url(finished_image), "detail": "high"},
                ],
            },
        ],
        text_format=DriftAssessment,
    )
    if response.output_parsed is None:
        raise RuntimeError("The geometry audit returned no structured result.")
    if metadata_out is not None:
        metadata_out["response_id"] = getattr(response, "id", None)
    return response.output_parsed


def audit_historical_spatial_contract(
    project: ProjectPaths,
    *,
    base_image: Path,
    finished_image: Path,
    spatial_contract: dict[str, Any],
    metadata_out: dict[str, Any] | None = None,
) -> HistoricalSpatialAssessment:
    """Audit protected broad anchors while allowing historical proxy replacement."""
    config = load_config(project)
    client = _client(project)
    response = client.responses.parse(
        model=config.ai.extraction_model,
        reasoning={"effort": config.ai.reasoning_effort},
        input=[
            {
                "role": "system",
                "content": (
                    "You are a strict historical-scene spatial-contract auditor. Image 1 is the bound base "
                    "render and Image 2 is a proposed interpretive finish. Allow proxy meshes, surface materials, "
                    "landscape detail, people, and architectural finish to change. Check only viewpoint/crop, "
                    "presence of protected features, and every required named spatial relationship. A draft or "
                    "needs-review evidence status does not make a protected anchor movable. Mark a constraint "
                    "failed when the candidate regularizes, aligns, duplicates, merges, removes, relocates, or "
                    "visually obscures a relationship that the contract requires to remain legible. Return every "
                    "required constraint id exactly once. Recommend accept only when all required constraints pass."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Audit this hash-bound contract:\n"
                            + json.dumps(spatial_contract, ensure_ascii=False, sort_keys=True)
                        ),
                    },
                    {"type": "input_image", "image_url": _data_url(base_image), "detail": "high"},
                    {"type": "input_image", "image_url": _data_url(finished_image), "detail": "high"},
                ],
            },
        ],
        text_format=HistoricalSpatialAssessment,
    )
    if response.output_parsed is None:
        raise RuntimeError("The historical spatial audit returned no structured result.")
    if metadata_out is not None:
        metadata_out["response_id"] = getattr(response, "id", None)
    return response.output_parsed


def _historical_spatial_assessment_failure(
    spatial_contract: dict[str, Any],
    assessment: HistoricalSpatialAssessment,
) -> str | None:
    required_ids = [
        str(constraint["id"])
        for constraint in spatial_contract.get("constraints", [])
        if constraint.get("required", True)
    ]
    observed_ids = [check.constraint_id for check in assessment.checks]
    if len(observed_ids) != len(set(observed_ids)):
        return "Historical spatial audit returned a required constraint more than once."
    missing = sorted(set(required_ids).difference(observed_ids))
    unexpected = sorted(set(observed_ids).difference(required_ids))
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        return (
            "Historical spatial audit did not cover the exact required constraint set ("
            + "; ".join(details)
            + ")."
        )
    if not assessment.viewpoint_and_crop_preserved:
        return "Historical spatial audit found that the viewpoint or crop changed."
    if not assessment.all_protected_features_present:
        return "Historical spatial audit found one or more protected features missing."
    failed = [check.constraint_id for check in assessment.checks if not check.passed]
    if failed:
        return "Historical spatial audit failed required constraints: " + ", ".join(failed) + "."
    low_confidence = [
        check.constraint_id for check in assessment.checks if check.confidence < _SPATIAL_ACCEPT_CONFIDENCE
    ]
    if low_confidence:
        return (
            "Historical spatial audit confidence is below the publication threshold for: "
            + ", ".join(low_confidence)
            + "."
        )
    if assessment.recommendation != "accept":
        return f"Historical spatial audit recommendation is {assessment.recommendation!r}, not 'accept'."
    return None


def register_finished_render(
    project: ProjectPaths,
    *,
    generated_image: Path,
    request_path: Path | None = None,
    destination: Path | None = None,
    provider: str = CODEX_IMAGE_PROVIDER,
    model: str = CODEX_IMAGE_MODEL,
    audit: bool | None = None,
    overwrite: bool = False,
    normalize_size: bool = False,
    manual_recommendation: str | None = None,
    spatial_recommendation: str | None = None,
    reviewer: str = "",
    review_notes: str = "",
) -> dict[str, Any]:
    """Verify and register an image produced by an interactive generator such as Codex."""
    if not _PROVIDER_PATTERN.fullmatch(provider):
        raise ValueError("Provider must be a lowercase identifier using letters, digits, '.', '_' or '-'.")
    if manual_recommendation not in {None, "accept", "review", "reject"}:
        raise ValueError("Manual recommendation must be accept, review, or reject.")
    if spatial_recommendation not in {None, "accept", "review", "reject"}:
        raise ValueError("Spatial recommendation must be accept, review, or reject.")
    if (manual_recommendation is not None or spatial_recommendation is not None) and not reviewer.strip():
        raise ValueError("A reviewer name is required with a manual or spatial recommendation.")
    generated_image = generated_image.expanduser().resolve()
    generated_metadata = _image_metadata(generated_image)
    if generated_metadata["format"] != "PNG":
        raise ValueError("Interactive finish results must be PNG files.")

    request_path = _resolve_finish_request_path(
        project,
        (request_path or project.exports_dir / "image_finish_request.json"),
        label="Finish request",
    )
    request = _validate_finish_request(project, request_path)
    request_id = str(request["request_id"])
    finish_mode = _request_finish_mode(request)
    if finish_mode is not FinishMode.historical_scene and spatial_recommendation is not None:
        raise ValueError("A spatial recommendation is only valid for historical_scene finishes.")
    if finish_mode is FinishMode.historical_scene and audit is True:
        raise ValueError("Strict geometry audit is not applicable to historical_scene finishes.")
    base = request["base_image"]
    base_image = _resolve_relative_path(project, base["path"], label="Base-image path")
    base_metadata = {field: base[field] for field in ("sha256", "width", "height", "format")}
    requested_output = request["desired_output"]
    dimensions_match = (generated_metadata["width"], generated_metadata["height"]) == (
        requested_output["width"],
        requested_output["height"],
    )
    if not dimensions_match:
        if not normalize_size:
            _assert_compatible_frame(requested_output, generated_metadata)
        _assert_compatible_aspect_ratio(requested_output, generated_metadata)

    requested_destination = _resolve_finish_output_path(
        project,
        _resolve_relative_path(
            project,
            request["desired_output"]["path"],
            label="Desired-output path",
        ),
        label="Desired-output path",
    )
    if destination is None:
        destination = requested_destination
    else:
        destination = _resolve_finish_output_path(
            project,
            destination,
            label="Finished image destination",
        )
    if destination == base_image:
        raise ValueError("Finished image destination cannot overwrite the authoritative base image.")
    existing_artifacts = _existing_finish_artifacts(destination)
    if existing_artifacts and not overwrite:
        paths = ", ".join(str(path) for path in existing_artifacts)
        raise FileExistsError(f"Finished image output set already exists: {paths}")

    config = load_config(project)
    audit_requested = request["geometry_audit_enabled"] if audit is None else audit
    audit_enabled = audit_requested
    audit_metadata: dict[str, Any] = {
        "requested": audit_requested,
        "policy_source": (
            "finish_mode"
            if finish_mode is FinishMode.historical_scene and audit is None
            else ("finish_request" if audit is None else "cli_override")
        ),
        "provider": "openai_responses_api",
        "model": config.ai.extraction_model,
        "reasoning_effort": config.ai.reasoning_effort,
        "response_id": None,
    }
    audit_status: str | None = None
    audit_reason: str | None = None
    if finish_mode is FinishMode.historical_scene:
        audit_status = "skipped"
        audit_reason = _HISTORICAL_AUDIT_SKIP_REASON
        audit_metadata.update(
            {
                "effective": False,
                "status": audit_status,
                "reason": audit_reason,
            }
        )
    normalization: dict[str, Any] | None = None
    source_artifact: dict[str, Any] | None = None
    assessment: DriftAssessment | None = None
    audit_error: str | None = None
    spatial_contract = request.get("spatial_contract")
    spatial_assessment: HistoricalSpatialAssessment | None = None
    spatial_audit_error: str | None = None
    spatial_audit_status: str | None = None
    spatial_audit_metadata: dict[str, Any] | None = None
    if finish_mode is FinishMode.historical_scene:
        spatial_audit_metadata = {
            "required": True,
            "provider": "openai_responses_api",
            "model": config.ai.extraction_model,
            "reasoning_effort": config.ai.reasoning_effort,
            "response_id": None,
        }
    manual_review = None
    if manual_recommendation is not None:
        manual_review = {
            "reviewed_at": utc_now(),
            "reviewer": reviewer.strip(),
            "recommendation": manual_recommendation,
            "scope": (
                "historical_plausibility"
                if finish_mode is FinishMode.historical_scene
                else "geometry_preservation"
            ),
            "notes": review_notes.strip(),
        }
    spatial_review = None
    if spatial_recommendation is not None:
        spatial_review = {
            "reviewed_at": utc_now(),
            "reviewer": reviewer.strip(),
            "recommendation": spatial_recommendation,
            "scope": "historical_spatial_contract",
            "notes": review_notes.strip(),
        }
    staging_path: Path | None = None
    try:
        publication_source = generated_image
        finished_metadata = generated_metadata
        if not dimensions_match:
            source_artifact = {"filename": generated_image.name, **generated_metadata}
            normalization = {
                "operation": "resize_lanczos",
                "from_width": generated_metadata["width"],
                "from_height": generated_metadata["height"],
                "to_width": requested_output["width"],
                "to_height": requested_output["height"],
            }
            staging_path = _temporary_sibling(destination)
            _atomic_resize_png(
                generated_image,
                staging_path,
                (int(requested_output["width"]), int(requested_output["height"])),
            )
            publication_source = staging_path
            finished_metadata = _image_metadata(publication_source)

        if audit_enabled:
            if project_openai_api_key(project, required=False) is None:
                audit_error = "OPENAI_API_KEY is not set; the result was registered but still requires manual geometry review."
            else:
                try:
                    assessment = audit_geometry(
                        project,
                        base_image=base_image,
                        finished_image=publication_source,
                        metadata_out=audit_metadata,
                    )
                except Exception as exc:  # preserve the derived image and a failure record for review
                    audit_error = f"{type(exc).__name__}: {exc}"

        if finish_mode is FinishMode.historical_scene:
            if not isinstance(spatial_contract, dict):
                raise HistoricalSpatialValidationError(
                    "Historical finish has no bound spatial contract; candidate was not published."
                )
            if spatial_recommendation in {"review", "reject"}:
                raise HistoricalSpatialValidationError(
                    f"Named spatial reviewer recommended {spatial_recommendation}; candidate was not published."
                )
            if project_openai_api_key(project, required=False) is None:
                spatial_audit_error = (
                    "OPENAI_API_KEY is not set; automatic protected-anchor audit is unavailable."
                )
            else:
                try:
                    spatial_assessment = audit_historical_spatial_contract(
                        project,
                        base_image=base_image,
                        finished_image=publication_source,
                        spatial_contract=spatial_contract,
                        metadata_out=spatial_audit_metadata,
                    )
                except Exception as exc:
                    spatial_audit_error = f"{type(exc).__name__}: {exc}"

            if spatial_assessment is not None:
                failure = _historical_spatial_assessment_failure(
                    spatial_contract,
                    spatial_assessment,
                )
                if failure is not None:
                    raise HistoricalSpatialValidationError(f"{failure} Candidate was not published.")
                spatial_audit_status = "complete"
            elif spatial_recommendation == "accept":
                spatial_audit_status = "manual_accept"
            else:
                reason = spatial_audit_error or "protected-anchor audit returned no assessment"
                raise HistoricalSpatialValidationError(
                    f"Historical spatial validation is incomplete: {reason} Candidate was not published. "
                    "Run the automatic audit or supply a named --spatial-recommendation accept after reviewing "
                    "every bound constraint."
                )

        refreshed_request = _validate_finish_request(project, request_path)
        if refreshed_request.get("request_id") != request_id:
            raise ValueError("Finish request was replaced while the result was being registered.")
        _assert_image_snapshot(
            publication_source,
            finished_metadata,
            label="Generated finish candidate",
        )

        if destination != publication_source:
            _atomic_copy(publication_source, destination)
        published_metadata = _assert_image_snapshot(
            destination,
            finished_metadata,
            label="Published finish image",
        )

        audit_path = _finish_audit_path(destination)
        if overwrite:
            audit_path.unlink(missing_ok=True)
        written_assessment = spatial_assessment if finish_mode is FinishMode.historical_scene else assessment
        if written_assessment is not None:
            _atomic_write_text(
                audit_path,
                json.dumps(written_assessment.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            )

        record_path = _write_finish_record(
            project,
            base_image=base_image,
            finished_image=destination,
            prompt=request["prompt"],
            provider=provider,
            model=model,
            manifest=request["manifest"],
            request_path=request_path,
            request_id=request_id,
            assessment=assessment,
            finish_mode=finish_mode,
            render_receipt=request.get("render_receipt"),
            base_metadata=base_metadata,
            finished_metadata=published_metadata,
            audit_metadata=audit_metadata,
            audit_error=audit_error,
            audit_status=audit_status,
            audit_reason=audit_reason,
            source_artifact=source_artifact,
            normalization=normalization,
            manual_review=manual_review,
            supporting_references=request.get("reference_images", []),
            spatial_contract=spatial_contract,
            spatial_assessment=spatial_assessment,
            spatial_audit_metadata=spatial_audit_metadata,
            spatial_audit_error=spatial_audit_error,
            spatial_audit_status=spatial_audit_status,
            spatial_review=spatial_review,
        )
        record = _read_json_object(record_path, label="finish provenance record")
        return {
            "base_image": str(base_image),
            "finished_image": str(destination),
            "provenance_record": str(record_path),
            "audit": assessment.model_dump(mode="json") if assessment else None,
            "audit_error": audit_error,
            "historical_spatial_audit": (
                spatial_assessment.model_dump(mode="json") if spatial_assessment else None
            ),
            "historical_spatial_audit_error": spatial_audit_error,
            "historical_spatial_audit_status": spatial_audit_status,
            "manual_review_required": record["manual_review_required"],
        }
    finally:
        if staging_path is not None:
            staging_path.unlink(missing_ok=True)


def finish_render(
    project: ProjectPaths,
    *,
    base_image: Path,
    destination: Path | None = None,
    prompt_path: Path | None = None,
    audit: bool | None = None,
    overwrite: bool = False,
    mode: FinishMode | str | None = None,
) -> dict[str, Any]:
    """Finish a render through the public OpenAI Image API.

    For Codex's built-in, account-included image generation, use
    :func:`prepare_finish_request` and :func:`register_finished_render` instead.
    """
    config = load_config(project)
    finish_mode = _coerce_finish_mode(config.ai.finish_mode if mode is None else mode)
    if finish_mode is FinishMode.historical_scene and audit is True:
        raise ValueError("Strict geometry audit is not applicable to historical_scene finishes.")
    project_openai_api_key(project)

    base_image = base_image.expanduser().resolve()
    base_metadata = _image_metadata(base_image)
    _relative_path(project, base_image, label="Base image")
    manifest = _manifest_binding(project)
    render_receipt = (
        _historical_render_receipt_binding(project, base_image=base_image)
        if finish_mode is FinishMode.historical_scene
        else None
    )
    spatial_contract = (
        _historical_spatial_contract_binding(project) if finish_mode is FinishMode.historical_scene else None
    )

    if destination is None:
        destination = _next_available_finish_path(project.renders_dir / "finished.png")
    destination = _resolve_finish_output_path(
        project,
        destination,
        label="Finished image destination",
    )
    if destination == base_image:
        raise ValueError("Finished image destination cannot overwrite the authoritative base image.")
    existing_artifacts = _existing_finish_artifacts(destination)
    if existing_artifacts and not overwrite:
        paths = ", ".join(str(path) for path in existing_artifacts)
        raise FileExistsError(f"Finished image output set already exists: {paths}")

    raw_prompt = _load_finish_prompt(project, prompt_path, finish_mode)
    prompt = _structured_finish_prompt(
        raw_prompt,
        finish_mode,
        spatial_contract=spatial_contract,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image_size = config.ai.image_size
    if image_size == "auto":
        image_size = f"{base_metadata['width']}x{base_metadata['height']}"
    else:
        _assert_compatible_frame(base_metadata, _size_metadata(image_size))
    _validate_api_image_size(config.ai.image_model, image_size)

    client = _client(project)
    staging_path = _temporary_sibling(destination)
    try:
        with base_image.open("rb") as image_handle:
            response = client.images.edit(
                model=config.ai.image_model,
                image=image_handle,
                prompt=prompt,
                quality=config.ai.image_quality,
                size=image_size,
                output_format="png",
            )
        _save_image_response(response, staging_path)
        finished_metadata = _image_metadata(staging_path)
        if finished_metadata["format"] != "PNG":
            raise ValueError("The Image API returned a non-PNG result for a PNG request.")
        _assert_compatible_frame(base_metadata, finished_metadata)

        audit_requested = (
            False
            if finish_mode is FinishMode.historical_scene
            else (config.ai.geometry_audit_enabled if audit is None else audit)
        )
        audit_enabled = audit_requested
        audit_metadata: dict[str, Any] = {
            "requested": audit_requested,
            "policy_source": (
                "finish_mode"
                if finish_mode is FinishMode.historical_scene and audit is None
                else ("project_config" if audit is None else "cli_override")
            ),
            "provider": "openai_responses_api",
            "model": config.ai.extraction_model,
            "reasoning_effort": config.ai.reasoning_effort,
            "response_id": None,
        }
        audit_status: str | None = None
        audit_reason: str | None = None
        if finish_mode is FinishMode.historical_scene:
            audit_status = "skipped"
            audit_reason = _HISTORICAL_AUDIT_SKIP_REASON
            audit_metadata.update(
                {
                    "effective": False,
                    "status": audit_status,
                    "reason": audit_reason,
                }
            )
        assessment: DriftAssessment | None = None
        audit_error: str | None = None
        if audit_enabled:
            try:
                assessment = audit_geometry(
                    project,
                    base_image=base_image,
                    finished_image=staging_path,
                    metadata_out=audit_metadata,
                )
            except Exception as exc:  # preserve the generated result with an explicit failed-audit record
                audit_error = f"{type(exc).__name__}: {exc}"

        spatial_assessment: HistoricalSpatialAssessment | None = None
        spatial_audit_error: str | None = None
        spatial_audit_status: str | None = None
        spatial_audit_metadata: dict[str, Any] | None = None
        if finish_mode is FinishMode.historical_scene:
            if not isinstance(spatial_contract, dict):
                raise HistoricalSpatialValidationError(
                    "Historical finish has no bound spatial contract; generated result was not published."
                )
            spatial_audit_metadata = {
                "required": True,
                "provider": "openai_responses_api",
                "model": config.ai.extraction_model,
                "reasoning_effort": config.ai.reasoning_effort,
                "response_id": None,
            }
            try:
                spatial_assessment = audit_historical_spatial_contract(
                    project,
                    base_image=base_image,
                    finished_image=staging_path,
                    spatial_contract=spatial_contract,
                    metadata_out=spatial_audit_metadata,
                )
            except Exception as exc:
                spatial_audit_error = f"{type(exc).__name__}: {exc}"
                raise HistoricalSpatialValidationError(
                    f"Historical protected-anchor audit failed before publication: {spatial_audit_error}"
                ) from exc
            failure = _historical_spatial_assessment_failure(
                spatial_contract,
                spatial_assessment,
            )
            if failure is not None:
                raise HistoricalSpatialValidationError(f"{failure} Generated result was not published.")
            spatial_audit_status = "complete"

        _assert_image_snapshot(base_image, base_metadata, label="Authoritative base image")
        current_manifest = _manifest_binding(project)
        if current_manifest["sha256"] != manifest["sha256"]:
            raise ValueError("Scene manifest changed while the Image API finish was running.")
        if finish_mode is FinishMode.historical_scene:
            current_spatial_contract = _historical_spatial_contract_binding(project)
            if current_spatial_contract != spatial_contract:
                raise ValueError(
                    "Historical spatial contract changed while the Image API finish was running."
                )
            current_render_receipt = _historical_render_receipt_binding(
                project,
                base_image=base_image,
            )
            if current_render_receipt != render_receipt:
                raise ValueError(
                    "Historical Blender render receipt changed while the Image API finish was running."
                )
        _assert_image_snapshot(staging_path, finished_metadata, label="Image API result")

        _atomic_copy(staging_path, destination)
        published_metadata = _assert_image_snapshot(
            destination,
            finished_metadata,
            label="Published finish image",
        )

        audit_path = _finish_audit_path(destination)
        if overwrite:
            audit_path.unlink(missing_ok=True)
        written_assessment = spatial_assessment if finish_mode is FinishMode.historical_scene else assessment
        if written_assessment is not None:
            _atomic_write_text(
                audit_path,
                json.dumps(written_assessment.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            )

        record_path = _write_finish_record(
            project,
            base_image=base_image,
            finished_image=destination,
            prompt=prompt,
            provider="openai_api",
            model=config.ai.image_model,
            manifest=manifest,
            request_path=None,
            request_id=None,
            assessment=assessment,
            finish_mode=finish_mode,
            render_receipt=render_receipt,
            base_metadata=base_metadata,
            finished_metadata=published_metadata,
            audit_metadata=audit_metadata,
            audit_error=audit_error,
            audit_status=audit_status,
            audit_reason=audit_reason,
            generation_metadata={
                "request": {
                    "quality": config.ai.image_quality,
                    "size": image_size,
                    "input_fidelity": GPT_IMAGE_2_INPUT_FIDELITY,
                    "output_format": "png",
                },
                "api_response": _api_response_metadata(response),
            },
            spatial_contract=spatial_contract,
            spatial_assessment=spatial_assessment,
            spatial_audit_metadata=spatial_audit_metadata,
            spatial_audit_error=spatial_audit_error,
            spatial_audit_status=spatial_audit_status,
        )
        record = _read_json_object(record_path, label="finish provenance record")
        return {
            "base_image": str(base_image),
            "finished_image": str(destination),
            "provenance_record": str(record_path),
            "audit": assessment.model_dump(mode="json") if assessment else None,
            "audit_error": audit_error,
            "historical_spatial_audit": (
                spatial_assessment.model_dump(mode="json") if spatial_assessment else None
            ),
            "historical_spatial_audit_error": spatial_audit_error,
            "historical_spatial_audit_status": spatial_audit_status,
            "manual_review_required": record["manual_review_required"],
        }
    finally:
        staging_path.unlink(missing_ok=True)
