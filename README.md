# django-strava

[![PyPI version](https://img.shields.io/pypi/v/django-strava)](https://pypi.org/project/django-strava/)
[![CI](https://github.com/django-health/django-strava/actions/workflows/ci.yml/badge.svg)](https://github.com/django-health/django-strava/actions/workflows/ci.yml)

A reusable Django app for the Strava V3 API under the 2026 Developer Program,
backed by [django-healthdatamodel](https://pypi.org/project/django-healthdatamodel/):
activities land as `Workout` rows (with metadata) and, optionally, heart-rate /
cadence / power streams as `Record` series — nothing Strava-shaped is stored
except the OAuth connection itself.

## Strava API notes (June 2026 Developer Program)

* Every new app lands in the **Standard tier**: 1 athlete by default,
  self-upgradeable to 10 in the [API settings dashboard](https://www.strava.com/settings/api),
  no review needed. The developer account must hold an active Strava subscription.
* Rate limits (default): 200 requests/15 min & 2,000/day overall; 100/15 min &
  1,000/day for reads. Doubled after the 10-athlete upgrade. The client retries
  429s and exposes the latest `X-RateLimit-*` headers.
* The API host moves to a new domain in 2027 (available 2027-01-04, mandatory
  2027-06-01). Set `STRAVA_API_BASE_URL` to cut over without a code change.
* Auth uses `Authorization: Bearer` headers and the new `POST /oauth/revoke`
  endpoint exclusively — nothing here depends on the transports retired in 2027.

## Quick start

1. Install alongside the storage layer:

   ```
   pip install django-strava
   ```

2. Add both apps to `INSTALLED_APPS`:

   ```python
   INSTALLED_APPS = [
       ...,
       "healthdatamodel",
       "strava",
   ]
   ```

3. Configure credentials from your [Strava API application](https://www.strava.com/settings/api):

   ```python
   STRAVA_CLIENT_ID = "..."
   STRAVA_CLIENT_SECRET = "..."
   STRAVA_REDIRECT_URI = "https://example.com/strava/callback/"
   # optional:
   STRAVA_CONNECT_SUCCESS_URL = "/"  # default: /admin/
   STRAVA_DEFAULT_SCOPES = ["read", "activity:read_all", "profile:read_all"]
   STRAVA_WEBHOOK_VERIFY_TOKEN = "..."  # only if using webhooks
   STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"  # 2027 host cutover knob
   ```

4. Route the app and migrate:

   ```python
   (path("strava/", include("strava.urls")),)
   ```

   ```
   python manage.py migrate
   ```

5. Send a logged-in user to `/strava/connect/` to run the OAuth flow, then sync:

   ```
   python manage.py sync_strava --user alice --days 30
   python manage.py sync_strava --days 7 --streams --stream-limit 10
   ```

## Webhooks

Route `strava/webhook/` publicly, set `STRAVA_WEBHOOK_VERIFY_TOKEN`, then:

```
python manage.py create_strava_subscription https://example.com/strava/webhook/
```

Every event POST emits `strava.signals.event_received`; connect a receiver
that calls (or enqueues) `strava.webhooks.process_event`, which re-fetches the
activity through the authenticated API and ingests/deletes accordingly, and
marks connections disconnected on athlete deauthorization events.

## Demo project

A click-through demo lives in `demo/` (login → connect → sync → browse):

```
uv sync --group dev
export STRAVA_CLIENT_ID=... STRAVA_CLIENT_SECRET=...
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Set your Strava application's Authorization Callback Domain to `localhost`,
then open http://localhost:8000/.
