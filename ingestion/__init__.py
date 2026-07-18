"""Step 1 (ingestion): scrape competitor Facebook ads via Apify into a local corpus.

Deliberately decoupled from the ``pipeline/`` package (Step 2 extraction): it does not touch
Vertex, BigQuery, or ADC. It reuses ``pipeline.config`` (token) and ``pipeline.logger`` only.
"""
