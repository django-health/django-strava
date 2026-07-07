"""Tiny user-facing views so you can click through the OAuth + sync flow
without bouncing through Django's admin.

* ``home`` — show connection status, link to start OAuth, button to trigger sync,
  and the most recent ingested workouts.
* ``sync`` — POST handler that runs ``sync_user`` for the requesting user and
  redirects home with a flash message.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from strava.ingest import sync_user
from strava.models import StravaConnection


@login_required
def home(request: HttpRequest) -> HttpResponse:
    connection = StravaConnection.objects.filter(customer=request.user).first()

    from healthdatamodel.models import Record, Workout

    record_count = Record.objects.filter(customer=request.user).count()
    workout_count = Workout.objects.filter(customer=request.user).count()
    recent_workouts = (
        Workout.objects.filter(customer=request.user)
        .order_by("-startDate")
        .prefetch_related("workoutmetadataentry_set")[:10]
    )

    return render(
        request,
        "demo/home.html",
        {
            "connection": connection,
            "record_count": record_count,
            "workout_count": workout_count,
            "recent_workouts": recent_workouts,
        },
    )


@login_required
@require_POST
def sync(request: HttpRequest) -> HttpResponse:
    try:
        connection = StravaConnection.objects.get(customer=request.user)
    except StravaConnection.DoesNotExist:
        messages.error(request, "Connect Strava first.")
        return redirect("demo-home")

    days = int(request.POST.get("days", "30"))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Unchecked checkboxes are absent from POST.
    with_streams = request.POST.get("with_streams") == "on"

    try:
        result = sync_user(
            connection,
            start=start,
            end=end,
            with_streams=with_streams,
            # One extra API read per activity; the Standard tier's read budget
            # is 100/15 min before the 10-athlete upgrade. Keep the demo polite.
            stream_activity_limit=10 if with_streams else None,
        )
    except Exception as exc:  # noqa: BLE001 — surface anything to the demo user
        messages.error(request, f"Sync failed: {exc}")
        return redirect("demo-home")

    summary = ", ".join(f"{k}={v}" for k, v in result.counts.items())
    messages.success(request, f"Synced {result.total} item(s): {summary}")
    return redirect("demo-home")
