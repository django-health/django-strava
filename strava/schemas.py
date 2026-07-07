"""Pydantic models for Strava OAuth request/response payloads."""

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict


class OAuthFlowState(BaseModel):
    """Per-request state stashed in the Django session between ``connect``
    and ``callback``. ``state`` defends against CSRF. Strava does not support
    PKCE (token exchange always requires ``client_secret``), so there is no
    code verifier here.
    """

    state: str
    scopes: list[str]


class StravaTokens(BaseModel):
    """Token-endpoint response from ``POST /api/v3/oauth/token``.

    Mirrors the JSON Strava returns for both ``authorization_code`` and
    ``refresh_token`` grants. ``athlete`` (a SummaryAthlete) only appears on
    the authorization-code grant. ``scope`` (space-delimited granted scopes)
    was added to the token response 2026-04-23 and is absent from older
    fixtures, so it defaults to empty.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    token_type: str = "Bearer"
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None  # unix epoch seconds
    expires_in: int | None = None
    scope: str = ""
    athlete: dict[str, Any] | None = None

    @property
    def scopes(self) -> list[str]:
        return self.scope.split() if self.scope else []

    def expires_at_datetime(self, *, now: datetime | None = None) -> datetime:
        """Absolute expiry as an aware datetime.

        Prefers the epoch ``expires_at`` (always present in real responses);
        falls back to ``now + expires_in``. Strava access tokens live 6 hours.
        """
        if self.expires_at is not None:
            return datetime.fromtimestamp(self.expires_at, tz=timezone.utc)
        anchor = now or datetime.now(timezone.utc)
        return anchor + timedelta(seconds=self.expires_in or 0)
