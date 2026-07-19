"""Configuration module for Media AI Platform."""

from src.config.settings import settings
from src.config.model_routing import get_model

__all__ = ["settings", "get_model"]
