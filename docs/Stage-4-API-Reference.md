# Stage 4: API Reference

Quick reference for all Stage 4 functions, classes, and usage patterns.

---

## Core Functions

### `scrape_landing_page()`
**Module**: `ingestion.landing_page_scraper`

```python
def scrape_landing_page(
    url: str,
    timeout_s: int = 10
) -> str | None:
    """Scrape HTML content from a landing page URL.
    
    Args:
        url: Product page URL to scrape
        timeout_s: HTTP request timeout in seconds (default 10)
    
    Returns:
        HTML content as string, or None on error
    
    Raises:
        None (returns None on all errors)
    
    Examples:
        html = scrape_landing_page("https://example.com/product")
        if html:
            print(f"Scraped {len(html)} bytes")
    """
```

**Error Handling**:
- Returns `None` on timeout, 403/503, connection errors
- Logs warnings for debugging but doesn't raise exceptions
- Per-ad fallback: upstream code handles None gracefully

---

### `extract_structured_data()`
**Module**: `ingestion.landing_page_scraper`

```python
def extract_structured_data(
    url: str,
    html: str
) -> ProductPage | None:
    """Extract product data from structured data in HTML (JSON-LD, OG tags).
    
    Args:
        url: The product page URL
        html: HTML content to parse
    
    Returns:
        ProductPage with extracted fields, or None if no data found
    
    Stage**: 4b (Structured Data Extraction)
    
    Examples:
        product = extract_structured_data(url, html)
        if product:
            print(f"Name: {product.product_name}")
            print(f"Category: {product.product_category}")
            print(f"Price: ${product.price}")
    """
```

**Extracted Fields**:
- `product_name` (from JSON-LD `name` or OG `og:title`)
- `product_category` (from JSON-LD `category`)
- `brand_name` (from JSON-LD `brand.name`)
- `price` (from JSON-LD `offers[0].price`)
- `price_currency` (from JSON-LD `offers[0].priceCurrency`)
- `rating` (from JSON-LD `aggregateRating.ratingValue`)
- `rating_count` (from JSON-LD `aggregateRating.reviewCount`)
- `marketing_copy` (from JSON-LD `description` or OG `og:description`)

**Metadata**:
- `extraction_method = "structured_data"`
- `confidence = 0.9`
- `url = <input_url>`
- `fallback_used = False`

---

### `extract_semantic_fields()`
**Module**: `ingestion.product_page_analyzer`

```python
def extract_semantic_fields(
    html: str,
    partial_product: ProductPage,
    ad_context: dict[str, str] | None = None
) -> ProductPage | None:
    """Extract semantic fields from HTML using Gemini.
    
    Args:
        html: Full HTML content of the landing page
        partial_product: ProductPage with structured data (from Stage 4b)
        ad_context: Optional marketing context from ad:
            {
                "title": "Ad headline",
                "body": "Ad copy text",
                "caption": "Ad caption"
            }
    
    Returns:
        Enriched ProductPage with semantic fields, or None on error
    
    Stage**: 4c (LLM Semantic Enrichment)
    
    Examples:
        # Without ad context
        product = extract_semantic_fields(html, partial_product)
        
        # With ad context (improves quality)
        ad_ctx = {"title": ad.title, "body": ad.body, "caption": ad.caption}
        product = extract_semantic_fields(html, partial_product, ad_context=ad_ctx)
    """
```

**Enriched Fields** (4c only):
- `product_subcategory` (e.g., "Pre-Workout" under Supplements)
- `usp` (Unique Selling Proposition, max 150 chars)
- `cultural_branding` (list of brand identity signals)
- `variants_featured` (list of variant descriptions)
- `shows_all_variants` (boolean)
- `price_range` (e.g., "$19.99-$29.99")

**Metadata**:
- `extraction_method = "structured_data+llm"`
- `confidence = 0.75`
- `url = <from partial_product>`
- `fallback_used = False`

**LLM Details**:
- **Model**: `google/gemini-3-flash` (via Replicate)
- **Temperature**: 0.1 (deterministic)
- **Response**: Structured JSON via Pydantic
- **Timeout**: Inherited from config (`replicate_timeout_s`)

