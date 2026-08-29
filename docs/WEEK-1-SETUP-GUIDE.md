# WEEK 1: Data Ingestion & BigQuery Setup

## Task 1: Set Up BigQuery Infrastructure

### Step 1a: Create the Dataset

Run this command in your terminal (with gcloud auth):

```bash
gcloud auth application-default login
bq mk --dataset --description "Ad intelligence and feature engineering" ad_intelligence
```

### Step 1b: Create the Table

Save this schema JSON to a file called `supplements_schema.json`:

```json
[
  {"name": "ad_id", "type": "STRING", "mode": "NULLABLE", "description": "Unique ad ID from Meta Ad Library"},
  {"name": "page_name", "type": "STRING", "mode": "NULLABLE"},
  {"name": "page_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "link_url", "type": "STRING", "mode": "NULLABLE"},
  {"name": "title", "type": "STRING", "mode": "NULLABLE"},
  {"name": "body", "type": "STRING", "mode": "NULLABLE"},
  {"name": "caption", "type": "STRING", "mode": "NULLABLE"},
  {"name": "cta_text", "type": "STRING", "mode": "NULLABLE"},
  {"name": "image_urls", "type": "STRING", "mode": "REPEATED"},
  {"name": "video_urls", "type": "STRING", "mode": "REPEATED"},
  {"name": "snapshot_url", "type": "STRING", "mode": "NULLABLE"},
  {"name": "days_active", "type": "INTEGER", "mode": "NULLABLE"},
  {"name": "is_active", "type": "BOOLEAN", "mode": "NULLABLE"},
  {"name": "collation_count", "type": "INTEGER", "mode": "NULLABLE"},
  {"name": "publisher_platforms", "type": "STRING", "mode": "REPEATED"},
  {"name": "impressions", "type": "INTEGER", "mode": "NULLABLE"},
  {"name": "spent_min", "type": "FLOAT64", "mode": "NULLABLE"},
  {"name": "spent_max", "type": "FLOAT64", "mode": "NULLABLE"},
  {"name": "product_name", "type": "STRING", "mode": "NULLABLE"},
  {"name": "product_category", "type": "STRING", "mode": "NULLABLE"},
  {"name": "product_subcategory", "type": "STRING", "mode": "NULLABLE"},
  {"name": "brand_name", "type": "STRING", "mode": "NULLABLE"},
  {"name": "price", "type": "FLOAT64", "mode": "NULLABLE"},
  {"name": "price_currency", "type": "STRING", "mode": "NULLABLE"},
  {"name": "price_range", "type": "STRING", "mode": "NULLABLE"},
  {"name": "rating", "type": "FLOAT64", "mode": "NULLABLE"},
  {"name": "rating_count", "type": "INTEGER", "mode": "NULLABLE"},
  {"name": "marketing_copy", "type": "STRING", "mode": "NULLABLE"},
  {"name": "usp", "type": "STRING", "mode": "NULLABLE"},
  {"name": "cultural_branding", "type": "STRING", "mode": "REPEATED"},
  {"name": "variants_featured", "type": "STRING", "mode": "REPEATED"},
  {"name": "shows_all_variants", "type": "BOOLEAN", "mode": "NULLABLE"},
  {"name": "extraction_method", "type": "STRING", "mode": "NULLABLE"},
  {"name": "product_page_confidence", "type": "FLOAT64", "mode": "NULLABLE"},
  {"name": "product_page_fallback_used", "type": "BOOLEAN", "mode": "NULLABLE"},
  {"name": "ingested_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
  {"name": "created_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
  {"name": "run_id", "type": "STRING", "mode": "NULLABLE"}
]
```

Then create the table:

```bash
bq mk \
  --table \
  --schema supplements_schema.json \
  --description "20k Supplements ads with enrichment" \
  ad_intelligence.supplements_20k_raw
```

### Step 1c: Verify

Check BigQuery Console:
```
https://console.cloud.google.com/bigquery?project=gen-lang-client-0573769868&p=gen-lang-client-0573769868&d=ad_intelligence&t=supplements_20k_raw
```

