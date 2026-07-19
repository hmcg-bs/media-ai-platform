# Step 1, Stage 4: Landing Page Analysis & Categorization

**Status**: ✅ Complete (v1)
**Version**: 1.0
**Date**: 2026-07-19

## Overview

Stage 4 validates and categorizes products from ad landing pages, addressing the problem that **Apify Facebook Ad Library scraping is noisy**—searching for "creatine" returns unrelated ads (marketing tools, courses, etc.). By scraping the `link_url` from each ad and extracting product metadata, we:

1. Confirm product relevance to the search query
2. Enrich ads with structured product information
3. Enable feature→performance correlation analysis in Step 3

This is an **optional post-hoc enrichment step**, not part of the core Step 1 pipeline. Ads continue flowing through Steps 1-3 with `product_page=null` if analysis is skipped.

---

## Architecture: Four Stages

### Stage 4a: HTML Scraping
**Function**: `scrape_landing_page(url, timeout_s=10) → str | None`
- HTTP GET with 10s timeout + redirect following
- Returns raw HTML or None on failure
- Logs failures but doesn't raise; per-ad fallback

### Stage 4b: Structured Data Extraction
**Function**: `extract_structured_data(url, html) → ProductPage | None`
- Extracts JSON-LD schema.org Product data
- Fallback to Open Graph meta tags
- Maps to `ProductPage` with `extraction_method="structured_data"`, `confidence=0.9`
- Extracts: name, category, brand, price, rating, marketing copy

**Limitations**: E-commerce sites (Amazon, Shopify) rich structured data. App stores (Google Play) and SPA-heavy sites lack JSON-LD/OG.

### Stage 4c: LLM Semantic Enrichment
**Function**: `extract_semantic_fields(html, partial_product) → ProductPage | None`
- Uses Gemini (cheap model) on raw HTML to extract semantic fields
- Requires structured data success (Stage 4b) as input
- Adds: subcategory, USP, cultural branding, variants, price ranges
- Returns merged ProductPage with `extraction_method="structured_data+llm"`, `confidence=0.75`

**Prompting**: HTML is sent directly to Gemini; no screenshots needed. LLM parses breadcrumbs, variant selectors, marketing copy, certifications/origins.

### Stage 4d: Integration & Enrichment
**Function**: `enrich_corpus(ads_file, output_file, use_llm=True) → int`
- CLI utility to post-hoc enrich existing ads.json with ProductPage data
- Per-ad fallback: failures don't drop ads (product_page remains null)
- Optional `--no-llm` flag for structured data only

**Usage**:
```bash
python -m ingestion.enrich_with_product_pages \
  --ads /path/to/ads.json \
  --out /path/to/enriched_ads.json
  # --no-llm  (optional, disable Gemini enrichment)
```

---

## ProductPage Schema

```python
class ProductPage(BaseModel):
    # Identification
    product_name: str = ""               # "Creatine Monohydrate"
    product_category: str = ""           # "Supplements", "Apparel"
    product_subcategory: str = ""        # "Pre-Workout", "Amino Acids"
    
    # Brand & positioning
    brand_name: str = ""
    price: float | None = None           # Detected currency or USD
    price_currency: str = "USD"
    price_range: str = ""                # "$19.99-$29.99" if variable
    
    # Content
    rating: float | None = None          # 0-5 star
    rating_count: int | None = None      # number of reviews
    marketing_copy: str = ""             # product description
    usp: str = ""                        # "Vegan, Non-GMO, Lab-Tested"
    
    # Brand signals (4c only)
    cultural_branding: list[str] = []    # ["American Made", "European"]
    variants_featured: list[str] = []    # ["Flavor: Strawberry", "Size: 500g"]
    shows_all_variants: bool = False
    
    # Metadata
    extraction_method: str = ""          # "structured_data" | "structured_data+llm"
    confidence: float = 0.0              # 0-1
    url: str = ""
    fallback_used: bool = False
```

Attached to `CompetitorAd` as optional field:
```python
class CompetitorAd(BaseModel):
    ...
    product_page: ProductPage | None = None  # Stage 4 analysis
```

---

## Integration Points

### Workflow
```
Step 1 Scraping (Stages 1-3)
└── ads.json (corpus of CompetitorAd, product_page=null)
    └── Optional: enrich_with_product_pages.py
        ├── 4a: scrape_landing_page(link_url) → html
        ├── 4b: extract_structured_data(html) → partial ProductPage
        ├── 4c: extract_semantic_fields(html, partial) → enriched ProductPage
        └── write enriched ads.json
            └── Step 3 Pattern Discovery (uses product_page.product_category, etc.)
```