**Requires**:
- `REPLICATE_API_TOKEN` environment variable
- Stage 4b success (non-None `partial_product`)

---

### `extract_product_page()`
**Module**: `ingestion.landing_page_scraper`

```python
def extract_product_page(
    url: str,
    html: str,
    use_llm_enrichment: bool = True,
    ad_context: dict[str, str] | None = None
) -> ProductPage | None:
    """Full pipeline: scrape + structured extract + optional LLM enrichment.
    
    Orchestrates Stages 4a-4c.
    
    Args:
        url: The product page URL
        html: HTML content (pre-scraped)
        use_llm_enrichment: Enable Stage 4c? (default True)
        ad_context: Optional ad marketing context
    
    Returns:
        Fully extracted ProductPage, or None if Stage 4b fails
    
    Stage**: 4a-4c (Full Pipeline)
    
    Examples:
        # Structured data only
        product = extract_product_page(url, html, use_llm_enrichment=False)
        
        # Full pipeline
        product = extract_product_page(url, html, use_llm_enrichment=True)
        
        # With ad context
        ad_ctx = {"title": ad.title, "body": ad.body, "caption": ad.caption}
        product = extract_product_page(url, html, use_llm_enrichment=True, ad_context=ad_ctx)
    """
```

**Flow**:
1. Stage 4b: `extract_structured_data(url, html)` → `partial_product` or None
2. If Stage 4b fails → return None
3. If `use_llm_enrichment=True` → Stage 4c: `extract_semantic_fields(html, partial_product, ad_context)`
4. Return enriched product or partial

---

### `enrich_corpus()`
**Module**: `ingestion.enrich_with_product_pages`

```python
def enrich_corpus(
    ads_file: Path,
    output_file: Path,
    use_llm: bool = True
) -> int:
    """Enrich ads corpus with landing page product analysis.
    
    Args:
        ads_file: Path to ads.json (array of CompetitorAd dicts)
        output_file: Path to write enriched ads.json
        use_llm: Enable LLM enrichment? (default True)
    
    Returns:
        0 on success, 1 on error
    
    Stage**: 4d (Batch Enrichment)
    
    Examples:
        # Structured data only
        enrich_corpus(
            Path("ads.json"),
            Path("ads_enriched.json"),
            use_llm=False
        )
        
        # Full pipeline with LLM
        enrich_corpus(
            Path("ads.json"),
            Path("ads_enriched.json"),
            use_llm=True
        )
    
    # CLI equivalent:
    python -m ingestion.enrich_with_product_pages \\
        --ads ads.json \\
        --out ads_enriched.json
    """
```

**Per-ad Fallback**:
- If `link_url` is empty → skip (ad continues with `product_page=None`)
- If scrape fails → skip (ad continues with `product_page=None`)
- If extraction fails → skip (ad continues with `product_page=None`)
- **No ads are dropped** from corpus

**Output Format**:
```json
[
  {
    "ad_archive_id": "...",
    "page_name": "...",
    "link_url": "https://...",
    "title": "...",
    "body": "...",
    "product_page": {
      "product_name": "...",
      "product_category": "...",
      "usp": "...",
      "extraction_method": "structured_data+llm",
      "confidence": 0.75,
      ...
    }
  },
  ...
]
```

---

## Data Models

### `ProductPage`
**Module**: `ingestion.product_page`

```python
class ProductPage(BaseModel):
    # Identification
    product_name: str = ""
    product_category: str = ""
    product_subcategory: str = ""
    
    # Brand & positioning
    brand_name: str = ""
    price: float | None = None
    price_currency: str = "USD"
    price_range: str = ""
    
    # Content
    rating: float | None = None
    rating_count: int | None = None
    marketing_copy: str = ""
    usp: str = ""
    
    # Brand signals
    cultural_branding: list[str] = Field(default_factory=list)
    variants_featured: list[str] = Field(default_factory=list)
    shows_all_variants: bool = False
    
    # Metadata
    extraction_method: str = ""
    confidence: float = 0.0
    url: str = ""
    fallback_used: bool = False
```

