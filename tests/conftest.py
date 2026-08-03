from datetime import datetime, timedelta, timezone

import pytest
from django.contrib.auth import get_user_model

from strava.models import StravaConnection


@pytest.fixture
def customer(db):
    return get_user_model().objects.create_user(username="alice", password="pw")


@pytest.fixture
def connection(customer):
    return StravaConnection.objects.create(
        customer=customer,
        athlete_id=134815,
        athlete_firstname="Alice",
        athlete_lastname="Athlete",
        access_token="access-token",
        refresh_token="refresh-token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        scopes=["read", "activity:read_all", "profile:read_all"],
    )


@pytest.fixture
def summary_activity():
    """A representative SummaryActivity from GET /athlete/activities."""
    return {
        "id": 987654321,
        "name": "Morning Run",
        "type": "Run",
        "sport_type": "Run",
        "start_date": "2026-06-14T13:10:00Z",
        "start_date_local": "2026-06-14T09:10:00Z",
        "timezone": "(GMT-05:00) America/New_York",
        "elapsed_time": 3720,
        "moving_time": 3600,
        "distance": 10012.3,
        "total_elevation_gain": 120.5,
        "average_speed": 2.78,
        "max_speed": 4.2,
        "average_heartrate": 152.4,
        "max_heartrate": 176.0,
        "has_heartrate": True,
        "kilojoules": None,
        "gear_id": "g12345",
        "device_name": "Garmin Forerunner 265",
        "trainer": False,
        "commute": False,
        "map": {"id": "a987654321", "summary_polyline": "abc~F`enc..."},
    }
