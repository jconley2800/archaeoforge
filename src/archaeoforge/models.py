from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigModel(BaseModel):
    """Base class for user-authored project configuration.

    Configuration typos must fail validation instead of silently falling back to a
    default value.
    """

    model_config = ConfigDict(extra="forbid")


class EvidenceClass(str, Enum):  # noqa: UP042 - keep established str(Enum) behavior
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ReviewStatus(str, Enum):  # noqa: UP042 - keep established str(Enum) behavior
    draft = "draft"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"


class SourceType(str, Enum):  # noqa: UP042 - keep established str(Enum) behavior
    excavation_report = "excavation_report"
    peer_reviewed_article = "peer_reviewed_article"
    ancient_text = "ancient_text"
    survey_scan = "survey_scan"
    museum_object = "museum_object"
    archival_plan = "archival_plan"
    photograph = "photograph"
    map = "map"
    book = "book"
    dataset = "dataset"
    web_reference = "web_reference"
    other = "other"


class FinishMode(str, Enum):  # noqa: UP042 - Typer and serialized config rely on str(Enum)
    precise_object_edit = "precise_object_edit"
    historical_scene = "historical_scene"


class ProjectIdentity(ConfigModel):
    id: str
    title: str
    place_name: str
    target_year: int = Field(description="BCE is negative, CE is positive. There is no year zero.")
    target_year_label: str
    description: str = ""
    spatial_reference: str = "LOCAL_METERS"
    units: Literal["m"] = "m"
    origin_note: str = "Local engineering coordinates. +X east, +Y north, +Z up."

    @field_validator("target_year")
    @classmethod
    def no_year_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Historical dating has no year zero. Use -1 for 1 BCE or 1 for 1 CE.")
        return value


class EvidencePolicy(ConfigModel):
    authoritative_statuses: list[ReviewStatus] = Field(default_factory=lambda: [ReviewStatus.approved])
    preview_statuses: list[ReviewStatus] = Field(
        default_factory=lambda: [ReviewStatus.approved, ReviewStatus.needs_review, ReviewStatus.draft]
    )
    require_evidence_for_geometry: bool = True
    require_locator_for_classes: list[EvidenceClass] = Field(
        default_factory=lambda: [EvidenceClass.A, EvidenceClass.B]
    )
    require_local_copy_for_authoritative: bool = False
    conservative_feature_classification: bool = True


