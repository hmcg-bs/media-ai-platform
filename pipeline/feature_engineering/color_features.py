"""Extract color-based features from creative analysis."""

from __future__ import annotations

from typing import Any


def categorize_color(hex_color: str | None) -> str:
    """Categorize a hex color into a color name."""
    if not hex_color or not isinstance(hex_color, str):
        return "unknown"

    hex_color = hex_color.lstrip("#").lower()

    # Convert hex to RGB
    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    except (ValueError, IndexError):
        return "unknown"

    # Simple heuristic classification
    max_component = max(r, g, b)
    min_component = min(r, g, b)

    # Grayscale
    if max_component - min_component < 30:
        if max_component > 200:
            return "white"
        elif max_component < 50:
            return "black"
        else:
            return "gray"

    # Dominant color
    if r == max_component:
        return "red"
    elif g == max_component:
        return "green"
    elif b == max_component:
        return "blue"

    return "other"


def calculate_luminance(hex_color: str | None) -> float:
    """Calculate perceived luminance (0-1) of a hex color using WCAG formula."""
    if not hex_color or not isinstance(hex_color, str):
        return 0.5

    hex_color = hex_color.lstrip("#").lower()

    try:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
    except (ValueError, IndexError):
        return 0.5

    # WCAG luminance formula
    r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
    g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
    b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4

    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_warm_color(hex_color: str | None) -> bool:
    """Determine if a color is warm (red/orange/yellow range)."""
    if not hex_color or not isinstance(hex_color, str):
        return False

    hex_color = hex_color.lstrip("#").lower()

    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    except (ValueError, IndexError):
        return False

    # Warm if red component is high and blue is low
    return r > 150 and b < 150


def extract_color_features(
    dominant_hex: str | None,
    palette_vibrancy: float | None,
    contrast_ratio_type: str | None,
    background_style: str | None,
) -> dict[str, Any]:
    """Extract color-based features."""
    features = {
        "dominant_color": categorize_color(dominant_hex),
        "palette_vibrancy": palette_vibrancy or 0.5,
        "psychological_warmth_index": 1.0 if is_warm_color(dominant_hex) else 0.0,
        "contrast_ratio_type": contrast_ratio_type or "unknown",
        "background_style": background_style or "unknown",
    }

    return features


def calculate_contrast_luminance(fg_hex: str | None, bg_hex: str | None) -> float:
    """Calculate contrast ratio between two colors using WCAG formula."""
    if not fg_hex or not bg_hex:
        return 1.0

    l1 = calculate_luminance(fg_hex)
    l2 = calculate_luminance(bg_hex)

    lighter = max(l1, l2)
    darker = min(l1, l2)

    if darker + 0.05 == 0:
        return 1.0

    return (lighter + 0.05) / (darker + 0.05)
