"""Extract text-based features from ad copy."""

from __future__ import annotations

import re
from typing import Any


def extract_text_length_features(title: str | None, body: str | None, usp: str | None) -> dict[str, int]:
    """Extract length-based features from text fields."""
    return {
        "title_length": len(title or ""),
        "body_length": len(body or ""),
        "usp_length": len(usp or ""),
    }


def extract_language_signals(text: str | None) -> dict[str, bool | int]:
    """Extract urgency, social proof, and other language signals."""
    if not text:
        return {
            "urgency_language": False,
            "social_proof_language": False,
        }

    text_lower = text.lower()

    urgency_keywords = {"limited", "today", "now", "sale", "urgent", "hurry", "quick", "fast", "offer ends"}
    social_proof_keywords = {"reviews", "rating", "bestseller", "trusted", "loved", "customers", "verified", "#1"}

    return {
        "urgency_language": any(kw in text_lower for kw in urgency_keywords),
        "social_proof_language": any(kw in text_lower for kw in social_proof_keywords),
    }


def extract_cta_features(cta_text: str | None) -> dict[str, Any]:
    """Extract CTA (call-to-action) features."""
    if not cta_text:
        return {
            "has_cta_text": False,
            "cta_type": "none",
        }

    cta_lower = cta_text.lower().strip()

    # Map CTA text to type
    cta_type_map = {
        "shop now": "shop_now",
        "buy now": "buy_now",
        "learn more": "learn_more",
        "get started": "get_started",
        "sign up": "sign_up",
        "download": "download",
        "visit": "visit",
        "order": "order",
    }

    detected_type = "other"
    for pattern, cta_type in cta_type_map.items():
        if pattern in cta_lower:
            detected_type = cta_type
            break

    return {
        "has_cta_text": True,
        "cta_type": detected_type,
    }


def extract_positioning_features(
    price: float | None,
    category_median_price: float | None,
    text: str | None,
) -> dict[str, bool | str]:
    """Extract positioning-based features (premium, seasonal, etc.)."""
    features = {
        "premium_positioning": False,
        "season_positioning": "none",
    }

    # Premium positioning: high price or premium language
    if price and category_median_price and price >= category_median_price * 1.5:
        features["premium_positioning"] = True

    if text:
        text_lower = text.lower()
        if any(kw in text_lower for kw in {"premium", "luxury", "exclusive", "elite"}):
            features["premium_positioning"] = True

        # Seasonal positioning detection
        if any(kw in text_lower for kw in {"christmas", "holiday", "xmas", "festive"}):
            features["season_positioning"] = "holiday"
        elif any(kw in text_lower for kw in {"summer", "beach", "warm", "hot"}):
            features["season_positioning"] = "summer"
        elif any(kw in text_lower for kw in {"back to school", "fall", "autumn"}):
            features["season_positioning"] = "back_to_school"

    return features
