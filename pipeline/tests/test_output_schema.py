"""Tests for pipeline/models/output_schema.py — Pydantic model coercion rules,
fully offline."""

from __future__ import annotations

from pipeline.models.output_schema import MarketingPsychology


class TestMarketingPsychologyReadingGradeLevelCoercion:
    """Regression: the unconstrained Replicate cognitive-stage prompt (no real
    JSON-schema enforcement) returns this field in unpredictable shapes —
    confirmed live: bare numbers (7, 8, 0), "Grade 5", "N/A", "6th Grade".
    A raw type mismatch used to fail validation for the whole
    MarketingPsychology object, silently discarding hook_framework and every
    other cheap-tier field along with it. Coercion must accept anything."""

    def test_bare_int_coerced_to_str(self) -> None:
        mp = MarketingPsychology(reading_grade_level=8)
        assert mp.reading_grade_level == "8"

    def test_bare_float_coerced_to_str(self) -> None:
        mp = MarketingPsychology(reading_grade_level=7.5)
        assert mp.reading_grade_level == "7.5"

    def test_grade_prefixed_string_passes_through(self) -> None:
        mp = MarketingPsychology(reading_grade_level="Grade 5")
        assert mp.reading_grade_level == "Grade 5"

    def test_na_string_passes_through(self) -> None:
        mp = MarketingPsychology(reading_grade_level="N/A")
        assert mp.reading_grade_level == "N/A"

    def test_none_coerced_to_empty_string(self) -> None:
        mp = MarketingPsychology(reading_grade_level=None)
        assert mp.reading_grade_level == ""

    def test_default_is_empty_string(self) -> None:
        mp = MarketingPsychology()
        assert mp.reading_grade_level == ""

    def test_other_fields_unaffected_by_coercion(self) -> None:
        mp = MarketingPsychology(
            hook_framework="PAS",
            primary_value_proposition="Real value prop",
            authority_flags=["doctor-recommended"],
            emoji_count=2,
            reading_grade_level=9,
        )
        assert mp.primary_value_proposition == "Real value prop"
        assert mp.authority_flags == ["doctor-recommended"]
        assert mp.emoji_count == 2
