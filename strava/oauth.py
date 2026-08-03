"""Strava OAuth 2.0 helpers.

Plain authorization-code flow over httpx — Strava does not support PKCE, and
the token endpoint always requires ``client_secret`` (confidential clients
only). The public API is:

* :func:`build_authorization_url` — produce the consent URL for the web-callback flow.
* :func:`exchange_code` — server-side code → token exchange.
* :func:`ingest_tokens` — persist tokens onto a :class:`StravaConnection`.
* :func:`refresh_access_token` — refresh a stored connection's access token.
* :func:`revoke` — revoke at Strava (``POST /oauth/revoke``) and mark the
  connection revoked. The legacy ``/oauth/deauthorize`` is retired 2027-06-01
  and is not used here.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
from django.conf import settings

from .constants import OAUTH_AUTHORIZATION_URL, OAUTH_REVOKE_URL, OAUTH_TOKEN_URL
from .models import ConnectionStatus, StravaConnection
from .schemas import OAuthFlowState, StravaTokens

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

log = logging.getLogger(__name__)


class OAuthError(Exception):
    """Base for OAuth-related errors raised by this module."""


class StateMismatchError(OAuthError):
    """Raised when the ``state`` returned from Strava doesn't match what we stashed."""


def build_authorization_url(
    *,
    scopes: list[str],
    state: str | None = None,
    approval_prompt: str = "auto",
) -> tuple[str, OAuthFlowState]:
    """Build the consent URL and the state to round-trip via the session.

    Strava's ``scope`` query param is comma-delimited (unlike the
    space-delimited token response). ``approval_prompt="force"`` re-shows the
    consent screen even when already authorized.
    """
    state = state or secrets.token_urlsafe(32)
    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "redirect_uri": settings.STRAVA_REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": approval_prompt,
        "scope": ",".join(scopes),
        "state": state,
    }
    auth_url = f"{OAUTH_AUTHORIZATION_URL}?{urlencode(params)}"
    return auth_url, OAuthFlowState(state=state, scopes=scopes)


def exchange_code(
    *,
    code: str,
    expected_state: str | None = None,
    received_state: str | None = None,
) -> StravaTokens:
    """Exchange an authorization code for tokens.

    Pass ``expected_state`` and ``received_state`` to enforce CSRF protection
    at this layer; pass neither to skip (e.g. when the upstream view already
    validated).
    """
    if expected_state is not None and received_state != expected_state:
        raise StateMismatchError("OAuth state mismatch")
    response = httpx.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise OAuthError(
            f"token exchange returned HTTP {response.status_code}: {response.text}"
        )
    return StravaTokens.model_validate(response.json())


def ingest_tokens(
    *,
    customer: AbstractBaseUser,
    tokens: StravaTokens | dict[str, Any],
    granted_scopes: list[str] | None = None,
) -> StravaConnection:
    """Persist tokens onto a :class:`StravaConnection` (create or update).

    ``granted_scopes`` should come from the callback's ``scope`` query param
    when available — Strava lets athletes grant a subset of requested scopes,
    and older token responses omit the granted set. Precedence:
    explicit argument > token-response ``scope`` field > empty.
    """
    parsed = (
        tokens
        if isinstance(tokens, StravaTokens)
        else StravaTokens.model_validate(tokens)
    )
    athlete = parsed.athlete or {}
    athlete_id = athlete.get("id")
    if athlete_id is None:
        raise OAuthError(
            "token response carried no athlete — ingest_tokens requires the "
            "authorization_code grant response"
        )

    connection, _ = StravaConnection.objects.update_or_create(
        customer=customer,
        defaults={
            "athlete_id": athlete_id,
            "athlete_firstname": athlete.get("firstname") or "",
            "athlete_lastname": athlete.get("lastname") or "",
            "athlete_profile": athlete.get("profile") or "",
            "access_token": parsed.access_token,
            "refresh_token": parsed.refresh_token or "",
            "token_expires_at": parsed.expires_at_datetime(),
            "scopes": granted_scopes if granted_scopes is not None else parsed.scopes,
            "status": ConnectionStatus.ACTIVE,
        },
    )
    return connection


def refresh_access_token(connection: StravaConnection) -> StravaConnection:
    """Refresh the connection's access token in place using its stored refresh token.

    Strava rotates the refresh token on every grant — always persist the one
    that comes back. If the current access token has more than an hour left,
    Strava returns it unchanged (that's fine; the expiry comes back too).
    """
    response = httpx.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": connection.refresh_token,
        },
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise OAuthError(
            f"token refresh returned HTTP {response.status_code}: {response.text}"
        )
    payload = response.json()
    if "access_token" not in payload:
        # Some intermediaries (corporate proxies) return 200 with an error body.
        raise OAuthError(f"token refresh returned no access token: {payload!r}")
    tokens = StravaTokens.model_validate(payload)
    connection.access_token = tokens.access_token
    connection.token_expires_at = tokens.expires_at_datetime()
    update_fields = ["access_token", "token_expires_at"]
    if tokens.refresh_token:
        connection.refresh_token = tokens.refresh_token
        update_fields.append("refresh_token")
    connection.save(update_fields=update_fields)
    return connection


def revoke(connection: StravaConnection) -> None:
    """Revoke the connection at Strava and mark it ``REVOKED`` locally.

    Uses the 2026 ``POST /oauth/revoke`` endpoint: HTTP Basic auth with
    ``client_id:client_secret`` and the token in the body. Best-effort: a
    non-2xx from Strava still flips the local status — the user-facing intent
    (disconnect) shouldn't be blocked by a transient Strava error.
    """
    token = connection.access_token or connection.refresh_token
    if token:
        try:
            httpx.post(
                OAUTH_REVOKE_URL,
                data={"token": token},
                auth=(str(settings.STRAVA_CLIENT_ID), settings.STRAVA_CLIENT_SECRET),
                timeout=10.0,
            )
        except httpx.HTTPError:
            pass
    connection.status = ConnectionStatus.REVOKED
    connection.save(update_fields=["status"])
