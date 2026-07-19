# Stage 4: Landing Page Analysis & Product Categorization

## Overview

**Stage 4** is Step 1's optional product validation layer that transforms raw Meta Ad Library scraping into structured, categorized product data. It addresses the fundamental problem: **Apify Ad Library scraping is noisy**—searching for "creatine" returns unrelated ads. By analyzing the landing pages that ads link to, we extract and validate product metadata, enabling accurate Step 3 pattern discovery.

**Current Status**: ✅ Fully implemented and tested (v1) with Replicate Gemini integration

---

## Architecture: Four Connected Stages

### Stage 4a: HTML Scraping & Page Detection
**File**: `ingestion/landing_page_scraper.py`  
**Function**: `scrape_landing_page(url: str, timeout_s: int = 10) → str | None`

**What it does**:
- Fetches HTML from the product landing page URL (from `CompetitorAd.link_url`)
- Handles HTTP errors gracefully (timeouts, 403 Forbidden, network errors)
- Returns raw HTML for downstream analysis or None on failure

**Key Design**:
- **10-second timeout** by default (configurable) to prevent hangs
- **Per-ad fallback**: If scraping fails, the ad continues with `product_page=None`
- **No parsing yet**: Returns raw HTML as-is; parsing happens in Stage 4b

**Example**:
```python
html = scrape_landing_page("https://www.example.com/product")
if html:
    print(f"Scraped {len(html)} bytes")
else:
    print("Failed to scrape; ad will continue with product_page=None")
```

**When it fails**: 403 Forbidden (anti-bot), 503 Service Unavailable, connection timeouts, malformed URLs

---

### Stage 4b: Structured Data Extraction
**File**: `ingestion/landing_page_scraper.py`  
**Functions**:
- `extract_json_ld(html: str) → dict | None` — Parse `<script type="application/ld+json">`
- `extract_og_tags(html: str) → dict[str, str]` — Extract Open Graph meta tags
- `extract_structured_data(url: str, html: str) → ProductPage | None` — Full orchestration

**What it does**:
- Extracts machine-readable product metadata embedded in HTML
- **Tries JSON-LD first** (most reliable for e-commerce)
- **Falls back to Open Graph tags** (simpler, more common)
- Returns a `ProductPage` with extracted fields or None if no data found

**Fields Extracted**:
| Field | Source | Example |
|-------|--------|---------|
| `product_name` | JSON-LD `name` or OG `og:title` | "Creatine Monohydrate 5g" |
| `product_category` | JSON-LD `category` | "Supplements" |
| `brand_name` | JSON-LD `brand.name` | "MyBrand" |
| `price` | JSON-LD `offers[0].price` | 19.99 |
| `price_currency` | JSON-LD `offers[0].priceCurrency` | "USD" |
| `rating` | JSON-LD `aggregateRating.ratingValue` | 4.7 |
| `rating_count` | JSON-LD `aggregateRating.reviewCount` | 1200 |
| `marketing_copy` | JSON-LD `description` or OG `og:description` | "Premium quality..." |

**Metadata**:
- `extraction_method: "structured_data"` — Marks source as Stage 4b
- `confidence: 0.9` — High confidence (structured data is explicit)

**Example**:
```python
product = extract_structured_data(url, html)
if product:
    print(f"Name: {product.product_name}")
    print(f"Category: {product.product_category}")
    print(f"Confidence: {product.confidence}")
else:
    print("No structured data found on page")
```

**When it succeeds**: E-commerce sites (Amazon, Shopify, brand stores) with JSON-LD or OG tags  
**When it fails**: App stores (Google Play lacks JSON-LD), SPA sites (JS-rendered content), custom sites without structured data

---

### Stage 4c: LLM Semantic Enrichment
**File**: `ingestion/product_page_analyzer.py`  
**Function**: `extract_semantic_fields(html: str, partial_product: ProductPage, ad_context: dict[str, str] | None = None) → ProductPage | None`

**What it does**:
- Uses Gemini (via Replicate) to understand product semantics from raw HTML
- **Requires Stage 4b success**: Takes the partial `ProductPage` as input
- **Extracts semantic fields** that don't exist in structured data
- **Optional ad context**: Includes ad copy (title, body, caption) to help Gemini understand marketing positioning

