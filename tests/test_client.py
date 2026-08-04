import pytest
import respx
from httpx import Response

from strava.client import StravaAPIError, StravaClient
from strava.constants import API_BASE_URL, OAUTH_TOKEN_URL

pytestmark = pytest.mark.django_db

REFRESH_RESPONSE = {
    "token_type": "Bearer",
    "access_token": "refreshed",
    "refresh_token": "rotated",
    "expires_at": 1781360000,
    "expires_in": 21600,
}


@respx.mock
def test_bearer_header(connection):
    route = respx.get(f"{API_BASE_URL}/athlete").mock(
        return_value=Response(200, json={"id": 134815})
    )
    with StravaClient(connection) as client:
        athlete = client.get_athlete()
    assert athlete == {"id": 134815}
    assert route.calls[0].request.headers["Authorization"] == "Bearer access-token"


@respx.mock
def test_proactive_refresh_when_expired(connection):
    connection.token_expires_at = connection.token_expires_at.replace(year=2020)
    connection.save()
    respx.post(OAUTH_TOKEN_URL).mock(return_value=Response(200, json=REFRESH_RESPONSE))
    route = respx.get(f"{API_BASE_URL}/athlete").mock(
        return_value=Response(200, json={})
    )
    with StravaClient(connection) as client:
        client.get_athlete()
    assert route.calls[0].request.headers["Authorization"] == "Bearer refreshed"


@respx.mock
def test_retry_once_on_401(connection):
    respx.post(OAUTH_TOKEN_URL).mock(return_value=Response(200, json=REFRESH_RESPONSE))
    route = respx.get(f"{API_BASE_URL}/athlete").mock(
        side_effect=[
            Response(401, json={"message": "Unauthorized"}),
            Response(200, json={}),
        ]
    )
    with StravaClient(connection) as client:
        client.get_athlete()
    assert route.call_count == 2
    assert route.calls[1].request.headers["Authorization"] == "Bearer refreshed"


@respx.mock
def test_second_401_raises(connection):
    respx.post(OAUTH_TOKEN_URL).mock(return_value=Response(200, json=REFRESH_RESPONSE))
    respx.get(f"{API_BASE_URL}/athlete").mock(
        return_value=Response(401, json={"message": "Unauthorized"})
    )
    with (
        StravaClient(connection) as client,
        pytest.raises(StravaAPIError) as excinfo,
    ):
        client.get_athlete()
    assert excinfo.value.status_code == 401


@respx.mock
def test_retry_on_429_honors_retry_after(connection):
    sleeps: list[float] = []
    route = respx.get(f"{API_BASE_URL}/athlete").mock(
        side_effect=[
            Response(
                429, headers={"Retry-After": "42"}, json={"message": "Rate Limit"}
            ),
            Response(200, json={}),
        ]
    )
    with StravaClient(connection, sleep=sleeps.append) as client:
        client.get_athlete()
    assert route.call_count == 2
    assert sleeps == [42.0]


@respx.mock
def test_retries_exhausted_raises(connection):
    respx.get(f"{API_BASE_URL}/athlete").mock(
        return_value=Response(503, json={"message": "unavailable"})
    )
    with (
        StravaClient(connection, max_retries=2, sleep=lambda s: None) as client,
        pytest.raises(StravaAPIError) as excinfo,
    ):
        client.get_athlete()
    assert excinfo.value.status_code == 503


@respx.mock
def test_rate_limit_headers_captured(connection):
    respx.get(f"{API_BASE_URL}/athlete").mock(
        return_value=Response(
            200,
            json={},
            headers={"X-RateLimit-Limit": "200,2000", "X-RateLimit-Usage": "3,17"},
        )
    )
    with StravaClient(connection) as client:
        client.get_athlete()
        assert client.last_rate_limit["X-RateLimit-Usage"] == "3,17"


@respx.mock
def test_list_activities_epoch_params(connection):
    from datetime import datetime, timezone

    route = respx.get(f"{API_BASE_URL}/athlete/activities").mock(
        return_value=Response(200, json=[])
    )
    with StravaClient(connection) as client:
        client.list_activities(
            after=datetime(2026, 6, 1, tzinfo=timezone.utc), before=1782000000
        )
    params = dict(route.calls[0].request.url.params)
    assert params["after"] == str(
        int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    )
    assert params["before"] == "1782000000"
    assert params["per_page"] == "200"


@respx.mock
def test_iter_activities_paginates_until_short_page(connection):
    pages = {
        "1": [{"id": i} for i in range(3)],
        "2": [{"id": 3}],
    }

    def responder(request):
        page = request.url.params["page"]
        return Response(200, json=pages.get(page, []))

    route = respx.get(f"{API_BASE_URL}/athlete/activities").mock(side_effect=responder)
    with StravaClient(connection) as client:
        activities = list(client.iter_activities(per_page=3))
    assert [a["id"] for a in activities] == [0, 1, 2, 3]
    assert route.call_count == 2  # short second page stops iteration


@respx.mock
def test_get_activity_streams_params(connection):
    route = respx.get(f"{API_BASE_URL}/activities/42/streams").mock(
        return_value=Response(200, json={"time": {"data": [0, 1]}})
    )
    with StravaClient(connection) as client:
        streams = client.get_activity_streams(42)
    assert "time" in streams
    params = dict(route.calls[0].request.url.params)
    assert params["key_by_type"] == "true"
    assert "heartrate" in params["keys"]


@respx.mock
def test_base_url_override_via_settings(connection, settings):
    # The 2027 host cutover: settings.STRAVA_API_BASE_URL wins over the default.
    settings.STRAVA_API_BASE_URL = "https://api-v3.strava.com"
    route = respx.get("https://api-v3.strava.com/athlete").mock(
        return_value=Response(200, json={})
    )
    with StravaClient(connection) as client:
        client.get_athlete()
    assert route.called
