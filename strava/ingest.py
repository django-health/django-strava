"""Map Strava V3 API payloads onto django-healthdatamodel records.

Strava is activity-centric, so the mapping is:

  SummaryActivity / DetailedActivity → :class:`WorkoutInput`
  activity streams (heartrate, cadence, watts) → list[:class:`RecordInput`]

Caveats — Strava's data model isn't 1:1 with Apple HealthKit (which is what
``healthdatamodel`` schemas mirror):

* ``sport_type`` strings (Run, Ride, VirtualRide, Padel, ...) are stored
  verbatim in ``workoutActivityType`` — Strava adds new sport types without
  notice (six added 2026-04-30 alone), so no enum mapping is attempted.
* ``calories`` only exists on DetailedActivity; SummaryActivity carries
  ``kilojoules`` (rides, from power meters). kJ is mechanical work, not
  metabolic energy, so it is kept as metadata rather than passed off as kcal.
* GPS ``latlng`` streams have no HealthKit analog and are not ingested; the
  summary polyline is kept as workout metadata instead.

The high-level orchestrator is :func:`sync_user`; :func:`sync_activity`
ingests a single activity (the webhook path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from healthdatamodel.constants import DataSource
from healthdatamodel.ingest import ingest_records, ingest_workouts
from healthdatamodel.schemas import MetadataEntry, RecordInput, WorkoutInput

from .client import StravaAPIError, StravaClient
from .constants import (
    SOURCE_NAME,
    STREAM_KEY_CADENCE,
    STREAM_KEY_HEARTRATE,
    STREAM_KEY_TIME,
    STREAM_KEY_WATTS,
)

if TYPE_CHECKING:
    from .models import StravaConnection

# Apple HealthKit identifiers not exported as enum members in healthdatamodel.
HK_HEART_RATE = "HKQuantityTypeIdentifierHeartRate"
HK_CYCLING_POWER = "HKQuantityTypeIdentifierCyclingPower"
HK_CYCLING_CADENCE = "HKQuantityTypeIdentifierCyclingCadence"

# Workout metadata key that carries the Strava activity id. Webhook delete
# events only give us that id, so this is the join key back to the Workout.
ACTIVITY_ID_METADATA_KEY = "strava_activity_id"

# Summary fields worth keeping on the workout, verbatim, when present.
_METADATA_FIELDS = (
    "sport_type",
    "average_heartrate",
    "max_heartrate",
    "average_cadence",
    "average_watts",
    "max_watts",
    "weighted_average_watts",
    "kilojoules",
    "average_speed",
    "max_speed",
    "total_elevation_gain",
    "elev_high",
    "elev_low",
    "gear_id",
    "device_name",
    "trainer",
    "commute",
    "timezone",
)

_STREAM_RECORD_TYPES = {
    STREAM_KEY_HEARTRATE: (HK_HEART_RATE, "count/min"),
    STREAM_KEY_CADENCE: (HK_CYCLING_CADENCE, "count/min"),
    STREAM_KEY_WATTS: (HK_CYCLING_POWER, "W"),
}


@dataclass
class SyncResult:
    """Per-category counts returned by :func:`sync_user`."""

    counts: dict[str, int] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _parse_dt(value: str | None) -> datetime:
    if not value:
        raise ValueError("missing datetime value")
    # Strava returns ISO-8601 with Z suffix, e.g. "2026-06-14T13:10:00Z".
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def map_activity(activity: dict[str, Any]) -> WorkoutInput:
    """Map a SummaryActivity (or DetailedActivity) dict to a WorkoutInput.

    ``duration`` is ``moving_time`` (elapsed_time only as a fallback) — moving
    time is what Strava itself reports as the workout duration. ``endDate``
    is anchored on elapsed time so the interval matches wall-clock reality.
    """
    start = _parse_dt(activity["start_date"])
    elapsed = int(activity.get("elapsed_time") or 0)
    duration = activity.get("moving_time")
    if duration is None:
        duration = elapsed
    end = start + timedelta(seconds=elapsed or int(duration))

    distance_m = activity.get("distance")
    distance_km = float(distance_m) / 1000.0 if distance_m else None

    calories = activity.get("calories")  # DetailedActivity only

    metadata = [MetadataEntry(key=ACTIVITY_ID_METADATA_KEY, value=str(activity["id"]))]
    if activity.get("name"):
        metadata.append(MetadataEntry(key="name", value=str(activity["name"])))
    for fieldname in _METADATA_FIELDS:
        value = activity.get(fieldname)
        if value is not None and value != "":
            metadata.append(MetadataEntry(key=fieldname, value=str(value)))
    polyline = (activity.get("map") or {}).get("summary_polyline")
    if polyline:
        metadata.append(MetadataEntry(key="summary_polyline", value=str(polyline)))

    return WorkoutInput(
        recordId=str(activity["id"]),
        startDate=start,
        endDate=end,
        creationDate=start,
        sourceName=SOURCE_NAME,
        durationUnit="s",
        duration=float(duration),
        workoutActivityType=str(
            activity.get("sport_type") or activity.get("type") or "Workout"
        ),
        caloriesBurned=float(calories) if calories is not None else None,
        caloriesUnit="kcal" if calories is not None else None,
        distance=distance_km,
        distanceUnit="km" if distance_km is not None else None,
        metadataEntry=metadata,
    )


def map_streams(activity: dict[str, Any], streams: dict[str, Any]) -> list[RecordInput]:
    """Map keyed activity streams to per-sample Records.

    Each sample in a mapped stream (heartrate/cadence/watts) becomes one
    point-in-time Record at ``activity.start_date + time[i]``. The ``time``
    stream anchors the offsets; without it nothing can be placed and the
    result is empty.
    """
    time_stream = (streams.get(STREAM_KEY_TIME) or {}).get("data")
    if not time_stream:
        return []
    start = _parse_dt(activity["start_date"])
    activity_id = activity["id"]

    records: list[RecordInput] = []
    for key, (hk_type, unit) in _STREAM_RECORD_TYPES.items():
        data = (streams.get(key) or {}).get("data")
        if not data:
            continue
        for offset, value in zip(time_stream, data):
            if value is None:
                continue
            at = start + timedelta(seconds=offset)
            records.append(
                RecordInput(
                    recordId=f"{activity_id}-{key}-{offset}",
                    startDate=at,
                    endDate=at,
                    creationDate=start,
                    sourceName=SOURCE_NAME,
                    type=hk_type,
                    value=str(value),
                    unit=unit,
                )
            )
    return records


# Orchestrator ----------------------------------------------------------------


def sync_user(
    connection: StravaConnection,
    *,
    start: datetime,
    end: datetime,
    with_streams: bool = False,
    stream_activity_limit: int | None = None,
    client: StravaClient | None = None,
) -> SyncResult:
    """Fetch + ingest activities for ``connection`` over [start, end].

    Pass a pre-built ``client`` to override the default (useful in tests).

    ``with_streams=True`` additionally fetches per-activity streams
    (heartrate, cadence, watts) and ingests them as Records. That costs one
    extra API read per activity — on the Standard tier's default read budget
    (100/15 min before the 10-athlete upgrade, 200 after) a big backfill will
    hit 429s, so ``stream_activity_limit`` caps how many activities get
    stream fetches per sync (newest first; ``None`` means no cap).
    """
    result = SyncResult()
    owns_client = client is None
    if client is None:
        client = StravaClient(connection)

    try:
        activities = list(client.iter_activities(after=start, before=end))
        workouts = [map_activity(a) for a in activities]
        # healthdatamodel has no unique constraints (ignore_conflicts never
        # fires), so a re-sync over an overlapping window would duplicate
        # rows. Deleting this source's workouts in the window first — after
        # the fetch succeeded — makes sync idempotent.
        _delete_workouts_in_window(connection, start=start, end=end)
        ingest_workouts(connection.customer, workouts, source=DataSource.STRAVA)
        result.counts["activities"] = len(workouts)

        if with_streams:
            stream_records = 0
            eligible = activities[:stream_activity_limit]
            for activity in eligible:
                try:
                    streams = client.get_activity_streams(activity["id"])
                except StravaAPIError as exc:
                    if exc.status_code == 404:
                        # Manual entries / privacy-restricted activities have
                        # no streams; skip rather than fail the sync.
                        continue
                    raise
                records = map_streams(activity, streams)
                _delete_stream_records_for_activity(connection, activity)
                ingest_records(connection.customer, records, source=DataSource.STRAVA)
                stream_records += len(records)
            result.counts["stream_records"] = stream_records
    finally:
        if owns_client:
            client.close()

    result.finished_at = datetime.now(timezone.utc)
    connection.last_sync_at = result.finished_at
    connection.save(update_fields=["last_sync_at"])
    return result


def _delete_workouts_in_window(
    connection: StravaConnection, *, start: datetime, end: datetime
) -> int:
    from healthdatamodel.models import Workout

    deleted, _ = Workout.objects.filter(
        customer=connection.customer,
        source=DataSource.STRAVA,
        startDate__gte=start,
        startDate__lt=end,
    ).delete()
    return deleted


def _delete_stream_records_for_activity(
    connection: StravaConnection, activity: dict[str, Any]
) -> int:
    from healthdatamodel.models import Record

    start = _parse_dt(activity["start_date"])
    end = start + timedelta(seconds=int(activity.get("elapsed_time") or 0))
    deleted, _ = Record.objects.filter(
        customer=connection.customer,
        source=DataSource.STRAVA,
        type__in=[hk for hk, _ in _STREAM_RECORD_TYPES.values()],
        startDate__gte=start,
        startDate__lte=end,
    ).delete()
    return deleted


def sync_activity(
    connection: StravaConnection,
    activity_id: int,
    *,
    client: StravaClient | None = None,
) -> WorkoutInput:
    """Fetch one activity (DetailedActivity, so ``calories`` is populated)
    and ingest it. This is the webhook create/update path. Idempotent: any
    previously ingested workout for this activity id is replaced."""
    owns_client = client is None
    if client is None:
        client = StravaClient(connection)
    try:
        detail = client.get_activity(activity_id)
    finally:
        if owns_client:
            client.close()
    workout = map_activity(detail)
    delete_activity(connection, activity_id)
    ingest_workouts(connection.customer, [workout], source=DataSource.STRAVA)
    return workout


def delete_activity(connection: StravaConnection, activity_id: int) -> int:
    """Delete the ingested Workout(s) for a Strava activity id.

    Webhook delete events only carry the activity id; the join back to
    healthdatamodel is the ``strava_activity_id`` metadata entry written by
    :func:`map_activity`. Returns the number of workouts deleted.
    """
    from healthdatamodel.models import Workout

    deleted, _ = Workout.objects.filter(
        customer=connection.customer,
        source=DataSource.STRAVA,
        workoutmetadataentry__key=ACTIVITY_ID_METADATA_KEY,
        workoutmetadataentry__value=str(activity_id),
    ).delete()
    return deleted
