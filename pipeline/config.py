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
    # Path to a service-account key JSON file. When set, GenAIClient
    # authenticates with it explicitly instead of falling back to user ADC
    # (`gcloud auth application-default login`) -- service accounts aren't
    # subject to Google's periodic interactive-reauth policy the way a
    # personal OAuth identity is, so this would be the permanent fix for the
    # "Reauthentication is needed" error recurring on every long-idle gap.
    # Empty string (default) preserves the previous ADC-only behavior.
    #
    # NOTE: this GCP org enforces constraints/iam.disableServiceAccountKeyCreation
    # (confirmed live, 2026-08-27) -- no downloadable key can be created here,
    # so this field stays for portability to a less-restricted project but
    # `impersonate_service_account` below is this org's actual working path.
    google_application_credentials_path: str = ""
    # Service account email to impersonate via the user's own already-
    # authenticated ADC (requires roles/iam.serviceAccountTokenCreator on
    # that service account, granted to the user) -- Google's own docs call
    # this "the preferred method for local development" specifically because
    # it avoids downloadable keys. Whether it also avoids the *periodic
    # reauth* prompt itself is unconfirmed -- impersonation still mints its
    # token from the user's own source credentials, so if the org's session-
    # reauth policy is enforced at the identity level, this may not fully
    # escape it. Worth trying regardless: even a reduced frequency is a win,
    # and it costs nothing when left empty (default, previous behavior).
    impersonate_service_account: str = ""

    # ─── Cognitive models (Gemini via Vertex AI or Replicate) ─────────
    # Cheap tier: marketing psychology / hook framework / vibe.
    # `gemini-2.0-flash-lite` 404s on this Vertex project (confirmed live,
    # 2026-08-27) -- this default went untested for most of this project's
    # life because enable_replicate_cognitive=True routed the cognitive
    # stage through Replicate instead, never actually calling Vertex direct.
    gemini_cheap_model: str = "gemini-2.5-flash-lite"
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

    # ─── Generation v1 (ad-image synthesis, wayfinder map #36) ─────────
    # Vertex-native, same google-genai/GenAIClient shape as the cognitive
    # stage — no new provider. Used for background/scene elements only;
    # faithful product-photo fidelity goes through Flux Kontext on
    # Replicate instead (a 4th scoped ADR-008-style exception — see
    # docs/meta-ad-image-model-stack.md and wayfinder issue #37/#36).
    # "Nano Banana Pro" (gemini-3.1-flash-image) 404s on this Vertex project
    # -- not yet rolled out here (confirmed live, 2026-08-27). Falls back to
    # gemini-2.5-flash-image, confirmed working; swap back once 3.1 is
    # available on this project without any other code change needed.
    gemini_image_model: str = "gemini-2.5-flash-image"
    flux_kontext_model: str = "black-forest-labs/flux-kontext-pro"
    # Round 6 (2026-08-29): mask-protected background inpainting, replacing
    # Flux Kontext's whole-image edit for the background/product step. Real
    # schemas confirmed live against Replicate's own API, not assumed --
    # flux-fill-pro takes an explicit `mask` (black=preserved, white=
    # inpainted); background-remover produces the alpha matte that mask is
    # built from. See pipeline/generation/masking.py for why this was needed
    # (product label text was getting garbled under Flux Kontext's full
    # re-render, since it has no mask input at all).
    # Community model -- confirmed live it 404s by bare name (unlike the
    # official black-forest-labs models above), needs a pinned :version like
    # qwen_vl_model/embedding_model do, per this file's own stated convention.
    background_remover_model: str = (
        "851-labs/background-remover:"
        "a029dff38972b5fda4ec5d75d7d1cd25aeff621d2cf4946a41055d7db66b80bc"
    )
    flux_fill_model: str = "black-forest-labs/flux-fill-pro"

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

    # ─── Landing-page scraping (Stage 4, tiered) ───────────────────────
    scrape_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    scrape_timeout_s: float = 10.0
    scrape_max_attempts: int = 4
    scrape_backoff_min_seconds: float = 3.0
    scrape_backoff_max_seconds: float = 60.0

    # Global bucket: shared by every request that is Shopify-hosted OR
    # not-yet-classified (i.e. almost all first touches of a domain — see
    # ingestion/rate_limiter.py). 2 req/s anchored to Shopify's documented
    # Admin API leaky-bucket rate, the only public reference point available;
    # confirmed necessary empirically — a burst of concurrent requests to
    # many different Shopify-hosted domains tripped a shared, platform-wide
    # block (unrelated domains failed together in the same window).
    shopify_global_rps: float = 2.0
    shopify_global_burst: int = 4

    # Light per-domain bucket, applied in addition to the global bucket, so
    # no single merchant gets hammered even when the global bucket has
    # headroom (protects the handful of high-volume repeated URLs).
    per_domain_rps: float = 0.5
    per_domain_burst: int = 2

    # ─── ZenRows managed scraping (JS rendering + proxy + anti-bot) ────
    # Supersedes the self-hosted TLS-impersonation approach — the tiered
    # scraper's rate limiter (above) fixed a volume-triggered block, but a
    # residual 45.5% of unique URLs still failed 429/403, broadly and
    # immediately across unrelated domains — a fingerprinting signature, not
    # volume. ZenRows handles that server-side instead of self-hosting it.
    # Also the only source for rating/rating_count at scale — Shopify's core
    # product.json schema has no review fields; those come from third-party
    # widgets (Loox/Yotpo/Judge.me) injected client-side.
    zenrows_api_key: str = ""
    zenrows_concurrency: int = 5
    zenrows_retries: int = 2
    zenrows_js_render: bool = True
    zenrows_wait_ms: int = 2000

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
