"""Thin, mockable wrapper over apify-client for the facebook-ads-library-scraper actor.

Exposes `run_ad_scrape(search_query, count, ...) -> list[dict]` (raw dataset items).
The actor scrapes Meta Ad Library search results. Lazily builds `ApifyClient(api_token)`,
calls the configured actor, and retries on transient failures (429, 5xx, timeout) via tenacity.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


class ApifyClientError(Exception):
    """Raised when Apify actor run fails."""

    pass


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is transient (429, 5xx, timeout)."""
    exc_str = str(exc).lower()
    return any(
        sig in exc_str for sig in ("429", "500", "502", "503", "504", "timeout", "readtimeout")
    )


class ApifyClient:
    """Lazily-built Apify client for running the facebook-ads-library-scraper actor."""

    def __init__(self, api_token: str, timeout_s: int = 300):
        self.api_token = api_token
        self.timeout_s = timeout_s
        self._client: Any = None

    @property
    def client(self) -> Any:
        """Lazily import and build the ApifyClient."""
        if self._client is None:
            from apify_client import ApifyClient as _ApifyClient

            self._client = _ApifyClient(token=self.api_token)
        return self._client

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
        before_sleep=lambda retry_state: logger.info(
            "apify_retry",
            attempt=retry_state.attempt_number,
            exc_str=str(retry_state.outcome.exception()),
        ),
    )
    def run_ad_scrape(
        self,
        search_query: str,
        count: int = 100,
        actor_id: str | None = None,
        country: str = "US",
    ) -> list[dict]:
        """Run the facebook-ads-library-scraper actor and return raw dataset items.

        Args:
            search_query: Facebook page name or URL (e.g., "apple", "linkedin").
                If not a full URL, will be converted to https://www.facebook.com/<query>
            count: Max ads to scrape (default 100).
            actor_id: Actor ID (default: from settings).
            country: 2-letter ISO country code or "ALL" (default "US").

        Returns:
            List of raw dataset items (Meta Ad Library items) from the actor run.

        Raises:
            ApifyClientError: If the run fails permanently.
        """
        settings = get_settings()
        actor_id = actor_id or settings.apify_actor_id

        # Convert search query to Facebook page URL if needed
        if search_query.startswith("http"):
            page_url = search_query
        else:
            page_url = f"https://www.facebook.com/{search_query}"

        input_dict = {
            "urls": [page_url],
            "limitPerSource": count,
            "count": count,
            "scrapePageAds": {
                "countryCode": country,
            },
        }

        logger.info(
            "apify_actor_start",
            actor_id=actor_id,
            page_url=page_url,
            country=country,
            count=count,
        )

        try:
            run = self.client.actor(actor_id).call(
                run_input=input_dict,
                timeout=timedelta(seconds=self.timeout_s),
            )
        except Exception as e:
            msg = f"Apify actor {actor_id} failed: {e}"
            logger.error("apify_actor_error", exc_str=str(e), actor_id=actor_id)
            raise ApifyClientError(msg) from e

        logger.info(
            "apify_actor_completed",
            actor_id=actor_id,
            run_status=run.get("status"),
        )

        # Fetch the dataset items (the raw ad records).
        try:
            items = self.client.dataset(run["defaultDatasetId"]).list_items()["items"]
        except Exception as e:
            msg = f"Failed to fetch dataset from {run.get('defaultDatasetId')}: {e}"
            logger.error("apify_dataset_error", exc_str=str(e))
            raise ApifyClientError(msg) from e

        logger.info(
            "apify_dataset_fetched",
            item_count=len(items),
        )

        return items


# Singleton instance + injectable run_fn for tests.
_client_instance: ApifyClient | None = None
_run_fn: Callable[[str, int, str | None, str], list[dict]] | None = None


def run_ad_scrape(
    search_query: str,
    count: int = 100,
    actor_id: str | None = None,
    country: str = "US",
    run_fn: Callable[[str, int, str | None, str], list[dict]] | None = None,
) -> list[dict]:
    """Run the facebook-ads-library-scraper actor, with injected run_fn for tests.

    Args:
        search_query: Search term for Ad Library scraping.
        count: Max ads to scrape.
        actor_id: Actor ID (default: from settings).
        country: Country code (default "US").
        run_fn: Injected function for offline tests. If provided, called instead of Apify.

    Returns:
        List of raw dataset items.
    """
    global _client_instance, _run_fn

    if run_fn is not None:
        return run_fn(search_query, count, actor_id, country)

    if _run_fn is not None:
        return _run_fn(search_query, count, actor_id, country)

    if _client_instance is None:
        settings = get_settings()
        _client_instance = ApifyClient(
            api_token=settings.apify_api_token,
            timeout_s=300,
        )

    return _client_instance.run_ad_scrape(search_query, count, actor_id, country)
