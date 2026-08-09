from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import EvidenceClaim, ReviewStatus, SourceRecord
from .project import ProjectPaths
from .util import json_dumps, sha256_text, utc_now

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    relative_path TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '',
    publication_year INTEGER,
    source_type TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT NOT NULL DEFAULT '',
    local_copy INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    ingested_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_relative_path
ON sources(relative_path) WHERE relative_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS source_pages (
    source_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    text_content TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    rendered_page_path TEXT,
    PRIMARY KEY(source_id, page_number),
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    property_name TEXT NOT NULL,
    claim TEXT NOT NULL,
    value_text TEXT NOT NULL DEFAULT '',
    value_number REAL,
    unit TEXT,
    locator TEXT NOT NULL DEFAULT '',
    quotation TEXT NOT NULL DEFAULT '',
    evidence_basis TEXT NOT NULL,
    evidence_class TEXT NOT NULL,
    confidence REAL NOT NULL,
    date_start INTEGER,
    date_end INTEGER,
    uncertainty TEXT NOT NULL DEFAULT '',
    alternative_group TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'manual',
    model_used TEXT NOT NULL DEFAULT '',
    response_id TEXT NOT NULL DEFAULT '',
    source_sha256_at_creation TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claim_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_claim_reviews_claim_id
ON claim_reviews(claim_id, reviewed_at);
"""


def connect(project: ProjectPaths) -> sqlite3.Connection:
    connection = sqlite3.connect(project.database)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(claims)")}
    if "source_sha256_at_creation" not in columns:
        connection.execute("ALTER TABLE claims ADD COLUMN source_sha256_at_creation TEXT NOT NULL DEFAULT ''")
    connection.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', '1')")
    connection.commit()
    return connection


def upsert_source(connection: sqlite3.Connection, source: SourceRecord) -> None:
    existing_row = connection.execute("SELECT metadata_json FROM sources WHERE id = ?", (source.id,)).fetchone()
    existing_metadata = json.loads(existing_row["metadata_json"] or "{}") if existing_row else {}
    merged_metadata = {**existing_metadata, **source.metadata}
    connection.execute(
        """
        INSERT INTO sources(
            id, relative_path, title, authors, publication_year, source_type, url, license,
            sha256, size_bytes, mime_type, local_copy, notes, metadata_json, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            relative_path=COALESCE(excluded.relative_path, sources.relative_path),
            title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE sources.title END,
            authors=CASE WHEN excluded.authors <> '' THEN excluded.authors ELSE sources.authors END,
            publication_year=COALESCE(excluded.publication_year, sources.publication_year),
            source_type=CASE WHEN excluded.source_type <> 'other' OR sources.source_type = 'other'
                THEN excluded.source_type ELSE sources.source_type END,
            url=CASE WHEN excluded.url <> '' THEN excluded.url ELSE sources.url END,
            license=CASE WHEN excluded.license <> '' THEN excluded.license ELSE sources.license END,
            sha256=CASE WHEN excluded.sha256 <> '' THEN excluded.sha256 ELSE sources.sha256 END,
            size_bytes=CASE WHEN excluded.size_bytes > 0 THEN excluded.size_bytes ELSE sources.size_bytes END,
            mime_type=CASE WHEN excluded.mime_type <> '' THEN excluded.mime_type ELSE sources.mime_type END,
            local_copy=MAX(excluded.local_copy, sources.local_copy),
            notes=CASE WHEN excluded.notes <> '' THEN excluded.notes ELSE sources.notes END,
            metadata_json=excluded.metadata_json,
            ingested_at=excluded.ingested_at
        """,
        (
            source.id, source.relative_path, source.title, source.authors, source.publication_year,
            source.source_type.value, source.url, source.license, source.sha256, source.size_bytes,
            source.mime_type, int(source.local_copy), source.notes, json_dumps(merged_metadata), utc_now(),
        ),
    )
    connection.commit()


def replace_pages(connection: sqlite3.Connection, source_id: str, pages: Iterable[dict[str, Any]]) -> None:
    connection.execute("DELETE FROM source_pages WHERE source_id = ?", (source_id,))
    connection.executemany(
        """INSERT INTO source_pages(source_id, page_number, text_content, char_count, image_count, rendered_page_path)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                source_id, page["page_number"], page.get("text_content", ""), page.get("char_count", 0),
                page.get("image_count", 0), page.get("rendered_page_path"),
            )
            for page in pages
        ],
    )
    connection.commit()