**Fields Enriched** (Stage 4c only):
| Field | What it answers | Example |
|-------|-----------------|---------|
| `product_subcategory` | More specific category | "Pre-Workout" (under Supplements) |
| `usp` | Unique selling proposition | "Vegan, Non-GMO, Third-party tested" |
| `cultural_branding` | Brand identity signals | ["American Made", "Hand-crafted"] |
| `variants_featured` | Product variants mentioned | ["Flavor: Strawberry", "Size: 500g"] |
| `shows_all_variants` | Does product page show all SKUs? | true/false |
| `price_range` | Price variation across sizes | "$19.99-$29.99" |

**Metadata**:
- `extraction_method: "structured_data+llm"` — Marks source as Stage 4b + 4c
- `confidence: 0.75` — Slightly lower (LLM interpretation vs. explicit data)

**The Ad Context Enhancement**:

When `ad_context` is provided (marketing context from the ad itself):
```python
ad_context = {
    "title": "Premium Creatine for Muscle Growth",
    "body": "Boost your workout performance...",
    "caption": "Save 20% today",
}
product = extract_semantic_fields(html, partial_product, ad_context=ad_context)
```

Gemini's prompt includes:
```
**Marketing Context from Ad:**
- Title: Premium Creatine for Muscle Growth
- Body: Boost your workout performance...
- Caption: Save 20% today
```

This helps Gemini:
1. Understand **how the product is being marketed** (category signals)
2. Extract **USP and positioning** from marketing copy
3. Identify **cultural branding** (premium, eco-friendly, etc.)

**Why it matters**:
- Ad copy provides semantic signals when landing page is generic
- Helps Gemini correctly categorize products (e.g., "creatine supplement" → Category: Supplements)
- Improves extraction quality when structured data is sparse

**Example**:
```python
# Without ad context
product = extract_semantic_fields(html, partial_product)
# Result: category="", usp="", cultural_branding=[]

# With ad context (same HTML, same product)
ad_ctx = {"title": ad.title, "body": ad.body, "caption": ad.caption}
product = extract_semantic_fields(html, partial_product, ad_context=ad_ctx)
# Result: category="Supplements", usp="Premium creatine", 
#         cultural_branding=["Fitness", "American Made"]
```

**Replicate Integration**:
- Uses `ReplicateVisionClient.extract_structured_text()` for text-only LLM calls
- Model: `google/gemini-3-flash` (fast, cost-effective)
- Temperature: 0.1 (deterministic, repeatable)
- Response format: Structured JSON via Pydantic schema

---

### Stage 4d: Integration & Corpus Enrichment
**Files**:
- `ingestion/landing_page_scraper.py` — `extract_product_page()` orchestrator
- `ingestion/enrich_with_product_pages.py` — CLI for batch corpus enrichment
- `ingestion/models.py` — `CompetitorAd.product_page` field

**Function**: `extract_product_page(url: str, html: str, use_llm_enrichment: bool = True, ad_context: dict[str, str] | None = None) → ProductPage | None`

**What it does**:
- **Orchestrates all stages** (4a-4c) into a single function
- Implements the **per-ad fallback pattern**: if extraction fails, ad continues with `product_page=None`
- Returns fully enriched `ProductPage` or None

**Usage**:
```python
# Simple: structured data only
product = extract_product_page(url, html, use_llm_enrichment=False)

# Full pipeline: structured + LLM enrichment
product = extract_product_page(url, html, use_llm_enrichment=True)

# With ad context
ad_context = {"title": ad.title, "body": ad.body, "caption": ad.caption}
product = extract_product_page(url, html, use_llm_enrichment=True, ad_context=ad_context)
```

**Integration into CompetitorAd**:
```python
class CompetitorAd(BaseModel):
    ad_archive_id: str
    page_id: str
    # ... all existing fields ...
    
    # Stage 4: Optional landing page analysis
    product_page: ProductPage | None = None
```

The `product_page` field is attached after scraping/extraction completes. If anything fails, it stays `None`.

**Batch Enrichment CLI**:
```bash
python -m ingestion.enrich_with_product_pages \
  --ads /path/to/ads.json \
  --out /path/to/enriched_ads.json
  # --no-llm  (optional: structured data only)
```

