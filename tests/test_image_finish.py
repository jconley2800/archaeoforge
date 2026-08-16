from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError
from typer.main import get_command
from typer.testing import CliRunner

from archaeoforge.cli import _run_image_finish_stage, app
from archaeoforge.compile_scene import compile_scene
from archaeoforge.config import write_config
from archaeoforge.image_finish import (
    HistoricalSpatialValidationError,
    _finish_request_id,
    finish_render,
    prepare_finish_request,
    register_finished_render,
)
from archaeoforge.models import (
    AIConfig,
    DriftAssessment,
    FinishMode,
    HistoricalSpatialAssessment,
    HistoricalSpatialCheck,
)
from archaeoforge.openai_client import OFFICIAL_OPENAI_BASE_URL, new_official_openai_client
from archaeoforge.project import load_config
from archaeoforge.template_semantics import template_recognizability
from archaeoforge.util import sha256_file

_API_IMAGE_SIZE = (1024, 640)
_TEST_SPATIAL_CONSTRAINT_ID = "WALL-1-RELATIVE-LAYOUT"


def _png(path: Path, size: tuple[int, int] = (64, 32), color: str = "#806040") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _write_render_receipt(project, base: Path) -> None:
    manifest = json.loads(project.scene_manifest.read_text(encoding="utf-8"))
    with Image.open(base) as image:
        beauty = {
            "path": base.relative_to(project.root).as_posix(),
            "sha256": sha256_file(base),
            "width": image.width,
            "height": image.height,
            "format": image.format,
        }
    receipt = {
        "render_receipt_schema": 1,
        "command": ["test-blender"],
        "log": "test",
        "blend_file": "test.blend",
        "rendered": True,
        "manifest": {
            "path": project.scene_manifest.relative_to(project.root).as_posix(),
            "sha256": sha256_file(project.scene_manifest),
            "input_fingerprint": manifest["input_fingerprint"],
        },
        "feature_templates": {
            feature["id"]: {
                "template": feature["template"],
                "recognizability": template_recognizability(feature["template"]),
            }
            for feature in manifest["features"]
        },
        "beauty_image": beauty,
    }
    project.blender_result.write_text(json.dumps(receipt), encoding="utf-8")


def _project_with_render(project_factory):
    project = project_factory()
    compile_scene(project, preview=True)
    base = _png(project.renders_dir / "beauty.png")
    _write_render_receipt(project, base)
    return project, base


def _project_with_api_render(project_factory):
    project = project_factory()
    compile_scene(project, preview=True)
    base = _png(project.renders_dir / "beauty.png", size=_API_IMAGE_SIZE)
    _write_render_receipt(project, base)
    return project, base


def _accepting_spatial_assessment() -> HistoricalSpatialAssessment:
    return HistoricalSpatialAssessment(
        viewpoint_and_crop_preserved=True,
        all_protected_features_present=True,
        checks=[
            HistoricalSpatialCheck(
                constraint_id=_TEST_SPATIAL_CONSTRAINT_ID,
                passed=True,
                confidence=0.99,
                observation="WALL-1 remains at the bound relative placement.",
                detected_changes=[],
            )
        ],
        detected_changes=[],
        recommendation="accept",
    )