def claim_fingerprint(claim: EvidenceClaim) -> str:
    payload = "|".join(
        [
            claim.source_id, claim.locator.strip().lower(), claim.subject.strip().lower(),
            claim.property.strip().lower(), claim.value_text.strip().lower(), str(claim.value_number),
            str(claim.unit).lower(), claim.claim.strip().lower(),
        ]
    )
    return sha256_text(payload)


def upsert_claim(connection: sqlite3.Connection, claim: EvidenceClaim) -> bool:
    fingerprint = claim_fingerprint(claim)
    source_row = connection.execute("SELECT sha256 FROM sources WHERE id = ?", (claim.source_id,)).fetchone()
    source_sha = claim.source_sha256_at_creation or (source_row["sha256"] if source_row else "")
    cursor = connection.execute(
        """
        INSERT INTO claims(
            id, source_id, subject, property_name, claim, value_text, value_number, unit,
            locator, quotation, evidence_basis, evidence_class, confidence, date_start, date_end,
            uncertainty, alternative_group, tags_json, review_status, created_by, model_used,
            response_id, source_sha256_at_creation, fingerprint, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO NOTHING
        """,
        (
            claim.id, claim.source_id, claim.subject, claim.property, claim.claim, claim.value_text,
            claim.value_number, claim.unit, claim.locator, claim.quotation, claim.evidence_basis,
            claim.evidence_class.value, claim.confidence, claim.date_start, claim.date_end,
            claim.uncertainty, claim.alternative_group, json_dumps(claim.tags), claim.review_status.value,
            claim.created_by, claim.model_used, claim.response_id, source_sha, fingerprint, utc_now(),
        ),
    )
    connection.commit()
    return cursor.rowcount > 0


