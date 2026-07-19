"""Tests for Stage 4c: LLM semantic extraction."""

from __future__ import annotations

from unittest.mock import patch

from ingestion.product_page import ProductPage
from ingestion.product_page_analyzer import (
    SemanticExtraction,
    extract_semantic_fields,
)


def test_extract_semantic_with_mock_llm() -> None:
    """Test LLM semantic extraction with mocked Gemini response."""
    partial_product = ProductPage(
        product_name="Optimum Nutrition Gold Standard Whey",
        brand_name="Optimum Nutrition",
        price=29.99,
        price_currency="USD",
        rating=4.7,
        rating_count=1200,
    )

    html_content = """
    <html>
        <head><title>Gold Standard Whey Protein</title></head>
        <body>
            <h1>Optimum Nutrition Gold Standard 100% Whey Protein</h1>
            <p>Premium whey protein powder. Available in vanilla, chocolate, strawberry flavors.</p>
            <p class="usp">Verified by Informed Choice. American Made. Third-party tested.</p>
            <div class="variants">
                <label><input type="radio" name="size"> 5 lbs ($49.99)</label>
                <label><input type="radio" name="size"> 10 lbs ($89.99)</label>
            </div>
            <p>Best-selling whey protein supplement for muscle recovery and fitness.</p>
        </body>
    </html>
    """

    # Mock the LLM response
    mock_extraction = SemanticExtraction(
        product_category="Supplements",
        product_subcategory="Protein Powder",
        usp="Premium whey, verified by Informed Choice, American Made",
        cultural_branding=["American Made", "Third-party tested"],
        variants_featured=["Flavor: Vanilla", "Flavor: Chocolate", "Flavor: Strawberry"],
        shows_all_variants=True,
        price_range="$49.99-$89.99",
    )

    with patch(
        "ingestion.product_page_analyzer.GenAIClient.extract_structured_text"
    ) as mock_llm:
        mock_llm.return_value = mock_extraction

        enriched = extract_semantic_fields(html_content, partial_product)

    assert enriched is not None
    assert enriched.product_name == "Optimum Nutrition Gold Standard Whey"
    assert enriched.product_category == "Supplements"
    assert enriched.product_subcategory == "Protein Powder"
    assert enriched.usp == "Premium whey, verified by Informed Choice, American Made"
    assert "American Made" in enriched.cultural_branding
    assert enriched.shows_all_variants is True
    assert enriched.price_range == "$49.99-$89.99"
    assert enriched.extraction_method == "structured_data+llm"
    assert enriched.confidence == 0.75


def test_extract_semantic_skips_empty_html() -> None:
    """Test that extraction skips empty HTML."""
    partial_product = ProductPage(product_name="Product")

    result = extract_semantic_fields("", partial_product)
    assert result is None


def test_extract_semantic_skips_no_product_name() -> None:
    """Test that extraction skips if product_name is empty."""
    partial_product = ProductPage(product_name="")

    result = extract_semantic_fields("<html></html>", partial_product)
    assert result is None


def test_extract_semantic_handles_llm_error() -> None:
    """Test graceful handling of LLM errors."""
    partial_product = ProductPage(product_name="Test Product")

    with patch(
        "ingestion.product_page_analyzer.GenAIClient.extract_structured_text"
    ) as mock_llm:
        mock_llm.side_effect = RuntimeError("LLM API error")

        result = extract_semantic_fields("<html>content</html>", partial_product)

    assert result is None


def test_extract_semantic_preserves_structured_fields() -> None:
    """Test that enrichment preserves already-extracted fields."""
    partial_product = ProductPage(
        product_name="Creatine Monohydrate",
        brand_name="MyBrand",
        price=19.99,
        price_currency="USD",
        rating=4.5,
        rating_count=500,
        marketing_copy="High-quality creatine",
    )

    mock_extraction = SemanticExtraction(
        product_category="Supplements",
        product_subcategory="Creatine",
    )

    with patch(
        "ingestion.product_page_analyzer.GenAIClient.extract_structured_text"
    ) as mock_llm:
        mock_llm.return_value = mock_extraction

        enriched = extract_semantic_fields("<html></html>", partial_product)

    assert enriched.product_name == "Creatine Monohydrate"
    assert enriched.brand_name == "MyBrand"
    assert enriched.price == 19.99
    assert enriched.rating == 4.5
    assert enriched.marketing_copy == "High-quality creatine"
