"""Project URL configuration."""

from django.contrib import admin
from django.contrib.auth import logout
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import include, path


def logout_view(request):
    logout(request)
    return redirect("login")

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", logout_view, name="logout"),

    # App routes
    path("", include("apps.dashboard.urls")),
    path("signals/", include("apps.signals.urls")),
    path("journal/", include("apps.journal.urls")),
    path("portfolio/", include("apps.portfolios.urls")),
]
