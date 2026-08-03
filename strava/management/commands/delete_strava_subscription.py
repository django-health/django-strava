from django.core.management.base import BaseCommand, CommandError

from ...webhooks import delete_subscription


class Command(BaseCommand):
    help = "Delete a Strava push subscription by id."

    def add_arguments(self, parser) -> None:
        parser.add_argument("subscription_id", type=int)

    def handle(self, *args, **options) -> None:
        try:
            delete_subscription(options["subscription_id"])
        except Exception as exc:  # noqa: BLE001 — surface the API error verbatim
            raise CommandError(f"delete failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Deleted."))
