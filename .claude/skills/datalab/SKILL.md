---
name: datalab
description: Work with the Datalab document-AI API via the datalab-python-sdk (DatalabClient) — convert, extract (structured schema), OCR, segment, and multi-step pipelines. Use when the user mentions Datalab, datalab_sdk, DatalabClient, marker, PipelineExecution, get_step_result, DATALAB_API_KEY, or wants to turn a PDF/image into markdown/JSON, extract structured fields with a schema, or run/save a Datalab pipeline execution.
---

# Datalab

Datalab (docs: https://documentation.datalab.to/) is a document-AI API. Python SDK
package is `datalab-python-sdk`, imported as `datalab_sdk`. Installed version here: **0.4.0**.

## Setup

```bash
uv add datalab-python-sdk                 # or: uv pip install datalab-python-sdk
export DATALAB_API_KEY=...                # required; DatalabClient() reads it
```

Run scripts with **plain `uv run`** (NOT `uv run --active`). This project's shell has a
stale `VIRTUAL_ENV` pointing at Homebrew 3.14; `--active` fails, bare `uv run` correctly
targets `.venv`.

## Two ways to use it

**1. One-shot methods** — `convert`, `extract`, `ocr`, `segment`. These return a result
object AND accept `save_output=` to write straight to disk. Prefer these for single tasks:

```python
from datalab_sdk import DatalabClient
client = DatalabClient()
client.convert("doc.pdf", save_output="out.json")          # markdown/html/json/chunks
```

**2. Pipelines** — a saved, versioned chain of steps (e.g. convert→extract). `run_pipeline`
returns a `PipelineExecution` that contains only **metadata + result_url pointers**, NOT the
extracted content. You must call `get_step_result(execution_id, step_index)` to get the
actual data (step 0 = convert, step 1 = extract, …). See [REFERENCE.md](REFERENCE.md).

```python
ex = client.run_pipeline("pl_...", file_path="ad.jpg", output_format="json")
result = client.get_pipeline_execution(ex.execution_id, max_polls=300, poll_interval=2)
data = client.get_step_result(result.execution_id, 1)      # <- the real extract output
```

## Saving results (the common ask)

- `PipelineExecution` is a **dataclass** → serialize with `dataclasses.asdict(obj)`.
- `get_step_result(...)` returns a **plain dict** → `json.dump` it directly.
- To save an execution you already ran **without re-billing**, use the helper instead of
  re-calling `run_pipeline`:

```bash
uv run python .claude/skills/datalab/scripts/save_execution.py pex_xxx --out ./datalab_out
```

## Gotchas (read before running)

- **`run_pipeline` bills on every call.** Don't re-run just to save output — fetch by
  `execution_id` instead. Cost is in `execution.rate_breakdown` (cents per 1000 pages).
- `extract` needs a schema: pass `page_schema` (JSON-schema string) or a saved `schema_id`.
- The printed `result` object looks empty of content — that's expected; content is behind
  `result_url` / `get_step_result`.
- **Extraction mode is `ExtractOptions.mode`** (default `"fast"`), NOT `extraction_mode`.
  Passing `extraction_mode=` is silently ignored → you get fast-mode behavior + billing.
- **`ConversionResult.extraction_schema_json` is a JSON string** from one-shot `extract()` —
  `json.loads` it before dumping, or it double-encodes on disk.
- **No font/size/color from any processor.** Datalab reads text+structure, not pixels; those
  come from pixel tooling using Datalab's bbox. See the full gotchas list in REFERENCE.

Full API surface, option fields, and confirmed gotchas: [REFERENCE.md](REFERENCE.md).
