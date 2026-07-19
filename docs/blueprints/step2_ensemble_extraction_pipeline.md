# Step 2: Ensemble Extraction Pipeline — Engineering Blueprint

> **Philosophy:** Use free, deterministic code for structural properties. Reserve Multimodal LLMs (Gemma/Gemini) exclusively for cognitive and semantic analysis. Eliminate hallucination risk and minimize compute cost.

---

## 1. System Architecture & Execution Flow

The pipeline is **stateless and event-driven**, progressing through four distinct stages:

```
[Raw Image in GCS] ──► [Cloud Workflows]
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
   [Stage 1: Deterministic]       [Stage 2: Async Scraper]
   - OpenCV Color Math            - Scrape Destination URL
   - Cloud Vision OCR             - Extract Product Image
   - Geometry Layout Engine                 │
            │                               │
            └───────────────┬───────────────┘
                            ▼
               [Stage 3: Cognitive Layer]
               - Gemini Flash-Lite (Layout, Vibe, Psych)
               - Gemma 4 (Cross-Verification & Nesting)
                            │
                            ▼
               [Stage 4: BigQuery Ingestion]
```

---

## 2. Phase-by-Phase Technical Blueprint

### Phase 2.1 — Deterministic Processing Engine (Python / Cloud Run)

**Trigger:** Image dropped into GCS bucket → Cloud Workflow → Cloud Run container.

#### 2.1.1 Metadata Extraction
Read image binary headers to extract:
- `width`, `height`, `aspect_ratio`, `file_size`

#### 2.1.2 OCR via Cloud Vision API
- Send image to **Google Cloud Vision API**
- Receive raw text strings with absolute pixel coordinates: `vertices: [{x, y}]` per text box

#### 2.1.3 Geometric Typography Hierarchy
Avoid using an LLM to guess headline structure. Use spatial area math instead:

$$\text{Block Area} = (\text{Max } X - \text{Min } X) \times (\text{Max } Y - \text{Min } Y)$$

- Largest area block → `primary_headline`
- Remaining blocks sorted descending by area → `secondary_copy`
- Calculate headline-to-secondary ratio for typographic hierarchy tracking

#### 2.1.4 Color Math (K-Means Clustering via OpenCV)
1. Mask out all OCR bounding boxes (prevents black text skewing palette results)
2. Run K-Means over remaining pixels → top 3 dominant `HEX` values
3. Sample the outermost **10% perimeter matrix** of the image → `background_color`

---

### Phase 2.2 — Asynchronous Product Verification Loop

**Goal:** Verify the ad creative matches the transactional intent of its destination URL.

- **Scraper:** Cloud Workflows triggers an async task that:
  1. Hits the landing page URL
  2. Extracts the primary `<meta property="og:image">` or main product listing image
  3. Saves it to a temp cache folder in GCS

- **Verification Gate:** The cached landing page image is passed alongside the ad creative into Stage 2.3

---

### Phase 2.3 — Multimodal Spatial & Cognitive Layer (Vertex AI)

Invokes models via **Vertex AI custom endpoints** using strict **JSON Schema Enforcement** (`response_mime_type="application/json"`).

#### Task A — Gemini Flash-Lite (Low Cost): Marketing Psychology & Vibe
- Analyze ad copy and visual style
- Identify:
  - Core **marketing hook framework** (e.g., PAS, AIDA, Social Proof, Direct Offer)
  - Target audience's **primary emotional lever** (e.g., Status, Urgency, Relief)
  - Overall **brand vibe**

#### Task B — Gemma 4 (High Reasoning): Deep Feature & Nested Relationship Extraction
- **Object Identification:**
  - `primary_product` with visual state
  - `secondary_props` (environment items, accessories)
  - Directional spatial and narrative relationships between objects

- **Human Micro-Detail Extraction** (if models present):
  - Demographic profile
  - Physical actions
  - Micro-expressions (e.g., slight smile, neutral corporate, extreme joy)
  - Wardrobe details
  - Environmental physical interactions (e.g., water droplets on skin, studio lighting reflections)

- **Visual Verification:**
  - Compare original ad image vs. scraped landing page image from Phase 2.2
  - Output `is_visually_verified_match: true/false`

---

## 3. Master JSON Schema

Every asset processed through Step 2 outputs an identical structural payload for clean, normalized BigQuery ML / XGBoost ingestion.

```json
{
  "ad_id": "string",
  "technical_metadata": {
    "width": "integer",
    "height": "integer",
    "aspect_ratio": "string (e.g., 1:1, 9:16)",
    "file_type": "string"
  },
  "color_profile": {
    "background_hex": "string",
    "background_style": "string (e.g., Studio, Gradient, Transparent)",
    "dominant_hex_palette": ["string"],
    "contrast_ratio_type": "string (e.g., High, Low, Monochromatic)"
  },
  "typography_hierarchy": {
    "primary_headline": {
      "text": "string",
      "canvas_coverage_percentage": "float"
    },
    "secondary_copy": [
      {
        "text": "string",
        "canvas_coverage_percentage": "float"
      }
    ],
    "headline_to_subtext_scale_ratio": "float"
  },
  "product_verification": {
    "landing_page_url": "string",
    "is_visually_verified_match": "boolean | null",
    "verification_confidence_score": "float"
  },
  "spatial_and_nested_objects": {
    "primary_product": {
      "name": "string",
      "visual_state": "string (e.g., Closed, Open, In-Use)"
    },
    "secondary_props": [
      {
        "name": "string",
        "type": "string (e.g., Environment, Accessory)"
      }
    ],
    "object_relationships": [
      {
        "subject": "string",
        "relationship_action": "string (e.g., writing_on, sitting_inside, pouring_into)",
        "object": "string"
      }
    ],
    "texture_demonstration": {
      "visible": "boolean",
      "texture_type": "string (e.g., Liquid Smear, Powder Dust, Foam)"
    }
  },
  "human_model_analysis": {
    "human_presence": "boolean",
    "model_count": "integer",
    "details": [
      {
        "estimated_demographic": "string",
        "action_performed": "string",
        "micro_expression": "string",
        "wardrobe_style": "string",
        "environmental_modifiers": ["string (e.g., water droplets, wind hair)"]
      }
    ]
  },
  "marketing_psychology": {
    "hook_framework": "string (Enum: PAS, AIDA, Before/After, Testimonial, Direct Offer)",
    "primary_value_proposition": "string",
    "authority_flags": ["string"],
    "emoji_count": "integer",
    "reading_grade_level": "string"
  }
}
```

---

## 4. Operational Guardrails (Solo Maintenance Mode)

### 4.1 Scraper Timeout & Fallback
- If a destination URL blocks the scraper or exceeds **10 seconds**, the Cloud Workflow:
  - Terminates the scraper task
  - Sets `is_visually_verified_match` to `null`
  - Pushes remaining image metrics forward
- **Rule:** The pipeline must never halt due to a single dead URL

### 4.2 Cost Containment
- Enforce daily hard limits via **Cloud Quotas** on Vertex AI API calls
- Default ceiling: **5,000 image extractions/day**
- Protects against accidental billing from infinite scraper loops

### 4.3 Dead Letter Queue (DLQ)
- If an image is corrupted or Gemini fails to parse a format:
  1. Cloud Workflows catches the error
  2. Moves the bad file to `gs://corrupted-ad-creatives/`
  3. Logs error code to **Cloud Logging**
  4. Proceeds to next image in queue without interruption
