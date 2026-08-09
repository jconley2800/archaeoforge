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

from .models import DriftAssessment, FinishMode
from .openai_client import new_official_openai_client, project_openai_api_key
from .project import ProjectPaths, load_config
from .util import sha256_file, sha256_text, utc_now

LEGACY_FINISH_REQUEST_SCHEMA = 1
FINISH_REQUEST_SCHEMA = 2
FINISH_RECORD_SCHEMA = 2
CODEX_IMAGE_PROVIDER = "codex_builtin_imagegen"
CODEX_IMAGE_MODEL = "gpt-image-2"
GPT_IMAGE_2_INPUT_FIDELITY = "automatic_high"
_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_GEOMETRY_ACCEPT_SCORE = 0.98

DEFAULT_FINISH_PROMPT = """Preserve the supplied camera, geometry, monument silhouettes, openings, stage counts,
wall alignments, street alignments, river position, relative scale, and object placement exactly. Do not redesign,
add, remove, enlarge, shrink, or relocate architecture. Improve only physically plausible materials, subtle surface
variation, atmospheric perspective, vegetation variation, distant human activity, and lighting. Do not add famous
structures that are absent from the base render. The base render is the authoritative spatial constraint.
"""

DEFAULT_HISTORICAL_SCENE_PROMPT = """Transform the schematic base render into a lifelike, inhabited historical
scene of {place_name} in {target_year_label}. Preserve its viewpoint, composition, broad site layout, named monument
relationships, waterways, roads, and scale hierarchy while replacing simplified forms with coherent, historically
plausible architecture, landscape, atmosphere, vegetation, and human activity. Make all interpretive detail
conservative and avoid anachronistic, fantasy, modern, or unsupported famous structures.
"""

_FINISH_CONSTRAINTS = (
    "Change only surface treatment, atmosphere, lighting, vegetation variation, and distant low-detail activity.",
    "Preserve the camera, crop, perspective, geometry, silhouettes, openings, stage counts, wall and road paths, "
    "waterways, relative scale, and every structure's placement.",
    "Do not add, remove, relocate, enlarge, shrink, or redesign architecture.",
    "Do not add text, labels, borders, logos, or watermarks.",
    "Treat the result as a non-authoritative presentation layer, never as archaeological evidence.",
)

_HISTORICAL_SCENE_CONSTRAINTS = (
    "Treat the base render as a spatial and compositional guide, not as exact finished geometry.",
    "Preserve the viewpoint, crop, broad site layout, relative positions of named monuments, waterways and roads, "
    "and the scene's scale hierarchy.",
    "You may replace schematic forms with coherent lifelike architecture and extend the surrounding environment "
    "with historically appropriate detail requested by the primary prompt.",
    "Do not add anachronistic, fantasy, modern, or unsupported famous structures.",
    "Do not add text, labels, borders, logos, or watermarks.",
    "Treat the result as an interpretive, non-authoritative presentation layer, never as archaeological evidence.",
)

_HISTORICAL_AUDIT_SKIP_REASON = (
    "Strict geometry audit is not applicable to historical_scene finishes because that mode permits "
    "interpretive geometry changes; a named historical-plausibility review is required for acceptance."
)


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
    if schema == FINISH_REQUEST_SCHEMA:
        if "finish_mode" not in request:
            raise ValueError("Finish request schema 2 must explicitly carry finish_mode.")
        return _coerce_finish_mode(request["finish_mode"])
    raise ValueError(f"Unsupported finish request schema: {schema!r}")


