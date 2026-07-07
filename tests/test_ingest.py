from datetime import datetime, timezone

import pytest
import respx
from healthdatamodel.constants import DataSource
from healthdatamodel.models import Record, Workout, WorkoutMetadataEntry
from httpx import Response

from strava.constants import API_BASE_URL
from strava.ingest import (
    ACTIVITY_ID_METADATA_KEY,
    HK_HEART_RATE,
    delete_activity,
    map_activity,
    map_streams,
    sync_activity,
    sync_user,
)

pytestmark = pytest.mark.django_db


def _metadata(workout: Workout) -> dict[str, str]:
    return {m.key: m.value for m in workout.workoutmetadataentry_set.all()}


class TestMapActivity:
    def test_summary_fields(self, summary_activity):
        workout = map_activity(summary_activity)
        assert workout.recordId == "987654321"
        assert workout.startDate == datetime(2026, 6, 14, 13, 10, tzinfo=timezone.utc)
        # endDate anchored on elapsed_time (3720s), duration on moving_time.
        assert (workout.endDate - workout.startDate).total_seconds() == 3720
        assert workout.duration == 3600.0
        assert workout.durationUnit == "s"
        assert workout.workoutActivityType == "Run"
        assert workout.distance == pytest.approx(10.0123)
        assert workout.distanceUnit == "km"
        assert workout.sourceName == "Strava"

    def test_no_calories_on_summary(self, summary_activity):
        workout = map_activity(summary_activity)
        assert workout.caloriesBurned is None
        assert workout.caloriesUnit is None

    def test_calories_on_detail(self, summary_activity):
        workout = map_activity({**summary_activity, "calories": 612.5})
        assert workout.caloriesBurned == 612.5
        assert workout.caloriesUnit == "kcal"

    def test_metadata(self, summary_activity):
        workout = map_activity(summary_activity)
        metadata = {m.key: m.value for m in workout.metadataEntry}
        assert metadata[ACTIVITY_ID_METADATA_KEY] == "987654321"
        assert metadata["name"] == "Morning Run"
        assert metadata["average_heartrate"] == "152.4"
        assert metadata["device_name"] == "Garmin Forerunner 265"
        assert metadata["summary_polyline"] == "abc~F`enc..."
        # None-valued fields are dropped.
        assert "kilojoules" not in metadata

    def test_moving_time_fallback_to_elapsed(self, summary_activity):
        workout = map_activity({**summary_activity, "moving_time": None})
        assert workout.duration == 3720.0

    def test_sport_type_fallback_to_type(self, summary_activity):
        activity = {**summary_activity, "sport_type": None, "type": "Ride"}
        assert map_activity(activity).workoutActivityType == "Ride"


class TestMapStreams:
    def test_heart_rate_stream(self, summary_activity):
        streams = {
            "time": {"data": [0, 5, 10]},
            "heartrate": {"data": [140, 150, None]},
        }
        records = map_streams(summary_activity, streams)
        assert len(records) == 2  # None sample dropped
        first = records[0]
        assert first.type == HK_HEART_RATE
        assert first.value == "140"
        assert first.unit == "count/min"
        assert first.startDate == datetime(2026, 6, 14, 13, 10, tzinfo=timezone.utc)
        assert records[1].startDate == datetime(
            2026, 6, 14, 13, 10, 5, tzinfo=timezone.utc
        )

    def test_no_time_stream_yields_nothing(self, summary_activity):
        assert map_streams(summary_activity, {"heartrate": {"data": [1]}}) == []

    def test_multiple_stream_types(self, summary_activity):
        streams = {
            "time": {"data": [0, 1]},
            "heartrate": {"data": [140, 141]},
            "watts": {"data": [250, 260]},
            "cadence": {"data": [88, 90]},
        }
        records = map_streams(summary_activity, streams)
        assert len(records) == 6
        assert {r.unit for r in records} == {"count/min", "W"}


class TestSyncUser:
    @respx.mock
    def test_ingests_workouts(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/athlete/activities").mock(
            return_value=Response(200, json=[summary_activity])
        )
        result = sync_user(
            connection,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert result.counts == {"activities": 1}
        workout = Workout.objects.get()
        assert workout.customer == connection.customer
        assert workout.source == DataSource.STRAVA
        assert workout.workoutActivityType == "Run"
        assert _metadata(workout)[ACTIVITY_ID_METADATA_KEY] == "987654321"
        connection.refresh_from_db()
        assert connection.last_sync_at is not None

    @respx.mock
    def test_resync_is_idempotent(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/athlete/activities").mock(
            return_value=Response(200, json=[summary_activity])
        )
        window = dict(
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        sync_user(connection, **window)
        sync_user(connection, **window)
        assert Workout.objects.count() == 1
        assert (
            WorkoutMetadataEntry.objects.filter(key=ACTIVITY_ID_METADATA_KEY).count()
            == 1
        )

    @respx.mock
    def test_with_streams(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/athlete/activities").mock(
            return_value=Response(200, json=[summary_activity])
        )
        respx.get(f"{API_BASE_URL}/activities/987654321/streams").mock(
            return_value=Response(
                200,
                json={
                    "time": {"data": [0, 5]},
                    "heartrate": {"data": [140, 150]},
                },
            )
        )
        result = sync_user(
            connection,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
            with_streams=True,
        )
        assert result.counts == {"activities": 1, "stream_records": 2}
        assert Record.objects.filter(type=HK_HEART_RATE).count() == 2

    @respx.mock
    def test_streams_404_skipped(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/athlete/activities").mock(
            return_value=Response(200, json=[summary_activity])
        )
        respx.get(f"{API_BASE_URL}/activities/987654321/streams").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        result = sync_user(
            connection,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
            with_streams=True,
        )
        assert result.counts == {"activities": 1, "stream_records": 0}

    @respx.mock
    def test_stream_activity_limit(self, connection, summary_activity):
        second = {**summary_activity, "id": 111, "start_date": "2026-06-15T08:00:00Z"}
        respx.get(f"{API_BASE_URL}/athlete/activities").mock(
            return_value=Response(200, json=[summary_activity, second])
        )
        stream_route = respx.get(f"{API_BASE_URL}/activities/987654321/streams").mock(
            return_value=Response(
                200, json={"time": {"data": [0]}, "heartrate": {"data": [140]}}
            )
        )
        sync_user(
            connection,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
            with_streams=True,
            stream_activity_limit=1,
        )
        assert stream_route.call_count == 1


class TestSyncActivity:
    @respx.mock
    def test_fetches_detail_and_replaces(self, connection, summary_activity):
        detail = {**summary_activity, "calories": 612.5}
        respx.get(f"{API_BASE_URL}/activities/987654321").mock(
            return_value=Response(200, json=detail)
        )
        sync_activity(connection, 987654321)
        sync_activity(connection, 987654321)  # webhook update — replaces, no dup
        workout = Workout.objects.get()
        assert _metadata(workout)["caloriesBurned"] == "612.5"


class TestDeleteActivity:
    @respx.mock
    def test_deletes_by_metadata_join(self, connection, summary_activity):
        respx.get(f"{API_BASE_URL}/athlete/activities").mock(
            return_value=Response(200, json=[summary_activity])
        )
        sync_user(
            connection,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert delete_activity(connection, 987654321) > 0
        assert Workout.objects.count() == 0

    def test_unknown_activity_is_noop(self, connection):
        assert delete_activity(connection, 42) == 0