def test_project_dotenv_cannot_redirect_openai_credentials(project_factory, monkeypatch):
    import openai

    project = project_factory()
    (project.root / ".env").write_text(
        "OPENAI_API_KEY=project-test-key\nOPENAI_BASE_URL=https://attacker.invalid/v1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    captured = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: captured.update(kwargs) or object())

    new_official_openai_client(project)

    assert captured == {
        "api_key": "project-test-key",
        "base_url": OFFICIAL_OPENAI_BASE_URL,
    }


def test_api_finish_model_is_restricted_to_supported_gpt_image_2_variants():
    with pytest.raises(ValidationError, match="gpt-image-2"):
        AIConfig(image_model="dall-e-2")

    with pytest.raises(ValidationError, match="image_input_fidelity"):
        AIConfig(image_input_fidelity="low")


def test_finish_mode_defaults_to_precise_and_rejects_unknown_values():
    assert AIConfig().finish_mode is FinishMode.precise_object_edit
    with pytest.raises(ValidationError, match="finish_mode"):
        AIConfig(finish_mode="cinematic_fantasy")


def test_prepare_finish_request_is_portable_hash_bound_and_stable(project_factory):
    project, base = _project_with_render(project_factory)

    first_path = prepare_finish_request(project, base_image=base)
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second_path = prepare_finish_request(project, base_image=base)
    second = json.loads(second_path.read_text(encoding="utf-8"))

    assert first["request_id"] == second["request_id"]
    assert first["base_image"] == {
        "path": "outputs/renders/beauty.png",
        "sha256": sha256_file(base),
        "width": 64,
        "height": 32,
        "format": "PNG",
    }
    assert first["desired_output"]["width"] == 64
    assert first["desired_output"]["height"] == 32
    assert first["generation"]["size"] == "64x32"
    assert first["generation"]["input_fidelity"] == "automatic_high"
    assert first["finish_request_schema"] == 4
    assert first["finish_mode"] == "precise_object_edit"
    assert first["manifest"]["input_fingerprint"]
    assert first["manifest"]["sha256"] == sha256_file(project.scene_manifest)
    assert str(project.root) not in json.dumps(first)
    assert "authoritative" in first["prompt"].lower()
    assert "precise-object-edit" in first["prompt"]
    assert "preserve its camera and geometry" in first["suggested_codex_prompt"]


def test_historical_scene_hash_binds_and_frontloads_needs_review_spatial_contract(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = project.exports_dir / "mode-bound-request.json"
    (project.prompts_dir / "finish.txt").write_text(
        "Preserve every geometric edge exactly.",
        encoding="utf-8",
    )

    prepare_finish_request(
        project,
        base_image=base,
        request_path=request_path,
        mode=FinishMode.precise_object_edit,
    )
    precise = json.loads(request_path.read_text(encoding="utf-8"))
    prepare_finish_request(
        project,
        base_image=base,
        request_path=request_path,
        overwrite_request=True,
        mode=FinishMode.historical_scene,
    )
    historical = json.loads(request_path.read_text(encoding="utf-8"))

    contract = historical["spatial_contract"]
    protected = contract["protected_features"]
    assert historical["finish_request_schema"] == 4
    assert historical["finish_mode"] == "historical_scene"
    assert historical["geometry_audit_enabled"] is False
    assert historical["render_receipt"]["sha256"] == sha256_file(project.blender_result)
    assert historical["render_receipt"]["beauty_image"]["sha256"] == sha256_file(base)
    assert contract["path"] == "data/historical_scene_spatial_contract.json"
    assert contract["sha256"] == sha256_file(project.root / "data" / "historical_scene_spatial_contract.json")
    assert contract["constraints"][0]["id"] == _TEST_SPATIAL_CONSTRAINT_ID
    assert contract["constraints"][0]["kind"] == "relative_layout"
    assert contract["constraints"][0]["feature_ids"] == ["WALL-1"]
    assert protected[0]["id"] == "WALL-1"
    assert protected[0]["review_status"] == "needs_review"
    assert protected[0]["geometry"] == {"type": "LineString", "coordinates": [[0, 0], [10, 0]]}
    assert historical["request_id"] != precise["request_id"]
    assert "Use case: historical-scene" in historical["prompt"]
    assert historical["prompt"].index("NON-NEGOTIABLE SPATIAL CONTRACT") < historical["prompt"].index(
        "Primary request:"
    )
    assert "WALL-1-RELATIVE-LAYOUT" in historical["prompt"]
    assert "evidence_status=needs_review" in historical["prompt"]
    assert "Proxy form and materials may change" in historical["prompt"]
    assert "lifelike, inhabited historical" in historical["prompt"]
    assert "Test Place" in historical["prompt"]
    assert "test period" in historical["prompt"]
    assert "Preserve every geometric edge exactly" not in historical["prompt"]
    assert "Do not add, remove, relocate" not in historical["prompt"]
    assert "lifelike historical scene" in historical["suggested_codex_prompt"]
    assert "NON-NEGOTIABLE SPATIAL CONTRACT" in historical["suggested_codex_prompt"]


def test_historical_scene_requires_a_configured_spatial_contract(project_factory):
    project, base = _project_with_render(project_factory)
    config = load_config(project)
    config.ai.historical_scene_spatial_contract = None
    write_config(config, project.config)

    with pytest.raises(ValueError, match="requires ai.historical_scene_spatial_contract"):
        prepare_finish_request(
            project,
            base_image=base,
            mode=FinishMode.historical_scene,
        )


def test_historical_scene_rejects_a_generated_candidate_as_the_spatial_base(project_factory):
    project, _ = _project_with_render(project_factory)
    candidate = _png(project.outputs_dir / "candidates" / "earlier-finish.png")

    with pytest.raises(ValueError, match="must use the current outputs/renders/beauty.png"):
        prepare_finish_request(project, base_image=candidate, mode=FinishMode.historical_scene)


def test_historical_scene_rejects_a_stale_manifest_render_receipt(project_factory):
    project, base = _project_with_render(project_factory)
    manifest = json.loads(project.scene_manifest.read_text(encoding="utf-8"))
    manifest["input_fingerprint"] = "recompiled-after-render"
    project.scene_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="receipt was produced from a different manifest"):
        prepare_finish_request(project, base_image=base, mode=FinishMode.historical_scene)


def test_historical_scene_rejects_beauty_changed_after_render(project_factory):
    project, base = _project_with_render(project_factory)
    _png(base, color="#102030")

    with pytest.raises(ValueError, match="beauty image changed after the recorded render"):
        prepare_finish_request(project, base_image=base, mode=FinishMode.historical_scene)


def test_historical_scene_rejects_a_protected_generic_landmark_base(project_factory):
    project, base = _project_with_render(project_factory)
    contract_path = project.root / "data" / "historical_scene_spatial_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["base_render_requirements"] = [
        {
            "id": "WALL-SEMANTIC-BASE",
            "feature_ids": ["WALL-1"],
            "minimum_recognizability": "identity_specific",
            "requirement": "The protected landmark must have an identity-specific native mesh.",
        }
    ]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match="base render is not semantically ready.*WALL-1.*type_specific"):
        prepare_finish_request(project, base_image=base, mode=FinishMode.historical_scene)


def test_historical_scene_binds_a_satisfied_base_render_requirement(project_factory):
    project, base = _project_with_render(project_factory)
    contract_path = project.root / "data" / "historical_scene_spatial_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["base_render_requirements"] = [
        {
            "id": "WALL-TYPE-BASE",
            "feature_ids": ["WALL-1"],
            "minimum_recognizability": "type_specific",
            "requirement": "The protected wall must retain a recognizable linear wall form.",
        }
    ]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    request_path = prepare_finish_request(project, base_image=base, mode=FinishMode.historical_scene)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["spatial_contract"]["base_render_requirements"] == contract["base_render_requirements"]
    assert "Protected semantic base-render requirements already satisfied" in request["prompt"]
    assert "WALL-TYPE-BASE" in request["prompt"]


