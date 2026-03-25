from django.urls import path

from . import views

app_name = "portfolios"

urlpatterns = [
    path("watchlist/", views.watchlist_list, name="watchlist"),
    path("watchlist/create/", views.watchlist_create, name="watchlist_create"),
    path("watchlist/<int:pk>/activate/", views.watchlist_set_active, name="watchlist_set_active"),
    path("watchlist/import/", views.watchlist_import, name="watchlist_import"),
    path("watchlist/refresh/", views.watchlist_refresh, name="watchlist_refresh"),
    path("watchlist/selection/<int:instrument_id>/", views.watchlist_selection_edit, name="watchlist_selection_edit"),
    path("watchlist/add/", views.watchlist_add_symbol, name="watchlist_add_symbol"),
    path("watchlist/add/<int:instrument_id>/", views.watchlist_add_instrument, name="watchlist_add_instrument"),
    path("watchlist/remove/<int:instrument_id>/", views.watchlist_remove_instrument, name="watchlist_remove_instrument"),
    path("risk-settings/", views.risk_settings, name="risk_settings"),
    path("risk-settings/ops-command-center/", views.ops_command_center, name="ops_command_center"),
    path("risk-settings/portfolio-health-score/", views.portfolio_health_score, name="portfolio_health_score"),
    path("risk-settings/stop-policy-followup/", views.stop_policy_followup, name="stop_policy_followup"),
    path("risk-settings/broker-reconciliation/", views.broker_position_reconciliation, name="broker_position_reconciliation"),
    path("risk-settings/broker-reconciliation/runs/<int:pk>/", views.broker_position_reconciliation_run_detail, name="broker_position_reconciliation_run_detail"),
    path("risk-settings/broker-reconciliation/runs/<int:pk>/resolve/<str:symbol>/", views.broker_position_reconciliation_resolve, name="broker_position_reconciliation_resolve"),
    path("holdings/", views.holdings_list, name="holdings"),
    path("holdings/performance/", views.holdings_performance, name="holdings_performance"),
    path("holdings/sector-exposure/", views.holdings_sector_exposure, name="holdings_sector_exposure"),
    path("holdings/presets/save/", views.save_holding_filter_preset, name="save_holding_filter_preset"),
    path("holdings/presets/<int:pk>/toggle-dashboard/", views.toggle_holding_filter_preset_dashboard, name="toggle_holding_filter_preset_dashboard"),
    path("holdings/presets/<int:pk>/delete/", views.delete_holding_filter_preset, name="delete_holding_filter_preset"),
    path("holdings/add/", views.holding_create, name="holding_add"),
    path("holdings/import/", views.holding_import, name="holding_import"),
    path("holdings/<int:pk>/", views.holding_detail, name="holding_detail"),
    path("holdings/<int:pk>/edit/", views.holding_edit, name="holding_edit"),
    path("holdings/<int:pk>/transfer-account/", views.holding_transfer_account, name="holding_transfer_account"),
    path("holdings/<int:pk>/add-shares/", views.holding_add_shares, name="holding_add_shares"),
    path("holdings/<int:pk>/resolve-reconciliation/", views.holding_resolve_reconciliation, name="holding_resolve_reconciliation"),
    path("holdings/<int:pk>/close-from-reconciliation/", views.holding_close_from_reconciliation, name="holding_close_from_reconciliation"),
    path("holdings/<int:pk>/close/", views.holding_close, name="holding_close"),
    path("holdings/<int:pk>/partial-sell/", views.holding_partial_sell, name="holding_partial_sell"),
    path("holdings/check-now/", views.holding_check_now, name="holding_check_now"),
]
