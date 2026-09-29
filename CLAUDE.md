# Notes for Claude (and human contributors)

This project is a reusable Django library — small, focused, with a real
shipping cadence to PyPI. Slice-sized changes; commit messages with the
"why"; tests for every behavior; live calibration over docs guessing.
It mirrors the architecture of the sister repo `django-google-health`
(oauth / client / ingest / webhooks modules + bundled `demo/` project).

## Strava API ground truth (2026 Developer Program)

* Still the V3 REST surface — the June 2026 change was tiers + transport, not
  schema. Canonical changelog: https://developers.strava.com/docs/changelog/
* Standard tier, self-upgraded to 10 athletes, is what this app targets. The
  owning Strava account needs an active subscription for API access.
* A new API host arrives 2027-01-04 (mandatory 2027-06-01). The exact
  hostname was ambiguous in the announcement (`api-v3.strava.com` vs
  `www.api-v3.strava.com`) — verify against the changelog before flipping the
  `API_BASE_URL` default in `strava/constants.py`; until then deployments use
  `settings.STRAVA_API_BASE_URL`.
* No PKCE — token exchange always needs `client_secret`. Refresh tokens
  rotate on every grant; always persist the returned one. Access tokens live
  6 hours.
* OAuth quirk: the authorize URL takes comma-delimited scopes; the token
  response's `scope` field (added 2026-04-23) is space-delimited; the callback
  redirect's `scope` query param is comma-delimited again.
* Read budget is tight (100 reads/15 min pre-upgrade): streams cost one read
  per activity, which is why `sync_user(with_streams=...)` defaults off and
  the demo caps stream fetches at 10 activities.

## Debugging the Strava API

Pull the access token from `db.sqlite3` and hit the endpoint directly with
httpx before guessing at code fixes:

```python
import os, django, httpx

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")
django.setup()
from strava.models import StravaConnection

conn = StravaConnection.objects.first()
hdr = {"Authorization": f"Bearer {conn.access_token}"}
# then GET https://www.strava.com/api/v3/athlete/activities etc.
```

Promote findings into code comments + tests, not just commit messages.

## healthdatamodel integration decisions

* `DataSource.STRAVA` exists upstream since django-healthdatamodel 0.9.0.
* healthdatamodel has NO unique constraints — `ingest_*`'s
  `ignore_conflicts=True` never dedups. This app therefore makes sync
  idempotent itself: `sync_user` deletes this source's workouts in the window
  (after the fetch succeeds) before re-ingesting; `sync_activity` /
  `delete_activity` join back via the `strava_activity_id` workout metadata
  entry.
* GPS `latlng` streams are deliberately not ingested (no HealthKit analog);
  the summary polyline is kept as workout metadata.
* Mappers are NOT live-calibrated yet — written from the API reference, not
  against real responses. First real sync: check `sport_type` casing,
  `calories` presence on detail fetches, and stream `time` alignment.

## Upstream contributions to django-healthdatamodel

Same autonomy as django-google-health's CLAUDE.md: when a change there
unblocks this project — PR, CI green, version bump (minor for additive),
squash-merge, tag `v<X>` on main (triggers PyPI publish via OIDC), bump the
floor here.

## Out of scope for this project

* **Token encryption at rest.** Production uses Postgres encryption at the
  storage layer.
* **Signup view in the demo.** `createsuperuser` is the way.
* **Club endpoints and `GET /segments/explore`.** Removed/restricted for
  Standard-tier apps 2026-09-01 — don't build against them.
