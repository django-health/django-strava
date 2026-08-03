from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import models


class ConnectionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISCONNECTED = "disconnected", "Disconnected"
    REVOKED = "revoked", "Revoked"


class StravaConnection(models.Model):
    """Per-user OAuth state for the Strava V3 API.

    Activity data persists through django-healthdatamodel; this model only
    holds the credentials needed to fetch it, plus the athlete identity used
    to route webhook events (``owner_id`` on event payloads).
    """

    customer = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="strava_connection",
    )
    athlete_id = models.BigIntegerField(db_index=True)
    athlete_firstname = models.CharField(max_length=255, blank=True, default="")
    athlete_lastname = models.CharField(max_length=255, blank=True, default="")
    athlete_profile = models.URLField(max_length=500, blank=True, default="")
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_expires_at = models.DateTimeField()
    scopes = models.JSONField(default=list)
    status = models.CharField(
        max_length=32,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.ACTIVE,
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Strava connection"
        verbose_name_plural = "Strava connections"

    def __str__(self) -> str:
        return (
            f"StravaConnection(customer={self.customer_id}, "
            f"athlete={self.athlete_id}, status={self.status})"
        )

    def is_token_expired(
        self, *, leeway_seconds: int = 60, now: datetime | None = None
    ) -> bool:
        """True if the access token is at or past ``token_expires_at - leeway``.

        Strava access tokens live 6 hours; the leeway buys time for an
        in-flight request to complete with the same token.
        """
        anchor = now or datetime.now(timezone.utc)
        return anchor >= self.token_expires_at - timedelta(seconds=leeway_seconds)
