"""Thin, mockable client for Datalab: plain convert + schema extract → copy analysis.

Uses **plain convert** (not the Style Preserver processor): it keeps the full text, including
the main headline that Style Preserver drops. ``analyze`` runs convert (with a checkpoint) then
extract (reusing that checkpoint, so convert isn't re-billed) against the ad-creative schema,
returning the parsed ``DatalabDocument`` (copy + geometry) and the extract dict (semantic roles
+ marketing ``message``). Injectable ``convert_fn``/``extract_fn`` keep tests offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipeline.config import Settings, get_settings
from pipeline.datalab.models import DatalabDocument
from pipeline.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "datalab" / "schemas" / "ad_creative_schema.json"
)

# convert: (image_path) -> (convert_json, checkpoint_id | None)
ConvertFn = Callable[[str], tuple[dict[str, Any], str | None]]
# extract: (checkpoint_id | None, image_path) -> extract dict
ExtractFn = Callable[[str | None, str], dict[str, Any]]


class DatalabDocumentClient:
    def __init__(
        self,
        convert_fn: ConvertFn | None = None,
        extract_fn: ExtractFn | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self._convert = convert_fn
        self._extract = extract_fn

    def _sdk(self):
        from datalab_sdk import DatalabClient  # lazy; needs DATALAB_API_KEY

        return DatalabClient(api_key=self.settings.datalab_api_key)

    def _converter(self) -> ConvertFn:
        if self._convert is None:
            from datalab_sdk.models import ConvertOptions

            client = self._sdk()

            def run(image_path: str) -> tuple[dict[str, Any], str | None]:
                result = client.convert(
                    image_path,
                    options=ConvertOptions(
                        output_format="json", mode="accurate",
                        add_block_ids=True, save_checkpoint=True,
                    ),
                )
                return (result.json or {}), result.checkpoint_id

            self._convert = run
        return self._convert

    def _extractor(self) -> ExtractFn:
        if self._extract is None:
            from datalab_sdk.models import ExtractOptions

            client = self._sdk()
            schema_str = _SCHEMA_PATH.read_text()

            def run(checkpoint_id: str | None, image_path: str) -> dict[str, Any]:
                options = ExtractOptions(
                    checkpoint_id=checkpoint_id, page_schema=schema_str,
                    mode="balanced", output_format="json",
                )
                # With a checkpoint we skip re-parsing; otherwise extract from the file.
                result = client.extract(
                    file_path=None if checkpoint_id else image_path, options=options
                )
                data = result.extraction_schema_json or {}
                return json.loads(data) if isinstance(data, str) else data

            self._extract = run
        return self._extract

    def convert(self, image_path: str | Path) -> DatalabDocument:
        """Convert only → parsed ``DatalabDocument`` (copy + geometry, no roles/message)."""
        logger.debug("datalab_convert", image=str(image_path))
        convert_json, _ = self._converter()(str(image_path))
        return DatalabDocument.model_validate(convert_json)

    def analyze(self, image_path: str | Path) -> tuple[DatalabDocument, dict[str, Any]]:
        """Convert + extract → (DatalabDocument, extract dict with roles + marketing message)."""
        logger.debug("datalab_analyze", image=str(image_path))
        convert_json, checkpoint = self._converter()(str(image_path))
        doc = DatalabDocument.model_validate(convert_json)
        extract = self._extractor()(checkpoint, str(image_path))
        return doc, extract
