# Step 5: Presentation & Stakeholder UI — Engineering Blueprint

> **Goal:** A fast, clean interface that lets media buyers and creative directors query the fine-tuned model and pattern data in plain language — without needing to touch BigQuery, Vertex AI, or any raw infrastructure.

---

## 1. Architecture Overview

```
[Stakeholder Browser]
        │
        ▼
[Cloud Run: Streamlit App]
   - Authenticates user (Google IAP)
   - Routes queries to correct backend
        │
        ├──► [Vertex AI SFT Endpoint]      (creative brief generation)
        │
        ├──► [BigQuery: pattern_discovery_results]  (data explorer view)
        │
        └──► [BigQuery: ads_master_view]   (ad performance lookup)
```

---

## 2. Cloud Run Deployment

### 2.1 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Streamlit config
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8080

CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
```

### 2.2 Cloud Run Configuration

```yaml
# cloud-run-service.yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: creative-agent-ui
  annotations:
    run.googleapis.com/ingress: internal-and-cloud-load-balancing
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "0"   # Scale to zero when idle
        autoscaling.knative.dev/maxScale: "3"   # Max 3 instances under load
        run.googleapis.com/cpu-throttling: "true"
    spec:
      containerConcurrency: 10
      timeoutSeconds: 60
      containers:
        - image: gcr.io/{project}/creative-agent-ui:latest
          resources:
            limits:
              memory: 512Mi
              cpu: "1"
          env:
            - name: GCP_PROJECT_ID
              valueFrom:
                secretKeyRef:
                  name: app-config
                  key: gcp_project_id
            - name: VERTEX_ENDPOINT_ID
              valueFrom:
                secretKeyRef:
                  name: app-config
                  key: vertex_endpoint_id
```

### 2.3 Authentication
Protect the Cloud Run service with **Google Identity-Aware Proxy (IAP)** so only authorised Google accounts (e.g., your team's Google Workspace domain) can access it. No custom auth code required.

```bash
gcloud run services add-iam-policy-binding creative-agent-ui \
  --member="domain:yourcompany.com" \
  --role="roles/run.invoker"
```

---

## 3. Application Structure

```
step5_ui/
├── app.py                  # Main Streamlit entry point — page router
├── pages/
│   ├── 01_brief_generator.py   # Chat interface → SFT endpoint
│   ├── 02_pattern_explorer.py  # BigQuery data visualisation
│   └── 03_ad_library.py        # Own + competitor ad browser
├── components/
│   ├── brief_card.py           # Renders a structured creative brief
│   ├── pattern_chart.py        # Renders feature importance charts
│   └── ad_card.py              # Renders individual ad with metrics
├── clients/
│   ├── vertex_client.py        # Calls the SFT endpoint
│   └── bigquery_client.py      # Queries pattern and performance tables
├── config.py
├── Dockerfile
└── requirements.txt
```

---

## 4. Page 1: Creative Brief Generator

The core feature. A stakeholder describes what they need; the app calls the fine-tuned SFT endpoint and renders a structured brief.

### 4.1 UI Layout

```python
# pages/01_brief_generator.py
import streamlit as st
from clients.vertex_client import query_sft_endpoint
from components.brief_card import render_brief_card

st.set_page_config(page_title="Brief Generator", layout="wide")
st.title("Creative Brief Generator")
st.caption("Powered by mathematically proven performance patterns")

# Input panel
with st.form("brief_form"):
    col1, col2, col3 = st.columns(3)
    with col1:
        category = st.selectbox("Product Category", PRODUCT_CATEGORIES)
    with col2:
        platform = st.selectbox("Platform / Format", AD_FORMATS)
    with col3:
        audience = st.text_input("Target Audience", placeholder="e.g., women 25-40")

    additional_context = st.text_area(
        "Additional Context (optional)",
        placeholder="e.g., launching new SPF moisturiser, premium positioning"
    )
    submitted = st.form_submit_button("Generate Brief", type="primary")

# Output panel
if submitted:
    with st.spinner("Generating brief from performance data..."):
        prompt = build_brief_prompt(category, platform, audience, additional_context)
        response = query_sft_endpoint(prompt)

    if response:
        render_brief_card(response)
    else:
        st.error("Failed to generate brief. Please try again.")
```

### 4.2 Brief Card Component

```python
# components/brief_card.py
import streamlit as st

def render_brief_card(brief: dict):
    st.success("Brief generated successfully")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Creative Direction")
        st.metric("Hook Framework", brief.get("hook_framework", "—"))
        st.metric("Background Style", brief.get("background_style", "—"))
        st.metric("Human Model", "Yes" if brief.get("human_presence") else "No")

    with col2:
        st.subheader("Performance Confidence")
        st.metric("Avg ROAS (training set)", f"{brief.get('avg_roas', 0):.2f}x")
        st.metric("Confidence Score", f"{brief.get('confidence_score', 0):.0%}")
        st.metric("Sample Size", f"{brief.get('sample_size', 0)} ads")

    st.subheader("Colour Palette")
    palette = brief.get("dominant_hex_palette", [])
    cols = st.columns(len(palette)) if palette else []
    for col, hex_code in zip(cols, palette):
        with col:
            st.markdown(
                f'<div style="background:{hex_code};height:60px;border-radius:8px;"></div>',
                unsafe_allow_html=True
            )
            st.caption(hex_code)

    with st.expander("Raw JSON Output"):
        st.json(brief)

    st.download_button(
        "Download Brief as JSON",
        data=str(brief),
        file_name="creative_brief.json",
        mime="application/json"
    )
