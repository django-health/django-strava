from django.core.management.base import BaseCommand

from ...webhooks import list_subscriptions


class Command(BaseCommand):
    help = "List this application's Strava push subscription(s)."

    def handle(self, *args, **options) -> None:
        subscriptions = list_subscriptions()
        if not subscriptions:
            self.stdout.write("No subscriptions.")
            return
        for sub in subscriptions:
            self.stdout.write(f"{sub!r}")
