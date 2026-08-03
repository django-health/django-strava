"""Register this app's webhook callback with Strava.

The callback URL must be publicly reachable and the ``strava.views.webhook``
view must be routed there BEFORE running this — Strava validates with an
immediate GET handshake. One subscription per application.
"""

from django.core.management.base import BaseCommand, CommandError

from ...webhooks import create_subscription


class Command(BaseCommand):
    help = "Create the Strava push subscription for this application."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "callback_url",
            help="Public URL of the webhook view, e.g. https://example.com/strava/webhook/",
        )

    def handle(self, *args, **options) -> None:
        try:
            result = create_subscription(callback_url=options["callback_url"])
        except Exception as exc:  # noqa: BLE001 — surface the API error verbatim
            raise CommandError(f"subscription failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Subscribed: {result!r}"))
