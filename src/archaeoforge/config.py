from __future__ import annotations

from pathlib import Path

import yaml

from .models import ProjectConfig, ProjectIdentity


def default_config(project_id: str, title: str, place_name: str, target_year: int, label: str) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectIdentity(
            id=project_id,
            title=title,
            place_name=place_name,
            target_year=target_year,
            target_year_label=label,
        )
    )


def write_config(config: ProjectConfig, destination: Path) -> None:
    destination.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