def test_historical_request_hash_binds_supporting_reference_images(project_factory, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    reference = _png(project.root / "assets" / "stair-plan.png", size=(32, 32), color="#203050")

    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
        reference_images=[reference],
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))

    assert request["reference_images"] == [
        {
            "image_index": 2,
            "role": "supporting_reference",
            "path": "assets/stair-plan.png",
            "sha256": sha256_file(reference),
            "width": 32,
            "height": 32,
            "format": "PNG",
        }
    ]
    assert "Image 2 is hash-bound" in request["prompt"]
    assert "Image 2: assets/stair-plan.png" in request["suggested_codex_prompt"]
    assert "do not use unbound iterative images" in request["suggested_codex_prompt"]

    candidate = _png(project.renders_dir / "candidate.png")
    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        spatial_recommendation="accept",
        reviewer="Spatial reviewer",
    )
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    assert record["reference_images"] == request["reference_images"]
    assert record["historical_spatial_review"]["recommendation"] == "accept"


def test_register_rejects_a_changed_supporting_reference(project_factory):
    project, base = _project_with_render(project_factory)
    reference = _png(project.root / "assets" / "stair-plan.png", size=(32, 32), color="#203050")
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
        reference_images=[reference],
    )
    _png(reference, size=(32, 32), color="#000000")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="Supporting reference image changed"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
        )


def test_precise_finish_rejects_supporting_reference_images(project_factory):
    project, base = _project_with_render(project_factory)
    reference = _png(project.root / "assets" / "stair-plan.png", size=(32, 32))

    with pytest.raises(ValueError, match="only for historical_scene"):
        prepare_finish_request(
            project,
            base_image=base,
            mode=FinishMode.precise_object_edit,
            reference_images=[reference],
        )


def test_babylon_historical_prompt_overrides_schematic_gate_placement():
    prompt_path = (
        Path(__file__).parents[1]
        / "projects"
        / "babylon_570_bce"
        / "prompts"
        / "finish_historical_scene.txt"
    )
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Class C, needs-review placeholders" in prompt
    assert "roadway must enter the northern gatehouse" in prompt
    assert "roughly 800-850 metres south-southwest" in prompt
    assert "They are not adjacent" in prompt
    assert "Exactly one visibly blue monumental portal complex" in prompt
    assert "all three unobstructed lowest-stage approach flights in a T-shaped plan" in prompt
    assert "one broad central staircase projects perpendicular to the exact midpoint" in prompt
    assert "forming the T's straight crossbar" in prompt
    assert "not become two diagonal fan arms" in prompt
    assert "no duplicate blue gate" in prompt.lower()
    assert "Preserve its broad relationships" not in prompt


def test_prepare_finish_versions_around_existing_output_sidecars(project_factory):
    project, base = _project_with_render(project_factory)
    (project.renders_dir / "finished.provenance.json").write_text("{}", encoding="utf-8")

    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))

    assert request["desired_output"]["path"] == "outputs/renders/finished-v2.png"


def test_prepare_finish_refuses_unsafe_request_paths_and_non_request_replacement(project_factory):
    project, base = _project_with_render(project_factory)
    config_before = project.config.read_bytes()
    manifest_before = project.scene_manifest.read_bytes()

    with pytest.raises(ValueError, match="must be inside"):
        prepare_finish_request(project, base_image=base, request_path=project.config)
    with pytest.raises(ValueError, match="not an ArchaeoForge finish request"):
        prepare_finish_request(
            project,
            base_image=base,
            request_path=project.scene_manifest,
            overwrite_request=True,
        )

    assert project.config.read_bytes() == config_before
    assert project.scene_manifest.read_bytes() == manifest_before


def test_prepare_finish_replaces_only_an_explicit_valid_request(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)

    with pytest.raises(FileExistsError, match="Finish request already exists"):
        prepare_finish_request(project, base_image=base, request_path=request_path)

    replaced = prepare_finish_request(
        project,
        base_image=base,
        request_path=request_path,
        overwrite_request=True,
    )
    assert replaced == request_path


def test_register_finish_writes_verified_image_and_provenance_without_api_key(
    project_factory,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png", color="#705840")

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        audit=False,
    )

    destination = Path(result["finished_image"])
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert destination.read_bytes() == candidate.read_bytes()
    assert record["request_id"] == request["request_id"]
    assert record["base_image"]["sha256"] == sha256_file(base)
    assert record["finished_image"]["sha256"] == sha256_file(destination)
    assert record["generation"] == {
        "provider": "codex_builtin_imagegen",
        "model": "gpt-image-2",
        "operation": "edit",
    }
    assert record["finish_mode"] == "precise_object_edit"
    assert record["finish_record_schema"] == 3
    assert record["authority"]["authoritative"] is False
    assert record["geometry_audit_status"] == "skipped"
    assert record["manual_review_required"] is True


def test_register_accepts_legacy_precise_request_without_a_mode(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["finish_request_schema"] = 1
    request.pop("finish_mode")
    request["request_id"] = _finish_request_id(request)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        audit=False,
    )
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))

    assert record["finish_mode"] == "precise_object_edit"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda request: request.pop("finish_mode"), "schema 4 must explicitly carry finish_mode"),
        (
            lambda request: request.__setitem__("finish_mode", "cinematic_fantasy"),
            "Unsupported finish mode",
        ),
    ],
)
def test_current_schema_requires_an_explicit_valid_finish_mode(
    project_factory,
    mutation,
    message,
):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    mutation(request)
    request["request_id"] = _finish_request_id(request)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match=message):
        register_finished_render(project, generated_image=candidate, request_path=request_path)


