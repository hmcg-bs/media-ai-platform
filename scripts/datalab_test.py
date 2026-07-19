"""Smoke test: run the Datalab pipeline on one image and print the extracted result.

Run:  uv run python scripts/datalab_test.py   (needs DATALAB_API_KEY set)
"""

import json
from pathlib import Path

from datalab_sdk import DatalabClient

PIPELINE_ID = "pl_km1gvTKosfim"
IMAGE = Path(__file__).parent.parent / "example_creatives" / "meta_ad_exp.jpg"
OUT = Path(__file__).parent / "datalab_out" / "extract.json"

client = DatalabClient()

# 1. Run the pipeline on the image.
execution = client.run_pipeline(PIPELINE_ID, file_path=str(IMAGE), output_format="json")

# 2. Wait for it to finish.
result = client.get_pipeline_execution(execution.execution_id, max_polls=300, poll_interval=2)
print(f"execution {result.execution_id} -> {result.status}")

# 3. Read the extract step's actual output (step 1 = extract).
data = client.get_step_result(result.execution_id, 1)

# 4. Show it and save it.
print(json.dumps(data, indent=2))
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=2))
print(f"\nsaved -> {OUT}")