def list_sources(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM sources ORDER BY title")]


def get_source(connection: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return dict(row) if row else None


def list_pages(connection: sqlite3.Connection, source_id: str) -> list[dict[str, Any]]:
    return [
        dict(row) for row in connection.execute(
            "SELECT * FROM source_pages WHERE source_id = ? ORDER BY page_number", (source_id,)
        )
    ]


def list_claims(
    connection: sqlite3.Connection,
    statuses: Iterable[ReviewStatus] | None = None,
    source_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if statuses:
        values = [status.value if isinstance(status, ReviewStatus) else str(status) for status in statuses]
        clauses.append(f"review_status IN ({','.join('?' for _ in values)})")
        params.extend(values)
    if source_id:
        clauses.append("source_id = ?")
        params.append(source_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(f"SELECT * FROM claims {where} ORDER BY source_id, locator, subject", params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        result.append(item)
    return result


def set_claim_status(
    connection: sqlite3.Connection,
    claim_id: str,
    status: ReviewStatus,
    *,
    reviewer: str = "unspecified",
    notes: str = "",
) -> bool:
    current = connection.execute("SELECT review_status FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if current is None:
        return False
    reviewed_at = utc_now()
    with connection:
        connection.execute("UPDATE claims SET review_status = ? WHERE id = ?", (status.value, claim_id))
        connection.execute(
            """INSERT INTO claim_reviews(claim_id, previous_status, new_status, reviewer, notes, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (claim_id, current["review_status"], status.value, reviewer.strip() or "unspecified", notes.strip(), reviewed_at),
        )
    return True


def list_reviews(connection: sqlite3.Connection, claim_id: str | None = None) -> list[dict[str, Any]]:
    if claim_id:
        rows = connection.execute(
            "SELECT * FROM claim_reviews WHERE claim_id = ? ORDER BY reviewed_at, id", (claim_id,)
        ).fetchall()
    else:
        rows = connection.execute("SELECT * FROM claim_reviews ORDER BY reviewed_at, id").fetchall()
    return [dict(row) for row in rows]


def _cell(row: dict[str, Any], key: str, default: str = "") -> str:
    return str(row.get(key) or default).strip()


def import_source_catalog(connection: sqlite3.Connection, csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    count = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not _cell(row, "id"):
                continue
            source = SourceRecord(
                id=_cell(row, "id"), relative_path=_cell(row, "relative_path") or None,
                title=_cell(row, "title", "Untitled"), authors=_cell(row, "authors"),
                publication_year=int(_cell(row, "publication_year")) if _cell(row, "publication_year") else None,
                source_type=_cell(row, "source_type", "other"), url=_cell(row, "url"),
                license=_cell(row, "license"), sha256=_cell(row, "sha256"),
                size_bytes=int(_cell(row, "size_bytes") or 0), mime_type=_cell(row, "mime_type"),
                local_copy=_cell(row, "local_copy").lower() in {"1", "true", "yes", "y"},
                notes=_cell(row, "notes"),
            )
            upsert_source(connection, source)
            count += 1
    return count


def import_claim_catalog(connection: sqlite3.Connection, csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    count = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not _cell(row, "id") or not _cell(row, "source_id"):
                continue
            claim = EvidenceClaim(
                id=_cell(row, "id"), source_id=_cell(row, "source_id"), subject=_cell(row, "subject"),
                property=_cell(row, "property"), claim=_cell(row, "claim"), value_text=_cell(row, "value_text"),
                value_number=float(_cell(row, "value_number")) if _cell(row, "value_number") else None,
                unit=_cell(row, "unit") or None, locator=_cell(row, "locator"), quotation=_cell(row, "quotation"),
                evidence_basis=_cell(row, "evidence_basis", "textual"),
                evidence_class=_cell(row, "evidence_class", "C"),
                confidence=float(_cell(row, "confidence") or 0.5),
                date_start=int(_cell(row, "date_start")) if _cell(row, "date_start") else None,
                date_end=int(_cell(row, "date_end")) if _cell(row, "date_end") else None,
                uncertainty=_cell(row, "uncertainty"), alternative_group=_cell(row, "alternative_group") or None,
                tags=[tag.strip() for tag in _cell(row, "tags").split(";") if tag.strip()],
                review_status=_cell(row, "review_status", "needs_review"),
                created_by=_cell(row, "created_by", "manual"), model_used=_cell(row, "model_used"),
                response_id=_cell(row, "response_id"),
                source_sha256_at_creation=_cell(row, "source_sha256_at_creation"),
            )
            if upsert_claim(connection, claim):
                count += 1
    return count


def export_claims_csv(connection: sqlite3.Connection, destination: Path) -> None:
    rows = list_claims(connection)
    latest_review = {review["claim_id"]: review for review in list_reviews(connection)}
    fieldnames = [
        "id", "source_id", "subject", "property_name", "claim", "value_text", "value_number", "unit",
        "locator", "quotation", "evidence_basis", "evidence_class", "confidence", "date_start", "date_end",
        "uncertainty", "alternative_group", "tags", "review_status", "created_by", "model_used", "response_id",
        "source_sha256_at_creation", "latest_reviewer", "latest_reviewed_at", "latest_review_notes",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            review = latest_review.get(row["id"], {})
            payload = {name: row.get(name, "") for name in fieldnames}
            payload["tags"] = ";".join(row.get("tags", []))
            payload["latest_reviewer"] = review.get("reviewer", "")
            payload["latest_reviewed_at"] = review.get("reviewed_at", "")
            payload["latest_review_notes"] = review.get("notes", "")
            writer.writerow(payload)