```

---

## 5. Page 2: Pattern Explorer

A read-only data view that lets stakeholders self-serve insights without needing BigQuery access.

```python
# pages/02_pattern_explorer.py
import streamlit as st
import pandas as pd
import altair as alt
from clients.bigquery_client import get_pattern_data, get_feature_importance

st.title("Pattern Explorer")
st.caption("Mathematically derived creative performance patterns")

# Feature importance chart
st.subheader("Feature Importance Ranking")
importance_df = get_feature_importance()
chart = alt.Chart(importance_df).mark_bar().encode(
    x=alt.X("importance_gain:Q", title="Importance (Gain)"),
    y=alt.Y("feature:N", sort="-x", title="Creative Feature"),
    color=alt.value("#4F46E5"),
    tooltip=["feature", "importance_gain", "importance_weight"]
).properties(height=350)
st.altair_chart(chart, use_container_width=True)

# Pattern data table
st.subheader("Top Performing Combinations")
filters = st.columns(3)
with filters[0]:
    hook_filter = st.multiselect("Hook Framework", HOOK_FRAMEWORKS)
with filters[1]:
    bg_filter = st.multiselect("Background Style", BACKGROUND_STYLES)
with filters[2]:
    min_samples = st.slider("Min Sample Size", 5, 50, 10)

patterns_df = get_pattern_data(
    hook_frameworks=hook_filter or None,
    background_styles=bg_filter or None,
    min_sample_size=min_samples
)
st.dataframe(
    patterns_df.style.background_gradient(subset=["avg_performance"], cmap="Greens"),
    use_container_width=True,
    hide_index=True
)
```

---

## 6. Vertex AI Client

```python
# clients/vertex_client.py
import json
from google.cloud import aiplatform
from logger import get_logger

logger = get_logger("vertex_client")

def query_sft_endpoint(prompt: str) -> dict | None:
    """
    Sends a prompt to the fine-tuned Vertex AI endpoint.
    Returns parsed JSON brief or None on failure.
    """
    aiplatform.init(project=PROJECT_ID, location=REGION)
    endpoint = aiplatform.Endpoint(VERTEX_ENDPOINT_ID)

    payload = {
        "instances": [
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ]
            }
        ],
        "parameters": {
            "temperature": 0.1,    # Low temperature for deterministic structured output
            "maxOutputTokens": 512,
            "topP": 0.9
        }
    }

    try:
        logger.info("vertex_request_sent", prompt_length=len(prompt))
        response = endpoint.predict(instances=payload["instances"], parameters=payload["parameters"])
        raw_text = response.predictions[0]
        parsed = json.loads(raw_text)
        logger.info("vertex_response_received", keys=list(parsed.keys()))
        return parsed

    except json.JSONDecodeError as e:
        logger.error("vertex_json_parse_failed", error=str(e), raw_response=raw_text[:200])
        return None
    except Exception as e:
        logger.error("vertex_request_failed", error=str(e))
        return None
```

---

## 7. Requirements

```
# requirements.txt
streamlit==1.35.0
google-cloud-aiplatform==1.58.0
google-cloud-bigquery==3.20.0
google-cloud-bigquery-storage==2.25.0
google-auth==2.29.0
pandas==2.2.2
altair==5.3.0
structlog==24.1.0
pydantic==2.7.1
pydantic-settings==2.2.1
tenacity==8.3.0
```

---

## 8. Operational Guardrails

### 8.1 Scale-to-Zero
Cloud Run is configured with `minScale: 0`. The app costs nothing when not in use. First cold start takes ~5-8 seconds — acceptable for an internal tool.

### 8.2 BigQuery Query Caching
Cache BigQuery results in Streamlit's session state to avoid re-querying on every widget interaction. Invalidate cache if data is older than 1 hour:

```python
@st.cache_data(ttl=3600)
def get_pattern_data(**kwargs) -> pd.DataFrame:
    ...
```

### 8.3 Response Fallback
If the SFT endpoint returns an unparseable response, display a graceful fallback:
```python
st.warning("The model returned an unexpected format. Showing the closest matching static pattern instead.")
fallback = get_top_pattern_from_bigquery(category, platform)
render_brief_card(fallback)
```

### 8.4 Error Visibility
Never show raw Python tracebacks to stakeholders. All errors surface as friendly `st.error()` messages. All raw error details go to Cloud Logging via `structlog`.
