#!/usr/bin/env python3
"""Read-only ArchaeoForge CLI and project discovery for Codex workflows."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REQUIRED_FILES = (
    "project.yaml",
    "data/source_catalog.csv",
    "data/evidence_seed.csv",
    "data/features.geojson",
)

OPTIONAL_FILES = ("prompts/finish_historical_scene.txt",)

GENERATED_FILES = (
    "outputs/exports/scene_manifest.json",
    "outputs/exports/evidence_register.csv",
    "outputs/reports/validation.json",
    "outputs/reports/index.html",
    "outputs/renders/beauty.png",
)


def _ancestors(path: Path):
    yield path
    yield from path.parents


def _project_root(candidate: Path) -> Path | None:
    current = candidate.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in _ancestors(current):
        if (directory / "project.yaml").is_file():
            return directory
    return None


def _checkout_root(candidate: Path) -> Path | None:
    current = candidate.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in _ancestors(current):
        if (directory / "pyproject.toml").is_file() and (directory / "src" / "archaeoforge").is_dir():
            return directory
    return None


def _resolve_executable(checkout: Path | None, search_from: Path) -> tuple[Path | None, str | None]:
    explicit = os.environ.get("ARCHAEOFORGE_CLI")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return path, "ARCHAEOFORGE_CLI"

    on_path = shutil.which("archaeoforge")
    if on_path:
        return Path(on_path).resolve(), "PATH"

    candidates: list[tuple[Path, str]] = []
    if checkout is not None:
        candidates.append((checkout / ".venv" / "bin" / "archaeoforge", "checkout_venv"))
    candidates.append(
        (
            Path.home() / "archaeoforge" / ".venv" / "bin" / "archaeoforge",
            "home_checkout_venv",
        )
    )
    for directory in _ancestors(search_from.resolve()):
        candidates.append((directory / ".venv" / "bin" / "archaeoforge", "ancestor_venv"))

    seen: set[Path] = set()
    for path, source in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved, source
    return None, None


def _command(executable: Path | None, *parts: str) -> list[str] | None:
    if executable is None:
        return None
    return [str(executable), *parts]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover an ArchaeoForge executable and summarize one project without writing files."
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Project, project child, checkout, or nearby path to inspect (default: current directory).",
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        help="Optional ArchaeoForge checkout containing .venv/bin/archaeoforge.",
    )
    parser.add_argument(
        "--require-project",
        action="store_true",
        help="Exit nonzero when no ancestor project.yaml can be found.",
    )
    args = parser.parse_args()

    selected = args.project.expanduser().resolve()
    project = _project_root(selected)
    checkout = _checkout_root(args.checkout) if args.checkout is not None else _checkout_root(selected)
    executable, executable_source = _resolve_executable(checkout, selected)
    if checkout is None and executable is not None:
        checkout = _checkout_root(executable)

    files: dict[str, bool] = {}
    finish_requests: list[str] = []
    if project is not None:
        files = {
            relative: (project / relative).is_file()
            for relative in (*REQUIRED_FILES, *OPTIONAL_FILES, *GENERATED_FILES)
        }
        exports = project / "outputs" / "exports"
        if exports.is_dir():
            finish_requests = [
                str(path.relative_to(project))
                for path in sorted(exports.glob("image_finish_request*.json"))
                if path.is_file()
            ]

    project_text = str(project) if project is not None else None
    beauty_path = project / "outputs/renders/beauty.png" if project is not None else None
    manifest_path = project / "outputs/exports/scene_manifest.json" if project is not None else None
    historical_prompt = project / "prompts/finish_historical_scene.txt" if project is not None else None
    payload = {
        "schema_version": 1,
        "read_only": True,
        "selected_path": str(selected),
        "checkout_root": str(checkout) if checkout is not None else None,
        "executable": str(executable) if executable is not None else None,
        "executable_source": executable_source,
        "project_root": project_text,
        "files": files,
        "finish_requests": finish_requests,
        "commands": {
            "doctor": _command(executable, "doctor", project_text) if project_text else None,
            "preview_run": (
                _command(executable, "run", project_text, "--preview", "--skip-ai") if project_text else None
            ),
            "prepare_historical_scene": (
                _command(
                    executable,
                    "prepare-finish",
                    str(beauty_path),
                    "--project",
                    project_text,
                    "--mode",
                    "historical_scene",
                    "--prompt",
                    str(historical_prompt),
                )
                if project_text
                and beauty_path is not None
                and beauty_path.is_file()
                and manifest_path is not None
                and manifest_path.is_file()
                and historical_prompt is not None
                and historical_prompt.is_file()
                else None
            ),
        },
        "problems": [],
    }
    if executable is None:
        payload["problems"].append(
            "No archaeoforge executable found on PATH, through ARCHAEOFORGE_CLI, or in a detected checkout .venv."
        )
    if project is None:
        payload["problems"].append("No project.yaml found at or above the selected path.")
    elif missing := [relative for relative in REQUIRED_FILES if not files[relative]]:
        payload["problems"].append(f"Missing required scaffold files: {', '.join(missing)}")

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")

    if executable is None:
        return 2
    if args.require_project and project is None:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
