from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("analytics/", views.analytics, name="analytics"),
    path("data-freshness/", views.data_freshness, name="data_freshness"),
    path("symbol-search/", views.symbol_search, name="symbol_search"),
    path("toggle-fast-mode/", views.toggle_fast_mode, name="toggle_fast_mode"),
]
