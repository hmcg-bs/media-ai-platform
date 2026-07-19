"""Settings and environment configuration using Pydantic BaseSettings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings read from .env file."""

    # ─── AI Providers ─────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    replicate_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # ─── Model Routing Defaults ───────────────────────────────────────
    default_copy_model: str = "claude-sonnet-4-6"
    default_vision_model: str = "gpt-4o"
    default_embedding_model: str = "text-embedding-3-small"
    critique_model: str = "claude-sonnet-4-6"
    fast_model: str = "gpt-4o-mini"

    # ─── Database ─────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/media_ai"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""

    # ─── Meta Ad Library API ──────────────────────────────────────────
    meta_access_token: str = ""
    meta_app_id: str | None = None

    # ─── Application ──────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"

    # ─── Optional: LangSmith Observability ────────────────────────────
    langchain_api_key: str | None = None
    langchain_tracing_v2: bool = False
    langchain_project: str = "media-ai-platform"

    # ─── Optional: Redis (Celery job queue, phase 2) ──────────────────
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )


# Global settings instance
settings = Settings()