**Validation**:
- All fields have safe defaults (empty strings, None, empty lists)
- No required fields — can always construct a ProductPage
- Pydantic v2 defensive validation

**Serialization**:
```python
product.model_dump()           # → dict
product.model_dump(mode="json")  # → JSON-serializable dict
product.model_dump_json()      # → JSON string
```

---

### `CompetitorAd`
**Module**: `ingestion.models`

```python
class CompetitorAd(BaseModel):
    ad_archive_id: str = ""
    page_id: str = ""
    page_name: str = ""
    start_date: str | None = None
    end_date: str | None = None
    is_active: bool = False
    days_active: int = 0
    collation_count: int = 0
    
    # Copy
    body: str = ""
    title: str = ""
    caption: str = ""
    link_url: str = ""
    cta_text: str = ""
    
    # Creative handles
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    publisher_platforms: list[str] = Field(default_factory=list)
    snapshot_url: str = ""
    
    # Filled after ingestion
    local_image_path: str | None = None
    ingested_at: str = ""
    
    # Stage 4: Landing page analysis
    product_page: ProductPage | None = None
```

**Usage**:
```python
# From Apify raw data
ad = normalize_ad(raw_item)  # Creates CompetitorAd

# Enrich with product page
ad.product_page = extract_product_page(ad.link_url, html)

# Access fields
category = ad.product_page.product_category if ad.product_page else None
```

---

## CLI Reference

### Enrich Corpus
```bash
python -m ingestion.enrich_with_product_pages \
  --ads /path/to/ads.json \
  --out /path/to/enriched_ads.json
  # --no-llm  (optional: structured data only)
```

**Options**:
- `--ads` (required): Input corpus file (ads.json)
- `--out` (required): Output file path
- `--no-llm`: Disable LLM enrichment; use structured data only

**Output**:
- Creates output directory if needed
- Writes enriched_ads.json with Stage 4 ProductPage data
- Logs enrichment progress and per-ad results

---

## Common Patterns

### Pattern 1: Extract Single Ad's Product
```python
from ingestion.landing_page_scraper import scrape_landing_page, extract_product_page

ad = ads[0]  # CompetitorAd instance
html = scrape_landing_page(ad.link_url, timeout_s=10)

if html:
    ad.product_page = extract_product_page(
        ad.link_url,
        html,
        use_llm_enrichment=True,
        ad_context={"title": ad.title, "body": ad.body, "caption": ad.caption}
    )
```

### Pattern 2: Batch Enrich from CLI
```bash
# Prepare corpus
python -m ingestion.ingest \
  --query "furniture" \
  --count 100 \
  --out /tmp/furniture_run

# Enrich with product pages
python -m ingestion.enrich_with_product_pages \
  --ads /tmp/furniture_run/ads.json \
  --out /tmp/furniture_run/ads_enriched.json
```

### Pattern 3: Use in Step 3 Pattern Discovery
```python
import json
from pathlib import Path

# Load enriched corpus
ads = json.loads(Path("ads_enriched.json").read_text())

# Group by product category
by_category = {}
for ad in ads:
    product = ad.get("product_page")
    if product and (cat := product.get("product_category")):
        by_category.setdefault(cat, []).append(ad)

# Analyze features by category
for category, cat_ads in by_category.items():
    print(f"\n{category}: {len(cat_ads)} ads")
    for ad in cat_ads[:3]:
        product = ad["product_page"]
        print(f"  - {product['product_name']}: {product['usp']}")
```

### Pattern 4: Conditional LLM Enrichment
```python
# Fast path: structured data only
if needs_speed:
    product = extract_product_page(url, html, use_llm_enrichment=False)
else:
    # Full quality: add LLM enrichment
    ad_ctx = {"title": title, "body": body, "caption": caption}
    product = extract_product_page(url, html, use_llm_enrichment=True, ad_context=ad_ctx)
```

---

## Error Handling

