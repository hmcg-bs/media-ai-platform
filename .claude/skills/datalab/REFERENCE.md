# Datalab SDK Reference

Verified against `datalab-python-sdk` **0.4.0**. Official docs:
https://documentation.datalab.to/ · LLM index: https://documentation.datalab.to/llms.txt

## Products

| Capability | Method | Notes |
|---|---|---|
| Document Conversion | `convert` | PDF/Word/spreadsheet → markdown, html, json, chunks |
| Structured Extraction | `extract` | Pull schema-defined fields with source citations |
| OCR | `ocr` | Text recognition, 90+ languages |
| Segmentation | `segment` | Split multi-doc PDFs into logical sections |
| Form Filling | `fill` | Auto-populate PDF/image forms |
| Track Changes | `track_changes` | Redlines/comments from Word |
| Pipelines | `run_pipeline` | Saved, versioned chain of the above steps |

## Auth & client

```python
from datalab_sdk import DatalabClient
client = DatalabClient()                 # reads DATALAB_API_KEY
client = DatalabClient(api_key="...")    # or pass explicitly
```
Missing key raises `DatalabAPIError: You must pass in an api_key or set DATALAB_API_KEY.`

## One-shot method signatures

All of these accept `file_path=` or `file_url=`, an `options=` dataclass, `save_output=`
(write result to disk), `stream_response_to=`, and polling controls `max_polls`/`poll_interval`.

```python
convert(file_path=None, file_url=None, options: ConvertOptions=None,
        save_output=None, stream_response_to=None, max_polls=300, poll_interval=1)
extract(file_path=None, file_url=None, options: ExtractOptions=None, save_output=None, ...)
ocr(file_path, options: ProcessingOptions=None, save_output=None, ...)
segment(file_path=None, file_url=None, options: SegmentOptions=None, save_output=None, ...)
```

### Options dataclasses (field: type)

**ConvertOptions** — `max_pages`, `skip_cache`, `page_range`, `paginate`,
`disable_image_extraction`, `disable_image_captions`, `fence_synthetic_captions`,
`additional_config`, `save_checkpoint`, `output_format` (`markdown|html|json|chunks`),
`mode`, `keep_spreadsheet_formatting`, `webhook_url`, `extras`, `add_block_ids`,
`include_markdown_in_chunks`, `token_efficient_markdown`, `eval_rubric_id`.

**ExtractOptions** — `max_pages`, `skip_cache`, `page_range`, `page_schema` (JSON-schema
string), `schema_id`, `schema_version`, `checkpoint_id`, `mode` (e.g. `balanced`),
`output_format`, `save_checkpoint`, `webhook_url`.

**SegmentOptions** — `max_pages`, `skip_cache`, `page_range`, `segmentation_schema`,
`checkpoint_id`, `mode`, `save_checkpoint`, `webhook_url`.

**ProcessingOptions** (OCR) — `max_pages`, `skip_cache`, `page_range`.

### Example: structured extraction with an inline schema

```python
from datalab_sdk import DatalabClient
from datalab_sdk.models import ExtractOptions

schema = '{"type":"object","properties":{"text_content":{"type":"string"}},"required":["text_content"]}'
client = DatalabClient()
res = client.extract(
    file_path="ad.jpg",
    options=ExtractOptions(page_schema=schema, mode="balanced", output_format="json"),
    save_output="extract.json",   # writes result straight to disk
)
```

## Pipelines

A pipeline is a saved chain of steps referenced by `pipeline_id` (`pl_...`). Running one
returns a `PipelineExecution` (an `execution_id` like `pex_...`).

```python
run_pipeline(pipeline_id, file_path=None, file_url=None, page_range=None,
             output_format=None, run_evals=False, skip_cache=False,
             webhook_url=None, version=None, max_polls=1, poll_interval=1) -> PipelineExecution
get_pipeline(pipeline_id) -> PipelineConfig
get_pipeline_execution(execution_id, max_polls=1, poll_interval=1) -> PipelineExecution
get_step_result(execution_id, step_index) -> dict        # THE ACTUAL CONTENT
list_pipelines(saved_only=True, include_archived=False, limit=50, offset=0) -> dict
create_pipeline(steps: list[PipelineProcessor]) -> PipelineConfig
create_extraction_schema(name, schema_json, description=None) -> ExtractionSchema
```

### `PipelineExecution` shape (what you get back)

A **dataclass** — serialize with `dataclasses.asdict(...)`. Key fields:

- `execution_id`, `pipeline_id`, `pipeline_version`, `status` (`completed`/…)
- `steps[]` — each a `PipelineExecutionStepResult` with `step_index`, `step_type`
  (`convert`/`extract`/…), `status`, `result_url`, `checkpoint_id`, timestamps.
  **`result_url` is a pointer, not content** — dereference with `get_step_result`.
