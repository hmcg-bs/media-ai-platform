# Creatives

Drop ad-creative images you want to analyse into **`input/`**. Step 2 writes one
JSON extraction document per image into **`output/`**.

- Supported: `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`
- The output filename uses the image's name (its `ad_id`), e.g.
  `input/glow_serum.jpg` → `output/glow_serum.json`.

## Run

```bash
uv run python -m pipeline.orchestrator --input ./creatives/input --out ./creatives/output
```

Requires `gcloud auth application-default login` (Cloud Vision + Vertex AI).
If a stage fails (e.g. no network), that stage's fields stay at defaults and the
run continues — you still get a JSON file.

> Images in `input/` are gitignored (they may be proprietary). The folders
> themselves are kept via `.gitkeep`.