def _structured_finish_prompt(prompt: str, mode: FinishMode | str = FinishMode.precise_object_edit) -> str:
    finish_mode = _coerce_finish_mode(mode)
    if finish_mode is FinishMode.historical_scene:
        constraints = "\n".join(f"- {item}" for item in _HISTORICAL_SCENE_CONSTRAINTS)
        return (
            "Use case: historical-scene\n"
            "Asset type: evidence-controlled interpretive archaeological scene visualization\n"
            f"Primary request: {prompt.strip()}\n"
            "Input images: Image 1 is the base render and spatial/compositional guide.\n"
            "Constraints:\n"
            f"{constraints}\n"
            "Avoid: anachronistic or fantasy architecture, changed viewpoint or crop, text, and watermark"
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
) -> str:
    finish_mode = _coerce_finish_mode(mode)
    if finish_mode is FinishMode.historical_scene:
        return (
            f"Use $imagegen to transform the local base image at {base_path} into a lifelike historical scene. "
            f"Follow the historical_scene prompt in {request_path} exactly, treating the base as a spatial and "
            "compositional guide while preserving its viewpoint and broad named site relationships. Keep the "
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
    existing_finish_artifacts = _existing_finish_artifacts(destination)
    if existing_finish_artifacts:
        paths = ", ".join(str(path) for path in existing_finish_artifacts)
        raise FileExistsError(f"Finished image output set already exists: {paths}")

    raw_prompt = _load_finish_prompt(project, prompt_path, finish_mode)
    prompt = _structured_finish_prompt(raw_prompt, finish_mode)
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
        "base_image": {"path": base_relative, **base_metadata},
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
    finish_mode = _request_finish_mode(request)
    if request.get("status") != "ready_for_interactive_generation":
        raise ValueError(f"Finish request is not ready for generation: {request.get('status')!r}")
    if not isinstance(request.get("geometry_audit_enabled"), bool):
        raise ValueError("Finish request geometry-audit policy must be true or false.")
    if request.get("request_id") != _finish_request_id(request):
        raise ValueError("Finish request content was changed after it was prepared.")
    if finish_mode is FinishMode.historical_scene and request["geometry_audit_enabled"]:
        raise ValueError("Historical-scene finish requests must disable strict geometry audit.")
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or sha256_text(prompt) != request.get("prompt_sha256"):
        raise ValueError("Finish request prompt is missing or its checksum does not match.")

    manifest = request.get("manifest")
    base = request.get("base_image")
    output = request.get("desired_output")
    request_project = request.get("project")
    if not all(isinstance(value, dict) for value in (manifest, base, output)):
        raise ValueError("Finish request is missing manifest, base-image, or output metadata.")
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

    base_path = _resolve_relative_path(project, str(base.get("path", "")), label="Base-image path")
    current_base = _image_metadata(base_path)
    if current_base["sha256"] != base.get("sha256"):
        raise ValueError("Authoritative base image changed after the finish request was prepared.")
    for field in ("width", "height", "format"):
        if current_base[field] != base.get(field):
            raise ValueError(
                "Authoritative base image metadata changed after the finish request was prepared."
            )

    output_path = _resolve_relative_path(project, str(output.get("path", "")), label="Desired-output path")
    _resolve_finish_output_path(project, output_path, label="Desired-output path")
    if output_path == base_path:
        raise ValueError("Finished image destination cannot overwrite the authoritative base image.")
    if output.get("format") != "PNG":
        raise ValueError("Finish request output format must be PNG.")
    if (output.get("width"), output.get("height")) != (base.get("width"), base.get("height")):
        raise ValueError("Finish request output dimensions must match the authoritative base image.")
    expected_instruction = _suggested_codex_prompt(
        str(base.get("path", "")),
        _relative_path(project, request_path, label="Finish request"),
        str(output.get("path", "")),
        finish_mode,
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
    reviewer: str = "",
    review_notes: str = "",
) -> dict[str, Any]:
    """Verify and register an image produced by an interactive generator such as Codex."""
    if not _PROVIDER_PATTERN.fullmatch(provider):
        raise ValueError("Provider must be a lowercase identifier using letters, digits, '.', '_' or '-'.")
    if manual_recommendation not in {None, "accept", "review", "reject"}:
        raise ValueError("Manual recommendation must be accept, review, or reject.")
    if manual_recommendation is not None and not reviewer.strip():
        raise ValueError("A reviewer name is required with a manual recommendation.")
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
        if assessment is not None:
            _atomic_write_text(
                audit_path,
                json.dumps(assessment.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
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
            base_metadata=base_metadata,
            finished_metadata=published_metadata,
            audit_metadata=audit_metadata,
            audit_error=audit_error,
            audit_status=audit_status,
            audit_reason=audit_reason,
            source_artifact=source_artifact,
            normalization=normalization,
            manual_review=manual_review,
        )
        record = _read_json_object(record_path, label="finish provenance record")
        return {
            "base_image": str(base_image),
            "finished_image": str(destination),
            "provenance_record": str(record_path),
            "audit": assessment.model_dump(mode="json") if assessment else None,
            "audit_error": audit_error,
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
    prompt = _structured_finish_prompt(raw_prompt, finish_mode)
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

        _assert_image_snapshot(base_image, base_metadata, label="Authoritative base image")
        current_manifest = _manifest_binding(project)
        if current_manifest["sha256"] != manifest["sha256"]:
            raise ValueError("Scene manifest changed while the Image API finish was running.")
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
        if assessment is not None:
            _atomic_write_text(
                audit_path,
                json.dumps(assessment.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
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
        )
        record = _read_json_object(record_path, label="finish provenance record")
        return {
            "base_image": str(base_image),
            "finished_image": str(destination),
            "provenance_record": str(record_path),
            "audit": assessment.model_dump(mode="json") if assessment else None,
            "audit_error": audit_error,
            "manual_review_required": record["manual_review_required"],
        }
    finally:
        staging_path.unlink(missing_ok=True)