This:
1. Loads existing ads corpus (ads.json)
2. For each ad with `link_url`:
   - Scrapes landing page (Stage 4a)
   - Extracts structured data (Stage 4b)
   - Optionally extracts semantic fields (Stage 4c) using ad context from the corpus
   - Attaches enriched `ProductPage` to ad
3. Writes enriched corpus to output file

**Per-ad fallback**: Failed extractions don't drop ads; they set `product_page=None` and continue.

---

## Data Flow Diagram

```
Apify Scraping (Step 1, Stages 1-3)
        ↓
CompetitorAd (with link_url, title, body, caption)
        ↓
Stage 4a: scrape_landing_page(link_url)
        ↓ (HTML or None)
        ├─→ [FAIL] → product_page=None → continue
        ↓
Stage 4b: extract_structured_data(html)
        ↓ (ProductPage or None)
        ├─→ [FAIL] → product_page=None → continue
        ↓
Stage 4c: extract_semantic_fields(html, partial_product, ad_context)
        ↓ (Enriched ProductPage or partial)
        ↓
Attach to CompetitorAd.product_page
        ↓
enrich_corpus() writes to ads.json
        ↓
Step 3: Pattern Discovery (uses product.product_category, usp, variants, etc.)
```

---

## ProductPage Schema

```python
class ProductPage(BaseModel):
    # Identification
    product_name: str = ""
    product_category: str = ""              # Stage 4b/4c
    product_subcategory: str = ""           # Stage 4c only
    
    # Brand & positioning
    brand_name: str = ""                    # Stage 4b
    price: float | None = None              # Stage 4b
    price_currency: str = "USD"             # Stage 4b
    price_range: str = ""                   # Stage 4c only
    
    # Content
    rating: float | None = None             # Stage 4b
    rating_count: int | None = None         # Stage 4b
    marketing_copy: str = ""                # Stage 4b
    usp: str = ""                           # Stage 4c only
    
    # Brand signals
    cultural_branding: list[str] = []       # Stage 4c only
    variants_featured: list[str] = []       # Stage 4c only
    shows_all_variants: bool = False        # Stage 4c only
    
    # Metadata
    extraction_method: str = ""             # "structured_data" or "structured_data+llm"
    confidence: float = 0.0                 # 0.9 (4b) or 0.75 (4c)
    url: str = ""                           # source link_url
    fallback_used: bool = False             # true if only ad title/body used
```

---

## Testing & Validation

**Unit Tests** (66 total, 7 for Stage 4):
- `test_landing_page_scraper.py` (13 tests) — HTML scraping, JSON-LD/OG extraction
- `test_product_page_analyzer.py` (7 tests) — LLM enrichment with/without ad context
- `test_enrich_with_product_pages.py` (5 tests) — Batch enrichment, per-ad fallback

**Integration Tests**:
- `test_furniture.py` — Real furniture ads (10 ads, ~50% extraction rate)
- `test_furniture_with_llm.py` — Full pipeline with LLM enrichment
- `test_furniture_with_ad_context.py` — Compares extraction with/without ad context

