"""Product category classifier using Gemini (via Replicate)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pipeline.clients.replicate_client import ReplicateVisionClient


class SupplementClassification(BaseModel):
    """Structured output schema for the supplement/non-supplement classifier."""

    is_supplement: bool = Field(
        description="True only if the ad promotes a consumable supplement product"
    )
    supplement_type: Literal[
        "vitamin", "mineral", "protein", "herbal", "skincare", "fitness", "other", "unknown"
    ] = Field(description="Category of supplement, or 'unknown' if not a supplement")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the classification")
    reasoning: str = Field(description="Brief explanation, under 30 words")


_PROMPT_TEMPLATE = """You are a product-category classifier. Look at this ad creative image \
and classify whether it is promoting a supplement product.

Ad text (may be incomplete or unhelpful — the image is authoritative):
{ad_text}

RULES:
- is_supplement = true ONLY if the ad is promoting a consumable supplement product (vitamins, minerals, protein powder, herbs, capsules, gummies, powders, etc.) — judge from the product shown in the image as well as the text
- is_supplement = false for: fitness equipment, gym memberships, topical-only skincare, pharmaceuticals/prescription drugs, unrelated products
- Many supplement ads use long "advertorial" story copy where the product only appears in the image or late in the text — do not assume non-supplement just because the visible text doesn't mention a product
- confidence reflects certainty (0.5-0.6 = borderline, 0.8+ = clear)
- Keep reasoning under 30 words

