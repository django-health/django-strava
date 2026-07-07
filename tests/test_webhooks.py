import pytest
import respx
from healthdatamodel.models import Workout
from httpx import Response

from strava import webhooks
from strava.constants import API_BASE_URL
from strava.models import ConnectionStatus

pytestmark = pytest.mark.django_db


def _event(**overrides):
    payload = {
        "object_type": "activity",
        "object_id": 987654321,
        "aspect_type": "create",
        "owner_id": 134815,
        "subscription_id": 120475,
        "event_time": 1516126040,
    }
    payload.update(overrides)
    return payload


class TestSubscriptionManagement:
    @respx.mock
    def test_create(self):
        route = respx.post(f"{API_BASE_URL}/push_subscriptions").mock(
            return_value=Response(201, json={"id": 120475})
        )
        result = webhooks.create_subscription(
            callback_url="https://example.com/strava/webhook/"
        )
        assert result == {"id": 120475}
        body = route.calls[0].request.content.decode()
        assert "verify_token=test-verify-token" in body
        assert "client_secret=test-client-secret" in body

    @respx.mock
    def test_list(self):
        respx.get(f"{API_BASE_URL}/push_subscriptions").mock(
            return_value=Response(200, json=[{"id": 120475}])
        )
        assert webhooks.list_subscriptions() == [{"id": 120475}]

    @respx.mock
    def test_delete(self):
        route = respx.delete(f"{API_BASE_URL}/push_subscriptions/120475").mock(
            return_value=Response(204)
        )
        webhooks.delete_subscription(120475)
        assert route.called


class TestProcessEvent:
    @respx.mock
    def test_activity_create_ingests(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/activities/987654321").mock(
            return_value=Response(200, json={**summary_activity, "calories": 500})
        )
        webhooks.process_event(_event())
        assert Workout.objects.filter(customer=connection.customer).count() == 1

    @respx.mock
    def test_activity_delete_removes(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/activities/987654321").mock(
            return_value=Response(200, json=summary_activity)
        )
        webhooks.process_event(_event())
        webhooks.process_event(_event(aspect_type="delete"))
        assert Workout.objects.count() == 0

    def test_unknown_owner_ignored(self, db):
        webhooks.process_event(_event(owner_id=999999))  # no raise

    def test_athlete_deauthorization(self, connection):
        webhooks.process_event(
            _event(
                object_type="athlete",
                object_id=134815,
                aspect_type="update",
                updates={"authorized": "false"},
            )
        )
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.DISCONNECTED

    def test_athlete_other_update_noop(self, connection):
        webhooks.process_event(
            _event(object_type="athlete", aspect_type="update", updates={})
        )
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ACTIVE