def test_schema_1_rejects_a_finish_mode_even_with_a_recomputed_request_id(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["finish_request_schema"] = 1
    request["request_id"] = _finish_request_id(request)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="schema 1 must not carry finish_mode"):
        register_finished_render(project, generated_image=candidate, request_path=request_path)


def test_schema_3_historical_request_rejects_enabled_strict_audit_policy(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["geometry_audit_enabled"] = True
    request["request_id"] = _finish_request_id(request)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="must disable strict geometry audit"):
        register_finished_render(project, generated_image=candidate, request_path=request_path)


def test_historical_scene_binds_no_strict_audit_and_named_accepts_can_clear_review(
    project_factory,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_geometry",
        lambda *_args, **_kwargs: pytest.fail("Historical scene mode must skip strict geometry audit."),
    )
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_historical_spatial_contract",
        lambda *_args, **_kwargs: _accepting_spatial_assessment(),
    )

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        manual_recommendation="accept",
        spatial_recommendation="accept",
        reviewer="Test reviewer",
    )
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))

    assert record["finish_mode"] == "historical_scene"
    assert record["geometry_audit_status"] == "skipped"
    assert "not applicable to historical_scene" in record["geometry_audit_reason"]
    assert record["geometry_audit_invocation"]["requested"] is False
    assert record["geometry_audit_invocation"]["effective"] is False
    assert record["geometry_audit_invocation"]["status"] == "skipped"
    assert record["geometry_audit_invocation"]["policy_source"] == "finish_mode"
    assert record["manual_review"]["recommendation"] == "accept"
    assert record["manual_review"]["scope"] == "historical_plausibility"
    assert record["historical_spatial_review"]["recommendation"] == "accept"
    assert record["historical_spatial_audit_status"] == "manual_accept"
    assert "manual_geometry_review" not in record
    assert result["manual_review_required"] is False


def test_historical_scene_rejects_an_explicit_strict_geometry_audit(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="not applicable to historical_scene"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            audit=True,
        )

    assert not (project.renders_dir / "finished.png").exists()


@pytest.mark.parametrize("recommendation", ["review", "reject"])
def test_historical_spatial_review_or_reject_blocks_publication(
    project_factory,
    recommendation,
):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")

    destination = project.renders_dir / "finished.png"

    with pytest.raises(HistoricalSpatialValidationError, match=f"recommended {recommendation}"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            spatial_recommendation=recommendation,
            reviewer="Test reviewer",
        )

    assert not destination.exists()
    assert not destination.with_suffix(".provenance.json").exists()


def test_historical_scene_missing_api_key_without_named_spatial_accept_blocks_publication(
    project_factory,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")
    destination = project.renders_dir / "finished.png"

    with pytest.raises(HistoricalSpatialValidationError, match="validation is incomplete"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
        )

    assert not destination.exists()
    assert not destination.with_suffix(".provenance.json").exists()


@pytest.mark.parametrize(
    ("assessment", "message"),
    [
        (
            HistoricalSpatialAssessment(
                viewpoint_and_crop_preserved=True,
                all_protected_features_present=True,
                checks=[
                    HistoricalSpatialCheck(
                        constraint_id=_TEST_SPATIAL_CONSTRAINT_ID,
                        passed=False,
                        confidence=0.99,
                        observation="WALL-1 moved.",
                        detected_changes=["placement changed"],
                    )
                ],
                detected_changes=["placement changed"],
                recommendation="reject",
            ),
            "failed required constraints",
        ),
        (
            HistoricalSpatialAssessment(
                viewpoint_and_crop_preserved=True,
                all_protected_features_present=True,
                checks=[],
                detected_changes=[],
                recommendation="accept",
            ),
            "did not cover the exact required constraint set",
        ),
    ],
)
def test_failed_or_incomplete_historical_spatial_audit_blocks_before_publication(
    project_factory,
    monkeypatch,
    assessment,
    message,
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")
    destination = project.renders_dir / "finished.png"
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_historical_spatial_contract",
        lambda *_args, **_kwargs: assessment,
    )

    with pytest.raises(HistoricalSpatialValidationError, match=message):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
        )

    assert not destination.exists()
    assert not destination.with_suffix(".provenance.json").exists()


def test_passing_historical_spatial_audit_publishes_and_records_contract(
    project_factory,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_historical_spatial_contract",
        lambda *_args, **_kwargs: _accepting_spatial_assessment(),
    )

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
    )
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    request = json.loads(request_path.read_text(encoding="utf-8"))

    assert Path(result["finished_image"]).exists()
    assert record["render_receipt"] == request["render_receipt"]
    assert record["render_receipt"]["sha256"] == sha256_file(project.blender_result)
    assert record["historical_spatial_contract"]["constraints"][0]["id"] == (_TEST_SPATIAL_CONSTRAINT_ID)
    assert record["historical_spatial_audit"]["recommendation"] == "accept"
    assert record["historical_spatial_audit_status"] == "complete"
    assert result["historical_spatial_audit"]["checks"][0]["passed"] is True


