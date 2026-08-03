from django.http import HttpResponse
from django.urls import include, path

urlpatterns = [
    path("strava/", include("strava.urls")),
    path("done/", lambda request: HttpResponse("ok"), name="done"),
]
