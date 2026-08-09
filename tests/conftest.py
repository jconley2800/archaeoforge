from __future__ import annotations

import json
from pathlib import Path

import pytest

from archaeoforge.config import default_config, write_config
from archaeoforge.db import connect, replace_pages, upsert_claim, upsert_source
from archaeoforge.models import EvidenceClaim, EvidenceClass, ReviewStatus, SourceRecord, SourceType
from archaeoforge.project import ProjectPaths, resolve_project
from archaeoforge.util import sha256_file


@pytest.fixture
def project_factory(tmp_path: Path):
    def factory(
        *,
        claim_status: ReviewStatus = ReviewStatus.needs_review,
        feature_status: ReviewStatus = ReviewStatus.needs_review,
        claim_date_start: int = -600,
        claim_date_end: int = -500,
        target_year: int = -570,
    ) -> ProjectPaths:
        root = tmp_path / f"project-{len(list(tmp_path.iterdir()))}"
        for directory in ("sources", "data", "assets", "prompts", "outputs"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        config = default_config("test-project", "Test Reconstruction", "Test Place", target_year, "test period")
        write_config(config, root / "project.yaml")
        source_path = root / "sources" / "source.txt"
        source_path.write_text("Measured wall width is 4 metres.\n", encoding="utf-8")
        project = resolve_project(root)
        connection = connect(project)
        source = SourceRecord(
            id="SRC-TEST",
            relative_path="sources/source.txt",
            title="Test source",
            source_type=SourceType.excavation_report,
            sha256=sha256_file(source_path),
            size_bytes=source_path.stat().st_size,
            mime_type="text/plain",
            local_copy=True,
        )
        upsert_source(connection, source)
        replace_pages(
            connection,
            source.id,
            [{"page_number": 1, "text_content": source_path.read_text(), "char_count": source_path.stat().st_size, "image_count": 0}],
        )
        claim = EvidenceClaim(
            id="EVID-TEST",
            source_id=source.id,
            subject="wall",
            property="width",
            claim="The wall width is 4 metres.",
            value_text="4 m",
            value_number=4.0,
            unit="m",
            locator="page 1",
            quotation="Measured wall width is 4 metres.",
            evidence_basis="textual",
            evidence_class=EvidenceClass.A,
            confidence=0.95,
            date_start=claim_date_start,
            date_end=claim_date_end,
            review_status=claim_status,
            created_by="test",
        )
        upsert_claim(connection, claim)
        connection.close()
        features = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "WALL-1",
                        "template": "wall",
                        "review_status": feature_status.value,
                        "evidence_class": "A",
                        "confidence": 0.95,
                        "date_start": -600,
                        "date_end": -500,
                        "evidence_ids": ["EVID-TEST"],
                        "params": {"width": 4, "height": 8, "material": "mudbrick"},
                    },
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
                }
            ],
        }
        (root / "data" / "features.geojson").write_text(json.dumps(features), encoding="utf-8")
        return project

    return factory
