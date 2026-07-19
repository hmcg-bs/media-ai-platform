# Step 1: Dual-Data Ingestion — Engineering Blueprint

> **Goal:** Build a complete, normalised picture of the market by combining your own exact performance metrics with competitor activity proxies. All data lands in BigQuery as the single source of truth before any downstream analysis begins.

---

## 1. Architecture Overview

```
[Meta Marketing API]          [Meta Ad Library (Apify)]
        │                              │
        ▼                              ▼
[Cloud Function]             [Cloud Scheduler → Apify]
   (exact metrics)              (competitor scrape)
        │                              │
        │                              ├──► [Raw Images → GCS]
        │                              │
        ▼                              ▼
[BigQuery: own_ads_performance]  [BigQuery: competitor_ads_raw]
        │                              │
        └──────────────┬───────────────┘
                       ▼
          [BigQuery: ads_master_view]
          (unified schema, ready for Step 2)
```

---

## 2. Internal Data Pipeline — Meta Marketing API

### 2.1 Trigger
A **Cloud Scheduler** job fires every 24 hours, invoking a lightweight **Cloud Function** (Python 3.12, 512MB RAM).

### 2.2 API Call Strategy
Pull the following fields per ad creative from the Meta Marketing API `/act_{ad_account_id}/ads` endpoint:

```python
fields = [
    "id",
    "name",
    "creative{id,image_url,video_id,body,title,call_to_action_type}",
    "adset{name,targeting,optimization_goal}",
    "campaign{name,objective}",
    "insights{spend,impressions,clicks,ctr,roas,cpc,cpp,frequency}",
    "status",
    "created_time",
    "updated_time",
]
```

### 2.3 BigQuery Write Schema — `own_ads_performance`

```json
{
  "ad_id": "STRING (PRIMARY KEY)",
  "ad_name": "STRING",
  "creative_id": "STRING",
  "image_url": "STRING",
  "gcs_image_path": "STRING",
  "ad_body": "STRING",
  "ad_title": "STRING",
  "call_to_action": "STRING",
  "campaign_name": "STRING",
  "campaign_objective": "STRING",
  "adset_name": "STRING",
  "targeting_age_min": "INTEGER",
  "targeting_age_max": "INTEGER",
  "targeting_genders": "STRING",
  "optimization_goal": "STRING",
  "spend": "FLOAT",
  "impressions": "INTEGER",
  "clicks": "INTEGER",
  "ctr": "FLOAT",
  "roas": "FLOAT",
  "cpc": "FLOAT",
  "cpp": "FLOAT",
  "frequency": "FLOAT",
  "status": "STRING",
  "created_time": "TIMESTAMP",
  "updated_time": "TIMESTAMP",
  "ingested_at": "TIMESTAMP"
}
```

### 2.4 Image Download to GCS
After each API response, the Cloud Function:
1. Downloads the `image_url` from Meta's CDN
2. Saves to `gs://{bucket}/raw-creatives/own/{ad_id}.{ext}`
3. Updates `gcs_image_path` in BigQuery

---

## 3. Competitor Data Pipeline — Meta Ad Library (Apify)

### 3.1 Why Apify
The Meta Ad Library has no official performance metrics. Apify's `meta-ads-library-scraper` actor handles browser automation and pagination, removing the need to manage a headless browser yourself.

### 3.2 Trigger
**Cloud Scheduler** fires the Apify actor via its REST API on a configurable cadence (e.g., daily or weekly per competitor brand).

```python
# Cloud Function triggers Apify actor
POST https://api.apify.com/v2/acts/{ACTOR_ID}/runs
Authorization: Bearer {APIFY_API_TOKEN}
Body: {
    "searchTerms": ["CompetitorBrandA", "CompetitorBrandB"],
    "country": "US",
    "adType": "all",
    "limit": 200
}
```

### 3.3 Apify Webhook → Cloud Function
Configure the Apify actor to call a **Cloud Function webhook** on completion. The webhook receives the dataset URL, fetches results, and writes to BigQuery.

### 3.4 BigQuery Write Schema — `competitor_ads_raw`

