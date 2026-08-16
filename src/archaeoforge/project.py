from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import ProjectConfig


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def config(self) -> Path: return self.root / "project.yaml"
    @property
    def state_dir(self) -> Path: return self.root / ".archaeoforge"
    @property
    def database(self) -> Path: return self.state_dir / "project.sqlite3"
    @property
    def cache_dir(self) -> Path: return self.state_dir / "cache"
    @property
    def log_dir(self) -> Path: return self.state_dir / "logs"
    @property
    def sources_dir(self) -> Path: return self.root / "sources"
    @property
    def data_dir(self) -> Path: return self.root / "data"
    @property
    def assets_dir(self) -> Path: return self.root / "assets"
    @property
    def prompts_dir(self) -> Path: return self.root / "prompts"
    @property
    def outputs_dir(self) -> Path: return self.root / "outputs"
    @property
    def exports_dir(self) -> Path: return self.outputs_dir / "exports"
    @property
    def renders_dir(self) -> Path: return self.outputs_dir / "renders"
    @property
    def reports_dir(self) -> Path: return self.outputs_dir / "reports"
    @property
    def scene_manifest(self) -> Path: return self.exports_dir / "scene_manifest.json"
    @property
    def blender_result(self) -> Path: return self.exports_dir / "blender_result.json"
    @property
    def validation_report(self) -> Path: return self.reports_dir / "validation.json"
    @property
    def html_report(self) -> Path: return self.reports_dir / "index.html"
    @property
    def blend_file(self) -> Path: return self.outputs_dir / f"{self.root.name}.blend"

    def ensure(self) -> None:
        if not self.config.exists():
            raise FileNotFoundError(f"No project.yaml found at {self.config}")
        for path in (
            self.state_dir, self.cache_dir, self.log_dir, self.sources_dir, self.data_dir,
            self.assets_dir, self.prompts_dir, self.outputs_dir, self.exports_dir,
            self.renders_dir, self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_project(path: str | Path) -> ProjectPaths:
    project = ProjectPaths(Path(path).expanduser().resolve())
    project.ensure()
    return project


def load_config(project: ProjectPaths) -> ProjectConfig:
    return ProjectConfig.model_validate(yaml.safe_load(project.config.read_text(encoding="utf-8")))