def test_register_finish_records_missing_audit_key_instead_of_orphaning_image(
    project_factory,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")

    result = register_finished_render(project, generated_image=candidate, request_path=request_path)

    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    assert Path(result["finished_image"]).exists()
    assert result["audit_error"]
    assert record["geometry_audit_status"] == "failed"
    assert record["manual_review_required"] is True


def test_register_finish_rejects_a_changed_base_render(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")
    _png(base, color="#000000")

    with pytest.raises(ValueError, match="base image changed"):
        register_finished_render(project, generated_image=candidate, request_path=request_path, audit=False)


def test_register_finish_rejects_a_tampered_request(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["prompt"] += " Add a palace."
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="changed after it was prepared"):
        register_finished_render(project, generated_image=candidate, request_path=request_path, audit=False)


def test_register_finish_rejects_a_tampered_bound_spatial_contract(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["spatial_contract"]["constraints"][0]["requirement"] = "Allow the wall to move."
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="changed after it was prepared"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            spatial_recommendation="accept",
            reviewer="Spatial reviewer",
        )

    assert not (project.renders_dir / "finished.png").exists()


def test_register_finish_rejects_a_stale_spatial_contract(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    contract_path = project.root / "data" / "historical_scene_spatial_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["notes"] = "Changed after request preparation."
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="spatial contract.*changed after"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            spatial_recommendation="accept",
            reviewer="Spatial reviewer",
        )

    assert not (project.renders_dir / "finished.png").exists()


def test_register_finish_rejects_a_tampered_finish_mode(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["finish_mode"] = "historical_scene"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="changed after it was prepared"):
        register_finished_render(project, generated_image=candidate, request_path=request_path, audit=False)


def test_register_finish_rejects_a_changed_handoff_instruction(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["suggested_codex_prompt"] = "Copy the candidate directly over the final output."
    request_path.write_text(json.dumps(request), encoding="utf-8")
    candidate = _png(project.renders_dir / "candidate.png")

    with pytest.raises(ValueError, match="handoff instruction was changed"):
        register_finished_render(project, generated_image=candidate, request_path=request_path, audit=False)


@pytest.mark.parametrize(
    ("candidate_factory", "message"),
    [
        (lambda path: path.write_text("not an image", encoding="utf-8") or path, "valid supported image"),
        (lambda path: _png(path, size=(32, 32)), "dimensions differ"),
    ],
)
def test_register_finish_rejects_invalid_or_reframed_output(
    project_factory,
    candidate_factory,
    message,
):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = project.renders_dir / "candidate.png"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    result = candidate_factory(candidate)
    candidate = result if isinstance(result, Path) else candidate

    with pytest.raises(ValueError, match=message):
        register_finished_render(project, generated_image=candidate, request_path=request_path, audit=False)


def test_register_finish_refuses_existing_destination_and_base_overwrite(project_factory):
    project, base = _project_with_render(project_factory)
    destination = project.renders_dir / "chosen.png"
    request_path = prepare_finish_request(project, base_image=base, destination=destination)
    candidate = _png(project.renders_dir / "candidate.png")
    _png(destination, color="#112233")

    with pytest.raises(FileExistsError, match="already exists"):
        register_finished_render(project, generated_image=candidate, request_path=request_path, audit=False)

    request_path.unlink()
    with pytest.raises(ValueError, match="cannot overwrite"):
        prepare_finish_request(project, base_image=base, destination=base)


def test_register_finish_can_normalize_same_aspect_output_and_record_manual_rejection(
    project_factory,
):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png", size=(128, 64))

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        audit=False,
        normalize_size=True,
        manual_recommendation="reject",
        reviewer="Geometry reviewer",
        review_notes="The generated camera moved.",
    )

    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    with Image.open(result["finished_image"]) as image:
        assert image.size == (64, 32)
    assert record["source_artifact"]["width"] == 128
    assert record["normalization"]["operation"] == "resize_lanczos"
    assert record["manual_review"]["recommendation"] == "reject"
    assert record["manual_review"]["scope"] == "geometry_preservation"
    assert record["manual_review_required"] is True


def test_normalized_finish_remains_review_required_even_after_manual_accept(
    project_factory,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png", size=(128, 64))

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        normalize_size=True,
        manual_recommendation="accept",
        spatial_recommendation="accept",
        reviewer="Historical reviewer",
    )

    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    assert record["manual_review"]["scope"] == "historical_plausibility"
    assert result["manual_review_required"] is True


def test_register_finish_treats_image_and_sidecars_as_one_output_set(project_factory):
    project, base = _project_with_render(project_factory)
    destination = project.renders_dir / "chosen.png"
    request_path = prepare_finish_request(project, base_image=base, destination=destination)
    candidate = _png(project.renders_dir / "candidate.png")
    _png(destination)
    provenance = destination.with_suffix(".provenance.json")
    audit_path = destination.with_suffix(".audit.json")
    provenance.write_text('{"old": true}', encoding="utf-8")
    audit_path.write_text('{"old": true}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="output set already exists"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            audit=False,
        )

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        audit=False,
        overwrite=True,
    )
    assert not audit_path.exists()
    assert json.loads(provenance.read_text(encoding="utf-8"))["request_id"]
    assert result["manual_review_required"] is True


def test_register_finish_cannot_publish_over_project_configuration(project_factory):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")
    config_before = project.config.read_bytes()

    with pytest.raises(ValueError, match="must be inside"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            destination=project.config,
            audit=False,
            overwrite=True,
        )

    assert project.config.read_bytes() == config_before


def test_contradictory_audit_acceptance_cannot_clear_manual_review(
    project_factory,
    monkeypatch,
):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_geometry",
        lambda *_args, **_kwargs: DriftAssessment(
            geometry_preservation_score=0.2,
            camera_preserved=False,
            major_silhouettes_preserved=False,
            object_placement_preserved=False,
            detected_changes=["camera moved"],
            recommendation="accept",
        ),
    )

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
        audit=True,
    )

    assert result["manual_review_required"] is True


