"""Tests for pipeline/model_training/category_trends.py -- offline,
synthetic embeddings, no real feature matrix or network calls needed."""

from __future__ import annotations

from pipeline.model_training.category_trends import EmbeddingClusterTrends


def _row(ad_id: str, embedding: list[float] | None, days_active=10, variants_featured_count=0):
    return {
        "ad_id": ad_id,
        "body_embedding": embedding if embedding is not None else [],
        "days_active": days_active,
        "variants_featured_count": variants_featured_count,
    }


class TestFitDegradesGracefullyWithTooFewRows:
    def test_fewer_embedded_rows_than_n_clusters_uses_fallback(self):
        rows = [_row(str(i), [0.1, 0.2], days_active=50) for i in range(3)]
        trends = EmbeddingClusterTrends(n_clusters=15).fit(rows)
        result = trends.transform(rows)
        # every row gets the same corpus-wide fallback, not a fabricated
        # per-cluster stat computed from a near-empty cluster
        assert all(r == result[0] for r in result)
        assert result[0]["cluster_mean_days_active"] == 50.0

    def test_no_embedded_rows_at_all_returns_zero_fallback(self):
        rows = [_row(str(i), None) for i in range(5)]
        trends = EmbeddingClusterTrends(n_clusters=15).fit(rows)
        result = trends.transform(rows)
        assert result[0]["cluster_mean_days_active"] == 0.0
        assert result[0]["cluster_variant_rate"] == 0.0


class TestClusterAssignmentSeparatesDistinctGroups:
    def test_distinct_clusters_get_distinct_trend_stats(self):
        # Two well-separated embedding regions, with very different
        # days_active in each -- a real clustering should tell them apart.
        low_rows = [_row(f"low_{i}", [0.0, 0.0, 0.0], days_active=5) for i in range(10)]
        high_rows = [_row(f"high_{i}", [10.0, 10.0, 10.0], days_active=500) for i in range(10)]
        train_rows = low_rows + high_rows

        trends = EmbeddingClusterTrends(n_clusters=2).fit(train_rows)

        low_probe = [_row("probe_low", [0.1, 0.1, 0.1])]
        high_probe = [_row("probe_high", [9.9, 9.9, 9.9])]
        low_result = trends.transform(low_probe)[0]
        high_result = trends.transform(high_probe)[0]

        assert low_result["cluster_mean_days_active"] < high_result["cluster_mean_days_active"]

    def test_row_with_no_embedding_gets_fallback_not_a_cluster_stat(self):
        low_rows = [_row(f"low_{i}", [0.0, 0.0], days_active=5) for i in range(10)]
        high_rows = [_row(f"high_{i}", [10.0, 10.0], days_active=500) for i in range(10)]
        trends = EmbeddingClusterTrends(n_clusters=2).fit(low_rows + high_rows)

        no_embedding_row = [_row("no_embed", None)]
        result = trends.transform(no_embedding_row)[0]
        # fallback == corpus-wide mean across ALL fit rows, not either
        # cluster's individual mean
        assert result["cluster_mean_days_active"] == 252.5  # (5*10 + 500*10) / 20


class TestNoLeakageFromTestRows:
    def test_transform_does_not_mutate_fitted_state(self):
        train_rows = [
            _row(f"train_{i}", [float(i), float(i)], days_active=i * 10) for i in range(20)
        ]
        trends = EmbeddingClusterTrends(n_clusters=3).fit(train_rows)
        stats_before = {k: dict(v) for k, v in trends._cluster_stats.items()}

        test_rows = [_row("test_1", [999.0, 999.0], days_active=99999)]
        trends.transform(test_rows)

        assert {k: dict(v) for k, v in trends._cluster_stats.items()} == stats_before

    def test_transform_on_train_and_test_rows_uses_same_fitted_clusters(self):
        train_rows = [
            _row(f"train_{i}", [float(i % 5), float(i % 5)], days_active=i) for i in range(30)
        ]
        trends = EmbeddingClusterTrends(n_clusters=5).fit(train_rows)

        # A test row identical to a train row's embedding should land in
        # the same cluster and get the same (train-only-derived) stats.
        train_result = trends.transform([train_rows[0]])[0]
        test_row = _row("test_match", train_rows[0]["body_embedding"])
        test_result = trends.transform([test_row])[0]
        assert train_result == test_result
