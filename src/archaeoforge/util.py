from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return value.strip("-") or "item"


def json_dumps(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent)


def text_windows(pages: Iterable[tuple[int, str]], max_chars: int) -> Iterator[tuple[str, list[int]]]:
    current: list[str] = []
    page_numbers: list[int] = []
    current_chars = 0
    for page_number, text in pages:
        labeled = f"\n\n=== PAGE {page_number} ===\n{text.strip()}"
        if current and current_chars + len(labeled) > max_chars:
            yield "".join(current), page_numbers.copy()
            current, page_numbers, current_chars = [], [], 0
        if len(labeled) <= max_chars:
            current.append(labeled)
            page_numbers.append(page_number)
            current_chars += len(labeled)
            continue
        if current:
            yield "".join(current), page_numbers.copy()
            current, page_numbers, current_chars = [], [], 0
        for offset in range(0, len(labeled), max_chars):
            yield labeled[offset : offset + max_chars], [page_number]
    if current:
        yield "".join(current), page_numbers