def test_register_uses_the_audit_policy_bound_into_the_request(project_factory, monkeypatch):
    project, base = _project_with_render(project_factory)
    config = load_config(project)
    config.ai.geometry_audit_enabled = False
    write_config(config, project.config)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")

    config.ai.geometry_audit_enabled = True
    write_config(config, project.config)
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_geometry",
        lambda *_args, **_kwargs: pytest.fail("The bound request disabled geometry audit."),
    )

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
    )
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))

    assert record["geometry_audit_status"] == "skipped"
    assert record["geometry_audit_invocation"]["requested"] is False
    assert record["geometry_audit_invocation"]["policy_source"] == "finish_request"


def test_register_records_the_exact_audit_model_and_response(project_factory, monkeypatch):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    assessment = DriftAssessment(
        geometry_preservation_score=1.0,
        camera_preserved=True,
        major_silhouettes_preserved=True,
        object_placement_preserved=True,
        detected_changes=[],
        recommendation="accept",
    )
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(
            parse=lambda **_kwargs: SimpleNamespace(
                id="resp_geometry_audit",
                output_parsed=assessment,
            )
        )
    )
    monkeypatch.setattr("archaeoforge.image_finish._client", lambda _project: fake_client)

    result = register_finished_render(
        project,
        generated_image=candidate,
        request_path=request_path,
    )
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))

    assert record["geometry_audit_invocation"] == {
        "requested": True,
        "policy_source": "finish_request",
        "provider": "openai_responses_api",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "response_id": "resp_geometry_audit",
    }
    assert result["manual_review_required"] is False


def test_register_revalidates_base_after_audit_before_publication(project_factory, monkeypatch):
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(project, base_image=base)
    candidate = _png(project.renders_dir / "candidate.png")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    def mutate_base(*_args, **_kwargs):
        _png(base, color="#000000")
        return DriftAssessment(
            geometry_preservation_score=1.0,
            camera_preserved=True,
            major_silhouettes_preserved=True,
            object_placement_preserved=True,
            detected_changes=[],
            recommendation="accept",
        )

    monkeypatch.setattr("archaeoforge.image_finish.audit_geometry", mutate_base)

    with pytest.raises(ValueError, match="base image changed"):
        register_finished_render(
            project,
            generated_image=candidate,
            request_path=request_path,
            audit=True,
        )

    assert not (project.renders_dir / "finished.png").exists()


def test_finish_render_uses_base_dimensions_and_records_api_result(
    project_factory,
    monkeypatch,
):
    project, base = _project_with_api_render(project_factory)
    config = load_config(project)
    config.ai.image_size = "auto"
    config.ai.geometry_audit_enabled = False
    write_config(config, project.config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    encoded = io.BytesIO()
    Image.new("RGB", _API_IMAGE_SIZE, "#506070").save(encoded, format="PNG")
    calls: list[dict] = []

    class FakeImages:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(encoded.getvalue()).decode("ascii"))],
                created=123,
                output_format="png",
                quality="high",
                size="1024x640",
                usage={"total_tokens": 7},
            )

    fake_client = SimpleNamespace(images=FakeImages())
    monkeypatch.setattr("archaeoforge.image_finish._client", lambda _project: fake_client)

    result = finish_render(project, base_image=base)

    assert calls[0]["model"] == "gpt-image-2"
    assert calls[0]["quality"] == "high"
    assert calls[0]["size"] == "1024x640"
    assert "input_fidelity" not in calls[0]
    assert "authoritative base render" in calls[0]["prompt"]
    assert Path(result["finished_image"]).exists()
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))
    assert record["generation"]["provider"] == "openai_api"
    assert record["generation"]["request"]["input_fidelity"] == "automatic_high"
    assert record["finish_record_schema"] == 3
    assert record["generation"]["api_response"]["usage"] == {"total_tokens": 7}
    assert record["geometry_audit_status"] == "skipped"


def test_historical_scene_api_finish_uses_mode_prompt_and_skips_strict_audit(
    project_factory,
    monkeypatch,
):
    project, base = _project_with_api_render(project_factory)
    config = load_config(project)
    config.ai.finish_mode = FinishMode.historical_scene
    config.ai.geometry_audit_enabled = True
    write_config(config, project.config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    encoded = io.BytesIO()
    Image.new("RGB", _API_IMAGE_SIZE, "#506070").save(encoded, format="PNG")
    calls: list[dict] = []

    class FakeImages:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(encoded.getvalue()).decode("ascii"))]
            )

    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: SimpleNamespace(images=FakeImages()),
    )
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_geometry",
        lambda *_args, **_kwargs: pytest.fail("Historical scene mode must skip strict geometry audit."),
    )
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_historical_spatial_contract",
        lambda *_args, **_kwargs: _accepting_spatial_assessment(),
    )

    result = finish_render(project, base_image=base)
    record = json.loads(Path(result["provenance_record"]).read_text(encoding="utf-8"))

    assert "Use case: historical-scene" in calls[0]["prompt"]
    assert "NON-NEGOTIABLE SPATIAL CONTRACT" in calls[0]["prompt"]
    assert _TEST_SPATIAL_CONSTRAINT_ID in calls[0]["prompt"]
    assert "evidence_status=needs_review" in calls[0]["prompt"]
    assert record["finish_mode"] == "historical_scene"
    assert record["geometry_audit_status"] == "skipped"
    assert "not applicable to historical_scene" in record["geometry_audit_reason"]
    assert record["geometry_audit_invocation"]["requested"] is False
    assert record["geometry_audit_invocation"]["effective"] is False
    assert record["geometry_audit_invocation"]["policy_source"] == "finish_mode"
    assert record["render_receipt"]["sha256"] == sha256_file(project.blender_result)
    assert record["render_receipt"]["beauty_image"]["sha256"] == sha256_file(base)
    assert record["historical_spatial_audit_status"] == "complete"
    assert record["historical_spatial_audit"]["recommendation"] == "accept"
    assert result["manual_review_required"] is True


