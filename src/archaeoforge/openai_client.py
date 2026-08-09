from __future__ import annotations

import os
from typing import Any

from dotenv import dotenv_values

from .project import ProjectPaths

OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"


def project_openai_api_key(project: ProjectPaths, *, required: bool = True) -> str | None:
    """Read only the API key from process state or the selected project's .env file.

    Loading a project's entire dotenv file into the process would let unrelated OpenAI SDK
    settings, such as a custom base URL, redirect a user's existing credential.
    """
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        env_path = project.root / ".env"
        if env_path.is_file():
            value = dotenv_values(env_path).get("OPENAI_API_KEY")
            key = value.strip() if isinstance(value, str) else ""
    if not key and required:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return key or None


def new_official_openai_client(project: ProjectPaths) -> Any:
    try:
        from openai import OpenAI
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("The OpenAI Python SDK is not installed.") from exc
    return OpenAI(
        api_key=project_openai_api_key(project),
        base_url=OFFICIAL_OPENAI_BASE_URL,
    )
