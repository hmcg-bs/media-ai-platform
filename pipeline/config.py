"""Single source of configuration for the Step 2 pipeline.

Every other module imports settings from here. No ``os.environ`` access and no
hardcoded GCP identifiers anywhere else in the codebase.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pipeline configuration, sourced from environment / ``.env``."""

    # ─── GCP core ─────────────────────────────────────────────────────
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    vertex_location: str = "us-central1"

    # ─── Cognitive models (Gemini via Vertex AI or Replicate) ─────────
    # Cheap tier: marketing psychology / hook framework / vibe.
    gemini_cheap_model: str = "gemini-2.0-flash-lite"
    # Deep tier: objects, spatial relationships, human micro-detail.
    gemini_deep_model: str = "gemini-2.5-flash"
    # Replicate Gemini model (alternative to Vertex AI).
    replicate_gemini_model: str = "google/gemini-3-flash"
    enable_replicate_cognitive: bool = False  # if True, use Replicate instead of Vertex

    # ─── Layered colour + VLM imagery + embeddings (ADR-008) ──────────
    # Prototyped on Replicate now, behind swappable clients; Vertex is the target.
    model_provider: str = "replicate"           # replicate | (future) vertex
    replicate_api_token: str = ""
    # Official models run by name; community models need a pinned :version.
    qwen_layers_model: str = "qwen/qwen-image-layered"
    qwen_vl_model: str = (
        "lucataco/qwen3-vl-8b-instruct:"
        "39e893666996acf464cff75688ad49ac95ef54e9f1c688fbc677330acc478e11"
    )
    embedding_model: str = (
        "zsxkib/embedding-gemma-300m:"
        "d753bd5a898a96666f233f9a33ab1c3fe6527a7be308e1cc9fdcda46abf3e233"
    )
    qwen_num_layers: int = 4
    embedding_dim: int = 768
    replicate_timeout_s: int = 300   # generous: community models cold-start slowly
    # Step 1 ingestion — Apify Meta Ad Library scraper.
    apify_api_token: str = ""
    apify_actor_id: str = "curious_coder/facebook-ads-library-scraper"

    # Datalab copy/positioning (plain convert; keeps the headline Style Preserver drops).
    datalab_api_key: str = ""
    enable_datalab_copy: bool = False  # replace Cloud Vision OCR with Datalab copy

    # Optional paid stages — default OFF so a plain orchestrator run stays offline/cheap.
    enable_layer_color: bool = False   # Qwen layers → background ColorProfile
    enable_imagery: bool = False       # Qwen3-VL → imagery_description
    enable_embeddings: bool = False    # embedding-gemma → <ad_id>.embeddings.json
    imagery_prompt: str = (
        "Describe the product and visual imagery in this advertisement — the objects, "
        "people, setting, and style. Do NOT transcribe or list the on-screen text."
    )

    # ─── Deterministic vision params ──────────────────────────────────
    kmeans_clusters: int = 3
    kmeans_perimeter_pct: float = 0.10          # outermost 10% = background sample
    max_image_dimension_px: int = 1024          # resize longest edge before Vertex

    # ─── Retry / timeouts ─────────────────────────────────────────────
    api_max_attempts: int = 3
    api_backoff_min_seconds: float = 2.0
    api_backoff_max_seconds: float = 10.0

    # ─── Runtime ──────────────────────────────────────────────────────
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
