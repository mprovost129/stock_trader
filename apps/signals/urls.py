from django.urls import path

from . import views

app_name = "signals"

urlpatterns = [
    path("", views.list_signals, name="list"),
    path("presets/save/", views.save_filter_preset, name="save_filter_preset"),
    path("presets/<int:pk>/toggle-dashboard/", views.toggle_filter_preset_dashboard, name="toggle_filter_preset_dashboard"),
    path("presets/<int:pk>/delete/", views.delete_filter_preset, name="delete_filter_preset"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/review/", views.mark_reviewed, name="mark_reviewed"),
    path("<int:pk>/skip/", views.skip_signal, name="skip_signal"),
    path("<int:pk>/open-paper-trade/", views.open_paper_trade_view, name="open_paper_trade"),
    path("paper-trades/<int:trade_id>/close/", views.close_paper_trade_view, name="close_paper_trade"),
    path("paper-trades/<int:trade_id>/sync/", views.sync_paper_trade_view, name="sync_paper_trade"),
    path("paper-trades/<int:trade_id>/management/", views.update_paper_trade_management_view, name="update_paper_trade_management"),
]