**Coverage**:
- ✅ Timeout handling (HTTP errors, 403/503)
- ✅ JSON-LD parsing (valid, malformed, missing)
- ✅ OG tag extraction (multiple tags, fallback)
- ✅ LLM extraction (with/without ad context, error handling)
- ✅ Per-ad fallback (failures don't drop ads)
- ✅ Batch enrichment (partial failures, concurrent calls)

---

## Usage Examples

### 1. Extract Product from a Single URL
```python
from ingestion.landing_page_scraper import scrape_landing_page, extract_product_page

url = "https://www.example.com/product"
html = scrape_landing_page(url, timeout_s=10)

if html:
    product = extract_product_page(url, html, use_llm_enrichment=True)
    if product:
        print(f"Product: {product.product_name}")
        print(f"Category: {product.product_category}")
        print(f"USP: {product.usp}")
```

### 2. Enrich Entire Ad Corpus
```bash
# Structured data only
python -m ingestion.enrich_with_product_pages \
  --ads creatives/apify/furniture/ads.json \
  --out creatives/apify/furniture/ads_enriched.json \
  --no-llm

# Full pipeline with LLM
python -m ingestion.enrich_with_product_pages \
  --ads creatives/apify/furniture/ads.json \
  --out creatives/apify/furniture/ads_enriched.json
```

### 3. Use Enriched Data in Step 3 Pattern Discovery
```python
import json
from pathlib import Path

ads_json = Path("ads_enriched.json")
ads = json.loads(ads_json.read_text())

# Filter by product category
supplements = [ad for ad in ads if ad.get("product_page", {}).get("product_category") == "Supplements"]
print(f"Found {len(supplements)} supplement ads")

# Analyze USPs
for ad in supplements:
    product = ad.get("product_page")
    if product and product.get("usp"):
        print(f"{product['product_name']}: {product['usp']}")
```

---

## Known Limitations & Future Work

### Current Limitations (v1)
- **App stores**: Google Play Store pages lack JSON-LD/OG (only product name extracted)
- **SPA sites**: JavaScript-rendered content not in initial HTML (would need Playwright)
- **Replicate rate limits**: ~6 requests/minute (free tier); upgrade needed for scale
- **No Vision API yet**: Screenshot-based extraction deferred (Stage 4b.5)

### Future Enhancements (v2+)
- **Vision API screenshots** (Stage 4b.5) — Analyze page layout, product images, price displays
- **Advanced LLM** (Stage 4c.5) — Chain-of-thought reasoning, multi-turn extraction
- **Visual verification** (Stage 4d.5) — Ensure ad image matches landing page product
- **Cached extractions** — Deduplicate URLs, avoid re-scraping same landing page

---

## Architecture Decisions

**Why separate HTML scraping from parsing?**
- Allows retry logic, timeout handling, and error logging at the HTTP layer
- Enables future Vision API integration (screenshot from HTML)
- Keeps stages independent and testable

**Why JSON-LD first, OG tags second?**
- JSON-LD is more structured and reliable for e-commerce
- OG tags are simpler and more common, but less detailed
- Fallback ensures we get *something* from most sites

**Why optional ad context?**
- Backward compatible — existing code works without changes
- Improves extraction quality when landing page is generic
- Helps with noisy searches (e.g., creatine → misclassified ads)

**Why Replicate instead of Vertex AI?**
- No GCP credentials needed in local development
- Cost-effective (pay per inference, not per resource)
- Consistent with project's existing Replicate infrastructure
- Easier to swap models without code changes

---

## Configuration

**Environment variables** (via `.env` or system):
```bash
REPLICATE_API_TOKEN=...  # Required for Stage 4c LLM
```

**Settings** (in `pipeline/config.py`):
```python
replicate_gemini_model = "google/gemini-3-flash"  # Model for 4c
replicate_timeout_s = 300  # Generous timeout for cold-starts
```

**Per-call configuration**:
```python
# Timeout for HTTP scraping
html = scrape_landing_page(url, timeout_s=10)

# Enable/disable LLM enrichment
product = extract_product_page(url, html, use_llm_enrichment=True)

# Batch enrichment options
enrich_corpus(ads_file, output_file, use_llm=True)
```

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `403 Forbidden` on scrape | Anti-bot protection | Retry with different User-Agent or skip ad |
| No structured data found | Page doesn't have JSON-LD/OG | Try LLM enrichment (4c) |
| LLM extraction returns empty fields | Replicate rate limit (429) | Wait or upgrade API token |
| `product_page=None` for all ads | Stage 4b failing universally | Check if URLs are valid, try sample page manually |
| Ad context not improving extraction | Ad copy too generic | May need Vision API (future Stage 4b.5) |

---

## Summary

**Stage 4** transforms noisy Ad Library scraping into validated, categorized product data through four connected stages:

1. **4a: Scraping** — Fetch landing page HTML
2. **4b: Structured extraction** — Parse JSON-LD and OG tags
3. **4c: LLM enrichment** — Gemini extracts semantic fields from HTML (optionally with ad context)
4. **4d: Integration** — Attach to ads, enable Step 3 pattern discovery

**Key design principles**:
- ✅ Per-ad fallback (failures don't drop ads)
- ✅ Optional LLM enrichment (backward compatible)
- ✅ Ad context support (improves extraction quality)
- ✅ Deterministic extraction (temp=0.1, structured output)
- ✅ Fully tested (66 tests, real-world validation)

**Next use**: Step 3 Pattern Discovery uses `product_page.product_category`, `usp`, `variants`, `cultural_branding` to correlate feature patterns with performance.
