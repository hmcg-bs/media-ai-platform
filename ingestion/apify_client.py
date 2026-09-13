"""Thin, mockable wrapper over apify-client for the facebook-ads-library-scraper actor.

Exposes `run_ad_scrape(search_query, count, ...) -> list[dict]` (raw dataset items).
The actor scrapes Meta Ad Library search results. Lazily builds `ApifyClient(api_token)`,
calls the configured actor, and retries on transient failures (429, 5xx, timeout) via tenacity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

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


@dataclass(frozen=True)
class ActorDatasetResult:
    items: list[dict]
    status_message: str

    @property
    def sources_exhausted(self) -> bool:
        """Whether the actor says it exhausted URLs rather than its result cap."""
        return "scraped all urls" in self.status_message.lower()


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

    def _run_actor_result(self, input_dict: dict[str, Any], actor_id: str) -> ActorDatasetResult:
        """Run one actor input and retain completion evidence with its dataset."""
        try:
            run = self.client.actor(actor_id).call(
                run_input=input_dict,
                timeout=timedelta(seconds=self.timeout_s),
            )
        except Exception as e:
            msg = f"Apify actor {actor_id} failed: {e}"
            logger.error("apify_actor_error", exc_str=str(e), actor_id=actor_id)
            raise ApifyClientError(msg) from e

        logger.info("apify_actor_completed", actor_id=actor_id, run_status=run.status)
        try:
            items = self.client.dataset(run.default_dataset_id).list_items().items
        except Exception as e:
            msg = f"Failed to fetch dataset from {run.default_dataset_id}: {e}"
            logger.error("apify_dataset_error", exc_str=str(e))
            raise ApifyClientError(msg) from e
        actor_errors = [str(item["error"]) for item in items if item.get("error")]
        if actor_errors:
            msg = f"Apify actor {actor_id} returned dataset errors: {actor_errors[:3]}"
            logger.error("apify_dataset_item_error", actor_id=actor_id, errors=actor_errors[:3])
            raise ApifyClientError(msg)
        logger.info("apify_dataset_fetched", item_count=len(items))
        return ActorDatasetResult(
            items=items,
            status_message=str(getattr(run, "status_message", "") or ""),
        )

    def _run_actor_input(self, input_dict: dict[str, Any], actor_id: str) -> list[dict]:
        """Run one actor input and return its default dataset items."""
        return self._run_actor_result(input_dict, actor_id).items

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
            search_query: Search term for Ad Library (e.g., "creatine", "apple").
            count: Max ads to scrape (default 100).
            actor_id: Actor ID (default: from settings).
            country: Country code (default "US"; actor normalizes to uppercase).

        Returns:
            List of raw dataset items (Meta Ad Library items) from the actor run.

        Raises:
            ApifyClientError: If the run fails permanently.
        """
        settings = get_settings()
        actor_id = actor_id or settings.apify_actor_id

        # Construct Ad Library search URL with the search query
        query = urlencode(
            {
                "active_status": "active",
                "ad_type": "all",
                "country": country.upper(),
                "is_targeted_country": "false",
                "media_type": "all",
                "q": search_query,
                "search_type": "keyword_unordered",
                "sort_data[direction]": "desc",
                "sort_data[mode]": "total_impressions",
            }
        )
        ad_library_url = f"https://www.facebook.com/ads/library/?{query}"

        input_dict = {
            "urls": [{"url": ad_library_url}],
            "count": count,
            "scrapeAdDetails": True,
            "scrapePageAds": {
                "activeStatus": "active",
                "countryCode": country.upper(),
                "period": "last30d",
                "sortBy": "impressions_desc",
            },
        }

        logger.info(
            "apify_actor_start",
            actor_id=actor_id,
            search_query=search_query,
            country=country,
            count=count,
        )

        return self._run_actor_input(input_dict, actor_id)

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
    def poll_ad_pages(
        self,
        page_ids: list[str],
        count: int,
        actor_id: str | None = None,
        country: str = "US",
    ) -> ActorDatasetResult:
        """Poll page inventories so tracked IDs can be matched exactly.

        A returned inactive row or stop date is explicit evidence. Absence is
        only a *miss* when the same page returned at least one result. This
        positive-control rule prevents redirect/source failures from turning
        an empty actor dataset into false deactivation evidence.
        """
        if not page_ids:
            return ActorDatasetResult(items=[], status_message="no pages requested")
        settings = get_settings()
        actor_id = actor_id or settings.apify_actor_id
        urls = [
            {
                "url": (
                    "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
                    f"&country={country.upper()}&view_all_page_id={page_id}"
                )
            }
            for page_id in page_ids
        ]
        input_dict = {
            "urls": urls,
            # The actor rejects paid-result limits below 10 even when fewer
            # source URLs are requested (confirmed against the live actor).
            "count": max(10, count),
            "scrapeAdDetails": True,
            "scrapePageAds": {
                "activeStatus": "all",
                "countryCode": country.upper(),
                "period": "",
                "sortBy": "impressions_desc",
            },
        }
        logger.info("apify_ad_page_poll_start", page_count=len(page_ids), country=country)
        return self._run_actor_result(input_dict, actor_id)


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
