"""Validate 30/60/90/180-day longevity estimates from lifecycle observations."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from lifelines import KaplanMeierFitter

from ingestion.ad_lifecycle import event_duration_days, observations_hash, resolve_lifecycle
from ingestion.gcp_warehouse import BigQueryWarehouse
from pipeline.artifacts import atomic_write_json

HORIZONS = (30, 60, 90, 180)
MIN_KNOWN_OUTCOMES = 20
MIN_ENDINGS = 5


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def build_survival_validation(
    ads: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    required_consecutive_misses: int = 2,
    horizons: tuple[int, ...] = HORIZONS,
) -> dict[str, Any]:
    """Build censoring-aware estimates and evidence coverage at each horizon."""
    lifecycle = resolve_lifecycle(observations, required_consecutive_misses)
    observations_by_ad: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        observations_by_ad.setdefault(str(row["ad_id"]), []).append(row)

    durations: list[float] = []
    events: list[bool] = []
    interval_events = 0
    for ad in ads:
        ad_id = str(ad.get("ad_archive_id") or "")
        state = lifecycle.get(ad_id)
        history = observations_by_ad.get(ad_id, [])
        if not state or not history or not ad.get("start_date"):
            continue
        if state["state"] in {"inactive_explicit", "inactive_inferred"}:
            duration = event_duration_days(ad, state)
            if duration is None:
                continue
            durations.append(float(duration))
            events.append(True)
            interval_events += int(state["state"] == "inactive_inferred")
        else:
            last_observed = max(_day(row["observed_at"]) for row in history)
            start = _day(ad["start_date"])
            durations.append(float(max(0, (last_observed - start).days)))
            events.append(False)

    computed_at = datetime.now(tz=UTC).isoformat()
    result: dict[str, Any] = {
        "report_schema_version": "survival-validation-v1",
        "computed_at": computed_at,
        "input_hash": observations_hash(observations),
        "required_consecutive_misses": required_consecutive_misses,
        "n_ads": len(durations),
        "events_observed": int(sum(events)),
        "interval_censored_events_approximated_at_first_missing": interval_events,
        "status": "ok" if sum(events) >= MIN_ENDINGS else "insufficient_observed_endings",
        "horizons": {},
    }
    if not durations:
        return result

    km = KaplanMeierFitter().fit(durations, event_observed=events)
    confidence = km.confidence_interval_survival_function_
    for horizon in horizons:
        # A horizon outcome is known when an ad ended on/before it, or was
        # observed through it. Ads censored before the horizon are unknown.
        eligible = sum(
            (event and duration <= horizon) or duration >= horizon
            for duration, event in zip(durations, events)
        )
        observed_through = sum(duration >= horizon for duration in durations)
        ended_by = sum(event and duration <= horizon for duration, event in zip(durations, events))
        survival = float(km.predict(horizon))
        prior_rows = confidence.loc[confidence.index <= horizon]
        if prior_rows.empty:
            lower = upper = 1.0
        else:
            lower, upper = (float(x) for x in prior_rows.iloc[-1].tolist())
        result["horizons"][str(horizon)] = {
            "survival_probability": round(survival, 4),
            "confidence_interval_95": [round(lower, 4), round(upper, 4)],
            "ads_observed_through_horizon": observed_through,
            "ads_eligible_for_outcome": eligible,
            "endings_observed_by_horizon": ended_by,
            "outcome_coverage": round(eligible / len(durations), 4),
            "status": (
                "ok"
                if ended_by >= MIN_ENDINGS and eligible >= MIN_KNOWN_OUTCOMES
                else "insufficient_evidence"
            ),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ad-longevity horizons from BigQuery.")
    parser.add_argument("--ads", type=Path, help="Optional local corpus; default is BigQuery")
    parser.add_argument("--observations", type=Path, help="Optional local observation list")
    parser.add_argument("--out", type=Path, default=Path("data/survival_validation.json"))
    parser.add_argument("--dataset")
    parser.add_argument("--required-misses", type=int, default=2)
    args = parser.parse_args()

    warehouse = BigQueryWarehouse(dataset_id=args.dataset)
    ads = json.loads(args.ads.read_text()) if args.ads else warehouse.fetch_tracked_ads()
    if args.observations:
        raw = json.loads(args.observations.read_text())
        observations = raw.get("observations", raw) if isinstance(raw, dict) else raw
    else:
        observations = warehouse.fetch_observations()
    report = build_survival_validation(ads, observations, args.required_misses)
    atomic_write_json(args.out, report)
    warehouse.ensure_schema()
    warehouse.append_survival_report(report)
    print(f"Wrote {args.out}: status={report['status']} events={report['events_observed']}")


if __name__ == "__main__":
    main()
