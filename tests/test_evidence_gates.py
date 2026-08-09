from __future__ import annotations

from archaeoforge.compile_scene import compile_scene
from archaeoforge.db import connect, list_reviews, set_claim_status
from archaeoforge.models import ReviewStatus


def test_authoritative_rejects_unapproved_claim(project_factory):
    project = project_factory(feature_status=ReviewStatus.approved, claim_status=ReviewStatus.needs_review)
    manifest = compile_scene(project, preview=False)
    assert manifest["statistics"]["compiled_features"] == 0
    assert "disallowed status" in " ".join(manifest["excluded_features"][0]["reasons"])


def test_preview_includes_needs_review_feature_and_claim(project_factory):
    project = project_factory()
    manifest = compile_scene(project, preview=True)
    assert manifest["statistics"]["compiled_features"] == 1
    assert manifest["features"][0]["id"] == "WALL-1"


def test_target_date_excludes_feature_claim(project_factory):
    project = project_factory(claim_date_start=-450, claim_date_end=-400)
    manifest = compile_scene(project, preview=True)
    assert manifest["statistics"]["compiled_features"] == 0
    assert "target year" in " ".join(manifest["excluded_features"][0]["reasons"])


def test_input_fingerprint_is_stable(project_factory):
    project = project_factory()
    first = compile_scene(project, preview=True)
    second = compile_scene(project, preview=True)
    assert first["input_fingerprint"] == second["input_fingerprint"]


def test_review_writes_append_only_audit(project_factory):
    project = project_factory()
    connection = connect(project)
    try:
        assert set_claim_status(
            connection, "EVID-TEST", ReviewStatus.approved, reviewer="Dr. Reviewer", notes="Checked page 1"
        )
        reviews = list_reviews(connection, "EVID-TEST")
    finally:
        connection.close()
    assert len(reviews) == 1
    assert reviews[0]["previous_status"] == "needs_review"
    assert reviews[0]["new_status"] == "approved"
    assert reviews[0]["reviewer"] == "Dr. Reviewer"


def test_modified_source_excludes_feature_even_in_preview(project_factory):
    project = project_factory()
    (project.sources_dir / "source.txt").write_text("Changed source bytes.\n", encoding="utf-8")
    manifest = compile_scene(project, preview=True)
    assert manifest["statistics"]["compiled_features"] == 0
    reasons = " ".join(manifest["excluded_features"][0]["reasons"])
    assert "registered checksum" in reasons or "source checksum has changed" in reasons