Respond only with JSON matching this schema, no other text:
{{"is_supplement": true/false, "supplement_type": "vitamin"|"mineral"|"protein"|"herbal"|"skincare"|"fitness"|"other"|"unknown", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}
"""


def _fetch_image_bytes(url: str, timeout_s: float = 15.0) -> bytes | None:
    """Fetch an ad creative image; returns None if the URL is dead/expired."""
    import httpx

    try:
        resp = httpx.get(url, timeout=timeout_s, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPError:
        return None


def _build_ad_text(title: str | None, body: str | None, cta_text: str | None) -> str:
    """Join available text fields; long-form advertorial bodies are used as-is
    since the model receives the image too and doesn't need the full body to
    locate the pitch."""
    parts = [p for p in [title, body, cta_text] if p]
    return " | ".join(parts) if parts else "(no text available)"


def classify_ad_as_supplement(
    title: str | None,
    body: str | None,
    cta_text: str | None,
    image_urls: list[str] | None = None,
) -> dict[str, Any]:
    """
    Classify an ad as supplement/non-supplement using Gemini (via Replicate).

    Uses the ad creative image when available — text alone is unreliable here:
    some ads only have unrendered template placeholders for title/body, and
    others use long-form advertorial copy where the product is mentioned late
    or only shown visually.

    Returns dict with:
      - is_supplement: bool (True if ad is about supplement product)
      - supplement_type: str (vitamin, mineral, protein, herbal, etc. or 'unknown')
      - confidence: float (0.0-1.0)
      - reasoning: str (brief explanation)
    """
    ad_text = _build_ad_text(title, body, cta_text)
    prompt = _PROMPT_TEMPLATE.format(ad_text=ad_text[:2000])

    client = ReplicateVisionClient()

    image_bytes = None
    if image_urls:
        image_bytes = _fetch_image_bytes(image_urls[0])

    if image_bytes is not None:
        result = client.extract_structured(
            prompt=prompt, image_bytes=image_bytes, schema=SupplementClassification
        )
    else:
        # Fall back to text-only if the image URL is dead/expired.
        result = client.extract_structured_text(prompt=prompt, schema=SupplementClassification)

    return result.model_dump()


def _classify_one(
    i: int,
    ad: dict[str, Any],
    already_classified: dict[str, dict],
    max_retries_per_ad: int,
    retry_backoff_s: float,
) -> tuple[int, dict[str, Any]]:
    """Classify a single ad with retry; returns (original_index, result)."""
    import time

    archive_id = ad.get("ad_archive_id")
    if archive_id and archive_id in already_classified:
        return i, already_classified[archive_id]

    classification = None
    last_error = None
    for attempt in range(max_retries_per_ad + 1):
        try:
            classification = classify_ad_as_supplement(
                title=ad.get("title"),
                body=ad.get("body"),
                cta_text=ad.get("cta_text"),
                image_urls=ad.get("image_urls"),
            )
            break
        except Exception as exc:  # noqa: BLE001 — one bad ad shouldn't kill the batch
            last_error = exc
            if attempt < max_retries_per_ad:
                time.sleep(retry_backoff_s)

    if classification is None:
        classification = {
            "is_supplement": None,
            "supplement_type": "unknown",
            "confidence": 0.0,
            "reasoning": f"classification_error: {last_error!r}",
        }

    return i, {**ad, "classification": classification}


def batch_classify_ads(
    ads: list[dict[str, Any]],
    batch_size: int = 10,
    requests_per_minute: float = 6.0,
    checkpoint_path: str | None = None,
    checkpoint_every: int = 3,
    already_classified: dict[str, dict] | None = None,
    max_retries_per_ad: int = 2,
    max_workers: int = 1,
) -> list[dict[str, Any]]:
    """Classify multiple ads, optionally in parallel.

    max_workers=1 (default) runs serially, paced to requests_per_minute — use
    this while Replicate account credit is under $5 (throttled to 6 req/min,
    see ReplicateError 429). Once credit is topped up there's no meaningful
    rate limit (verified: 20 concurrent calls, 0 throttling errors) — pass a
    higher max_workers (e.g. 15) for large batches; ~1.1s/call amortized
    vs. ~10s/call serially paced.

    If checkpoint_path is given, partial results are flushed to disk every
    checkpoint_every completions so a killed/interrupted run doesn't lose
    progress. already_classified (keyed by ad_archive_id) lets a re-run skip
    ads a previous attempt already finished. Output preserves input order
    (indexed, not append-order) so positional matching against ground truth
    by index still works when run concurrently.

    A single ad's failure (network timeout, bad image) no longer crashes the
    whole batch — retried a couple of times, then recorded with an error
    classification so the run continues and checkpoints stay intact.
    """
    import json
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    already_classified = already_classified or {}
    results: list[dict[str, Any] | None] = [None] * len(ads)
    lock = threading.Lock()
    completed = 0
    retry_backoff_s = 60.0 / requests_per_minute if max_workers <= 1 else 1.0

    def checkpoint_locked() -> None:
        if checkpoint_path and (completed % checkpoint_every == 0):
            with open(checkpoint_path, "w") as f:
                json.dump([r for r in results if r is not None], f, indent=2)

    if max_workers <= 1:
        min_interval_s = 60.0 / requests_per_minute
        calls_made = 0
        for i, ad in enumerate(ads):
            archive_id = ad.get("ad_archive_id")
            is_cached = archive_id and archive_id in already_classified
            print(f"  Classifying ad {i+1}/{len(ads)}...", end='\r')
            if not is_cached:
                if calls_made > 0:
                    time.sleep(min_interval_s)
                calls_made += 1
            _, result = _classify_one(
                i, ad, already_classified, max_retries_per_ad, retry_backoff_s
            )
            results[i] = result
            completed += 1
            checkpoint_locked()
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(
                    _classify_one, i, ad, already_classified, max_retries_per_ad, retry_backoff_s
                ): i
                for i, ad in enumerate(ads)
            }
            for fut in as_completed(futures):
                idx, result = fut.result()
                with lock:
                    results[idx] = result
                    completed += 1
                    print(f"  Classified {completed}/{len(ads)} ads...", end='\r')
                    checkpoint_locked()

    if checkpoint_path:
        with open(checkpoint_path, "w") as f:
            json.dump(results, f, indent=2)

    print(f"  Classified {len(ads)} ads             ")
    return results
