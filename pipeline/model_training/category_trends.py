"""Category-trend / pattern-transfer features: cluster ads by creative
embedding similarity, then attach each ad's cluster's train-only historical
performance stats as new features -- "this ad's embedding neighborhood
skews toward long-running/high-variant ads" as a signal a raw per-ad model
can't see on its own.

Fit strictly on train (KMeans + per-cluster stats) and only ever transform
test rows against that fit, so no test-set outcome ever leaks into a
feature test rows are then scored with.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.cluster import KMeans

TREND_FEATURE_NAMES = ("cluster_mean_days_active", "cluster_variant_rate")


def _embedding_matrix(rows: list[dict[str, Any]], column: str) -> tuple[np.ndarray, list[int]]:
    """(matrix, row_indices) for rows with a non-empty embedding in
    `column` -- a row with an empty/missing vector can't be clustered."""
    vectors, indices = [], []
    for i, row in enumerate(rows):
        vec = row.get(column)
        if vec:
            vectors.append(vec)
            indices.append(i)
    if not vectors:
        return np.empty((0, 0)), []
    return np.array(vectors), indices


class EmbeddingClusterTrends:
    """Fit once on train rows; call .transform() on train or test rows to
    attach cluster-level trend features computed only from the training
    fit -- never re-fit or re-computed against rows being transformed."""

    def __init__(
        self,
        embedding_column: str = "body_embedding",
        n_clusters: int = 15,
        random_state: int = 42,
    ):
        self.embedding_column = embedding_column
        self.n_clusters = n_clusters
        self.random_state = random_state
        self._kmeans: KMeans | None = None
        self._cluster_stats: dict[int, dict[str, float]] = {}
        self._fallback_stats: dict[str, float] = dict.fromkeys(TREND_FEATURE_NAMES, 0.0)

    def fit(self, rows: list[dict[str, Any]]) -> EmbeddingClusterTrends:
        matrix, valid_indices = _embedding_matrix(rows, self.embedding_column)

        def _stats_for(indices: list[int]) -> dict[str, float]:
            if not indices:
                return dict(self._fallback_stats)
            days_active = [rows[i].get("days_active", 0) for i in indices]
            variant_flags = [rows[i].get("variants_featured_count", 0) > 0 for i in indices]
            return {
                "cluster_mean_days_active": float(np.mean(days_active)),
                "cluster_variant_rate": float(np.mean(variant_flags)),
            }

        self._fallback_stats = _stats_for(valid_indices)

        if len(valid_indices) < self.n_clusters:
            # Not enough embedded rows to form n_clusters -- degrade to the
            # single corpus-wide fallback rather than raising or fabricating
            # clusters that are mostly empty/duplicated.
            self._kmeans = None
            return self

        self._kmeans = KMeans(n_clusters=self.n_clusters, random_state=self.random_state, n_init=10)
        labels = self._kmeans.fit_predict(matrix)

        for cluster_id in range(self.n_clusters):
            member_indices = [valid_indices[i] for i, lbl in enumerate(labels) if lbl == cluster_id]
            self._cluster_stats[cluster_id] = _stats_for(member_indices)
        return self

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, float]]:
        """One {cluster_mean_days_active, cluster_variant_rate} dict per
        row, same order as `rows`. Rows with no embedding (or when fit()
        degraded to the single-cluster fallback) get the corpus-wide
        fallback stats -- never a fabricated per-cluster value for data
        that was never actually clustered."""
        results = [dict(self._fallback_stats) for _ in rows]
        if self._kmeans is None:
            return results

        matrix, valid_indices = _embedding_matrix(rows, self.embedding_column)
        if not valid_indices:
            return results

        labels = self._kmeans.predict(matrix)
        for row_idx, cluster_id in zip(valid_indices, labels, strict=True):
            results[row_idx] = dict(self._cluster_stats.get(int(cluster_id), self._fallback_stats))
        return results
