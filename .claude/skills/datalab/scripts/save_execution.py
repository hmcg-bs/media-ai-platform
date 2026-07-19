#!/usr/bin/env python
"""Save a Datalab pipeline execution's outputs to disk WITHOUT re-running (no re-billing).

Fetches the execution metadata + every step's actual result by execution_id.

Usage:
    uv run python .claude/skills/datalab/scripts/save_execution.py pex_xxx --out ./datalab_out

Requires DATALAB_API_KEY in the environment.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from datalab_sdk import DatalabClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution_id", help="Datalab execution id, e.g. pex_xxx")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("datalab_out"),
        help="Output directory (default: ./datalab_out)",
    )
    parser.add_argument("--max-polls", type=int, default=300)
    parser.add_argument("--poll-interval", type=int, default=2)
    args = parser.parse_args()

    client = DatalabClient()  # reads DATALAB_API_KEY
    args.out.mkdir(parents=True, exist_ok=True)

    result = client.get_pipeline_execution(
        args.execution_id, max_polls=args.max_polls, poll_interval=args.poll_interval
    )

    # 1) Execution metadata (PipelineExecution is a dataclass).
    (args.out / "execution_metadata.json").write_text(
        json.dumps(asdict(result), indent=2)
    )

    # 2) Actual per-step content (get_step_result returns a plain dict).
    for step in result.steps:
        data = client.get_step_result(result.execution_id, step.step_index)
        filename = f"step_{step.step_index}_{step.step_type}.json"
        (args.out / filename).write_text(json.dumps(data, indent=2))
        print(f"saved {filename}")

    print(f"\nStatus: {result.status}. All outputs written to {args.out}")


if __name__ == "__main__":
    main()
