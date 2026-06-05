"""Small HTTP helper with retry/back-off shared by every source client."""

from __future__ import annotations

import logging
import time

import requests

from .config import SETTINGS

logger = logging.getLogger(__name__)


def get_with_retry(url: str, params: dict | None = None) -> requests.Response:
    """GET a URL, retrying with exponential back-off on transient failures.

    Raises the last exception if every attempt fails so the caller (and the
    Data Factory activity) sees the pipeline fail loudly rather than loading
    partial data silently.
    """
    last_exc: Exception | None = None
    for attempt in range(1, SETTINGS.max_retries + 1):
        try:
            resp = requests.get(
                url, params=params, timeout=SETTINGS.request_timeout_seconds
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            backoff = 2 ** (attempt - 1)
            logger.warning(
                "Request to %s failed (attempt %d/%d): %s. Retrying in %ds.",
                url,
                attempt,
                SETTINGS.max_retries,
                exc,
                backoff,
            )
            time.sleep(backoff)

    assert last_exc is not None
    raise last_exc
