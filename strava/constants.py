"""Scopes, stream keys, and service URLs for the Strava V3 API.

Strava's June 2026 Developer Program update kept the V3 REST surface but
changes the transport over 2026-2027:

* A new API host becomes available 2027-01-04 and mandatory 2027-06-01. The
  default here stays on ``www.strava.com`` until the new host is live; override
  with ``settings.STRAVA_API_BASE_URL`` to cut over without a code change.
* Access tokens must be sent as ``Authorization: Bearer`` headers (the old
  form-param style stops working 2027-06-01). This package only ever uses the
  header.
* ``POST /oauth/deauthorize`` is retired 2027-06-01 in favor of
  ``POST /oauth/revoke`` (HTTP Basic ``client_id:client_secret``).

OAuth stays on ``www.strava.com`` even after the API host moves.
"""

OAUTH_AUTHORIZATION_URL = "https://www.strava.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
OAUTH_REVOKE_URL = "https://www.strava.com/oauth/revoke"

# Announced replacement host (available 2027-01-04, mandatory 2027-06-01):
# https://api-v3.strava.com — verify the exact hostname against
# https://developers.strava.com/docs/changelog/ before flipping the default.
API_BASE_URL = "https://www.strava.com/api/v3"

SCOPE_READ = "read"
SCOPE_READ_ALL = "read_all"
SCOPE_PROFILE_READ_ALL = "profile:read_all"
SCOPE_PROFILE_WRITE = "profile:write"
SCOPE_ACTIVITY_READ = "activity:read"
SCOPE_ACTIVITY_READ_ALL = "activity:read_all"
SCOPE_ACTIVITY_WRITE = "activity:write"

DEFAULT_SCOPES = (
    SCOPE_READ,
    SCOPE_ACTIVITY_READ_ALL,
    SCOPE_PROFILE_READ_ALL,
)

# Time-series stream types accepted by GET /activities/{id}/streams.
STREAM_KEY_TIME = "time"
STREAM_KEY_LATLNG = "latlng"
STREAM_KEY_DISTANCE = "distance"
STREAM_KEY_ALTITUDE = "altitude"
STREAM_KEY_HEARTRATE = "heartrate"
STREAM_KEY_CADENCE = "cadence"
STREAM_KEY_WATTS = "watts"
STREAM_KEY_VELOCITY = "velocity_smooth"
STREAM_KEY_TEMP = "temp"
STREAM_KEY_MOVING = "moving"
STREAM_KEY_GRADE = "grade_smooth"

# What sync fetches when streams are requested: the types we can express as
# healthdatamodel Records, plus `time` which anchors every sample.
DEFAULT_STREAM_KEYS = (
    STREAM_KEY_TIME,
    STREAM_KEY_HEARTRATE,
    STREAM_KEY_CADENCE,
    STREAM_KEY_WATTS,
)

# Human-readable, stored in Record.sourceName / Workout.sourceName. The machine
# identifier is ``healthdatamodel.constants.DataSource.STRAVA`` (added in 0.9.0).
SOURCE_NAME = "Strava"

# Max per_page accepted by GET /athlete/activities.
MAX_PER_PAGE = 200
