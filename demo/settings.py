"""Minimal Django settings for the bundled demo project.

Demonstrates installing both ``healthdatamodel`` (storage) and ``strava``
(this app) alongside Django's built-in apps.

Strava OAuth credentials and the webhook verify token are read from
environment variables so you can run the demo without editing this file:

* ``STRAVA_CLIENT_ID``
* ``STRAVA_CLIENT_SECRET``
* ``STRAVA_REDIRECT_URI`` (must EXACTLY match the Authorization Callback
  Domain configured at https://www.strava.com/settings/api — for local dev
  set that domain to ``localhost``)
* ``STRAVA_WEBHOOK_VERIFY_TOKEN`` (optional, only for webhook testing)

Note the 2026 Developer Program requirement: the Strava account that owns the
API application needs an active Strava subscription.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-demo-key-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "healthdatamodel",
    "strava",
    "demo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "demo.urls"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REDIRECT_URI = os.environ.get(
    "STRAVA_REDIRECT_URI",
    "http://localhost:8000/strava/callback/",
)
STRAVA_WEBHOOK_VERIFY_TOKEN = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "")

# Where strava.views.callback / disconnect redirect to. Library default is
# /admin/; for the demo we want the user-facing homepage.
STRAVA_CONNECT_SUCCESS_URL = "/"
