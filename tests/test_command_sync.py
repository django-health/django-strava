from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
import respx
from django.core.management import CommandError, call_command
from healthdatamodel.models import Workout
from httpx import Response

from strava.constants import API_BASE_URL
from strava.models import ConnectionStatus

pytestmark = pytest.mark.django_db


def _call(*args):
    out, err = StringIO(), StringIO()
    call_command("sync_strava", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


@respx.mock
def test_syncs_active_connections(connection, summary_activity):
    summary_activity["start_date"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    respx.get(f"{API_BASE_URL}/athlete/activities").mock(
        return_value=Response(200, json=[summary_activity])
    )
    out, _ = _call()
    assert "activities=1" in out
    assert Workout.objects.count() == 1


def test_no_connections(db):
    out, _ = _call()
    assert "No matching active connections" in out


def test_skips_inactive(connection):
    connection.status = ConnectionStatus.REVOKED
    connection.save()
    out, _ = _call()
    assert "No matching active connections" in out


@respx.mock
def test_user_filter(connection, summary_activity):
    respx.get(f"{API_BASE_URL}/athlete/activities").mock(
        return_value=Response(200, json=[])
    )
    out, _ = _call("--user", "alice", "--days", "7")
    assert "Syncing 1 connection(s)" in out


def test_unknown_user(db):
    with pytest.raises(CommandError):
        _call("--user", "nobody")


def test_start_without_end(db):
    with pytest.raises(CommandError):
        _call("--start", "2026-05-01")


@respx.mock
def test_failure_exits_nonzero(connection):
    # 404 is non-retryable, so the failure surfaces without backoff sleeps.
    respx.get(f"{API_BASE_URL}/athlete/activities").mock(
        return_value=Response(404, json={"message": "boom"})
    )
    with pytest.raises(CommandError, match="1 connection"):
        _call("--hours", "1")
