"""Master JSON Schema for Step 2 extraction output, as Pydantic v2 models.

These models are the **contract** between Step 2 and the (future) Step 3 join.
They serve double duty:

1. ``PipelineContext`` is the mutable accumulator passed between stages.
2. The cognitive sub-models (``MarketingPsychology``, ``SpatialAndNestedObjects``,
   ``HumanModelAnalysis``) are handed to Gemini as ``response_schema`` for
   structured output.

Every field is optional with a safe default so a failed stage degrades
gracefully (null/empty) without breaking validation of the final document.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# ─── Enums ────────────────────────────────────────────────────────────


class HookFramework(StrEnum):
    """Marketing hook framework. UNKNOWN is the graceful fallback."""

    PAS = "PAS"
    AIDA = "AIDA"
    BEFORE_AFTER = "Before/After"
    TESTIMONIAL = "Testimonial"
    DIRECT_OFFER = "Direct Offer"
    SOCIAL_PROOF = "Social Proof"
    UNKNOWN = "Unknown"


# ─── Stage 1: technical metadata (deterministic) ──────────────────────


class TechnicalMetadata(BaseModel):
    width: int = 0
    height: int = 0
    aspect_ratio: str = ""          # e.g. "1:1", "9:16"
    file_type: str = ""


# ─── Stage 2: typography hierarchy (Cloud Vision OCR) ─────────────────


class TextBlock(BaseModel):
    text: str = ""
    canvas_coverage_percentage: float = 0.0


class TypographyHierarchy(BaseModel):
    primary_headline: TextBlock = Field(default_factory=TextBlock)
    secondary_copy: list[TextBlock] = Field(default_factory=list)
    headline_to_subtext_scale_ratio: float = 0.0


# ─── Stage 2: copywriting features (quantitative, from Datalab) ───────


class CopywritingFeatures(BaseModel):
    """Numeric/categorical copywriting signals derived from the ad's text.

    All fields are ML-ready (numbers, booleans, or a single categorical label).
    Deliberately performance-free — this describes the copy, never how it did.
    """

    copy_block_count: int = 0
    total_word_count: int = 0
    total_char_count: int = 0
    headline_word_count: int = 0
    headline_char_count: int = 0
    avg_words_per_block: float = 0.0
    uppercase_ratio: float = 0.0            # share of letters that are UPPERCASE
    exclamation_count: int = 0
    question_count: int = 0
    emoji_count: int = 0
    reading_grade_level: float = 0.0        # Flesch–Kincaid grade (deterministic)
    hook_type: str = ""                     # raw Datalab hook_type (pre-mapping)
    cta_present: bool = False
    claimed_benefits_count: int = 0
    has_price: bool = False
    has_badge: bool = False
    has_legal: bool = False


# ─── Stage 2: placement / positioning geometry (from Datalab bboxes) ──


class ElementPlacement(BaseModel):
    """One element's normalized (0–1) position and size on the canvas."""

    role: str = ""                 # semantic_role (copy) or asset_type (asset)
    kind: str = ""                 # "copy" | "asset"
    x_center: float = 0.0
    y_center: float = 0.0
    width: float = 0.0
    height: float = 0.0
    coverage_percentage: float = 0.0
    zone: str = ""                 # top | middle | bottom


class Placement(BaseModel):
    """Where copy and visual assets sit on the canvas (the 'positioning' component).

    Distinct from Composition (z-order/overlap) and object relationships.
    """

    elements: list[ElementPlacement] = Field(default_factory=list)
    copy_canvas_coverage: float = 0.0       # summed copy area / canvas
    asset_canvas_coverage: float = 0.0      # summed asset area / canvas
    whitespace_ratio: float = 0.0           # 1 − union-ish element coverage
    copy_vs_image_balance: float = 0.0      # copy_cov / (copy_cov + asset_cov)
    text_alignment: str = ""                # left | center | right | mixed
    headline_x_center: float = 0.0
    headline_y_center: float = 0.0
    headline_zone: str = ""                 # top | middle | bottom
    n_blocks_top: int = 0
    n_blocks_middle: int = 0
    n_blocks_bottom: int = 0


# ─── Stage 3: colour profile (OpenCV K-Means) ─────────────────────────


class ColorProfile(BaseModel):
    background_hex: str = ""
    background_style: str = ""       # e.g. Studio, Gradient, Transparent
    dominant_hex_palette: list[str] = Field(default_factory=list)
    contrast_ratio_type: str = ""    # e.g. High, Low, Monochromatic


# ─── Stage 4: product verification (DEFERRED — always null in v1) ──────


class ProductVerification(BaseModel):
    landing_page_url: str | None = None
    is_visually_verified_match: bool | None = None
    verification_confidence_score: float = 0.0


# ─── Stage 5: cognitive — spatial / objects (deep Gemini tier) ────────


class PrimaryProduct(BaseModel):
    name: str = ""
    visual_state: str = ""           # e.g. Closed, Open, In-Use


class SecondaryProp(BaseModel):
    name: str = ""
    type: str = ""                   # e.g. Environment, Accessory


class ObjectRelationship(BaseModel):
    subject: str = ""
    relationship_action: str = ""    # e.g. writing_on, pouring_into
    object: str = ""


class TextureDemonstration(BaseModel):
    visible: bool = False
    texture_type: str = ""           # e.g. Liquid Smear, Powder Dust, Foam


