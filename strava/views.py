"""HTTP views for OAuth + webhook events.

The OAuth views (``connect`` / ``callback`` / ``disconnect``) cover the
web-callback flow used in admin / dev / testing.

The ``webhook`` view satisfies Strava's subscription-validation handshake
(GET) and emits a :data:`strava.signals.event_received` signal for every
event POST.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from . import oauth, webhooks
from .constants import DEFAULT_SCOPES
from .models import StravaConnection
from .signals import event_received

SESSION_KEY = "strava_oauth_flow"


def _scopes() -> list[str]:
    return list(getattr(settings, "STRAVA_DEFAULT_SCOPES", DEFAULT_SCOPES))


def _success_url() -> str:
    return getattr(settings, "STRAVA_CONNECT_SUCCESS_URL", "/admin/")


@login_required
@require_http_methods(["GET"])
def connect(request: HttpRequest) -> HttpResponse:
    auth_url, flow_state = oauth.build_authorization_url(scopes=_scopes())
    request.session[SESSION_KEY] = flow_state.model_dump()
    return redirect(auth_url)


@login_required
@require_http_methods(["GET"])
def callback(request: HttpRequest) -> HttpResponse:
    code = request.GET.get("code")
    received_state = request.GET.get("state")
    error = request.GET.get("error")
    if error:
        return HttpResponseBadRequest(f"OAuth error: {error}")
    if not code:
        return HttpResponseBadRequest("Missing authorization code")

    stashed = request.session.pop(SESSION_KEY, None)
    if not stashed:
        return HttpResponseBadRequest("No OAuth flow in progress")
    flow_state = oauth.OAuthFlowState.model_validate(stashed)

    try:
        tokens = oauth.exchange_code(
            code=code,
            expected_state=flow_state.state,
            received_state=received_state,
        )
    except oauth.StateMismatchError:
        return HttpResponseBadRequest("OAuth state mismatch")

    # Strava reports the scopes the athlete actually granted (possibly a
    # subset of what we asked for) on the callback query string.
    granted = request.GET.get("scope")
    granted_scopes = granted.split(",") if granted else None
    oauth.ingest_tokens(
        customer=request.user, tokens=tokens, granted_scopes=granted_scopes
    )
    return redirect(_success_url())


@login_required
@require_POST
def disconnect(request: HttpRequest) -> HttpResponse:
    try:
        connection = StravaConnection.objects.get(customer=request.user)
    except StravaConnection.DoesNotExist:
        return redirect(_success_url())
    oauth.revoke(connection)
    return redirect(_success_url())


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request: HttpRequest) -> HttpResponse:
    """Strava push-subscription endpoint.

    GET is the subscription-validation handshake: verify ``hub.verify_token``
    against settings and echo ``hub.challenge`` (Strava requires the response
    within 2 seconds). POST is event delivery: emit ``event_received`` and
    return 200 immediately — Strava retries non-200s, and heavy processing
    belongs in the signal handler (ideally behind a queue).
    """
    if request.method == "GET":
        if not webhooks.verify_token_matches(request.GET.get("hub.verify_token")):
            return HttpResponseForbidden("verify token mismatch")
        challenge = request.GET.get("hub.challenge", "")
        return JsonResponse({"hub.challenge": challenge})

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponseBadRequest("invalid JSON body")

    event_received.send(sender=None, payload=payload)
    return HttpResponse(status=200)
