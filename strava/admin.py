from django.contrib import admin

from .models import StravaConnection


@admin.register(StravaConnection)
class StravaConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "athlete_id",
        "status",
        "connected_at",
        "last_sync_at",
    )
    list_filter = ("status",)
    search_fields = ("customer__username", "athlete_id")
    readonly_fields = ("connected_at",)