class SpatialAndNestedObjects(BaseModel):
    primary_product: PrimaryProduct = Field(default_factory=PrimaryProduct)
    secondary_props: list[SecondaryProp] = Field(default_factory=list)
    object_relationships: list[ObjectRelationship] = Field(default_factory=list)
    texture_demonstration: TextureDemonstration = Field(
        default_factory=TextureDemonstration
    )


# ─── Stage 5: cognitive — human model analysis (deep Gemini tier) ─────


class HumanDetail(BaseModel):
    estimated_demographic: str = ""
    action_performed: str = ""
    micro_expression: str = ""
    wardrobe_style: str = ""
    environmental_modifiers: list[str] = Field(default_factory=list)


class HumanModelAnalysis(BaseModel):
    human_presence: bool = False
    model_count: int = 0
    details: list[HumanDetail] = Field(default_factory=list)


# ─── Stage 5: cognitive — marketing psychology (cheap Gemini tier) ────


class MarketingPsychology(BaseModel):
    hook_framework: HookFramework = HookFramework.UNKNOWN
    primary_value_proposition: str = ""
    authority_flags: list[str] = Field(default_factory=list)
    emoji_count: int = 0
    reading_grade_level: str = ""


# ─── Top-level document ───────────────────────────────────────────────


class ExtractionResult(BaseModel):
    """The complete Step 2 output document for a single ad creative."""

    ad_id: str = ""
    technical_metadata: TechnicalMetadata = Field(default_factory=TechnicalMetadata)
    color_profile: ColorProfile = Field(default_factory=ColorProfile)
    typography_hierarchy: TypographyHierarchy = Field(
        default_factory=TypographyHierarchy
    )
    copywriting_features: CopywritingFeatures = Field(
        default_factory=CopywritingFeatures
    )
    placement: Placement = Field(default_factory=Placement)
    product_verification: ProductVerification = Field(
        default_factory=ProductVerification
    )
    spatial_and_nested_objects: SpatialAndNestedObjects = Field(
        default_factory=SpatialAndNestedObjects
    )
    human_model_analysis: HumanModelAnalysis = Field(
        default_factory=HumanModelAnalysis
    )
    marketing_psychology: MarketingPsychology = Field(
        default_factory=MarketingPsychology
    )
    # Free-text imagery description (Qwen3-VL, ADR-008) — the alternative imagery
    # path to the Gemini deep tier's structured spatial_and_nested_objects.
    imagery_description: str = ""

    def flatten_features(self) -> dict[str, object]:
        """Flatten the copywriting + positioning signals into one numeric ML row.

        Scalars only (numbers, bools, single categorical labels) — ready for a
        dataframe / BigQuery. Contains NO performance/label field by design;
        Performance joins downstream in Step 3, never inside Extraction.
        """
        cw = self.copywriting_features
        pl = self.placement
        return {
            "ad_id": self.ad_id,
            "aspect_ratio": self.technical_metadata.aspect_ratio,
            # copywriting
            "copy_block_count": cw.copy_block_count,
            "total_word_count": cw.total_word_count,
            "total_char_count": cw.total_char_count,
            "headline_word_count": cw.headline_word_count,
            "headline_char_count": cw.headline_char_count,
            "avg_words_per_block": cw.avg_words_per_block,
            "uppercase_ratio": cw.uppercase_ratio,
            "exclamation_count": cw.exclamation_count,
            "question_count": cw.question_count,
            "emoji_count": cw.emoji_count,
            "reading_grade_level": cw.reading_grade_level,
            "hook_framework": self.marketing_psychology.hook_framework.value,
            "cta_present": int(cw.cta_present),
            "claimed_benefits_count": cw.claimed_benefits_count,
            "has_price": int(cw.has_price),
            "has_badge": int(cw.has_badge),
            "has_legal": int(cw.has_legal),
            "headline_to_subtext_scale_ratio": (
                self.typography_hierarchy.headline_to_subtext_scale_ratio
            ),
            # positioning / placement
            "copy_canvas_coverage": pl.copy_canvas_coverage,
            "asset_canvas_coverage": pl.asset_canvas_coverage,
            "whitespace_ratio": pl.whitespace_ratio,
            "copy_vs_image_balance": pl.copy_vs_image_balance,
            "text_alignment": pl.text_alignment,
            "headline_x_center": pl.headline_x_center,
            "headline_y_center": pl.headline_y_center,
            "headline_zone": pl.headline_zone,
            "n_blocks_top": pl.n_blocks_top,
            "n_blocks_middle": pl.n_blocks_middle,
            "n_blocks_bottom": pl.n_blocks_bottom,
        }


class PipelineContext(BaseModel):
    """Mutable state carried through the stage chain.

    Wraps the ``ExtractionResult`` being built plus the inputs and per-run
    bookkeeping each stage needs (image path, raw bytes, stage failures).
    """

    ad_id: str
    image_path: str
    image_bytes: bytes | None = None
    result: ExtractionResult = Field(default_factory=ExtractionResult)
    failed_stages: list[str] = Field(default_factory=list)

    # Transient inter-stage working state (NOT part of the output document).
    # OCR bounding boxes (vertex lists) passed from Stage 2 to Stage 3 so the
    # colour stage can mask text pixels before clustering.
    ocr_boxes: list[list[tuple[int, int]]] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def model_post_init(self, _context: object) -> None:
        # Keep the result's ad_id in sync with the context.
        self.result.ad_id = self.ad_id
