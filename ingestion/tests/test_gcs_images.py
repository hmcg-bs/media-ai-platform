from __future__ import annotations

import io

from google.api_core.exceptions import Forbidden
from PIL import Image

from ingestion.gcs_images import (
    GCSImageStore,
    compress_reference_image,
    estimate_monthly_storage_cost,
)


def _jpeg(width: int = 2200, height: int = 1100) -> bytes:
    image = Image.new("RGB", (width, height), (20, 100, 180))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def test_compression_bounds_dimension_and_emits_valid_webp():
    source = _jpeg()
    compressed = compress_reference_image(source, max_dimension=1600, quality=82)
    assert (compressed.width, compressed.height) == (1600, 800)
    assert compressed.stored_bytes < compressed.source_bytes
    with Image.open(io.BytesIO(compressed.content)) as image:
        assert image.format == "WEBP"


def test_storage_cost_uses_binary_gibibytes():
    assert estimate_monthly_storage_cost(1024**3) == 0.02


def test_bucket_metadata_denial_does_not_block_bucket_scoped_object_writer():
    class FakeClient:
        def __init__(self):
            self.created = False

        def bucket(self, name):
            return object()

        def get_bucket(self, name):
            raise Forbidden("bucket metadata is intentionally unavailable")

        def create_bucket(self, name, location):
            self.created = True

    client = FakeClient()
    store = GCSImageStore(bucket_name="existing-bucket", client=client)
    store.ensure_bucket()
    assert client.created is False
