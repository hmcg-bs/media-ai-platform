# Scrape & Load Supplements Ads to BigQuery

## Quick Start

### Step 1: Ensure Your BigQuery Dataset Exists

Your dataset is already created at: `clean-patrol-496108-m9.20k_supplement_trial`

Verify it exists:
```bash
bq ls -d "clean-patrol-496108-m9"
```

### Step 2: Set Environment Variables

Make sure your Apify API token is configured:

```bash
# Option A: Set in .env file
echo "APIFY_API_TOKEN=your_token_here" >> .env

# Option B: Set as environment variable
export APIFY_API_TOKEN="your_token_here"
```

### Step 3: Run the Scraper

Basic usage (20k ads, Supplements, US):
```bash
cd "/Users/avinaashpadman/Desktop/Media AI Platform"
python -m ingestion.scrape_and_load_supplements
```

With custom options:
```bash
python -m ingestion.scrape_and_load_supplements \
  --search-query "supplements" \
  --count 20000 \
  --project-id "clean-patrol-496108-m9" \
  --dataset-id "20k_supplement_trial" \
  --table-id "ads_raw" \
  --output-file "/tmp/supplements_ads.json"
```

### Step 4: Monitor Progress

The script logs to stdout and structlog. Watch for:
- `scrape_start` - Apify actor starting
- `scrape_complete` - Raw items received from Apify
- `normalize_complete` - Ads normalized to schema
- `bigquery_load_start` - Loading to BigQuery
- `bigquery_load_complete` - Success!

Example output:
```
✅ Pipeline complete!
   Scraped: 20000 ads
   Normalized: 19847 ads
   Loaded to BigQuery: 19847 rows
   Table: clean-patrol-496108-m9.20k_supplement_trial.ads_raw

   Query: bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM `clean-patrol-496108-m9.20k_supplement_trial.ads_raw`'
```

### Step 5: Verify Data in BigQuery

Check row count:
```bash
bq query --use_legacy_sql=false 'SELECT COUNT(*) as total FROM `clean-patrol-496108-m9.20k_supplement_trial.ads_raw`'
```

Preview first 5 rows:
```bash
bq query --use_legacy_sql=false 'SELECT title, body, days_active, product_category FROM `clean-patrol-496108-m9.20k_supplement_trial.ads_raw` LIMIT 5'
```

Check for failures:
```bash
bq query --use_legacy_sql=false 'SELECT COUNT(*) as failed FROM `clean-patrol-496108-m9.20k_supplement_trial.ads_raw` WHERE title IS NULL OR body IS NULL'
```

---

## What the Script Does

1. **Scrapes** 20k ads from Meta Ad Library via Apify
   - Search query: "supplements"
   - Country: US
   - Active ads only
   - ~5-10 minutes on free tier

2. **Normalizes** raw Apify output to `CompetitorAd` schema
   - Maps fields (title, body, days_active, collation_count, etc.)
   - Handles missing fields gracefully
   - Logs normalization failures

3. **Loads** to BigQuery
   - Project: `clean-patrol-496108-m9`
   - Dataset: `20k_supplement_trial`
   - Table: `ads_raw`
   - Adds `run_id` and `ingested_at` metadata

---

## Troubleshooting

### "APIFY_API_TOKEN not configured"
Set your token in `.env`:
```bash
echo "APIFY_API_TOKEN=your_actual_token" > .env
```

### "Failed to connect to BigQuery"
Ensure gcloud auth:
```bash
gcloud auth application-default login
```

### "Actor timeout" (Apify)
Increase timeout (default 300s):
```python
# In script: ApifyClient(api_token=token, timeout_s=600)
```

### BigQuery load errors
Check first 5 errors in logs. Common issues:
- Mismatched schema (types)
- Invalid JSON in string fields
- NaN/Infinity in numeric fields

### "Apify rate limited" (429 error)
Script auto-retries with exponential backoff. Free tier: ~6 requests/min
For faster scraping, upgrade Apify plan or run multiple times

---

## Next Steps

After successful BigQuery load:
1. ✅ Move to WEEK 2: Feature extraction & validation
2. ✅ Build validation UI (GitHub issue #8)
3. ✅ Extract all 94 features from ads
4. ✅ Validate LLM/vision fields to 95% accuracy

See GitHub issues:
- #4: Scrape 20k ads (this script!)
- #5: Normalize ads
- #6: Enrich with ProductPage
- #7: Load to BigQuery

---

## Cost Summary

- **Apify scraping**: ~$15 (20k ads × $0.75/1k)
- **BigQuery load**: ~$0.02 (100MB data scanned)
- **BigQuery storage**: ~$0.02/month (100MB @ $0.25/month)

**Total**: ~$15.04 one-time cost
