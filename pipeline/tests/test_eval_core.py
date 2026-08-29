"""Tests for pipeline/validation/eval_core.py — shared accuracy/confusion-
matrix helpers, fully offline (pure functions, no I/O beyond load/save)."""

from __future__ import annotations

from pipeline.validation import eval_core


class TestLoadSaveGoldenSet:
    def test_round_trips(self, tmp_path):
        path = tmp_path / "golden.json"
        data = [{"ad_id": "1", "expected_x": True}]
        eval_core.save_golden_set(data, path)
        assert eval_core.load_golden_set(path) == data

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "golden.json"
        eval_core.save_golden_set([], path)
        assert path.exists()


class TestComputeJudgmentRate:
    def test_all_true_gives_full_accuracy(self):
        golden_set = [{"expected_x": True}, {"expected_x": True}]
        result = eval_core.compute_judgment_rate(golden_set, "expected_x")
        assert result["accuracy"] == 1.0
        assert result["total_evaluated"] == 2
        assert result["correct"] == 2

    def test_mixed_true_false(self):
        golden_set = [{"expected_x": True}, {"expected_x": False}, {"expected_x": True}]
        result = eval_core.compute_judgment_rate(golden_set, "expected_x")
        assert result["accuracy"] == 2 / 3
        assert result["correct"] == 2

    def test_unlabeled_entries_excluded_not_counted_wrong(self):
        golden_set = [{"expected_x": True}, {"expected_x": None}, {"expected_x": None}]
        result = eval_core.compute_judgment_rate(golden_set, "expected_x")
        assert result["total_evaluated"] == 1
        assert result["accuracy"] == 1.0

    def test_no_labels_returns_zero_not_crash(self):
        golden_set = [{"expected_x": None}]
        result = eval_core.compute_judgment_rate(golden_set, "expected_x")
        assert result["total_evaluated"] == 0
        assert result["accuracy"] == 0.0


class TestComputeBooleanAccuracy:
    def test_matching_predictions_are_correct(self):
        golden_set = [
            {"predicted_x": True, "expected_x": True},
            {"predicted_x": False, "expected_x": False},
        ]
        result = eval_core.compute_boolean_accuracy(golden_set, "predicted_x", "expected_x")
        assert result["accuracy"] == 1.0

    def test_mismatched_predictions_lower_accuracy(self):
        golden_set = [
            {"predicted_x": True, "expected_x": False},
            {"predicted_x": True, "expected_x": True},
        ]
        result = eval_core.compute_boolean_accuracy(golden_set, "predicted_x", "expected_x")
        assert result["accuracy"] == 0.5

    def test_unlabeled_entries_excluded(self):
        golden_set = [
            {"predicted_x": True, "expected_x": True},
            {"predicted_x": True, "expected_x": None},
        ]
        result = eval_core.compute_boolean_accuracy(golden_set, "predicted_x", "expected_x")
        assert result["total_evaluated"] == 1


class TestComputeCategoricalAccuracy:
    def test_perfect_accuracy(self):
        golden_set = [
            {"ad_id": "1", "predicted_hook": "PAS", "expected_hook": "PAS"},
            {"ad_id": "2", "predicted_hook": "AIDA", "expected_hook": "AIDA"},
        ]
        result = eval_core.compute_categorical_accuracy(
            golden_set, "predicted_hook", "expected_hook"
        )
        assert result["accuracy"] == 1.0
        assert result["mismatches"] == []

    def test_per_category_accuracy_and_mismatches(self):
        golden_set = [
            {"ad_id": "1", "predicted_hook": "PAS", "expected_hook": "PAS"},
            {"ad_id": "2", "predicted_hook": "AIDA", "expected_hook": "PAS"},
            {"ad_id": "3", "predicted_hook": "Unknown", "expected_hook": "Unknown"},
        ]
        result = eval_core.compute_categorical_accuracy(
            golden_set, "predicted_hook", "expected_hook"
        )
        assert result["per_category_accuracy"]["PAS"] == 0.5
        assert result["per_category_accuracy"]["Unknown"] == 1.0
        assert len(result["mismatches"]) == 1
        assert result["mismatches"][0] == {"ad_id": "2", "expected": "PAS", "predicted": "AIDA"}

    def test_unlabeled_entries_excluded(self):
        golden_set = [
            {"ad_id": "1", "predicted_hook": "PAS", "expected_hook": "PAS"},
            {"ad_id": "2", "predicted_hook": "PAS", "expected_hook": None},
        ]
        result = eval_core.compute_categorical_accuracy(
            golden_set, "predicted_hook", "expected_hook"
        )
        assert result["total_evaluated"] == 1

    def test_no_labeled_entries_returns_zero_not_crash(self):
        golden_set = [{"ad_id": "1", "predicted_hook": "PAS", "expected_hook": None}]
        result = eval_core.compute_categorical_accuracy(
            golden_set, "predicted_hook", "expected_hook"
        )
        assert result["accuracy"] == 0.0
        assert result["total_evaluated"] == 0


class TestComputeCountAccuracy:
    def test_exact_match(self):
        golden_set = [{"predicted_n": 2, "expected_n": 2}]
        result = eval_core.compute_count_accuracy(golden_set, "predicted_n", "expected_n")
        assert result["exact_accuracy"] == 1.0
        assert result["within_tolerance_accuracy"] == 1.0

    def test_off_by_one_within_default_tolerance(self):
        golden_set = [{"predicted_n": 2, "expected_n": 3}]
        result = eval_core.compute_count_accuracy(
            golden_set, "predicted_n", "expected_n", tolerance=1
        )
        assert result["exact_accuracy"] == 0.0
        assert result["within_tolerance_accuracy"] == 1.0

    def test_off_by_two_exceeds_default_tolerance(self):
        golden_set = [{"predicted_n": 0, "expected_n": 2}]
        result = eval_core.compute_count_accuracy(
            golden_set, "predicted_n", "expected_n", tolerance=1
        )
        assert result["within_tolerance_accuracy"] == 0.0

    def test_unlabeled_entries_excluded(self):
        golden_set = [{"predicted_n": 2, "expected_n": None}]
        result = eval_core.compute_count_accuracy(golden_set, "predicted_n", "expected_n")
        assert result["total_evaluated"] == 0


class TestPrintFunctionsDoNotCrash:
    """These only print — just confirm they run against real result shapes
    without raising, so a KeyError in a print helper isn't the failure mode
    that surfaces in production."""

    def test_print_boolean_result(self, capsys):
        result = eval_core.compute_boolean_accuracy(
            [{"predicted_x": True, "expected_x": True}], "predicted_x", "expected_x"
        )
        eval_core.print_boolean_result("Test Field", result)
        assert "Test Field" in capsys.readouterr().out

    def test_print_categorical_result(self, capsys):
        result = eval_core.compute_categorical_accuracy(
            [{"ad_id": "1", "predicted_hook": "PAS", "expected_hook": "AIDA"}],
            "predicted_hook",
            "expected_hook",
        )
        eval_core.print_categorical_result("Test Field", result)
        assert "Test Field" in capsys.readouterr().out

    def test_print_count_result(self, capsys):
        result = eval_core.compute_count_accuracy(
            [{"predicted_n": 1, "expected_n": 1}], "predicted_n", "expected_n"
        )
        eval_core.print_count_result("Test Field", result)
        assert "Test Field" in capsys.readouterr().out
