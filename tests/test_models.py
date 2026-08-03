from datetime import datetime, timedelta, timezone

import pytest
from django.db import IntegrityError

from strava.models import ConnectionStatus, StravaConnection

pytestmark = pytest.mark.django_db


def test_str(connection):
    assert "134815" in str(connection)
    assert "active" in str(connection)


def test_one_connection_per_customer(connection, customer):
    with pytest.raises(IntegrityError):
        StravaConnection.objects.create(
            customer=customer,
            athlete_id=999,
            access_token="x",
            refresh_token="y",
            token_expires_at=datetime.now(timezone.utc),
        )


def test_cascade_delete(connection, customer):
    customer.delete()
    assert StravaConnection.objects.count() == 0


def test_defaults(connection):
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.last_sync_at is None
    assert connection.connected_at is not None


def test_is_token_expired():
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    connection = StravaConnection(
        token_expires_at=now + timedelta(seconds=120),
    )
    assert not connection.is_token_expired(now=now)
    assert connection.is_token_expired(now=now, leeway_seconds=180)
    assert connection.is_token_expired(now=now + timedelta(seconds=61))