```json
{
  "ad_id": "STRING (Meta Ad Library ID)",
  "brand_name": "STRING",
  "ad_creative_url": "STRING",
  "gcs_image_path": "STRING",
  "ad_copy": "STRING",
  "page_name": "STRING",
  "page_id": "STRING",
  "ad_start_date": "DATE",
  "ad_end_date": "DATE (nullable — null = still active)",
  "days_active": "INTEGER (COMPUTED COLUMN)",
  "platforms": "STRING (REPEATED)",
  "ad_snapshot_url": "STRING",
  "currency": "STRING",
  "ingested_at": "TIMESTAMP"
}
```

> **`days_active` as Performance Proxy**
> Because Meta Ad Library withholds exact metrics for competitor ads, `days_active` serves as the proxy. An ad that has been running for 60+ days without being paused is a strong signal of profitability — advertisers do not keep losing ads running.

```sql
-- BigQuery computed column definition
days_active AS (
    CASE
        WHEN ad_end_date IS NULL THEN DATE_DIFF(CURRENT_DATE(), ad_start_date, DAY)
        ELSE DATE_DIFF(ad_end_date, ad_start_date, DAY)
    END
)
```

### 3.5 Image Download to GCS
After each scrape write, a second Cloud Function:
1. Fetches `ad_creative_url`
2. Saves to `gs://{bucket}/raw-creatives/competitor/{brand_name}/{ad_id}.{ext}`
3. Updates `gcs_image_path` in BigQuery
4. **Triggers Step 2 pipeline** via a GCS object-created event notification

---

## 4. Unified Master View

A BigQuery view joins both tables into a single normalised schema used by all downstream steps.

```sql
-- BigQuery View: ads_master_view
CREATE OR REPLACE VIEW `{project}.{dataset}.ads_master_view` AS
SELECT
    ad_id,
    'own'                           AS data_source,
    creative_id,
    gcs_image_path,
    ad_body                         AS ad_copy,
    ad_title,
    campaign_objective,
    roas                            AS performance_metric,
    'roas'                          AS metric_type,
    created_time                    AS ad_start_date,
    ingested_at
FROM `{project}.{dataset}.own_ads_performance`
WHERE status = 'ACTIVE'

UNION ALL

SELECT
    ad_id,
    'competitor'                    AS data_source,
    NULL                            AS creative_id,
    gcs_image_path,
    ad_copy,
    NULL                            AS ad_title,
    NULL                            AS campaign_objective,
    days_active                     AS performance_metric,
    'days_active'                   AS metric_type,
    ad_start_date,
    ingested_at
FROM `{project}.{dataset}.competitor_ads_raw`;
```

---

## 5. Operational Guardrails

### 5.1 Meta API Rate Limiting
- The Meta Marketing API enforces rate limits per ad account
- Use cursor-based pagination with a 2-second sleep between pages
- Cache the API access token in **Google Secret Manager** — never in environment variables or code

### 5.2 Apify Budget Guard
- Set a max monthly Apify compute unit budget in the Apify console
- Tie the actor run timeout to 10 minutes; kill and log if exceeded

### 5.3 Deduplication on Write
BigQuery does not enforce unique constraints. Use `MERGE` (upsert) instead of `INSERT`:

```sql
MERGE `{dataset}.competitor_ads_raw` T
USING (SELECT @ad_id AS ad_id, @brand_name AS brand_name, ...) S
ON T.ad_id = S.ad_id
WHEN MATCHED THEN UPDATE SET T.ingested_at = CURRENT_TIMESTAMP(), ...
WHEN NOT MATCHED THEN INSERT VALUES (S.ad_id, S.brand_name, ...);
```

### 5.4 Schema Evolution
When the Meta API adds or removes fields, the Cloud Function must not crash. Use `dict.get()` with safe defaults and log any unexpected missing fields as `WARNING` events.

### 5.5 Dead Letter Queue
If a BigQuery write fails after 3 retries, write the raw JSON payload to `gs://{bucket}/dlq/step1/{timestamp}_{ad_id}.json` and emit a Cloud Logging `ERROR` for manual review.
