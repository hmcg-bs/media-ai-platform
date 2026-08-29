"""Censored survival analysis for `days_active` -- Cox Proportional Hazards
via `lifelines`, replacing plain regression for this one target.

Root cause this exists to fix (confirmed live, not assumed): every ad in the
corpus has an `end_date`, but 94.2% of ads (2,576/2,736) share exactly one
of two end_date values -- the corpus's own two scrape-run dates, not each
ad's real individual death date. A genuine per-ad "the ad stopped running
here" event would essentially never cluster this heavily across hundreds of
unrelated advertisers by chance. Treating every end_date as a real observed
death made a plain time-based-split regression catastrophically wrong (test
R2 as low as -171): the newest-started ads in the test set have tiny
days_active purely because little time had passed since they started
relative to the scrape snapshot, not because of any real feature signal.

Cox regression handles this correctly by design: a *censored* observation
("this ad had survived at least N days when we last saw it, still running")
is real, informative data about a lower bound on its true lifetime, not a
broken/wrong label the way plain regression treats it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd
from lifelines import CoxPHFitter
from lifelines.utils import concordance_index

from pipeline.model_training.preprocessing import build_xy


def identify_scrape_dates(ads: list[dict[str, Any]], frequency_threshold: float = 0.01) -> set[str]:
    """Returns the set of end_date values that are almost certainly scrape-
    run stamps, not genuine individual ad-death dates -- any end_date shared
    by more than `frequency_threshold` of the corpus. A real death date
    being independently distributed across many unrelated products/
    advertisers essentially never clusters this heavily by chance; a
    scrape-run timestamp does, by construction."""
    end_dates = [a["end_date"] for a in ads if a.get("end_date")]
    if not end_dates:
        return set()
    counts = Counter(end_dates)
    threshold_count = frequency_threshold * len(end_dates)
    return {date for date, count in counts.items() if count > threshold_count}


def build_survival_frame(
    rows: list[dict[str, Any]],
    ads: list[dict[str, Any]],
    scrape_dates: set[str] | None = None,
) -> pd.DataFrame:
    """Joins feature-matrix rows to the ingestion corpus (for end_date) and
    returns a DataFrame with every row's original columns plus `duration`
    (== days_active -- valid as "time observed so far" whether censored or
    not) and `event_observed` (True: a genuine, non-scrape-date end_date was
    recorded; False: censored, still running or unknown at scrape time).
    Rows with no matching ad or no end_date are dropped -- can't establish
    censoring status for them."""
    if scrape_dates is None:
        scrape_dates = identify_scrape_dates(ads)
    end_date_by_id = {a["ad_archive_id"]: a.get("end_date") for a in ads}

    records = []
    for row in rows:
        end_date = end_date_by_id.get(row.get("ad_id"))
        if not end_date:
            continue
        record = dict(row)
        record["duration"] = row.get("days_active", 0)
        record["event_observed"] = end_date not in scrape_dates
        records.append(record)
    return pd.DataFrame(records)


def build_survival_xy(
    rows: list[dict[str, Any]],
    ads: list[dict[str, Any]],
    scrape_dates: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """(X, duration, event_observed) for Cox fitting/scoring. Reuses
    build_xy's exact days_active covariate-building (raw embeddings
    excluded -- Cox's Newton-Raphson fit doesn't handle thousands of
    partly-collinear one-hot/embedding dims well, unlike a tree model;
    category-trend cluster features already carry embedding signal in a
    much more compact, Cox-friendly form) and drops rows with no matching
    ad/end_date on top of that, so all three return values stay aligned."""
    if scrape_dates is None:
        scrape_dates = identify_scrape_dates(ads)
    end_date_by_id = {a["ad_archive_id"]: a.get("end_date") for a in ads}

    rows_with_end_date = [r for r in rows if end_date_by_id.get(r.get("ad_id"))]
    X, duration = build_xy(rows_with_end_date, "days_active", include_embeddings=False)

    event_observed = pd.Series(
        [end_date_by_id[r["ad_id"]] not in scrape_dates for r in rows_with_end_date],
        name="event_observed",
    )
    # days_active is always populated (extractor.py defaults it to 0, never
    # None) so build_xy's own null-drop should never actually remove a row
    # here -- assert rather than silently risk X/duration/event_observed
    # drifting out of alignment if that ever stops being true.
    assert len(X) == len(event_observed) == len(duration), (
        f"row count mismatch after build_xy filtering: X={len(X)}, "
        f"duration={len(duration)}, event_observed={len(event_observed)}"
    )
    return X, duration, event_observed


def drop_zero_variance_columns(
    X_train: pd.DataFrame, X_test: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Drops any train-column with zero variance from both frames (same
    columns dropped from test, so predict_partial_hazard sees a matching
    shape). Confirmed live as the actual cause of a real ConvergenceError
    against the full feature set, not a hypothetical: several creative_*
    columns are ~97-100% one repeated value even before scaling (see
    data_quality.py's own point-mass/zero-variance flags on the same
    matrix) -- after StandardScaler, a genuinely-constant input column
    becomes a literal all-zero column (sklearn sets std=1 for a zero-std
    column to avoid a divide-by-zero, so (constant - mean) / 1 == 0 for
    every row), which is exactly the singular-design-matrix case Cox's
    Newton-Raphson solver can't invert."""
    variances = X_train.var(numeric_only=False)
    zero_variance_cols = [c for c in X_train.columns if variances.get(c, 1.0) == 0.0]
    if not zero_variance_cols:
        return X_train, X_test, []
    return (
        X_train.drop(columns=zero_variance_cols),
        X_test.drop(columns=zero_variance_cols),
        zero_variance_cols,
    )


def fit_cox_model(
    X_train: pd.DataFrame,
    duration_train: pd.Series,
    event_train: pd.Series,
    penalizer: float = 1.0,
) -> CoxPHFitter:
    """Ridge-penalized (penalizer > 0) Cox PH fit -- plain unpenalized Cox
    struggles to converge with this many (partly collinear, one-hot-heavy)
    covariates; the penalty is what keeps fitting numerically stable, not
    just a regularization nicety here. penalizer=1.0 (stronger than
    lifelines' own 0.1-ish typical examples) confirmed live as necessary
    against the real ~85-covariate design matrix -- 0.1 alone still hit a
    ConvergenceError even after dropping zero-variance columns."""
    frame = X_train.copy()
    frame["duration"] = duration_train.to_numpy()
    frame["event_observed"] = event_train.to_numpy()
    model = CoxPHFitter(penalizer=penalizer)
    model.fit(frame, duration_col="duration", event_col="event_observed")
    return model


def evaluate_cox_model(
    model: CoxPHFitter,
    X_test: pd.DataFrame,
    duration_test: pd.Series,
    event_test: pd.Series,
) -> dict[str, Any]:
    """Concordance index (C-index) is the standard survival-analysis
    evaluation metric -- the fraction of comparable ad pairs the model
    correctly ranks by relative survival time, well-defined even with
    censored observations (unlike RMSE/R2, which need a true final value
    for every row). 0.5 = random ranking, 1.0 = perfect ranking."""
    risk_scores = model.predict_partial_hazard(X_test)
    c_index = concordance_index(duration_test, -risk_scores, event_test)

    coefs = model.params_.sort_values(key=abs, ascending=False)
    top_covariates = [(str(name), round(float(val), 4)) for name, val in coefs.head(20).items()]

    return {
        "n_test": len(X_test),
        "n_events_observed_test": int(event_test.sum()),
        "concordance_index": round(float(c_index), 4),
        "top_covariates": top_covariates,
    }
