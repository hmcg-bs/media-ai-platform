"""Atomic, immutable Meta-evidence bundles for the Generation boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.generation.guide import extract_generation_guide

BUNDLE_SCHEMA_VERSION = "meta-generation-handoff-bundle-v1"
LONGEVITY_EVENT_SEMANTICS = (
    "is_active=false; legacy end_date heuristic only when activity status is absent"
)
_GUIDE_KEYS = {
    "visual_directives",
    "copy_style_directives",
    "positioning_context",
    "non_directional_signals",
    "excluded_notes",
}
_SIGNAL_KEYS = {"dimension", "value", "direction", "magnitude", "source"}
ACCEPTED_V3_HASHES = {
    "generation_guide": "63b0ecc8e3c88e8a9b5b7627aebf8441a5b084d054d3f23dade0b45d3556a110",
    "training_report": "2d9307fa9f671744ca053af545b3d191f39e81e2757d41ab95f9a534a463043e",
    "survival_validation": "2dddda67eea155498dcdb14ee690b15649fa40cab7986a23115f274c1492d0cb",
}
_CANONICAL_FILENAMES = {
    "generation_guide": "meta_model_handoff_supplements_generation_guide_v3.json",
    "training_report": "meta_model_handoff_supplements_training_report_v3.json",
    "survival_validation": "meta_model_handoff_supplements_survival_validation_v1.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_guide(guide: dict[str, Any]) -> None:
    if set(guide) != _GUIDE_KEYS:
        raise ValueError("generation guide must contain exactly the five v3 contract keys")
    for bucket in ("visual_directives", "copy_style_directives", "positioning_context"):
        if not isinstance(guide[bucket], list):
            raise ValueError(f"generation guide {bucket} must be a list")
        for signal in guide[bucket]:
            if set(signal) != _SIGNAL_KEYS:
                raise ValueError(f"generation guide {bucket} signal shape is invalid")
            if signal["source"] != "shap:composite_success_score":
                raise ValueError("only directional composite SHAP signals may enter generation")


def _validate_training_report(report: dict[str, Any], freeze_existing_v3: bool) -> None:
    if report.get("report_schema_version") != "meta-ads-training-report-v2":
        raise ValueError("training report schema is unsupported")
    manifest = report.get("run_manifest", {})
    if manifest.get("longevity_event_semantics") != LONGEVITY_EVENT_SEMANTICS:
        raise ValueError("training report longevity semantics do not match the accepted contract")
    for field in ("matrix_sha256", "ads_sha256", "implementation_sha256"):
        value = str(manifest.get(field) or "")
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"training report {field} is not a SHA-256")
    commit = str(manifest.get("git_commit") or "")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("training report git_commit is not a full commit hash")
    if not manifest.get("git_branch") or manifest.get("git_worktree_dirty") is not False:
        raise ValueError("training report must come from a named clean Git branch")
    split = report.get("split", {})
    model = (
        report.get("model_results", {})
        .get("composite_success_score", {})
        .get("without_embeddings", {})
    )
    for field in ("n_train", "n_test"):
        if split.get(field) != model.get(field):
            raise ValueError(f"training split/model {field} mismatch")
    if split.get("advertiser_overlap") != 0 or model.get("advertiser_overlap") != 0:
        raise ValueError("training report contains advertiser leakage")
    if model.get("test_mae", float("inf")) >= model.get("baseline_mae", float("-inf")):
        raise ValueError("no-embedding model does not beat its point baseline")
    with_embeddings = (
        report.get("model_results", {})
        .get("composite_success_score", {})
        .get("with_embeddings", {})
    )
    if with_embeddings.get("test_mae", float("-inf")) <= model["test_mae"] or with_embeddings.get(
        "top_20pct_precision", float("inf")
    ) >= model.get("top_20pct_precision", float("-inf")):
        raise ValueError(
            "generation handoff requires embeddings to be worse on MAE and ranking precision"
        )
    for candidate in (model, with_embeddings):
        for field in ("n_train", "n_test"):
            if candidate.get(field) != split.get(field):
                raise ValueError(f"training split/model {field} mismatch")
        if candidate.get("split_strategy") != split.get("strategy"):
            raise ValueError("training split/model strategy mismatch")
        if candidate.get("advertiser_overlap") != 0:
            raise ValueError("training report contains advertiser leakage")
    cox = report.get("model_results", {}).get("days_active", {}).get("cox_survival", {})
    if freeze_existing_v3:
        if (
            cox.get("evaluation_status") != "insufficient_observed_events"
            or cox.get("n_events_observed_train") != 0
            or cox.get("n_events_observed_test") != 0
            or cox.get("top_covariates") != []
        ):
            raise ValueError("existing v3 freeze requires zero-event, empty-covariate Cox evidence")
    elif report.get("promotion_decision", {}).get("status") != "promoted":
        raise ValueError("a new handoff bundle requires an objective promoted decision")


def _validate_survival(report: dict[str, Any], freeze_existing_v3: bool) -> None:
    if report.get("report_schema_version") != "survival-validation-v1":
        raise ValueError("survival report schema is unsupported")
    if len(str(report.get("input_hash") or "")) != 64:
        raise ValueError("survival input_hash is invalid")
    if freeze_existing_v3:
        if (
            report.get("status") != "insufficient_observed_endings"
            or report.get("events_observed") != 2
        ):
            raise ValueError("existing v3 freeze requires exactly two observed warehouse endings")
        if set(report.get("horizons", {})) != {"30", "60", "90", "180"}:
            raise ValueError("existing v3 survival horizons are incomplete")
        if any(row.get("status") != "insufficient_evidence" for row in report["horizons"].values()):
            raise ValueError("existing v3 survival horizons must remain insufficient evidence")


def build_handoff_bundle(
    guide_file: Path,
    training_report_file: Path,
    survival_report_file: Path,
    output_dir: Path,
    version: str,
    freeze_existing_v3: bool = False,
) -> dict[str, Any]:
    """Validate, copy, hash, and atomically publish one immutable bundle."""
    if freeze_existing_v3 and not (
        version == "supplements-v3" or version.startswith("supplements-v3-bundle-")
    ):
        raise ValueError("--freeze-existing-v3 requires a supplements-v3 bundle version")
    guide = json.loads(guide_file.read_text())
    training = json.loads(training_report_file.read_text())
    survival = json.loads(survival_report_file.read_text())
    _validate_guide(guide)
    _validate_training_report(training, freeze_existing_v3)
    _validate_survival(survival, freeze_existing_v3)
    derived = extract_generation_guide(training_report_file).model_dump(mode="json")
    for bucket in ("visual_directives", "copy_style_directives", "positioning_context"):
        if guide[bucket] != derived[bucket]:
            raise ValueError(
                f"generation guide {bucket} does not match report-derived SHAP evidence"
            )
    if output_dir.exists():
        raise FileExistsError(f"immutable handoff bundle already exists: {output_dir}")

    sources = {
        "generation_guide": guide_file,
        "training_report": training_report_file,
        "survival_validation": survival_report_file,
    }
    if len({source.name for source in sources.values()}) != len(sources):
        raise ValueError("handoff source filenames must be distinct")
    if freeze_existing_v3:
        actual_hashes = {role: _sha256(path) for role, path in sources.items()}
        if actual_hashes != ACCEPTED_V3_HASHES:
            raise ValueError("existing v3 artifacts do not match the frozen accepted hashes")
    schemas = {
        "generation_guide": "generation-guide-v1",
        "training_report": training["report_schema_version"],
        "survival_validation": survival["report_schema_version"],
    }
    manifest = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_version": version,
        "evidence_version": "supplements-v3" if freeze_existing_v3 else version,
        "status": "frozen_existing_evidence" if freeze_existing_v3 else "promoted",
        "loop_isolation": "generated outputs are prohibited as Meta performance labels",
        "source_git": {
            key: training["run_manifest"].get(key)
            for key in ("git_branch", "git_commit", "git_worktree_dirty")
        },
        "files": {},
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_target = output_dir.parent / f".{output_dir.name}.bundle"
    with exclusive_output(lock_target):
        if output_dir.exists():
            raise FileExistsError(f"immutable handoff bundle already exists: {output_dir}")
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
        try:
            for role, source in sources.items():
                destination_name = _CANONICAL_FILENAMES[role]
                destination = temp_dir / destination_name
                shutil.copyfile(source, destination)
                manifest["files"][role] = {
                    "path": destination_name,
                    "sha256": _sha256(destination),
                    "schema_version": schemas[role],
                }
            atomic_write_json(temp_dir / "manifest.json", manifest)
            os.replace(temp_dir, output_dir)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    return manifest


def verify_handoff_bundle(output_dir: Path) -> dict[str, Any]:
    """Recompute every content hash before transfer or consumption."""
    manifest_file = output_dir / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if manifest.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError("handoff bundle manifest schema is unsupported")
    if set(manifest.get("files", {})) != {
        "generation_guide",
        "training_report",
        "survival_validation",
    }:
        raise ValueError("handoff bundle must contain exactly the three contract roles")
    for role, entry in manifest.get("files", {}).items():
        relative_path = Path(entry["path"])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"handoff bundle {role} path escapes the bundle")
        path = output_dir / relative_path
        if output_dir.resolve() not in path.resolve().parents:
            raise ValueError(f"handoff bundle {role} path escapes the bundle")
        if not path.is_file() or _sha256(path) != entry["sha256"]:
            raise ValueError(f"handoff bundle {role} content hash mismatch")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify an immutable Generation handoff.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--guide", type=Path, required=True)
    build.add_argument("--training-report", type=Path, required=True)
    build.add_argument("--survival-report", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--freeze-existing-v3", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify":
        manifest = verify_handoff_bundle(args.bundle)
        print(f"Verified handoff bundle {manifest['bundle_version']}: {args.bundle}")
        return
    for path in (args.guide, args.training_report, args.survival_report):
        if not path.is_file():
            parser.error(f"input not found: {path}")
    manifest = build_handoff_bundle(
        args.guide,
        args.training_report,
        args.survival_report,
        args.out,
        args.version,
        args.freeze_existing_v3,
    )
    print(f"Built handoff bundle {manifest['bundle_version']}: {args.out}")


if __name__ == "__main__":
    main()
