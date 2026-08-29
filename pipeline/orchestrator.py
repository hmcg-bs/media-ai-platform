"""Step 2 orchestrator — local CLI.

Runs the ensemble extraction stages over a folder of ad-creative images and
writes one validated JSON document per image. A single stage failure never
halts the run: the failed stage's fields stay at schema defaults and the
pipeline continues (per-stage fallback).

Usage:
    python -m pipeline.orchestrator --input ./examples --out ./out
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.config import get_settings
from pipeline.logger import configure_logging, get_logger
from pipeline.models.output_schema import PipelineContext
from pipeline.stages.base_stage import BaseStage, StageError
from pipeline.stages.stage_01_metadata import MetadataStage
from pipeline.stages.stage_02_ocr import OCRStage
from pipeline.stages.stage_03_color import ColorStage
from pipeline.stages.stage_05_cognitive import CognitiveStage

logger = get_logger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def build_default_stages(settings=None) -> list[BaseStage]:
    """The v1 stage chain. Optional Replicate stages (ADR-008) append when enabled.

    Paid stages are lazily imported so a plain offline run never needs the replicate SDK.
    """
    settings = settings or get_settings()
    stages: list[BaseStage] = [MetadataStage()]
    if settings.enable_datalab_copy:  # Datalab copy replaces Cloud Vision OCR
        from pipeline.stages.stage_02_datalab_copy import DatalabCopyStage

        stages.append(DatalabCopyStage(settings=settings))
    else:
        stages.append(OCRStage())
    stages += [ColorStage()]
    # CognitiveStage: use Replicate if enabled, else default to Vertex AI
    stages.append(CognitiveStage(use_replicate=settings.enable_replicate_cognitive))
    if settings.enable_layer_color:
        from pipeline.stages.stage_06_layer_color import LayerColorStage

        stages.append(LayerColorStage(settings=settings))  # overrides color_profile
    if settings.enable_imagery:
        from pipeline.stages.stage_07_imagery import ImageryStage

        stages.append(ImageryStage(settings=settings))
    return stages


def write_embeddings(context: PipelineContext, out_dir: Path) -> None:
    """Embed the extracted copy + imagery and write ``<ad_id>.embeddings.json``."""
    from pipeline.clients.replicate_client import EmbeddingClient
    from pipeline.embedding.embed import embed_creative

    # These calls run after other Replicate stages, so ride out the low-credit 429
    # burst limit with generous retry/backoff.
    settings = get_settings().model_copy(
        update={
            "api_max_attempts": 6,
            "api_backoff_min_seconds": 8.0,
            "api_backoff_max_seconds": 20.0,
        }
    )
    client = EmbeddingClient(settings=settings)
    ty = context.result.typography_hierarchy
    copy_text = " ".join(
        [ty.primary_headline.text, *(b.text for b in ty.secondary_copy)]
    ).strip()
    emb = embed_creative(
        client,
        copy_text,
        context.result.imagery_description,
        ad_id=context.ad_id,
    )
    (out_dir / f"{context.ad_id}.embeddings.json").write_text(
        emb.model_dump_json(indent=2)
    )
    logger.info("embeddings_written", ad_id=context.ad_id, dim=emb.dim)


def _run_stage(stage: BaseStage, context: PipelineContext) -> None:
    """Run one stage in place, recording (not raising) a StageError."""
    try:
        stage.process(context)
    except StageError as exc:
        logger.error(
            "stage_failed",
            stage=stage.name,
            ad_id=context.ad_id,
            error_type=type(exc.original).__name__ if exc.original else "StageError",
            error_msg=str(exc),
        )
        context.failed_stages.append(stage.name)


def run_one(
    image_path: Path, stages: list[BaseStage], image_bytes: bytes | None = None
) -> PipelineContext:
    """Run all stages on a single image with per-stage fallback.

    ``image_bytes``, when provided, is set on the context up front so every
    stage (all of which operate on ``context.image_bytes`` directly, not
    ``image_path`` — only Stage 1's fallback reads the path) skips its own
    file I/O. Lets a caller fetch bytes in memory (e.g. from a remote URL)
    without ever writing an image to disk; ``image_path`` still supplies
    ``ad_id`` via its stem and is kept for logging/bookkeeping only in that
    case, never opened.

    CognitiveStage (Stage 5), when present, runs concurrently with every
    stage before it rather than waiting for that chain to finish first. The
    OCR->Color dependency (Color masks out OCR's text boxes before k-means —
    see stage_03_color.py) means that chain must stay sequential internally,
    but nothing in it touches marketing_psychology/spatial_and_nested_objects/
    human_model_analysis, the only fields CognitiveStage writes — confirmed
    disjoint, so running them as two threads over the same mutable context is
    safe. This is the dominant lever for per-ad latency: Stage 5's two Gemini
    calls alone average ~18-19s, versus ~3-5s for the whole pre-chain.
    """
    context = PipelineContext(
        ad_id=image_path.stem, image_path=str(image_path), image_bytes=image_bytes
    )

    cognitive_idx = next(
        (i for i, s in enumerate(stages) if isinstance(s, CognitiveStage)), None
    )

    if cognitive_idx is None:
        for stage in stages:
            _run_stage(stage, context)
        return context

    pre_stages = stages[:cognitive_idx]
    cognitive_stage = stages[cognitive_idx]
    post_stages = stages[cognitive_idx + 1 :]

    def run_chain() -> None:
        for stage in pre_stages:
            _run_stage(stage, context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        chain_future = pool.submit(run_chain)
        cognitive_future = pool.submit(_run_stage, cognitive_stage, context)
        chain_future.result()
        cognitive_future.result()

    for stage in post_stages:
        _run_stage(stage, context)

    return context


def process_folder(
    input_dir: Path, out_dir: Path, stages: list[BaseStage], settings=None
) -> int:
    """Process every image in ``input_dir``; write JSON to ``out_dir``. Returns count."""
    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )
    logger.info("run_started", input_dir=str(input_dir), image_count=len(images))

    for image_path in images:
        context = run_one(image_path, stages)
        out_path = out_dir / f"{context.ad_id}.json"
        out_path.write_text(
            json.dumps(context.result.model_dump(mode="json"), indent=2)
        )
        if settings.enable_embeddings:
            try:
                write_embeddings(context, out_dir)
            except Exception as exc:  # noqa: BLE001 — never fail the run on embeddings
                logger.error("embeddings_failed", ad_id=context.ad_id, error=str(exc))
        logger.info(
            "asset_written",
            ad_id=context.ad_id,
            out_path=str(out_path),
            failed_stages=context.failed_stages,
        )

    logger.info("run_completed", processed=len(images))
    return len(images)


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 2 ensemble extraction (local).")
    parser.add_argument("--input", required=True, type=Path, help="Folder of images")
    parser.add_argument("--out", required=True, type=Path, help="Output folder for JSON")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    if not args.input.is_dir():
        raise SystemExit(f"Input folder not found: {args.input}")

    count = process_folder(
        args.input, args.out, build_default_stages(settings), settings
    )
    print(f"Processed {count} image(s) -> {args.out}")


if __name__ == "__main__":
    main()
