from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from .db import connect, get_source, list_pages, list_sources, upsert_claim
from .models import EvidenceClaim, EvidenceClaimDraft, EvidenceClass, ExtractionBatch, ReviewStatus
from .openai_client import new_official_openai_client
from .project import ProjectPaths, load_config
from .util import sha256_text, text_windows

if TYPE_CHECKING:
    from openai import OpenAI


SYSTEM_PROMPT = """You extract archaeological reconstruction evidence from supplied sources.

Rules:
1. Extract only claims explicitly supported by the supplied source. Do not fill gaps from general knowledge.
2. Separate observation, textual assertion, reconstruction, comparison, and speculation.
3. Give the precise page, figure, plate, object number, plan label, or scan locator when visible.
4. Preserve a short exact quotation for textual claims when possible. Never fabricate a quotation.
5. Convert dimensions into a numeric value and unit only when the source provides enough information.
6. Keep mutually inconsistent alternatives as separate claims with the same alternative_group.
7. Use BCE negative and CE positive. There is no year zero.
8. Class A means directly measured or observed. B means strongly constrained reconstruction. C means comparative inference. D means cinematic completion.
9. If a page or image is illegible, omit the claim and describe the limitation in warnings.
"""


def _new_client(project: ProjectPaths) -> OpenAI:
    return new_official_openai_client(project)


def _source_path(project: ProjectPaths, source: dict[str, Any]) -> Path | None:
    if not source.get("relative_path"):
        return None
    path = (project.root / source["relative_path"]).resolve()
    return path if path.exists() else None


def _normalise_draft(draft: EvidenceClaimDraft) -> EvidenceClaimDraft:
    if draft.evidence_class == EvidenceClass.A:
        draft.evidence_class = EvidenceClass.B
        suffix = "Automatically downgraded from A to B pending human verification against the primary record."
        draft.uncertainty = f"{draft.uncertainty} {suffix}".strip()
    draft.confidence = min(draft.confidence, 0.65 if draft.evidence_basis == "visual" else 0.85)
    return draft


def _save_batch(
    connection: Any, source_id: str, batch: ExtractionBatch, *, model: str, response_id: str
) -> int:
    inserted = 0
    for draft in batch.claims:
        draft = _normalise_draft(draft)
        payload = json.dumps(draft.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        claim = EvidenceClaim(
            **draft.model_dump(),
            id=f"CLM-{source_id.replace('SRC-', '')[:8]}-{sha256_text(payload)[:10].upper()}",
            source_id=source_id,
            review_status=ReviewStatus.needs_review,
            created_by="openai_structured_extraction",
            model_used=model,
            response_id=response_id,
        )
        if upsert_claim(connection, claim):
            inserted += 1
    return inserted


def _request_kwargs(config: Any) -> dict[str, Any]:
    return {"reasoning": {"effort": config.ai.reasoning_effort}}


def _extract_pdf(client: OpenAI, project: ProjectPaths, path: Path) -> tuple[ExtractionBatch, str]:
    config = load_config(project)
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > config.ai.max_source_mb:
        raise ValueError(f"{path.name} is {size_mb:.1f} MB; configured limit is {config.ai.max_source_mb} MB.")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    response = client.responses.parse(
        model=config.ai.extraction_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file", "filename": path.name,
                        "file_data": f"data:application/pdf;base64,{encoded}",
                        "detail": config.ai.pdf_detail,
                    },
                    {
                        "type": "input_text",
                        "text": (
                            f"Target: {config.project.place_name}, {config.project.target_year_label}. "
                            "Extract spatial, dimensional, material, chronological, environmental, and decorative evidence."
                        ),
                    },
                ],
            },
        ],
        text_format=ExtractionBatch,
        **_request_kwargs(config),
    )
    if response.output_parsed is None:
        raise RuntimeError("The model returned no structured extraction result.")
    return response.output_parsed, response.id


def _extract_image(client: OpenAI, project: ProjectPaths, path: Path) -> tuple[ExtractionBatch, str]:
    config = load_config(project)
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        path.suffix.lower()
    )
    if mime:
        image_bytes = path.read_bytes()
    else:
        with Image.open(path) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            image_bytes = buffer.getvalue()
        mime = "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = client.responses.parse(
        model=config.ai.extraction_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Target: {config.project.place_name}, {config.project.target_year_label}. "
                            "Extract only visually supportable claims. Do not invent measurements without a scale or label."
                        ),
                    },
                    {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "original"},
                ],
            },
        ],
        text_format=ExtractionBatch,
        **_request_kwargs(config),
    )
    if response.output_parsed is None:
        raise RuntimeError("The model returned no structured extraction result.")
    return response.output_parsed, response.id


def _extract_text(
    client: OpenAI, project: ProjectPaths, source: dict[str, Any], pages: list[dict[str, Any]]
) -> list[tuple[ExtractionBatch, str]]:
    config = load_config(project)
    results: list[tuple[ExtractionBatch, str]] = []
    pairs = [(int(page["page_number"]), page["text_content"]) for page in pages if page["text_content"].strip()]
    for text, page_numbers in text_windows(pairs, config.ai.max_text_chars_per_request):
        response = client.responses.parse(
            model=config.ai.extraction_model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Source: {source['title']}\nTarget: {config.project.place_name}, "
                        f"{config.project.target_year_label}.\nPages: {page_numbers}.\n{text}"
                    ),
                },
            ],
            text_format=ExtractionBatch,
            **_request_kwargs(config),
        )
        if response.output_parsed is not None:
            results.append((response.output_parsed, response.id))
    return results


def extract_project(project: ProjectPaths, *, source_id: str | None = None) -> dict[str, Any]:
    config = load_config(project)
    if not config.ai.enabled:
        raise RuntimeError("AI extraction is disabled in project.yaml.")
    client = _new_client(project)
    connection = connect(project)
    sources = [get_source(connection, source_id)] if source_id else list_sources(connection)
    sources = [source for source in sources if source]
    result: dict[str, Any] = {
        "sources_considered": len(sources), "sources_processed": 0, "claims_inserted": 0, "warnings": [],
    }
    try:
        for source in sources:
            path = _source_path(project, source)
            if path is None:
                result["warnings"].append(f"{source['id']}: no local source file; skipped.")
                continue
            try:
                suffix = path.suffix.lower()
                batches: list[tuple[ExtractionBatch, str]] = []
                if suffix == ".pdf" and config.ai.use_direct_pdf_input:
                    batches.append(_extract_pdf(client, project, path))
                elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
                    batches.append(_extract_image(client, project, path))
                else:
                    batches.extend(_extract_text(client, project, source, list_pages(connection, source["id"])))
                if not batches:
                    result["warnings"].append(f"{source['id']}: no supported extractable content; skipped.")
                    continue
                for batch, response_id in batches:
                    result["claims_inserted"] += _save_batch(
                        connection, source["id"], batch, model=config.ai.extraction_model, response_id=response_id
                    )
                    result["warnings"].extend(f"{source['id']}: {warning}" for warning in batch.warnings)
                result["sources_processed"] += 1
            except Exception as exc:
                result["warnings"].append(f"{source['id']}: {type(exc).__name__}: {exc}")
    finally:
        connection.close()
    return result
