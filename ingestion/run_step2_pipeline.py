"""Step 2 bridge: run the existing creative-analysis pipeline (pipeline/) over
ads in the ingestion corpus (data/supplements_enriched.json).

No image is ever persisted to disk — every pipeline stage operates on
``context.image_bytes`` directly (confirmed by reading stage_02_ocr.py,
stage_03_color.py, stage_05_cognitive.py: each asserts ``image_bytes is not
None`` and never touches ``image_path``; only Stage 1 has a file-read
fallback, unused here since bytes are always pre-supplied). Each ad's primary
creative image is fetched into memory, run through the stage chain, and only
the resulting JSON is written — the bytes are discarded immediately after.

Resumable via output-file-existence: an ad whose ``out/step2/<ad_id>.json``
already exists is skipped, so a second run only processes what's missing.
This stands in for a download cache without needing one.

Usage:
    uv run python -m ingestion.run_step2_pipeline --sample-size 30
    uv run python -m ingestion.run_step2_pipeline
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from ingestion.download import _get_extension_from_url
from pipeline.config import get_settings
from pipeline.logger import configure_logging, get_logger
from pipeline.orchestrator import build_default_stages, run_one
from pipeline.stages.base_stage import BaseStage

logger = get_logger(__name__)


def fetch_image_bytes(url: str, timeout: int = 10) -> bytes | None:
    """Fetches image bytes into memory; never writes to disk. Returns None
    (logged, not raised) on any failure — mirrors ingestion/download.py's
    honesty convention of never dropping an ad over a failed fetch.

    HTTPException (e.g. IncompleteRead) is caught alongside URLError/OSError/
    ValueError: confirmed live that Facebook's CDN closing a connection
    mid-read raises http.client.IncompleteRead, which is neither — it went
    uncaught here, propagated out of a worker thread, and crashed a ~3-hour
    run at future.result() (nothing downstream was catching it either)."""
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 — known Facebook CDN URLs
            return response.read()
    except (URLError, OSError, ValueError, HTTPException) as e:
        logger.warning("step2_image_fetch_failed", url=url, error=str(e))
        return None


_Outcome = str  # one of "processed" / "no_images" / "fetch_failed" / "error"


def _process_one_ad(
    ad: dict,
    ad_id: str,
    out_dir: Path,
    stages: list[BaseStage],
    fetch_fn: Callable[[str], bytes | None],
) -> _Outcome:
    """Fetch -> run stage chain -> write JSON for one ad. Returns an outcome
    string rather than mutating shared counters directly, so this is safe to
    call from multiple worker threads at once — the caller does all counting
    sequentially as results come back.

    The whole body is wrapped in a catch-all: confirmed live that an
    unexpected exception from one ad (an unhandled HTTPException on fetch,
    but in principle any other freak per-ad failure) propagated all the way
    up through future.result() in run_step2_pipeline and crashed the entire
    multi-hour batch — losing nothing already written (resumable), but
    aborting everything still queued. run_one() itself is deliberately left
    loud on unexpected internal errors (StageError is the only kind a stage
    should raise; anything else is a real bug worth surfacing during
    dev/tests) — this catch-all exists one layer up, at the long-running
    batch-job boundary, where "skip this one ad and keep going" is the
    correct tradeoff instead."""
    try:
        image_urls = ad.get("image_urls") or []
        if not image_urls:
            logger.info("step2_skip_no_images", ad_id=ad_id)
            return "no_images"

        url = image_urls[0]
        image_bytes = fetch_fn(url)
        if image_bytes is None:
            return "fetch_failed"

        ext = _get_extension_from_url(url)
        context = run_one(Path(f"{ad_id}.{ext}"), stages, image_bytes=image_bytes)
        out_path = out_dir / f"{ad_id}.json"
        out_path.write_text(json.dumps(context.result.model_dump(mode="json"), indent=2))
        logger.info("step2_asset_written", ad_id=ad_id, failed_stages=context.failed_stages)
        return "processed"
    except Exception as exc:  # noqa: BLE001 — one ad's freak failure must never abort the batch
        logger.error("step2_ad_processing_failed", ad_id=ad_id, error=str(exc))
        return "error"


def run_step2_pipeline(
    ads_file: Path,
    out_dir: Path,
    sample_size: int | None = None,
    seed: int = 42,
    stages: list[BaseStage] | None = None,
    fetch_fn: Callable[[str], bytes | None] = fetch_image_bytes,
    concurrency: int = 1,
) -> int:
    """Processes every ad in ``ads_file`` (or a random sample of
    ``sample_size``) through the Step 2 stage chain, writing one JSON per ad
    to ``out_dir``. Returns the count of ads newly processed this run.
    ``stages``/``fetch_fn`` are injectable for offline tests — default to
    the real stage chain and a real network fetch.

    ``concurrency`` (default 1, sequential) processes up to that many ads at
    once via a thread pool — each ad's dominant cost is network I/O wait
    (image fetch + the two Gemini calls inside CognitiveStage), so this is
    the main lever for total run time. Every stage in the default chain is
    confirmed safe to share across concurrently-processed ads: stages are
    either fully stateless (MetadataStage, OCRStage, CognitiveStage — the
    latter's client is a stateless-per-call HTTP client) or were fixed to be
    (ColorStage used to stash its k-means result on instance state between
    two lines of one process() call — see stage_03_color.py — now returns it
    as a local value instead).
    """
    settings = get_settings()
    ads = json.loads(ads_file.read_text())

    if sample_size is not None:
        rng = random.Random(seed)
        ads = rng.sample(ads, min(sample_size, len(ads)))

    out_dir.mkdir(parents=True, exist_ok=True)
    if stages is None:
        stages = build_default_stages(settings)

    processed = 0
    skipped_existing = 0
    skipped_no_images = 0
    fetch_failed = 0
    errored = 0

    # Resumability check stays sequential and up front, before any ad is
    # dispatched to a worker — preserves the existing "never even attempt a
    # fetch for an already-done ad" guarantee regardless of concurrency.
    pending: list[tuple[dict, str]] = []
    for ad in ads:
        ad_id = ad.get("ad_archive_id")
        if not ad_id:
            continue
        out_path = out_dir / f"{ad_id}.json"
        if out_path.exists():
            skipped_existing += 1
            continue
        pending.append((ad, ad_id))

    def _tally(outcome: _Outcome) -> None:
        nonlocal processed, skipped_no_images, fetch_failed, errored
        if outcome == "processed":
            processed += 1
        elif outcome == "no_images":
            skipped_no_images += 1
        elif outcome == "fetch_failed":
            fetch_failed += 1
        elif outcome == "error":
            errored += 1

    if concurrency <= 1:
        for ad, ad_id in pending:
            _tally(_process_one_ad(ad, ad_id, out_dir, stages, fetch_fn))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(_process_one_ad, ad, ad_id, out_dir, stages, fetch_fn): ad_id
                for ad, ad_id in pending
            }
            for future in as_completed(futures):
                _tally(future.result())

    logger.info(
        "step2_run_completed",
        processed=processed,
        skipped_existing=skipped_existing,
        skipped_no_images=skipped_no_images,
        fetch_failed=fetch_failed,
        errored=errored,
    )
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 2 bridge: run the creative pipeline over the ingestion corpus."
    )
    parser.add_argument("--ads", type=Path, default=Path("data/supplements_enriched.json"))
    parser.add_argument("--out", type=Path, default=Path("out/step2"))
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Process a random sample of this size instead of the full corpus (for pilots).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of ads to process at once (default 4; each ad's cost is "
        "mostly network I/O wait, so this is the main speed lever).",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    count = run_step2_pipeline(
        args.ads,
        args.out,
        sample_size=args.sample_size,
        seed=args.seed,
        concurrency=args.concurrency,
    )
    print(f"Processed {count} ad(s) -> {args.out}")


if __name__ == "__main__":
    main()
