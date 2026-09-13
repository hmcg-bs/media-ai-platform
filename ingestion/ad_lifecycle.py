"""Conservative lifecycle observations for public commercial Meta ads."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from ingestion.normalize import normalize_ad


def _observation_id(run_id: str, ad_id: str) -> str:
    return hashlib.sha256(f"{run_id}:{ad_id}".encode()).hexdigest()


def build_initial_observations(
    ads: list[dict[str, Any]], run_id: str, observed_at: str
) -> list[dict[str, Any]]:
    """Represent a persisted scrape corpus as the first lifecycle observation."""
    rows = []
    for ad in ads:
        ad_id = str(ad.get("ad_archive_id") or "")
        if not ad_id:
            continue
        # This Apify actor populates end_date with the current delivery-window
        # boundary even for active ads. Only an explicit inactive flag is stop
        # evidence in normalized corpus rows.
        inactive = ad.get("is_active") is False
        source_observed_at = str(ad.get("ingested_at") or observed_at)
        source_run_id = f"{run_id}:{source_observed_at}"
        rows.append(
            {
                "observation_id": _observation_id(source_run_id, ad_id),
                "run_id": source_run_id,
                "ad_id": ad_id,
                "page_id": str(ad.get("page_id") or ""),
                "observed_at": source_observed_at,
                "observed_status": "explicitly_inactive" if inactive else "active",
                "evidence": "initial_corpus_snapshot",
                "reported_start_date": ad.get("start_date"),
                "reported_stop_date": ad.get("end_date"),
                "poll_source": "ingestion_corpus",
                "poll_complete": True,
                "raw_payload": None,
            }
        )
    return rows


def build_poll_observations(
    known_ads: list[dict[str, Any]],
    raw_results: list[dict[str, Any]],
    run_id: str,
    observed_at: str,
    poll_complete: bool = True,
    complete_page_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Join one paid poll result to tracked IDs without overclaiming absence.

    `not_observed` is not the same as inactive. It becomes inferred inactivity
    only after repeated complete polls; explicit inactive/stop fields are
    immediately usable events.
    """
    returned: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for raw in raw_results:
        normalized = normalize_ad(raw).model_dump(mode="json")
        if normalized["ad_archive_id"]:
            returned[normalized["ad_archive_id"]] = (normalized, raw)

    observations = []
    for ad in known_ads:
        ad_id = str(ad.get("ad_archive_id") or "")
        if not ad_id:
            continue
        match = returned.get(ad_id)
        page_id = str(ad.get("page_id") or "")
        ad_poll_complete = (
            page_id in complete_page_ids if complete_page_ids is not None else poll_complete
        )
        if match:
            normalized, raw = match
            official_stop = raw.get("ad_delivery_stop_time") or raw.get(
                "adDeliveryStopTime"
            )
            explicit_inactive = normalized.get("is_active") is False or bool(official_stop)
            status = "explicitly_inactive" if explicit_inactive else "active"
            evidence = "reported_stop_or_inactive" if explicit_inactive else "returned_active"
            start_date = normalized.get("start_date") or ad.get("start_date")
            stop_date = normalized.get("end_date")
            payload: dict[str, Any] | None = raw
        else:
            status = "not_observed" if ad_poll_complete else "unknown"
            evidence = (
                "missing_from_page_with_returned_results"
                if ad_poll_complete
                else "page_poll_without_positive_control"
            )
            start_date = ad.get("start_date")
            stop_date = None
            payload = None
        observations.append(
            {
                "observation_id": _observation_id(run_id, ad_id),
                "run_id": run_id,
                "ad_id": ad_id,
                "page_id": page_id,
                "observed_at": observed_at,
                "observed_status": status,
                "evidence": evidence,
                "reported_start_date": start_date,
                "reported_stop_date": stop_date,
                "poll_source": "apify_page_inventory",
                "poll_complete": ad_poll_complete,
                "raw_payload": payload,
            }
        )
    return observations


def resolve_lifecycle(
    observations: list[dict[str, Any]],
    required_consecutive_misses: int = 2,
) -> dict[str, dict[str, Any]]:
    """Resolve latest state and an interval-censored stop from observations."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[str(row["ad_id"])].append(row)

    resolved = {}
    for ad_id, rows in grouped.items():
        rows.sort(key=lambda r: str(r["observed_at"]))
        last_active_at = None
        consecutive_misses = 0
        first_miss_at = None
        state = "unknown"
        stop_date = None
        event_at = None
        for row in rows:
            status = row["observed_status"]
            if status == "active":
                state = "active"
                last_active_at = str(row["observed_at"])
                consecutive_misses = 0
                first_miss_at = None
            elif status == "explicitly_inactive":
                state = "inactive_explicit"
                stop_date = row.get("reported_stop_date")
                event_at = str(row["observed_at"])
                consecutive_misses = 0
            elif status == "not_observed" and row.get("poll_complete"):
                consecutive_misses += 1
                first_miss_at = first_miss_at or str(row["observed_at"])
                if (
                    state != "inactive_explicit"
                    and consecutive_misses >= required_consecutive_misses
                ):
                    state = "inactive_inferred"
                    event_at = first_miss_at
            # Unknown/incomplete polls neither reset nor advance misses.
        resolved[ad_id] = {
            "ad_id": ad_id,
            "state": state,
            "last_active_at": last_active_at,
            "first_missing_at": first_miss_at,
            "event_at": event_at,
            "reported_stop_date": stop_date,
            "consecutive_complete_misses": consecutive_misses,
            "observation_count": len(rows),
        }
    return resolved


def event_duration_days(ad: dict[str, Any], lifecycle: dict[str, Any]) -> int | None:
    """Return conservative event duration (first missing is interval upper bound)."""
    start = ad.get("start_date")
    if not start or not lifecycle.get("event_at"):
        return None
    event_value = lifecycle.get("reported_stop_date") or lifecycle["event_at"]
    try:
        start_day = date.fromisoformat(str(start)[:10])
        event_day = datetime.fromisoformat(str(event_value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            event_day = date.fromisoformat(str(event_value)[:10])
        except ValueError:
            return None
    return max(0, (event_day - start_day).days)


def observations_hash(observations: list[dict[str, Any]]) -> str:
    canonical = json.dumps(observations, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
