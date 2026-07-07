"""Django signals emitted by the webhook receiver.

``event_received`` fires for every well-formed POST to the webhook view.
Connect a handler to drive ingest:

.. code-block:: python

    from django.dispatch import receiver
    from strava.signals import event_received
    from strava.webhooks import process_event

    @receiver(event_received)
    def on_event(sender, payload, **kwargs):
        process_event(payload)  # or hand off to celery / etc.

Sender is ``None`` (signal is namespace-only). The ``payload`` keyword carries
the parsed JSON body exactly as Strava sent it. Remember Strava expects a 200
within 2 seconds — heavy handlers belong on a queue.
"""

import django.dispatch

event_received = django.dispatch.Signal()
