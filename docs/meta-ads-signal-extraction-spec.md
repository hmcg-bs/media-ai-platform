# Meta Ads Proxy Signal Extraction Specification

This document provides a technical blueprint and implementation plan for an AI agent to build a data pipeline. The pipeline extracts, cleans, and structures competitive proxy signals from Meta Ad Library and destination funnels. These structured signals serve as inputs to construct a **Composite Success Score ($Y$)** and extract corresponding **Ad Features ($X$)** to train an ad performance prediction model.

---

## 1. Pipeline Architecture Overview

The extraction pipeline consists of three core phases that transform raw public Meta Ad data into clean, structured model features:

```
┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────┐
│   Phase 1: Discovery   │ ───> │  Phase 2: Data Pull    │ ───> │   Phase 3: Forensic    │
│                        │      │                        │      │      Extraction        │
└────────────────────────┘      └────────────────────────┘      └────────────────────────┘
 - Resolve official Brand       - Graph API /ads_archive        - Scrape Landing Pages
   Page IDs (avoid same-          using strict page_id lists    - Parse UTM parameters
   name false positives)        - Extract creative assets,      - Scan tech footprint
 - Define tracking GEOs and       copy, start_date, and           (Meta Pixel/CAPI,
   platform matrices              impression-range buckets        Shopify/Magento/Woo)
                                                                - Reconstruct permalinks
                                                                  to harvest comments
```

---

## 2. Technical Extraction Specifications

### Phase 1: Brand Discovery & False-Positive Mitigation

To prevent data pollution from same-name local business pages (e.g., several local bakeries sharing the same name in different countries), the pipeline must resolve and track brands by **Page ID** and **Target Domain** rather than Page Name [405].

#### Discovery Steps for the Agent:
1. Perform an initial manual/programmatic lookup in the Meta Ad Library to retrieve the official **Page ID** of the target brand [405, 497].
2. Verify the Page ID by cross-referencing the Page's verified **website domain** against the known brand site [405]. Same-name local businesses almost never share the same domain [405].
3. For large-scale brands, identify all regional child Pages (e.g., `Brand US`, `Brand DE`) to capture global active ad counts and aggregate them [495].

---

### Phase 2: Creative & Delivery Metadata Pull (API & Web Scraping)

The agent should programmatically pull active ad datasets using the **Meta Marketing API (Ad Archive / Ads Directory Endpoint)** [498, 499].

#### Meta Ads Archive API Specifications:
* **Endpoint:** `GET https://graph.facebook.com/v20.0/ads_archive` [1022]
* **Query Parameters:**
  * `access_token`: App-scoped developer access token [1021, 1022].
  * `search_page_ids`: A comma-separated list of resolved **Page IDs** (max 10 per request to optimize rate limits) [1021, 1022]. Do *not* use broad keyword terms unless mapping categories, as Page IDs ensure deterministic results [405, 1021].
  * `ad_active_status`: Set strictly to `ACTIVE` [499, 1022].
  * `ad_reached_countries`: JSON array of target countries, e.g., `['US', 'GB', 'FR']` [499, 1022]. *Note: This parameter is strictly required by the Graph API.* [1022]
  * `fields`: Extract the following metadata fields [499, 1022]:
    * `id`: The unique Ad ID.
    * `ad_delivery_start_time`: Used to calculate **Ad Longevity** ($Ad\_Age = Current\_Date - Start\_Time$) [1022].
    * `ad_creative_bodies`: Raw caption text for messaging analysis.
    * `ad_creative_link_captions` & `ad_creative_link_descriptions`: Headline and copy details.
    * `publisher_platforms`: Platforms where the ad is currently active (e.g., `facebook`, `instagram`, `messenger`, `audience_network`) to compute platform proliferation [236, 1139].
    * `ad_snapshot_url`: Link to retrieve high-resolution images or raw `.mp4` video files [1022, 1025].
    * `estimated_audience_size` & `impressions` (for EU/UK targets): Sum range midpoints to get exact volume weights [477, 1080].

