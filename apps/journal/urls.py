from django.urls import path

from . import views

app_name = "journal"

urlpatterns = [
    path("", views.list_entries, name="list"),
    path("new/<int:signal_id>/", views.new_for_signal, name="new_for_signal"),
]