def test_historical_scene_api_finish_rejects_explicit_strict_audit_before_api_call(
    project_factory,
    monkeypatch,
):
    project, base = _project_with_api_render(project_factory)
    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: pytest.fail("The API must not be called for an inapplicable audit."),
    )

    with pytest.raises(ValueError, match="not applicable to historical_scene"):
        finish_render(
            project,
            base_image=base,
            mode=FinishMode.historical_scene,
            audit=True,
        )


def test_finish_render_rejects_a_configured_size_that_changes_the_frame(
    project_factory,
    monkeypatch,
):
    project, base = _project_with_api_render(project_factory)
    config = load_config(project)
    config.ai.image_size = "32x32"
    write_config(config, project.config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: pytest.fail("The API must not be called for a reframed output size."),
    )

    with pytest.raises(ValueError, match="dimensions differ"):
        finish_render(project, base_image=base, audit=False)


def test_finish_render_rejects_invalid_model_size_before_api_call(project_factory, monkeypatch):
    project, base = _project_with_api_render(project_factory)
    _png(base, size=(1025, 640))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: pytest.fail("The API must not be called for an invalid GPT Image 2 size."),
    )

    with pytest.raises(ValueError, match="divisible by 16"):
        finish_render(project, base_image=base, audit=False)


def test_finish_render_rejects_too_few_pixels_before_api_call(project_factory, monkeypatch):
    project, base = _project_with_render(project_factory)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: pytest.fail("The API must not be called below the GPT Image 2 pixel floor."),
    )

    with pytest.raises(ValueError, match="at least 655,360 pixels"):
        finish_render(project, base_image=base, audit=False)


def test_finish_render_stages_and_validates_before_overwriting_output(project_factory, monkeypatch):
    project, base = _project_with_api_render(project_factory)
    config = load_config(project)
    config.ai.geometry_audit_enabled = False
    write_config(config, project.config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    destination = _png(project.renders_dir / "finished.png", color="#123456")
    provenance = destination.with_suffix(".provenance.json")
    audit_path = destination.with_suffix(".audit.json")
    provenance.write_text("old provenance", encoding="utf-8")
    audit_path.write_text("old audit", encoding="utf-8")
    image_before = destination.read_bytes()

    encoded = io.BytesIO()
    Image.new("RGB", _API_IMAGE_SIZE, "#654321").save(encoded, format="JPEG")
    fake_client = SimpleNamespace(
        images=SimpleNamespace(
            edit=lambda **_kwargs: SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(encoded.getvalue()).decode("ascii"))]
            )
        )
    )
    monkeypatch.setattr("archaeoforge.image_finish._client", lambda _project: fake_client)

    with pytest.raises(ValueError, match="non-PNG"):
        finish_render(
            project,
            base_image=base,
            destination=destination,
            audit=False,
            overwrite=True,
        )

    assert destination.read_bytes() == image_before
    assert provenance.read_text(encoding="utf-8") == "old provenance"
    assert audit_path.read_text(encoding="utf-8") == "old audit"


def test_finish_render_rejects_url_responses_without_fetching_them(project_factory, monkeypatch):
    project, base = _project_with_api_render(project_factory)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    fake_client = SimpleNamespace(
        images=SimpleNamespace(
            edit=lambda **_kwargs: SimpleNamespace(
                data=[SimpleNamespace(b64_json=None, url="file:///etc/passwd")]
            )
        )
    )
    monkeypatch.setattr("archaeoforge.image_finish._client", lambda _project: fake_client)

    with pytest.raises(RuntimeError, match="no base64 image data"):
        finish_render(project, base_image=base, audit=False)

    assert not (project.renders_dir / "finished.png").exists()


def test_finish_render_revalidates_base_before_publication(project_factory, monkeypatch):
    project, base = _project_with_api_render(project_factory)
    config = load_config(project)
    config.ai.geometry_audit_enabled = False
    write_config(config, project.config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    encoded = io.BytesIO()
    Image.new("RGB", _API_IMAGE_SIZE, "#506070").save(encoded, format="PNG")

    def edit(**_kwargs):
        _png(base, color="#000000")
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(encoded.getvalue()).decode("ascii"))]
        )

    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: SimpleNamespace(images=SimpleNamespace(edit=edit)),
    )

    with pytest.raises(ValueError, match="base image.*changed"):
        finish_render(project, base_image=base, audit=False)

    assert not (project.renders_dir / "finished.png").exists()


