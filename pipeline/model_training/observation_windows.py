"""Immutable collection-window manifests for longitudinal Meta evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ingestion.ad_lifecycle import resolve_lifecycle
from pipeline.artifacts import atomic_write_json, exclusive_output

WINDOW_SCHEMA_VERSION = "meta-observation-window-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _load_observations(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text())
    return payload.get("observations", payload) if isinstance(payload, dict) else payload


def build_observation_window(
    ads_file: Path,
    observations_file: Path | None = None,
    previous_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind exact corpus/observation bytes and characterize temporal novelty."""
    ads = json.loads(ads_file.read_text())
    observations = _load_observations(observations_file)
    ad_ids = [str(ad.get("ad_archive_id") or "") for ad in ads]
    if not ad_ids or any(not ad_id for ad_id in ad_ids):
        raise ValueError("every frozen ad must have a stable ad_archive_id")
    if len(ad_ids) != len(set(ad_ids)):
        raise ValueError("a collection window cannot contain duplicate ad IDs")
    observations = [row for row in observations if str(row.get("ad_id") or "") in set(ad_ids)]
    page_ids = {str(ad.get("page_id") or "") for ad in ads if ad.get("page_id")}
    observed_times = sorted(str(ad.get("ingested_at")) for ad in ads if ad.get("ingested_at"))
    statuses = Counter(str(row.get("observed_status") or "unknown") for row in observations)
    resolved = resolve_lifecycle(observations) if observations else {}
    resolved_states = Counter(str(row["state"]) for row in resolved.values())

    previous_ids = set(previous_window.get("ad_ids", [])) if previous_window else set()
    previous_pages = set(previous_window.get("page_ids", [])) if previous_window else set()
    previous_end = previous_window.get("observed_at_max") if previous_window else None
    current_start = observed_times[0] if observed_times else None
    temporal_order = (
        "baseline"
        if previous_window is None
        else "after_previous_window"
        if previous_end and current_start and current_start > previous_end
        else "not_proven_after_previous_window"
    )
    role = "baseline" if previous_window is None else "future_holdout"
    source = {
        "ads_sha256": _sha256(ads_file),
        "observations_sha256": _sha256(observations_file) if observations_file else None,
        "previous_window_id": previous_window.get("window_id") if previous_window else None,
        "ad_ids_sha256": _content_sha256(sorted(ad_ids)),
    }
    window_id = _content_sha256({"role": role, **source})
    return {
        "report_schema_version": WINDOW_SCHEMA_VERSION,
        "window_id": window_id,
        "role": role,
        "status": "frozen",
        "ads_file": str(ads_file),
        "observations_file": str(observations_file) if observations_file else None,
        **source,
        "n_ads": len(ad_ids),
        "n_advertisers": len(page_ids),
        "ad_ids": sorted(ad_ids),
        "page_ids": sorted(page_ids),
        "observed_at_min": current_start,
        "observed_at_max": observed_times[-1] if observed_times else None,
        "temporal_order_status": temporal_order,
        "n_new_ads": len(set(ad_ids) - previous_ids),
        "n_existing_ads": len(set(ad_ids) & previous_ids),
        "n_new_advertisers": len(page_ids - previous_pages),
        "observation_status_counts": dict(sorted(statuses.items())),
        "resolved_lifecycle_state_counts": dict(sorted(resolved_states.items())),
        "complete_observations": sum(bool(row.get("poll_complete")) for row in observations),
        "eligible_for_old_model_evaluation": (
            role == "future_holdout"
            and temporal_order == "after_previous_window"
            and bool(set(ad_ids) - previous_ids)
        ),
    }


def write_immutable_window(path: Path, manifest: dict[str, Any]) -> None:
    """Publish once; a different payload may never replace a frozen window."""
    if path.exists():
        existing = json.loads(path.read_text())
        if existing == manifest:
            return
        raise FileExistsError(f"immutable observation window already exists: {path}")
    with exclusive_output(path):
        if path.exists():
            raise FileExistsError(f"immutable observation window already exists: {path}")
        atomic_write_json(path, manifest)


def verify_observation_window(
    manifest: dict[str, Any],
    ads_file: Path,
    observations_file: Path | None = None,
    previous_window: dict[str, Any] | None = None,
) -> None:
    """Recompute portable window identity and exact source membership."""
    if manifest.get("report_schema_version") != WINDOW_SCHEMA_VERSION:
        raise ValueError("observation-window schema is unsupported")
    if manifest.get("status") != "frozen":
        raise ValueError("observation window is not frozen")
    if manifest.get("ads_sha256") != _sha256(ads_file):
        raise ValueError("observation window ads hash mismatch")
    expected_observation_hash = manifest.get("observations_sha256")
    if expected_observation_hash:
        if observations_file is None or expected_observation_hash != _sha256(observations_file):
            raise ValueError("observation window lifecycle hash mismatch")
    elif observations_file is not None:
        raise ValueError("unbound lifecycle observations were supplied")
    ads = json.loads(ads_file.read_text())
    ad_ids = sorted(str(ad.get("ad_archive_id") or "") for ad in ads)
    if any(not ad_id for ad_id in ad_ids) or ad_ids != manifest.get("ad_ids"):
        raise ValueError("observation window ad membership mismatch")
    ad_ids_sha256 = _content_sha256(ad_ids)
    if ad_ids_sha256 != manifest.get("ad_ids_sha256"):
        raise ValueError("observation window ad membership hash mismatch")
    source = {
        "ads_sha256": manifest["ads_sha256"],
        "observations_sha256": expected_observation_hash,
        "previous_window_id": manifest.get("previous_window_id"),
        "ad_ids_sha256": ad_ids_sha256,
    }
    expected_window_id = _content_sha256({"role": manifest.get("role"), **source})
    if manifest.get("window_id") != expected_window_id:
        raise ValueError("observation window identity mismatch")
    rebuilt = build_observation_window(ads_file, observations_file, previous_window)
    for key, value in rebuilt.items():
        if key not in {"ads_file", "observations_file"} and manifest.get(key) != value:
            raise ValueError(f"observation window derived field mismatch: {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze an immutable Meta collection window.")
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--previous-window", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.ads, args.observations, args.previous_window):
        if path is not None and not path.exists():
            parser.error(f"input not found: {path}")
    previous = json.loads(args.previous_window.read_text()) if args.previous_window else None
    manifest = build_observation_window(args.ads, args.observations, previous)
    write_immutable_window(args.out, manifest)
    print(
        f"Frozen {manifest['role']} window {manifest['window_id']}: "
        f"ads={manifest['n_ads']} eligible={manifest['eligible_for_old_model_evaluation']}"
    )


if __name__ == "__main__":
    main()
