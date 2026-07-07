from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import Response

from strava import oauth
from strava.constants import OAUTH_REVOKE_URL, OAUTH_TOKEN_URL
from strava.models import ConnectionStatus, StravaConnection

pytestmark = pytest.mark.django_db

TOKEN_RESPONSE = {
    "token_type": "Bearer",
    "access_token": "new-access",
    "refresh_token": "new-refresh",
    "expires_at": 1781350000,
    "expires_in": 21600,
    "scope": "read activity:read_all",
    "athlete": {
        "id": 134815,
        "firstname": "Alice",
        "lastname": "Athlete",
        "profile": "https://example.com/avatar.jpg",
    },
}


def test_build_authorization_url():
    url, flow_state = oauth.build_authorization_url(
        scopes=["read", "activity:read_all"]
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.hostname == "www.strava.com"
    assert parsed.path == "/oauth/authorize"
    assert params["client_id"] == ["12345"]
    assert params["response_type"] == ["code"]
    # Strava's authorize endpoint takes comma-delimited scopes.
    assert params["scope"] == ["read,activity:read_all"]
    assert params["state"] == [flow_state.state]
    assert flow_state.scopes == ["read", "activity:read_all"]


def test_exchange_code_state_mismatch():
    with pytest.raises(oauth.StateMismatchError):
        oauth.exchange_code(code="c", expected_state="a", received_state="b")


@respx.mock
def test_exchange_code():
    route = respx.post(OAUTH_TOKEN_URL).mock(
        return_value=Response(200, json=TOKEN_RESPONSE)
    )
    tokens = oauth.exchange_code(
        code="the-code", expected_state="s", received_state="s"
    )
    assert tokens.access_token == "new-access"
    assert tokens.athlete["id"] == 134815
    assert tokens.scopes == ["read", "activity:read_all"]
    sent = dict(
        pair.split("=") for pair in route.calls[0].request.content.decode().split("&")
    )
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["client_secret"] == "test-client-secret"


@respx.mock
def test_exchange_code_error():
    respx.post(OAUTH_TOKEN_URL).mock(
        return_value=Response(400, json={"message": "Bad Request", "errors": []})
    )
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(code="bad")


def test_ingest_tokens_creates_connection(customer):
    connection = oauth.ingest_tokens(customer=customer, tokens=TOKEN_RESPONSE)
    assert connection.athlete_id == 134815
    assert connection.athlete_firstname == "Alice"
    assert connection.access_token == "new-access"
    assert connection.refresh_token == "new-refresh"
    assert connection.token_expires_at == datetime.fromtimestamp(
        1781350000, tz=timezone.utc
    )
    assert connection.scopes == ["read", "activity:read_all"]
    assert connection.status == ConnectionStatus.ACTIVE


def test_ingest_tokens_granted_scopes_override(customer):
    connection = oauth.ingest_tokens(
        customer=customer,
        tokens=TOKEN_RESPONSE,
        granted_scopes=["read", "activity:read"],
    )
    assert connection.scopes == ["read", "activity:read"]


def test_ingest_tokens_updates_existing(connection):
    updated = oauth.ingest_tokens(
        customer=connection.customer,
        tokens={**TOKEN_RESPONSE, "access_token": "rotated"},
    )
    assert updated.pk == connection.pk
    assert updated.access_token == "rotated"
    assert StravaConnection.objects.count() == 1


def test_ingest_tokens_requires_athlete(customer):
    with pytest.raises(oauth.OAuthError):
        oauth.ingest_tokens(
            customer=customer, tokens={**TOKEN_RESPONSE, "athlete": None}
        )


@respx.mock
def test_refresh_access_token_rotates_refresh_token(connection):
    respx.post(OAUTH_TOKEN_URL).mock(
        return_value=Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "refreshed-access",
                "refresh_token": "rotated-refresh",
                "expires_at": 1781360000,
                "expires_in": 21600,
            },
        )
    )
    oauth.refresh_access_token(connection)
    connection.refresh_from_db()
    assert connection.access_token == "refreshed-access"
    assert connection.refresh_token == "rotated-refresh"
    assert connection.token_expires_at == datetime.fromtimestamp(
        1781360000, tz=timezone.utc
    )


@respx.mock
def test_refresh_access_token_error(connection):
    respx.post(OAUTH_TOKEN_URL).mock(
        return_value=Response(401, json={"message": "invalid_grant"})
    )
    with pytest.raises(oauth.OAuthError):
        oauth.refresh_access_token(connection)


@respx.mock
def test_revoke_marks_revoked(connection):
    route = respx.post(OAUTH_REVOKE_URL).mock(return_value=Response(200, json={}))
    oauth.revoke(connection)
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.REVOKED
    # Basic auth with client_id:client_secret per the 2026 revoke endpoint.
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")


@respx.mock
def test_revoke_best_effort_on_error(connection):
    respx.post(OAUTH_REVOKE_URL).mock(return_value=Response(500))
    oauth.revoke(connection)
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.REVOKED