- `config_snapshot` — the frozen step config (incl. the `page_schema` used)
- `input_config` — `{filename, page_range, output_format}`
- `rate_breakdown` — billing: per-step `rate_per_1000_cents` and `total_rate_per_1000_cents`

### Fetch + save the real content

```python
import json
from dataclasses import asdict

result = client.get_pipeline_execution(execution_id, max_polls=300, poll_interval=2)
# metadata:
open("execution_metadata.json","w").write(json.dumps(asdict(result), indent=2))
# actual data per step:
for step in result.steps:
    data = client.get_step_result(result.execution_id, step.step_index)
    open(f"step_{step.step_index}_{step.step_type}.json","w").write(json.dumps(data, indent=2))
```

## Billing note

Every `run_pipeline` / one-shot call is billed by page (`rate_per_1000_cents`). Example seen
in this project: convert(markdown) 400 + extract(balanced-checkpoint) 2100 = 2500 c/1000 pages.
To save a result you already produced, **fetch by `execution_id`** — never re-run to save.
`skip_cache=False` lets identical inputs hit cache, but fetching is free and deterministic.

## Gotchas confirmed in practice (SDK 0.4.0)

These were verified against real runs — they are non-obvious and cost time to rediscover.

- **Extraction mode is `ExtractOptions.mode`, not `extraction_mode`.** The field is literally
  `mode` (default `"fast"`); set `mode="balanced"` for verification+reasoning. The SDK sends
  it verbatim as `mode`. Passing `extraction_mode=` is silently ignored (you get fast-mode
  behavior + billing). Note `ConvertOptions.mode` is a *different* axis — parsing quality
  (`fast`/`balanced`/`accurate`).
- **`ConversionResult.extraction_schema_json` is a JSON *string*, not a dict.** From the
  one-shot `extract()`, parse it: `data = json.loads(data) if isinstance(data, str) else data`.
  Otherwise `json.dump` double-encodes it on disk. (The pipeline `get_step_result` path
  returns it already-parsed — the inconsistency is real.)
- **Datalab upscales the image; bboxes are in the convert-canvas space, not original pixels.**
  A 1080×1920 image came back on a 1540×2744 canvas (the `Page` block bbox). To crop the
  original image at a block's bbox, scale by `img_dim / canvas_dim` first. The canvas size is
  the `Page` block's bbox in the convert JSON.
- **`output_format="json"` carries per-block `bbox` + `polygon`; markdown/chunks do not.**
  Empirically true even though the docs are vague about it. Required if you want coordinates.
- **Text nested inside a Figure/Picture/Diagram/Table has no standalone text bbox.** Those
  labels appear only inside the container block's HTML; the container's bbox is a whole region,
  not a per-line box. Don't match a label to a container block (you'll get a nonsense giant
  box) — exclude image/table block types when attaching text bboxes.
- **No typography/color anywhere.** No processor (convert/extract/segment/ocr) or `extras`
  option returns font family, size, weight, or text color. Only bold/italic survive as
  `<b>`/`<i>` in the block HTML. Font size ≈ derive from bbox height ÷ line count; text color ≈
  crop the original image at the bbox and sample. Custom processors are AI-generated config to
  fine-tune conversion output, not a way to add pixel-style extraction.

## Environment gotcha (this repo)

The shell exports a stale `VIRTUAL_ENV` pointing at Homebrew's Python 3.14 framework, which
is not the uv project env. Consequences:
- `uv run --active ...` → errors ("not a compatible environment / not a virtual environment").
- **Fix:** use plain `uv run python ...`; uv ignores the stale var (with a warning) and uses
  the project `.venv` (CPython 3.12).

## Full client method list (0.4.0)

`convert, extract, ocr, segment, fill, track_changes, run_pipeline, run_custom_pipeline,
get_pipeline, get_pipeline_execution, get_execution_status, get_step_result, get_step_types,
create_pipeline, create_pipeline_version, save_pipeline, update_pipeline, discard_pipeline_draft,
archive_pipeline, unarchive_pipeline, list_pipelines, list_pipeline_versions,
list_pipeline_executions, get_pipeline_rate, create_extraction_schema, get_extraction_schema,
update_extraction_schema, delete_extraction_schema, list_extraction_schemas,
upload_files, list_files, get_file_metadata, get_file_download_url, delete_file, create_document,
create_workflow, execute_workflow, get_workflow, update_workflow, delete_workflow, list_workflows,
run_custom_processor, get_custom_processor_status, list_custom_processors,
list_custom_processor_versions, set_active_processor_version, archive_custom_processor`.