### When to Use
- **Always**: Structured data only (4a-4b) for e-commerce sites (Amazon, Shopify)
- **If GCP creds + budget**: Add LLM (4c) for app stores, SPA sites, missing metadata
- **Skip**: If Step 3 doesn't need product categorization; focus on ad copy features

---

## Testing & Validation

### Unit Tests (64 total)
- **landing_page_scraper**: 13 tests
  - JSON-LD parsing (script tags, malformed JSON, missing fields)
  - OG tag extraction (regex, multiple tags)
  - Field mapping from both sources
  - Full pipeline orchestration (with/without LLM)

- **product_page_analyzer**: 5 tests
  - LLM extraction with mocked Gemini
  - Edge cases (empty HTML, missing product_name)
  - Graceful error handling
  - Field preservation from structured data

- **enrich_with_product_pages**: 5 tests
  - File I/O (missing files, invalid JSON)
  - Per-ad fallback on scrape/extract failure
  - Batch enrichment on real corpus structure

### Integration Tests
- **test_real_pages.py**: Tests on 5 Google Play Store product pages
  - Result: 5/5 scraped, 5/5 extracted (structured only)
  - Finding: App stores lack rich structured data; LLM enrichment would help

- **test_creatine_v2.py**: Tests on creatine supplement ads
  - Result: Apify returns 1 ad (not 10); no link_url
  - Finding: Meta Ad Library has sparse creatine inventory or search API constraints

---

## Known Limitations (v1)

### Structured Data (4b)
- Google Play Store pages: Only product name extracted; no category/brand/price/rating
- SPA-heavy sites: May load data via JavaScript after initial HTML load (Vision API needed)
- Regional sites: May require country-specific parsing

### LLM Enrichment (4c)
- Requires GCP project + Gemini API access (credential-bound)
- Temperature=0.1 + structured output enforce consistency but may miss edge cases
- No screenshot/Vision API yet; relies on HTML text parsing
- Timeout: If page takes >10s to load, scraping fails (configured timeout)

### Apify Scraping (upstream)
- "creatine" query returns only 1 result in Meta Ad Library (low inventory or API constraints)
- Some ads may have `link_url=null` (no landing page)

---

## Performance & Cost

### Extraction Cost Estimate (per ad)
| Stage | API | Cost | Notes |
|-------|-----|------|-------|
| 4a | HTTP | ~0.01ms | Local; no API charge |
| 4b | Parsing | ~0.5ms | Regex + JSON; deterministic |
| 4c | Gemini cheap | ~$0.00002 | gemini-2.0-flash-lite @ $0.075/M tokens |

**Example**: Enriching 1,000 ads with LLM ≈ $0.02 total Gemini cost.

### Latency
- Without LLM: ~1-2s per ad (dominated by HTTP timeout budget)
- With LLM: ~3-5s per ad (including Gemini round-trip)

---

## Deferred (Future Versions)

### Stage 4b.5: Vision API Screenshots
- Use headless Chrome / Playwright to screenshot landing page
- Send to Cloud Vision API for object/label detection
- Extract visual product features (images, price displays, ratings, variant UI)

### Stage 4c.5: Advanced LLM
- Use deeper Gemini model for nuanced USP/branding extraction
- Chain-of-thought prompting for multi-step reasoning
- Cross-reference product with brand reputation signals

### Stage 4d.5: Visual Verification
- Use Vision API to verify product in ad matches product on landing page
- Flag `is_visually_verified_match: bool` for confidence scoring
- Detect bait-and-switch or misleading landing pages

---

## Usage Example

### Basic Enrichment (Structured Data Only)
```bash
python -m ingestion.enrich_with_product_pages \
  --ads creatives/apify/google/ads.json \
  --out creatives/apify/google/ads_enriched.json \
  --no-llm
```

### Full Enrichment with LLM
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python -m ingestion.enrich_with_product_pages \
  --ads creatives/apify/google/ads.json \
  --out creatives/apify/google/ads_enriched.json
```

### Programmatic Use
```python
from ingestion.landing_page_scraper import scrape_landing_page, extract_product_page

html = scrape_landing_page("https://example.com/product")
product = extract_product_page("https://example.com/product", html, use_llm_enrichment=True)
print(product.product_category, product.usp, product.confidence)
```

---

## See Also
- **ADR-005**: Supersede Media AI Platform; defer ROAS/XGBoost until own-ad data exists
- **ADR-006**: Step 2 Extraction architecture; mentions Stage 4 deferred
- **CONTEXT.md**: Domain glossary; see "Longevity", "Variant proxy"
- **Apify Facebook Ad Library Scraper**: Upstrem data source (curious_coder/facebook-ads-library-scraper)
