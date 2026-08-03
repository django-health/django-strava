"""Wire Strava webhook events straight into ingest.

Processing synchronously inside the request is fine for a single-athlete
demo; production deployments should hand off to a task queue here instead —
Strava expects the webhook POST to return 200 within 2 seconds.
"""

from django.dispatch import receiver

from strava.signals import event_received
from strava.webhooks import process_event


@receiver(event_received)
def on_event(sender, payload, **kwargs) -> None:
    process_event(payload)