---

## Task 2: Configure Apify Scraper

### Step 2a: Apify Actor Configuration

The Apify actor for Meta Ad Library scraping should be configured with:

```json
{
  "searchQuery": "supplements",
  "country": "US",
  "maxAds": 20000,
  "fields": [
    "id",
    "adCopy",
    "displayedUrl",
    "pageDetails",
    "media",
    "isPaused",
    "dayStarted",
    "dayEnded",
    "platforms"
  ]
}
```

**Cost**: ~$0.75 per 1,000 ads = $15 total for 20k ads

### Step 2b: Run Scraper

Use the ingestion pipeline's Apify client to trigger the scraper. See: `ingestion/apify_client.py`

```python
from ingestion.apify_client import run_ad_scrape

raw_items = run_ad_scrape(
    search_query="supplements",
    count=20000,
    country="US"
)
print(f"Scraped {len(raw_items)} ads")
```

---

## Task 3: Normalize & Enrich Data

### Step 3a: Normalize to CompetitorAd Schema

```python
from ingestion.normalize import normalize_ad
from ingestion.apify_client import run_ad_scrape

raw_items = run_ad_scrape(search_query="supplements", count=20000)
normalized_ads = [normalize_ad(item) for item in raw_items]
print(f"Normalized {len(normalized_ads)} ads")
```

### Step 3b: Enrich with ProductPage (Stage 4)

```python
from ingestion.enrich_with_product_pages import enrich_ads_with_product_pages
import json

# Load normalized ads
with open("normalized_ads.json") as f:
    ads = json.load(f)

# Enrich with ProductPage
enriched = enrich_ads_with_product_pages(ads, use_llm=True)
print(f"Enriched {len(enriched)} ads")

# Save enriched
with open("enriched_ads.json", "w") as f:
    json.dump(enriched, f)
```

### Step 3c: Load to BigQuery

```python
from google.cloud import bigquery
import json

client = bigquery.Client()
table_id = "gen-lang-client-0573769868.ad_intelligence.supplements_20k_raw"

with open("enriched_ads.json") as f:
    ads = json.load(f)

# Convert to records for BigQuery
records = [
    {
        "ad_id": ad.get("ad_id"),
        "title": ad.get("title"),
        "body": ad.get("body"),
        "days_active": ad.get("days_active"),
        "collation_count": ad.get("collation_count"),
        "product_category": ad.get("product_page", {}).get("product_category"),
        "price": ad.get("product_page", {}).get("price"),
        "run_id": "run_001"
        # ... add all other fields
    }
    for ad in ads
]

job = client.insert_rows_json(table_id, records)
print(f"✅ Loaded {len(records)} rows to BigQuery")
```

---

## Success Criteria

- [ ] BigQuery dataset `ad_intelligence` created
- [ ] Table `supplements_20k_raw` created with correct schema (40 fields)
- [ ] Apify scraper configured for Supplements (20k ads, ~$15)
- [ ] 20k ads scraped and normalized
- [ ] 20k ads enriched with ProductPage (Stage 4)
- [ ] 20k rows loaded to BigQuery
- [ ] Can query: `SELECT COUNT(*) FROM ad_intelligence.supplements_20k_raw` → 20k rows

---

## Troubleshooting

**BigQuery dataset not created**: Check project ID, ensure gcloud authenticated
**Apify rate limit**: Stagger requests or adjust max_concurrent_pages
**ProductPage enrichment slow**: Parallelizes by default; takes ~3-5 hours for 20k ads
**BigQuery load failures**: Check schema matches records (handle NaN, null types)

---

## Next Steps

Once WEEK 1 complete:
- [ ] Verify all 20k rows in BigQuery
- [ ] Move to WEEK 2: Feature extraction & validation
- [ ] See GitHub issue #8 (Build validation UI)

---

**GitHub Issues**: 
- #2: Set up BigQuery schema
- #3: Configure Apify scraper
- #4: Scrape 20k ads
- #5-7: Normalize, enrich, load to BigQuery