class AIConfig(ConfigModel):
    enabled: bool = False
    extraction_model: str = "gpt-5.6-terra"
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    pdf_detail: Literal["low", "high", "auto"] = "high"
    max_source_mb: int = 48
    max_text_chars_per_request: int = 60_000
    use_direct_pdf_input: bool = True
    image_model: Literal["gpt-image-2", "gpt-image-2-2026-04-21"] = "gpt-image-2"
    image_quality: Literal["low", "medium", "high"] = "high"
    # Retained so existing project files remain valid. GPT Image 2 always applies
    # high input fidelity automatically and does not accept this API parameter.
    image_input_fidelity: Literal["high"] = "high"
    image_size: str = "auto"
    finish_enabled: bool = False
    finish_backend: Literal["interactive_handoff", "openai_api"] = "interactive_handoff"
    finish_mode: FinishMode = FinishMode.precise_object_edit
    geometry_audit_enabled: bool = True

    @field_validator("image_size")
    @classmethod
    def valid_image_size(cls, value: str) -> str:
        if value == "auto":
            return value
        try:
            width_text, height_text = value.lower().split("x", maxsplit=1)
            width, height = int(width_text), int(height_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("image_size must be 'auto' or WIDTHxHEIGHT") from exc
        if width <= 0 or height <= 0:
            raise ValueError("image_size dimensions must be positive")
        return f"{width}x{height}"


class CameraConfig(ConfigModel):
    """Camera placement.

    With ``auto_frame`` enabled the camera is solved from the compiled scene bounds so the
    whole reconstruction fits the frame; ``location`` and ``target`` are then only used as
    the manual fallback.
    """

    auto_frame: bool = True
    azimuth_degrees: float = Field(
        default=145.0, description="Compass bearing of the camera from the site. 0 is north, 90 is east."
    )
    elevation_degrees: float = Field(default=24.0, ge=1.0, le=89.0)
    margin: float = Field(default=1.06, ge=1.0, description="Framing slack. 1.0 fits the bounds exactly.")
    target_height_bias: float = Field(
        default=0.0, description="Shifts the aim point up as a fraction of the site height."
    )
    frame_includes_context: bool = Field(
        default=False, description="Include terrain and context sheets when solving the framing."
    )
    location: tuple[float, float, float] = (320.0, -420.0, 260.0)
    target: tuple[float, float, float] = (0.0, 60.0, 20.0)
    lens_mm: float = 48.0
    orthographic: bool = False
    ortho_scale: float = 600.0


class SunConfig(ConfigModel):
    """Sun placement.

    ``elevation_degrees`` and ``azimuth_degrees`` are the normal controls. Setting
    ``elevation_degrees`` to null falls back to the raw Blender Euler in ``rotation_degrees``.
    """

    elevation_degrees: float | None = 42.0
    azimuth_degrees: float = Field(
        default=215.0, description="Compass bearing of the sun. 0 is north, 90 is east, 180 is south."
    )
    rotation_degrees: tuple[float, float, float] | None = None
    energy: float = 2.6
    angle_degrees: float = 1.6


class SkyConfig(ConfigModel):
    """World background and ambient fill.

    Blender ignores ``World.color`` for any world that uses nodes, which is every world it
    creates, so the sky has to be a node tree. Without it the unlit side of every wall
    collapses to a flat grey.
    """

    procedural_sky: bool = True
    strength: float = 0.7
    turbidity: float = Field(default=2.6, ge=1.0, le=10.0)
    ground_albedo: float = Field(default=0.32, ge=0.0, le=1.0)
    air_density: float = Field(default=1.0, ge=0.0)
    dust_density: float = Field(default=1.5, ge=0.0)
    color: tuple[float, float, float, float] = (0.28, 0.42, 0.62, 1.0)


class BlenderConfig(ConfigModel):
    executable: str = "blender"
    engine: str = "BLENDER_EEVEE"
    resolution_x: int = 1024
    resolution_y: int = 1024
    resolution_percentage: int = 100
    samples: int = 64
    transparent_background: bool = False
    view_transform: str = "AgX"
    look: str = ""
    exposure: float = -1.0
    shadows: bool = True
    raytracing: bool = True
    shadow_ray_count: int = 4
    shadow_step_count: int = 6
    camera: CameraConfig = Field(default_factory=CameraConfig)
    sun: SunConfig = Field(default_factory=SunConfig)
    sky: SkyConfig = Field(default_factory=SkyConfig)
    render_mode: Literal["realistic", "evidence"] = "realistic"
    save_blend: bool = True
    render_passes: bool = True


class GISConfig(ConfigModel):
    target_crs: str = "EPSG:32638"
    georeference_transform: Literal["affine", "polynomial2", "tps"] = "affine"
    resampling: Literal["near", "bilinear", "cubic"] = "cubic"


class ProjectConfig(ConfigModel):
    schema_version: int = 1
    project: ProjectIdentity
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    ai: AIConfig = Field(default_factory=AIConfig)
    blender: BlenderConfig = Field(default_factory=BlenderConfig)
    gis: GISConfig = Field(default_factory=GISConfig)


class SourceRecord(BaseModel):
    id: str
    relative_path: str | None = None
    title: str
    authors: str = ""
    publication_year: int | None = None
    source_type: SourceType = SourceType.other
    url: str = ""
    license: str = ""
    sha256: str = ""
    size_bytes: int = 0
    mime_type: str = ""
    local_copy: bool = False
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceClaimDraft(BaseModel):
    subject: str
    property: str
    claim: str
    value_text: str = ""
    value_number: float | None = None
    unit: str | None = None
    locator: str
    quotation: str = ""
    evidence_basis: Literal["textual", "visual", "mixed", "metadata", "comparative"] = "textual"
    evidence_class: EvidenceClass
    confidence: float = Field(ge=0.0, le=1.0)
    date_start: int | None = None
    date_end: int | None = None
    uncertainty: str = ""
    alternative_group: str | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def order_dates(self) -> EvidenceClaimDraft:
        if self.date_start is not None and self.date_end is not None and self.date_start > self.date_end:
            raise ValueError("date_start must be less than or equal to date_end")
        return self


class EvidenceClaim(EvidenceClaimDraft):
    id: str
    source_id: str
    source_sha256_at_creation: str = ""
    review_status: ReviewStatus = ReviewStatus.needs_review
    created_by: str = "manual"
    model_used: str = ""
    response_id: str = ""


class ExtractionBatch(BaseModel):
    document_summary: str = ""
    claims: list[EvidenceClaimDraft] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    object_type: Literal["project", "source", "claim", "feature", "geometry", "pipeline"]
    object_id: str = ""
    remediation: str = ""


class DriftAssessment(BaseModel):
    geometry_preservation_score: float = Field(ge=0.0, le=1.0)
    camera_preserved: bool
    major_silhouettes_preserved: bool
    object_placement_preserved: bool
    detected_changes: list[str] = Field(default_factory=list)
    recommendation: Literal["accept", "review", "reject"]
