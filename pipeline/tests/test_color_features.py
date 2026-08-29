"""Tests for pipeline/feature_engineering/color_features.py — pure functions,
fully offline."""

from __future__ import annotations

from pipeline.feature_engineering.color_features import (
    categorize_color,
    compute_palette_vibrancy,
    extract_color_features,
    is_warm_color,
)


class TestComputePaletteVibrancy:
    def test_none_palette_returns_none(self) -> None:
        assert compute_palette_vibrancy(None) is None

    def test_empty_palette_returns_none(self) -> None:
        assert compute_palette_vibrancy([]) is None

    def test_single_color_returns_none(self) -> None:
        """A single color has no variance to compute a stdev from."""
        assert compute_palette_vibrancy(["#FF0000"]) is None

    def test_monochrome_palette_scores_near_zero(self) -> None:
        """Regression: the whole point of vibrancy is distinguishing a dull,
        near-monochrome palette from a mixed vivid/muted one — confirmed
        live against real Step 2 output shape (a grayscale palette)."""
        vibrancy = compute_palette_vibrancy(["#808080", "#909090", "#707070"])
        assert vibrancy is not None
        assert vibrancy < 0.05

    def test_mixed_saturation_palette_scores_higher(self) -> None:
        vibrancy = compute_palette_vibrancy(["#FF0000", "#808080", "#FFEEEE"])
        assert vibrancy is not None
        assert vibrancy > 0.1

    def test_malformed_hex_entries_skipped_not_crashed(self) -> None:
        # One bad entry shouldn't take down the whole computation.
        vibrancy = compute_palette_vibrancy(["#FF0000", "not-a-color", "#00FF00"])
        assert vibrancy is not None


class TestExtractColorFeatures:
    def test_real_step2_color_profile_data(self) -> None:
        """Regression: extractor.py used to hardcode
        dominant_hex="#808080"/palette_vibrancy=0.5/etc. — this confirms
        real Step 2 ColorProfile data flows through correctly instead."""
        features = extract_color_features(
            dominant_hex="#CFEDDB",
            palette_vibrancy=compute_palette_vibrancy(["#CFEDDB", "#D8F89E", "#59B5BA"]),
            contrast_ratio_type="High",
            background_style="Studio",
        )
        assert features["dominant_color"] == categorize_color("#CFEDDB")
        assert features["contrast_ratio_type"] == "High"
        assert features["background_style"] == "Studio"
        assert features["psychological_warmth_index"] == (1.0 if is_warm_color("#CFEDDB") else 0.0)

    def test_none_inputs_fall_back_to_defaults(self) -> None:
        features = extract_color_features(
            dominant_hex=None,
            palette_vibrancy=None,
            contrast_ratio_type=None,
            background_style=None,
        )
        assert features["dominant_color"] == "unknown"
        assert features["palette_vibrancy"] == 0.5
        assert features["contrast_ratio_type"] == "unknown"
        assert features["background_style"] == "unknown"
