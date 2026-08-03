"""Strava webhook (push subscription) helpers.

Strava delivers activity/athlete events to a single registered callback URL
per application. Subscription management is a server-to-server API
authenticated with ``client_id``/``client_secret``; event delivery itself is
unauthenticated — the only verification is the ``hub.verify_token`` echo at
subscribe time, so event handlers must treat payloads as untrusted hints and
re-fetch data through the authenticated API (which :func:`process_event`
does).

Event payload shape::

    {
        "object_type": "activity" | "athlete",
        "object_id": 12345678,            # activity id, or athlete id
        "aspect_type": "create" | "update" | "delete",
        "updates": {"title": "...", ...}, # on update; {"authorized": "false"}
                                          # on athlete deauthorization
        "owner_id": 134815,               # athlete id
        "subscription_id": 120475,
        "event_time": 1516126040
    }
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from . import ingest
from .models import ConnectionStatus, StravaConnection

log = logging.getLogger(__name__)

SUBSCRIPTION_PATH = "push_subscriptions"


def _api_base() -> str:
    from .constants import API_BASE_URL

    return getattr(settings, "STRAVA_API_BASE_URL", API_BASE_URL).rstrip("/")


def verify_token_matches(candidate: str | None) -> bool:
    """Compare a ``hub.verify_token`` from Strava against settings.

    An empty/unset ``STRAVA_WEBHOOK_VERIFY_TOKEN`` matches nothing — the
    handshake endpoint stays closed until a token is configured.
    """
    expected = getattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "")
    return bool(expected) and candidate == expected


# Subscription management (server-to-server) ----------------------------------


def create_subscription(*, callback_url: str) -> dict[str, Any]:
    """``POST /push_subscriptions`` — register the callback URL.

    Strava immediately GETs ``callback_url`` with a challenge that the
    :func:`strava.views.webhook` view must echo, so the URL has to be publicly
    reachable *before* this call. One subscription per application.
    """
    response = httpx.post(
        f"{_api_base()}/{SUBSCRIPTION_PATH}",
        data={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
            "callback_url": callback_url,
            "verify_token": settings.STRAVA_WEBHOOK_VERIFY_TOKEN,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def list_subscriptions() -> list[dict[str, Any]]:
    """``GET /push_subscriptions`` — view the application's subscription."""
    response = httpx.get(
        f"{_api_base()}/{SUBSCRIPTION_PATH}",
        params={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def delete_subscription(subscription_id: int) -> None:
    """``DELETE /push_subscriptions/{id}``."""
    response = httpx.delete(
        f"{_api_base()}/{SUBSCRIPTION_PATH}/{subscription_id}",
        params={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
        },
        timeout=30.0,
    )
    response.raise_for_status()


# Event processing -------------------------------------------------------------


def process_event(payload: dict[str, Any]) -> None:
    """Route one webhook event to the matching connection and act on it.

    * activity create/update → re-fetch via the API and (re)ingest
    * activity delete → remove the ingested workout
    * athlete update with ``{"authorized": "false"}`` → mark the connection
      disconnected (the athlete revoked access from Strava's side)

    Call this from a ``strava.signals.event_received`` handler — directly for
    small deployments, or after handing off to a task queue. Unknown owners
    and malformed payloads log and return rather than raise: Strava retries
    non-200s, and a poison event must not wedge delivery.
    """
    owner_id = payload.get("owner_id")
    connection = StravaConnection.objects.filter(athlete_id=owner_id).first()
    if connection is None:
        log.warning("webhook event for unknown athlete %s — ignoring", owner_id)
        return

    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    object_id = payload.get("object_id")

    if object_type == "athlete":
        updates = payload.get("updates") or {}
        if str(updates.get("authorized")).lower() == "false":
            connection.status = ConnectionStatus.DISCONNECTED
            connection.save(update_fields=["status"])
            log.info(
                "athlete %s deauthorized — connection marked disconnected", owner_id
            )
        return

    if object_type != "activity" or object_id is None:
        log.warning("unhandled webhook payload: %r", payload)
        return

    if aspect_type in ("create", "update"):
        ingest.sync_activity(connection, int(object_id))
    elif aspect_type == "delete":
        ingest.delete_activity(connection, int(object_id))
    else:
        log.warning("unhandled aspect_type %r for activity %s", aspect_type, object_id)
