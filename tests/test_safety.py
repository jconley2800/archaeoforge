from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from archaeoforge.cli import app
from archaeoforge.compile_scene import compile_scene
from archaeoforge.config import default_config, write_config
from archaeoforge.db import connect, set_claim_status
from archaeoforge.models import ProjectConfig, ReviewStatus
from archaeoforge.project import ProjectPaths, load_config
from archaeoforge.report import export_chatgpt_handoff, generate_report

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain_cli_output(value: str) -> str:
    return " ".join(_ANSI_ESCAPE_RE.sub("", value).split())


def test_report_escapes_malicious_source_title(project_factory):
    project = project_factory()
    compile_scene(project, preview=True)
    connection = connect(project)
    try:
        connection.execute(
            "UPDATE sources SET title = ? WHERE id = ?",
            ("<script>alert(1)</script>", "SRC-TEST"),
        )
        connection.commit()
    finally:
        connection.close()

    html = generate_report(project).read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_chatgpt_handoff_recursively_redacts_absolute_local_paths(project_factory):
    project = project_factory()
    project_path = str(project.root)
    posix_secret = "/srv/private-archive/field notes/source.pdf"
    windows_secret = r"C:\Users\Researcher\private\source.pdf"
    file_uri_secret = "file:///home/researcher/private/source.pdf"
    public_url = "https://example.org/archive/source.pdf"

    config = load_config(project)
    config.project.description = f"Working copy at {project_path}/notes"
    config.blender.executable = "/opt/private-tools/blender"
    write_config(config, project.config)

    connection = connect(project)
    try:
        connection.execute(
            "UPDATE sources SET notes = ?, metadata_json = ?, url = ? WHERE id = ?",
            (
                f"Local scan: {posix_secret}; Windows copy: {windows_secret}",
                json.dumps(
                    {
                        "nested": [
                            {"cache_path": f"{project_path}/.archaeoforge/cache/page.png"},
                            file_uri_secret,
                        ]
                    }
                ),
                public_url,
                "SRC-TEST",
            ),
        )
        connection.execute(
            "UPDATE claims SET uncertainty = ? WHERE id = ?",
            (f"Checked against {project_path}/sources/source.txt", "EVID-TEST"),
        )
        connection.commit()
        set_claim_status(
            connection,
            "EVID-TEST",
            ReviewStatus.approved,
            reviewer="Safety test",
            notes="Reviewer workspace: /mnt/reviewer/private/checklist.txt",
        )
    finally:
        connection.close()

    project.validation_report.parent.mkdir(parents=True, exist_ok=True)
    project.validation_report.write_text(
        json.dumps(
            {
                "valid": True,
                "features_path": f"{project_path}/data/features.geojson",
                "issues": [{"message": "Loaded /var/private/validation.log"}],
            }
        ),
        encoding="utf-8",
    )
    project.scene_manifest.parent.mkdir(parents=True, exist_ok=True)
    project.scene_manifest.write_text(
        json.dumps(
            {
                "project_root": project_path,
                "statistics": {},
                "nested": {"operator_path": "/home/operator/archaeoforge/manifest.json"},
            }
        ),
        encoding="utf-8",
    )

    payload = json.loads(export_chatgpt_handoff(project).read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)

    for secret in (
        project_path,
        posix_secret,
        windows_secret,
        file_uri_secret,
        "/opt/private-tools/blender",
        "/mnt/reviewer/private/checklist.txt",
        "/var/private/validation.log",
        "/home/operator/archaeoforge/manifest.json",
    ):
        assert secret not in serialized
    assert public_url in serialized
    assert "private-archive" not in serialized
    assert "field notes/source.pdf" not in serialized
    assert r"Users\Researcher\private" not in serialized
    assert payload["sources"][0]["relative_path"] == "sources/source.txt"
    assert "<redacted-local-path>" in serialized
    assert "project_root" not in payload["scene_manifest"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"scheema_version": 1}),
        lambda payload: payload["blender"]["camera"].update({"azmuth_degrees": 145}),
    ],
)
def test_project_config_rejects_unknown_fields(mutate):
    payload = default_config("test", "Test", "Test place", -570, "570 BCE").model_dump(mode="json")
    mutate(payload)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectConfig.model_validate(payload)


def test_init_force_does_not_overwrite_existing_project_file(tmp_path: Path):
    root = tmp_path / "existing-project"
    root.mkdir()
    config_path = root / "project.yaml"
    config_path.write_text("do-not-replace\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["init", str(root), "--force"])

    assert result.exit_code == 2
    assert "Refusing to overwrite existing project files" in result.output
    assert config_path.read_text(encoding="utf-8") == "do-not-replace\n"
    assert not (root / "data" / "features.geojson").exists()


def test_init_overwrite_requires_both_explicit_flags(tmp_path: Path):
    runner = CliRunner()
    root = tmp_path / "project"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    config_path = root / "project.yaml"
    config_path.write_text("do-not-replace\n", encoding="utf-8")

    missing_force = runner.invoke(app, ["init", str(root), "--overwrite-existing"])
    assert missing_force.exit_code == 2
    assert "requires --force" in _plain_cli_output(missing_force.output)
    assert config_path.read_text(encoding="utf-8") == "do-not-replace\n"

    overwrite = runner.invoke(
        app,
        ["init", str(root), "--force", "--overwrite-existing", "--title", "Replacement"],
    )
    assert overwrite.exit_code == 0
    assert load_config(ProjectPaths(root)).project.title == "Replacement"


def test_init_overwrite_rejects_dangling_scaffold_symlink(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside-project.yaml"
    (root / "project.yaml").symlink_to(outside)

    result = CliRunner().invoke(
        app,
        ["init", str(root), "--force", "--overwrite-existing"],
    )

    assert result.exit_code == 2
    assert "symlinked project paths" in result.output
    assert not outside.exists()
    assert (root / "project.yaml").is_symlink()


def test_init_rejects_symlinked_project_root(tmp_path: Path):
    outside = tmp_path / "outside-project"
    outside.mkdir()
    root_link = tmp_path / "project-link"
    root_link.symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(
        app,
        ["init", str(root_link), "--force", "--overwrite-existing"],
    )

    assert result.exit_code == 2
    assert "symlinked project root" in result.output
    assert not (outside / "project.yaml").exists()


def test_init_overwrite_rejects_symlinked_scaffold_directory(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    outside_data = tmp_path / "outside-data"
    outside_data.mkdir()
    outside_feature = outside_data / "features.geojson"
    outside_feature.write_text("do-not-replace\n", encoding="utf-8")
    (root / "data").symlink_to(outside_data, target_is_directory=True)

    result = CliRunner().invoke(
        app,
        ["init", str(root), "--force", "--overwrite-existing"],
    )

    assert result.exit_code == 2
    assert "symlinked project paths" in result.output
    assert outside_feature.read_text(encoding="utf-8") == "do-not-replace\n"
    assert not (root / "project.yaml").exists()
