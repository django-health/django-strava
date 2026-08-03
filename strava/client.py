"""HTTP client for the Strava V3 REST API.

Sync-only wrapper around ``httpx.Client``, built from a
:class:`~strava.models.StravaConnection`.

Responsibilities:

* Authorization: inject ``Bearer <access_token>`` headers (mandatory for all
  apps by 2027-06-01; form-param tokens are never used).
* Token freshness: refresh proactively when the stored expiry is within the
  leeway, and retry once on a 401 to absorb external token invalidation.
* Resilience: retry 3× with exponential backoff on 429 and 5xx; honor
  ``Retry-After``. Standard-tier apps get 200 requests/15 min overall and
  100 reads/15 min (double that after the self-service 10-athlete upgrade),
  so 429s are a fact of life — the latest ``X-RateLimit-*`` /
  ``X-ReadRateLimit-*`` headers are kept on :attr:`last_rate_limit`.
* Pagination: :meth:`iter_activities` walks ``page``/``per_page`` until an
  empty page.

The API host is configurable via ``settings.STRAVA_API_BASE_URL`` — Strava's
replacement host becomes available 2027-01-04 and mandatory 2027-06-01.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterator, Mapping

import httpx
from django.conf import settings

from . import oauth
from .constants import API_BASE_URL, DEFAULT_STREAM_KEYS, MAX_PER_PAGE

if TYPE_CHECKING:
    from .models import StravaConnection


DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0

_RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Usage",
    "X-ReadRateLimit-Limit",
    "X-ReadRateLimit-Usage",
)


class StravaAPIError(Exception):
    """Non-retryable error returned by the Strava API."""

    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.payload = payload


class StravaClient:
    """Thin REST client. Use as a context manager so the underlying httpx
    session is closed deterministically.
    """

    def __init__(
        self,
        connection: StravaConnection,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        backoff_seconds: float = BASE_BACKOFF_SECONDS,
        sleep: callable = time.sleep,
    ):
        self.connection = connection
        resolved = base_url or getattr(settings, "STRAVA_API_BASE_URL", API_BASE_URL)
        self._base = resolved.rstrip("/")
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout)
        #: dict of the latest X-RateLimit-* / X-ReadRateLimit-* header values,
        #: each a "15min,daily" comma pair as sent by Strava.
        self.last_rate_limit: dict[str, str] = {}

    # context manager ------------------------------------------------------

    def __enter__(self) -> StravaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # core request loop ----------------------------------------------------

    def _ensure_fresh_token(self) -> None:
        if self.connection.is_token_expired():
            oauth.refresh_access_token(self.connection)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.connection.access_token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        self._ensure_fresh_token()
        url = f"{self._base}/{path.lstrip('/')}"
        retried_after_401 = False
        attempt = 0

        while True:
            response = self._http.request(
                method,
                url,
                params=params,
                headers=self._auth_headers(),
            )
            self._capture_rate_limit(response)

            if response.status_code == 401 and not retried_after_401:
                # Either clock skew or the token was invalidated externally —
                # force a refresh and retry once.
                oauth.refresh_access_token(self.connection)
                retried_after_401 = True
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                self._sleep(self._compute_backoff(response, attempt))
                attempt += 1
                continue

            if response.status_code >= 400:
                payload = _safe_json(response)
                message = _extract_error_message(payload, response.text)
                raise StravaAPIError(response.status_code, message, payload)

            if not response.content:
                return {}
            return response.json()

    def _capture_rate_limit(self, response: httpx.Response) -> None:
        captured = {
            h: response.headers[h] for h in _RATE_LIMIT_HEADERS if h in response.headers
        }
        if captured:
            self.last_rate_limit = captured

    def _compute_backoff(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self._backoff * (2**attempt)

    # resource methods -----------------------------------------------------

    def get_athlete(self) -> dict[str, Any]:
        """``GET /athlete`` — the authenticated athlete's profile."""
        return self._request("GET", "athlete")

    def list_activities(
        self,
        *,
        page: int = 1,
        per_page: int = MAX_PER_PAGE,
        before: datetime | int | None = None,
        after: datetime | int | None = None,
    ) -> list[dict[str, Any]]:
        """One page of ``GET /athlete/activities``.

        Use :meth:`iter_activities` to walk all pages. ``before``/``after``
        accept aware datetimes or raw epoch seconds.
        """
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if before is not None:
            params["before"] = _epoch(before)
        if after is not None:
            params["after"] = _epoch(after)
        return self._request("GET", "athlete/activities", params=params)

    def iter_activities(
        self,
        *,
        per_page: int = MAX_PER_PAGE,
        before: datetime | int | None = None,
        after: datetime | int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate every SummaryActivity across pages (newest first)."""
        page = 1
        while True:
            batch = self.list_activities(
                page=page, per_page=per_page, before=before, after=after
            )
            yield from batch
            if len(batch) < per_page:
                return
            page += 1

    def get_activity(
        self, activity_id: int, *, include_all_efforts: bool = False
    ) -> dict[str, Any]:
        """``GET /activities/{id}`` — DetailedActivity (adds calories,
        description, splits, ...)."""
        return self._request(
            "GET",
            f"activities/{activity_id}",
            params={"include_all_efforts": include_all_efforts},
        )

    def get_activity_streams(
        self,
        activity_id: int,
        *,
        keys: tuple[str, ...] = DEFAULT_STREAM_KEYS,
    ) -> dict[str, Any]:
        """``GET /activities/{id}/streams`` keyed by type.

        Returns ``{stream_type: {"data": [...], "series_type": ..., ...}}``.
        A 404 means the activity has no streams (manual entries) — callers
        should treat that as empty rather than fatal.
        """
        return self._request(
            "GET",
            f"activities/{activity_id}/streams",
            params={"keys": ",".join(keys), "key_by_type": "true"},
        )


def _epoch(value: datetime | int) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(value)


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        message = payload.get("message")
        if message:
            errors = payload.get("errors")
            if errors:
                return f"{message} ({errors!r})"
            return str(message)
    return fallback or "(no body)"