#### API Rate-Limit Handling Rules:
* Limit request batches to 10 Page IDs [1021, 1024].
* Respect dynamic rate-limiting headers by implementing an **exponential backoff algorithm** with a minimum base wait of 2.0 seconds between paginated queries [1021, 1044].
* Store cursor-based pagination state (`paging.cursors.after`) safely to resume extraction on timeout [499, 1023].

---

### Phase 3: Post-Click Funnel & Infrastructure Forensics

For every active ad captured, the agent must scrape its destination URL (retrieved from the ad's CTA link) to extract tracking maturity and landing page design elements [598].

#### 1. UTM Parameter Parsing:
The agent must parse query parameters from the landing page URL and map them to campaign taxonomic depth [1075]:
* Extract `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, and `utm_content` [1075].
* Look for dynamic tokens (e.g., `{{campaign.name}}`, `{{placement}}`, `{{ad.id}}`) in the parameters [1139, 1140]. The presence of these tokens signifies advanced programmatic buying [1141].
* Analyze the structure of `utm_content` [1076]:
  * Structured hierarchies (e.g., `UGC_V1_Hook3_CTA2`) indicate a mature testing matrix with distinct visual/hook variations [1076].
  * Legacy structures (e.g., `utm_medium=cpc` instead of `paid-social`) indicate less sophisticated tracking setups [1076].

#### 2. Tracking Footprint & Platform Detection:
Analyze the landing page source code to detect marketing tags [7]:
* **Meta Pixel presence:** Locate the global base code `fbq('init', 'Pixel_ID')` [353].
* **Meta Conversions API (CAPI) Integration:** Detect hybrid server-side tracking (e.g., presence of Conversions API Gateways, GTM server-side hosting, sGTM, or Cometly/Stape integrations) [1, 14, 1076].
* **E-Commerce Stack:** Detect the CMS platform (e.g., Shopify, Magento 2, WooCommerce) to assess catalog feed hygiene [2, 8, 1077].

---

### Phase 4: Social Proof & Engagement Harvesting

Since Meta does not provide public conversion data for commercial ads, the agent must bypass standard Ad Library frames and fetch **dark post permalinks** to harvest comments and engagement metrics [1078].

#### 1. Permalink Reconstruction:
Reconstruct the live permanent URL of the Facebook ad post using the following schema [813]:
```
https://www.facebook.com/[Advertiser_ID]/posts/[Ad_ID_or_Library_ID]
```
For Instagram-native placements, extract the `"View on Instagram"` link directly from the ad's detail card metadata [578].

#### 2. Comment Harvesting and Sentiment Analysis:
Scrape the live comment threads on the reconstructed permalink and classify them using NLP [1079]:
* **Positive Testimonials / High Intent:** Classify comments signaling direct purchase intent (e.g., *"How long is shipping?"*, *"Is this available in blue?"*, *"Just ordered!"*) [328, 329, 1079].
* **Brand Detractors / Critical Feedback:** Flag negative comment trolls or complaints about pricing and shipping delays, which lower the ad relevance score [327, 1079].
* **Comment Velocity:** Track the timestamp of the last 10 comments to identify if the ad is actively supported by spend, or if it is a dormant "active" ad receiving near-zero impressions [1079, 1083].

---

## 3. Algorithmic Guardrails & Data Cleaning

Systemic noise in Meta's ad delivery model can skew features. The agent must implement these cleaning filters:

### 1. The "Longevity Trap" Filter (Cost-Cap Dormancy)
* **The Problem:** Under bid-cap or cost-cap bidding, media buyers keep unprofitable ads "Active," but the Meta algorithm has choked impressions to zero because the ad failed to win bids [1083]. 
* **The Filter:** Discard any ad with a "Low Impression Count" badge or an impression range of under 100, regardless of high ad age ($Ad\_Age > 45$ Days) [1072, 1073].

### 2. The Decoy Ad Filter
* **The Problem:** Competitors occasionally run legacy or underperforming creatives in isolated campaigns at nominal budgets (e.g., \$1/day) to skew spying databases [1084].
* **The Filter:** Exclude ads with high longevity but zero comment velocity, static visual states, and low impression-range buckets [1084].

### 3. Catalog/Advantage+ Creative Glitches
* **The Problem:** For catalog ads and Advantage+ creatives, the Ad Library often displays a static placeholder product image rather than the live creative seen in feeds [896, 1084].
* **The Mitigation:** Identify catalog ads via `uses_dynamic_creative` flags or pattern matching in URLs. Group dynamic variants under a parent "Catalog ID" and evaluate their creative lifecycle in aggregate [1084].

---

## 4. Extraction Target Output Schema

For each extracted ad, output a structured JSON record matching this schema to feed into the training dataset:

```json
{
  "ad_id": "23859604812",
  "advertiser_page_id": "104928502",
  "advertiser_name": "Divi",
  "target_domain": "diviofficial.com",
  "ad_delivery_start_date": "2026-06-15",
  "extracted_timestamp": "2026-08-10T08:53:34Z",
  "ad_age_days": 56,
  "last_comment_velocity_days": 1.2,
  "publisher_platforms": ["facebook", "instagram", "messenger"],
  "variant_duplication_count": 9,
  "impression_range_bucket": "100k_to_1M",
  "cta_type": "SHOP_NOW",
  "destination_url": "https://diviofficial.com/products/scalp-serum?utm_source=facebook&utm_medium=InstagramStories&utm_campaign=summer_sale&utm_content=UGC_V1_Hook3_CTA2",
  "utm_parameters": {
    "utm_source": "facebook",
    "utm_medium": "InstagramStories",
    "utm_campaign": "summer_sale",
    "utm_content": "UGC_V1_Hook3_CTA2"
  },
  "infrastructure": {
    "pixel_detected": true,
    "capi_integration": true,
    "ecommerce_platform": "Shopify"
  },
  "engagement_metrics": {
    "total_comment_count": 245,
    "sentiment_score": 0.82,
    "purchase_intent_comment_ratio": 0.34
  },
  "creative_features_x": {
    "format": "VIDEO",
    "copy_word_count": 87,
    "social_proof_detected": true,
    "social_proof_type": "QUANTIFIABLE_CUSTOMER_COUNT",
    "hook_type": "BENEFIT_FIRST",
    "visual_composition": "UGC_STYLE",
    "color_scheme": "WARM_NEUTRAL"
  }
}
```

---

## 5. Agent Action Plan & Implementation Checklist

The AI agent should execute the extraction pipeline by proceeding through these steps:

- [ ] **Step 1: Bootstrap Target Brand Lists**
  * Parse the input list of competitors and query the Ad Library API to resolve unique Page IDs.
  * Extract and store target web domains for each ID.
- [ ] **Step 2: API Harvest**
  * Execute Graph API search queries across all Page IDs for `ad_active_status=ACTIVE`.
  * Handle cursor pagination, log rate-limit responses, and save raw JSON outputs.
- [ ] **Step 3: Funnel Scraping & Header Detection**
  * Iterate through extracted destination URLs.
  * Scrape landing pages to parse UTM strings, dynamic parameter presence, Pixel scripts, and server-side CAPI headers.
- [ ] **Step 4: Permalink SocialProof Scraping**
  * Reconstruct permalinks and fetch live comment feeds.
  * Run sentiment classification and calculate comment recency.
- [ ] **Step 5: Apply Guardrails & Output Dataset**
  * Execute filtering algorithms to remove cost-cap dormant ads and decoy ads.
  * Compile the cleaned, structured records into the final JSON file for model training.
