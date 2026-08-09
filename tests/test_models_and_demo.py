from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from archaeoforge.compile_scene import compile_scene
from archaeoforge.config import default_config
from archaeoforge.db import connect, import_claim_catalog, import_source_catalog
from archaeoforge.extract import _normalise_draft
from archaeoforge.ingest import ingest_project
from archaeoforge.models import EvidenceClaimDraft, EvidenceClass
from archaeoforge.project import resolve_project
from archaeoforge.report import export_chatgpt_handoff, generate_report
from archaeoforge.validate import validate_project


def test_ai_class_a_claim_is_downgraded_pending_human_review():
    draft = EvidenceClaimDraft(
        subject="wall",
        property="width",
        claim="Measured width",
        locator="figure 2",
        evidence_class=EvidenceClass.A,
        confidence=0.99,
    )
    result = _normalise_draft(draft)
    assert result.evidence_class == EvidenceClass.B
    assert result.confidence <= 0.85
    assert "downgraded" in result.uncertainty.lower()


def test_historical_year_zero_is_rejected():
    with pytest.raises(ValidationError, match="no year zero"):
        default_config("x", "x", "x", 0, "year zero")


def test_babylon_preview_runs_end_to_end(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    source = repository_root / "projects" / "babylon_570_bce"
    destination = tmp_path / "babylon_570_bce"
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".archaeoforge", "outputs", "__pycache__"),
    )
    project = resolve_project(destination)
    connection = connect(project)
    try:
        assert import_source_catalog(connection, project.data_dir / "source_catalog.csv") == 5
    finally:
        connection.close()
    result = ingest_project(project)
    assert result["failed"] == 0
    connection = connect(project)
    try:
        assert import_claim_catalog(connection, project.data_dir / "evidence_seed.csv") == 9
    finally:
        connection.close()
    validation = validate_project(project, preview=True)
    assert validation["valid"] is True
    manifest = compile_scene(project, preview=True)
    assert manifest["statistics"]["compiled_features"] == 10
    report = generate_report(project)
    assert report.exists()
    assert "Schematic Pipeline Starter" in report.read_text(encoding="utf-8")
    handoff = export_chatgpt_handoff(project)
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["validation"]["valid"] is True
    assert len(payload["evidence_claims"]) == 9
    assert payload["scene_manifest"]["statistics"]["compiled_features"] == 10
    assert "project_root" not in payload["scene_manifest"]