### Handling None Returns
```python
product = extract_product_page(url, html, use_llm_enrichment=True)

if product is None:
    # Stage 4b failed — no structured data found
    # Ad continues with product_page=None
    logger.warning(f"No product data for {url}")
elif product.confidence < 0.75:
    # Extraction succeeded but with low confidence
    logger.info(f"Low-confidence extraction for {product.product_name}")
else:
    # High-confidence extraction
    logger.info(f"Extracted {product.product_name} (confidence: {product.confidence})")
```

### Handling Per-ad Failures
```python
# enrich_corpus() doesn't raise on failures
result = enrich_corpus(ads_file, out_file, use_llm=True)

if result == 0:
    print("Enrichment complete (some ads may have product_page=None)")
else:
    print("Enrichment failed (check logs)")

# Check enrichment rates
enriched = json.loads(Path(out_file).read_text())
enriched_count = sum(1 for ad in enriched if ad.get("product_page"))
print(f"Enriched: {enriched_count}/{len(enriched)} ads")
```

---

## Configuration & Secrets

**Required Environment Variables**:
```bash
REPLICATE_API_TOKEN=<your-replicate-token>  # For Stage 4c LLM calls
```

**Optional Configuration** (in `.env`):
```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/gcp-key.json  # Not needed for Stage 4
```

**Settings** (in `pipeline/config.py`):
```python
replicate_gemini_model: str = "google/gemini-3-flash"
replicate_timeout_s: int = 300
api_max_attempts: int = 3
api_backoff_min_seconds: float = 2.0
api_backoff_max_seconds: float = 10.0
```

---

## Testing

### Run Tests
```bash
# All Stage 4 tests
uv run pytest ingestion/tests/test_landing_page_scraper.py \
  ingestion/tests/test_product_page_analyzer.py \
  ingestion/tests/test_enrich_with_product_pages.py -v

# Specific test
uv run pytest ingestion/tests/test_product_page_analyzer.py::test_extract_semantic_with_ad_context -v

# With coverage
uv run pytest ingestion/tests/ --cov=ingestion --cov-report=html
```

### Test Files
- `ingestion/tests/test_landing_page_scraper.py` — Stage 4a/4b
- `ingestion/tests/test_product_page_analyzer.py` — Stage 4c (with/without ad context)
- `ingestion/tests/test_enrich_with_product_pages.py` — Stage 4d (batch enrichment)

### Integration Tests
- `ingestion/test_furniture.py` — Real ads without LLM
- `ingestion/test_furniture_with_llm.py` — Real ads with LLM
- `ingestion/test_furniture_with_ad_context.py` — Compares with/without ad context

---

## Performance & Costs

**Latency per Ad**:
| Stage | Duration | Notes |
|-------|----------|-------|
| 4a: Scrape | 1-3s | Dominated by HTTP timeout budget |
| 4b: Structured | 0.1s | Regex + JSON parsing |
| 4c: LLM | 8-15s | Replicate API round-trip |
| **Total** | **9-18s** | **~0.9 seconds per ad (parallel calls only)** |

**Cost per Ad** (Replicate pricing):
- `google/gemini-3-flash`: ~$0.00002 per inference
- 1,000 ads = ~$0.02 total LLM cost
- Negligible HTTP scraping cost

**Optimization**:
- Structured data (4b) is fast; use it to filter candidates
- LLM (4c) is expensive; run on high-value subset only
- Batch runs can be parallelized (per-ad isolation)

---

## Troubleshooting

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `ReplicateError 429 (throttled)` | Rate limit hit | Wait or upgrade API token |
| `no_structured_data` (all ads) | URLs return 403/empty | Verify URLs, check User-Agent |
| `product_page=None` for all | Stage 4b failing universally | Test single URL manually |
| LLM returns empty fields | HTML too generic or truncated | Check ad_context is provided |
| `timeout_error` in scrape | Page takes >10s to load | Increase timeout_s parameter |
| `REPLICATE_API_TOKEN not set` | Missing env var | Export in shell or .env file |

---

## See Also
- **Full documentation**: `docs/Stage-4-Landing-Page-Analysis.md`
- **Architecture Decision Records**: `docs/adr/ADR-006.md`
- **Blueprint**: `docs/blueprints/Step-1-Stage-4-Landing-Page-Analysis.md`
