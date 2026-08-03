import json
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from django.urls import reverse
from httpx import Response

from strava.constants import OAUTH_TOKEN_URL
from strava.models import ConnectionStatus, StravaConnection
from strava.signals import event_received
from strava.views import SESSION_KEY

from .test_oauth import TOKEN_RESPONSE

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged_in(client, customer):
    client.force_login(customer)
    return client


class TestConnect:
    def test_requires_login(self, client):
        response = client.get(reverse("strava:connect"))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_redirects_to_strava(self, logged_in):
        response = logged_in.get(reverse("strava:connect"))
        assert response.status_code == 302
        parsed = urlparse(response["Location"])
        assert parsed.hostname == "www.strava.com"
        params = parse_qs(parsed.query)
        assert params["client_id"] == ["12345"]
        stash = logged_in.session[SESSION_KEY]
        assert params["state"] == [stash["state"]]


class TestCallback:
    def _start_flow(self, client):
        client.get(reverse("strava:connect"))
        return client.session[SESSION_KEY]["state"]

    @respx.mock
    def test_happy_path(self, logged_in, customer):
        state = self._start_flow(logged_in)
        respx.post(OAUTH_TOKEN_URL).mock(
            return_value=Response(200, json=TOKEN_RESPONSE)
        )
        response = logged_in.get(
            reverse("strava:callback"),
            {"code": "the-code", "state": state, "scope": "read,activity:read_all"},
        )
        assert response.status_code == 302
        assert response["Location"] == "/done/"
        connection = StravaConnection.objects.get(customer=customer)
        assert connection.athlete_id == 134815
        # Granted scopes come from the callback query param (comma-split).
        assert connection.scopes == ["read", "activity:read_all"]

    def test_error_param(self, logged_in):
        response = logged_in.get(reverse("strava:callback"), {"error": "access_denied"})
        assert response.status_code == 400

    def test_missing_code(self, logged_in):
        response = logged_in.get(reverse("strava:callback"))
        assert response.status_code == 400

    def test_no_flow_in_progress(self, logged_in):
        response = logged_in.get(
            reverse("strava:callback"), {"code": "c", "state": "s"}
        )
        assert response.status_code == 400

    def test_state_mismatch(self, logged_in):
        self._start_flow(logged_in)
        response = logged_in.get(
            reverse("strava:callback"), {"code": "c", "state": "wrong"}
        )
        assert response.status_code == 400


class TestDisconnect:
    @respx.mock
    def test_revokes(self, logged_in, connection):
        respx.post("https://www.strava.com/oauth/revoke").mock(
            return_value=Response(200, json={})
        )
        response = logged_in.post(reverse("strava:disconnect"))
        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.REVOKED

    def test_no_connection_redirects(self, logged_in):
        response = logged_in.post(reverse("strava:disconnect"))
        assert response.status_code == 302

    def test_get_not_allowed(self, logged_in, connection):
        response = logged_in.get(reverse("strava:disconnect"))
        assert response.status_code == 405


class TestWebhook:
    def test_handshake_echoes_challenge(self, client):
        response = client.get(
            reverse("strava:webhook"),
            {
                "hub.mode": "subscribe",
                "hub.challenge": "15f7d1a91c1f40f8a748fd134752feb3",
                "hub.verify_token": "test-verify-token",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"hub.challenge": "15f7d1a91c1f40f8a748fd134752feb3"}

    def test_handshake_rejects_bad_token(self, client):
        response = client.get(
            reverse("strava:webhook"),
            {"hub.challenge": "x", "hub.verify_token": "wrong"},
        )
        assert response.status_code == 403

    def test_handshake_rejects_when_unconfigured(self, client, settings):
        settings.STRAVA_WEBHOOK_VERIFY_TOKEN = ""
        response = client.get(
            reverse("strava:webhook"), {"hub.challenge": "x", "hub.verify_token": ""}
        )
        assert response.status_code == 403

    def test_post_emits_signal(self, client):
        received = []

        def handler(sender, payload, **kwargs):
            received.append(payload)

        # weak=False: the default weak reference would let the local handler
        # be garbage-collected before the request runs.
        event_received.connect(handler, weak=False)
        try:
            payload = {
                "object_type": "activity",
                "object_id": 987654321,
                "aspect_type": "create",
                "owner_id": 134815,
                "subscription_id": 120475,
                "event_time": 1516126040,
            }
            response = client.post(
                reverse("strava:webhook"),
                data=json.dumps(payload),
                content_type="application/json",
            )
        finally:
            event_received.disconnect(handler)
        assert response.status_code == 200
        assert received == [payload]

    def test_post_invalid_json(self, client):
        response = client.post(
            reverse("strava:webhook"), data="{nope", content_type="application/json"
        )
        assert response.status_code == 400