def test_historical_api_finish_revalidates_render_receipt_before_publication(
    project_factory,
    monkeypatch,
):
    project, base = _project_with_api_render(project_factory)
    config = load_config(project)
    config.ai.finish_mode = FinishMode.historical_scene
    write_config(config, project.config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    encoded = io.BytesIO()
    Image.new("RGB", _API_IMAGE_SIZE, "#506070").save(encoded, format="PNG")

    def edit(**_kwargs):
        receipt = json.loads(project.blender_result.read_text(encoding="utf-8"))
        receipt["log"] = "receipt changed while the Image API request was running"
        project.blender_result.write_text(json.dumps(receipt), encoding="utf-8")
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(encoded.getvalue()).decode("ascii"))]
        )

    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: SimpleNamespace(images=SimpleNamespace(edit=edit)),
    )
    monkeypatch.setattr(
        "archaeoforge.image_finish.audit_historical_spatial_contract",
        lambda *_args, **_kwargs: _accepting_spatial_assessment(),
    )

    with pytest.raises(ValueError, match="render receipt changed while the Image API finish was running"):
        finish_render(project, base_image=base)

    assert not (project.renders_dir / "finished.png").exists()
    assert not (project.renders_dir / "finished.provenance.json").exists()


def test_finish_render_cannot_publish_outside_renders(project_factory, monkeypatch):
    project, base = _project_with_api_render(project_factory)
    config_before = project.config.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(
        "archaeoforge.image_finish._client",
        lambda _project: pytest.fail("The API must not be called for an unsafe destination."),
    )

    with pytest.raises(ValueError, match="must be inside"):
        finish_render(
            project,
            base_image=base,
            destination=project.config,
            audit=False,
            overwrite=True,
        )

    assert project.config.read_bytes() == config_before


@pytest.mark.parametrize(
    ("enabled", "skip_ai", "skip_blender", "no_render", "blender_rendered", "beauty", "reason"),
    [
        (False, False, False, False, True, True, "ai.finish_enabled is false"),
        (True, True, False, False, True, True, "--skip-ai"),
        (True, False, True, False, True, True, "--skip-blender"),
        (True, False, False, True, True, True, "--no-render"),
        (True, False, False, False, False, True, "Blender render did not complete"),
        (True, False, False, False, True, False, "beauty render not found"),
    ],
)
def test_orchestrated_finish_reports_every_skip_reason(
    project_factory,
    enabled,
    skip_ai,
    skip_blender,
    no_render,
    blender_rendered,
    beauty,
    reason,
):
    project = project_factory()
    config = load_config(project)
    config.ai.finish_enabled = enabled
    if beauty:
        _png(project.renders_dir / "beauty.png")

    result = _run_image_finish_stage(
        project,
        config,
        skip_ai=skip_ai,
        skip_blender=skip_blender,
        no_render=no_render,
        blender_rendered=blender_rendered,
    )

    assert result == {"skipped": True, "reason": reason}


def test_orchestrated_interactive_finish_prepares_pending_handoff(project_factory):
    project, _ = _project_with_render(project_factory)
    config = load_config(project)
    config.ai.finish_enabled = True
    config.ai.finish_backend = "interactive_handoff"
    config.ai.finish_mode = FinishMode.historical_scene

    result = _run_image_finish_stage(
        project,
        config,
        skip_ai=False,
        skip_blender=False,
        no_render=False,
        blender_rendered=True,
    )

    assert result["status"] == "pending_external_finish"
    assert result["backend"] == "interactive_handoff"
    assert Path(result["request"]).exists()
    request = json.loads(Path(result["request"]).read_text(encoding="utf-8"))
    assert request["finish_mode"] == "historical_scene"


def test_orchestrated_api_finish_runs_after_render(project_factory, monkeypatch):
    project = project_factory()
    beauty = _png(project.renders_dir / "beauty.png")
    config = load_config(project)
    config.ai.finish_enabled = True
    config.ai.finish_backend = "openai_api"
    config.ai.finish_mode = FinishMode.historical_scene
    calls = []

    def fake_finish(selected_project, *, base_image, mode):
        calls.append((selected_project, base_image, mode))
        return {"finished_image": "finished.png"}

    monkeypatch.setattr("archaeoforge.cli.finish_render", fake_finish)

    result = _run_image_finish_stage(
        project,
        config,
        skip_ai=False,
        skip_blender=False,
        no_render=False,
        blender_rendered=True,
    )

    assert result == {"finished_image": "finished.png"}
    assert calls == [(project, beauty, FinishMode.historical_scene)]


def test_register_finish_cli_exits_2_when_historical_spatial_validation_is_pending(
    project_factory,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project, base = _project_with_render(project_factory)
    request_path = prepare_finish_request(
        project,
        base_image=base,
        mode=FinishMode.historical_scene,
    )
    candidate = _png(project.renders_dir / "candidate.png")

    result = CliRunner().invoke(
        app,
        [
            "register-finish",
            str(candidate),
            "--project",
            str(project.root),
            "--request",
            str(request_path),
        ],
    )

    assert result.exit_code == 2
    assert "validation_blocked" in result.stdout
    assert not (project.renders_dir / "finished.png").exists()
    assert not (project.renders_dir / "finished.provenance.json").exists()


@pytest.mark.parametrize("command", ["finish", "prepare-finish"])
def test_finish_commands_expose_historical_scene_mode(command):
    cli_command = get_command(app).commands[command]
    mode_option = next(parameter for parameter in cli_command.params if parameter.name == "mode")

    assert "--mode" in mode_option.opts
    assert set(mode_option.type.choices) == {"precise_object_edit", "historical_scene"}
