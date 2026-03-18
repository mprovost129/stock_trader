from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from apps.marketdata.models import Instrument, PriceBar
from apps.signals.models import AlertDelivery, OperatorNotification, Signal

from .forms import AccountRetentionPolicyOverrideCloneForm, AccountRetentionPolicyOverrideForm, AccountRetentionPolicyTemplateApplyForm, AccountRetentionPolicyTemplateForm, AccountTransferForm, AddSharesForm, BrokerPositionImportForm, BrokerPositionResolutionForm, BrokerSnapshotForm, HeldPositionForm, HoldingImportForm, PartialSellForm, ReconciliationResolveForm, SavedFilterPresetForm, StopPolicyResolutionNoteForm, UserRiskProfileForm, WatchlistCreateForm, WatchlistImportForm, WatchlistSelectionForm, WatchlistSymbolForm
from .models import AccountRetentionPolicyOverride, AccountRetentionPolicyTemplate, BrokerPositionImportResolution, BrokerPositionImportRun, HeldPosition, HoldingAlert, HoldingTransaction, ImportedBrokerSnapshot, InstrumentSelection, SavedFilterPreset, UserRiskProfile, Watchlist
from .watchlists import activate_watchlist, active_watchlist_instrument_ids, ensure_active_watchlist
from apps.signals.services.alerts import get_enabled_delivery_channels
from apps.signals.services.delivery_health import get_delivery_health_summary

from .services import (
    apply_holding_import_rows,
    apply_buy_add,
    apply_partial_sale,
    apply_account_transfer,
    apply_watchlist_import_rows,
    build_holding_health_snapshot,
    build_broker_position_reconciliation,
    build_holding_import_reconciliation,
    create_broker_position_import_run,
    build_watchlist_import_reconciliation,
    check_open_held_positions,
    deserialize_broker_position_import_rows,
    deserialize_import_rows,
    deserialize_watchlist_import_rows,
    parse_broker_position_import_csv,
    parse_holding_import_csv,
    parse_watchlist_import,
    serialize_broker_position_import_rows,
    serialize_import_rows,
    serialize_watchlist_import_rows,
    summarize_open_holdings,
    summarize_portfolio_exposure,
    summarize_portfolio_health_history,
    summarize_portfolio_health_score,
    summarize_account_drawdown_monitoring,
    summarize_account_exposure_heatmap,
    summarize_account_holding_queues,
    summarize_account_retention_overrides,
    summarize_account_retention_template_recommendations,
    summarize_account_retention_templates,
    summarize_account_retention_template_drift,
    summarize_evidence_lifecycle_automation,
    run_evidence_lifecycle_automation,
    save_portfolio_health_snapshot,
    summarize_account_risk_posture,
    summarize_account_stop_guardrails,
    summarize_broker_snapshot_posture,
    summarize_holding_performance,
    summarize_holding_risk_guardrails,
    summarize_stop_discipline_history,
    summarize_stop_discipline_trends,
    summarize_stop_policy_timeliness,
    summarize_stop_policy_followup_queue,
    summarize_stop_policy_exception_trends,
    resolve_pending_stop_policy_events,
    summarize_watchlist_sectors,
    record_holding_transaction,
    record_broker_reconciliation_resolution,
    summarize_broker_reconciliation_run,
    resolve_evidence_retention_days,
)


HOLDING_FILTER_FIELDS = (
    "min_price",
    "max_price",
    "recommendation",
    "status",
    "source",
    "reconciliation",
    "account",
)


def _clean_filter_value(value):
    value = (value or "").strip()
    return value


def _extract_holding_filter_params(source):
    filters = {}
    for field in HOLDING_FILTER_FIELDS:
        value = _clean_filter_value(source.get(field))
        if value:
            filters[field] = value
    return filters


def _holding_filter_querystring(filters: dict) -> str:
    clean_filters = {key: value for key, value in filters.items() if value not in (None, "")}
    return urlencode(clean_filters)




def _prefilled_holding_create_initial(request):
    initial = {}
    instrument_id = _clean_filter_value(request.GET.get("instrument_id"))
    if instrument_id.isdigit():
        instrument = Instrument.objects.filter(pk=int(instrument_id), is_active=True).first()
        if instrument:
            initial["instrument"] = instrument.pk
    account_label = _clean_filter_value(request.GET.get("account_label"))
    if account_label:
        initial["account_label"] = account_label
    for field in ("quantity", "average_entry_price", "thesis", "notes"):
        value = _clean_filter_value(request.GET.get(field))
        if value:
            initial[field] = value
    opened_at = _clean_filter_value(request.GET.get("opened_at"))
    if opened_at:
        initial["opened_at"] = opened_at
    return initial


def _prefilled_holding_detail_forms(position, request):
    buy_price_default = position.last_price or position.average_entry_price
    sale_price_default = position.last_price or position.average_entry_price
    add_shares_initial = {
        "buy_price": _clean_filter_value(request.GET.get("buy_price")) or buy_price_default,
        "quantity": _clean_filter_value(request.GET.get("buy_quantity")) or None,
        "notes": _clean_filter_value(request.GET.get("buy_notes")),
    }
    partial_sell_initial = {
        "quantity": _clean_filter_value(request.GET.get("sale_quantity")) or (position.quantity if position.status == HeldPosition.Status.OPEN else None),
        "sale_price": _clean_filter_value(request.GET.get("sale_price")) or sale_price_default,
        "notes": _clean_filter_value(request.GET.get("sale_notes")),
    }
    close_form_initial = {
        "price": _clean_filter_value(request.GET.get("close_price")) or "",
        "note": _clean_filter_value(request.GET.get("close_note")),
    }
    return add_shares_initial, partial_sell_initial, close_form_initial


def _build_reconciliation_apply_url(*, run, action: str, item) -> tuple[str, str]:
    symbol = ""
    if isinstance(item, dict):
        symbol = (item.get("symbol") or "").strip().upper()
    else:
        symbol = getattr(getattr(item, "instrument", None), "symbol", "") or getattr(item, "symbol", "")
        symbol = symbol.strip().upper()

    if action == "broker_only":
        instrument = Instrument.objects.filter(symbol__iexact=symbol, is_active=True).first()
        params = {
            "instrument_id": instrument.pk if instrument else "",
            "quantity": item.get("broker_quantity") if isinstance(item, dict) else "",
            "average_entry_price": (item.get("broker_market_price") if isinstance(item, dict) else "") or "",
            "account_label": run.account_label or "",
            "notes": f"Prefilled from broker reconciliation run #{run.pk} ({run.source_label}) for {symbol}.",
        }
        return (
            "Open add-holding flow",
            f"{reverse('portfolios:holding_add')}?{urlencode({k: v for k, v in params.items() if v not in (None, '', [])})}",
        )
    if action == "tracked_only":
        position_id = item.get("tracked_position_id") if isinstance(item, dict) else item.pk
        position = HeldPosition.objects.select_related("instrument").filter(pk=position_id, user=run.user).first()
        if not position:
            return ("Open holdings", reverse("portfolios:holdings"))
        params = {
            "close_price": position.last_price or position.average_entry_price or "",
            "close_note": f"Prefilled from broker reconciliation run #{run.pk} ({run.source_label}). Broker export did not show this symbol.",
        }
        return (
            "Open close flow",
            f"{reverse('portfolios:holding_detail', kwargs={'pk': position.pk})}?{urlencode({k: v for k, v in params.items() if v not in (None, '', [])})}#reconciliation-review",
        )

    position_id = item.get("tracked_position_id") if isinstance(item, dict) else getattr(item.get("tracked"), "pk", None)
    tracked = HeldPosition.objects.select_related("instrument").filter(pk=position_id, user=run.user).first()
    if not tracked:
        return ("Open holdings", reverse("portfolios:holdings"))
    quantity_diff = Decimal(str(item.get("quantity_diff") if isinstance(item, dict) else item.get("quantity_diff") or 0))
    if quantity_diff < 0:
        params = {
            "buy_quantity": abs(quantity_diff),
            "buy_price": tracked.last_price or tracked.average_entry_price or "",
            "buy_notes": f"Prefilled from broker reconciliation run #{run.pk} ({run.source_label}). Broker qty exceeds tracked qty for {symbol}.",
        }
        return (
            "Open add-shares flow",
            f"{reverse('portfolios:holding_detail', kwargs={'pk': tracked.pk})}?{urlencode({k: v for k, v in params.items() if v not in (None, '', [])})}#record-added-buy",
        )
    params = {
        "sale_quantity": quantity_diff,
        "sale_price": tracked.last_price or tracked.average_entry_price or "",
        "sale_notes": f"Prefilled from broker reconciliation run #{run.pk} ({run.source_label}). Tracked qty exceeds broker qty for {symbol}.",
    }
    return (
        "Open partial-sale flow",
        f"{reverse('portfolios:holding_detail', kwargs={'pk': tracked.pk})}?{urlencode({k: v for k, v in params.items() if v not in (None, '', [])})}#record-partial-sale",
    )




def _build_reconciliation_apply_resolve_params(*, run, symbol: str, resolution_action: str) -> dict:
    return {
        "reconcile_run_id": str(run.pk),
        "reconcile_symbol": (symbol or "").strip().upper(),
        "reconcile_resolution_action": resolution_action,
        "reconcile_apply_resolve": "1",
    }


def _extract_broker_apply_resolution(request, *, from_post: bool = False) -> dict | None:
    source = request.POST if from_post else request.GET
    if (source.get("reconcile_apply_resolve") or "").strip() != "1":
        return None
    run_id = (source.get("reconcile_run_id") or "").strip()
    symbol = (source.get("reconcile_symbol") or "").strip().upper()
    action = (source.get("reconcile_resolution_action") or "").strip().upper()
    if not run_id.isdigit() or not symbol or not action:
        return None
    return {"run_id": int(run_id), "symbol": symbol, "action": action}


def _build_broker_apply_resolution_context(request) -> dict | None:
    payload = _extract_broker_apply_resolution(request, from_post=False)
    if not payload:
        return None
    run = BrokerPositionImportRun.objects.filter(pk=payload["run_id"], user=request.user).first()
    if not run:
        return None
    return {
        "run": run,
        "symbol": payload["symbol"],
        "action": payload["action"],
        "action_label": dict(BrokerPositionImportResolution.Action.choices).get(payload["action"], payload["action"]),
    }


def _maybe_record_broker_apply_resolution(request, *, user, tracked_position=None, note: str = "") -> int | None:
    payload = _extract_broker_apply_resolution(request, from_post=True)
    if not payload:
        return None
    run = BrokerPositionImportRun.objects.filter(pk=payload["run_id"], user=user).first()
    if not run:
        return None
    record_broker_reconciliation_resolution(
        run=run,
        user=user,
        symbol=payload["symbol"],
        action=payload["action"],
        note=note,
        tracked_position=tracked_position,
    )
    return run.pk


def _attach_reconciliation_apply_shortcuts(*, summary: dict, run) -> dict:
    summary = dict(summary)
    for key, action in (
        ("quantity_mismatches", BrokerPositionImportResolution.Action.QUANTITY_ACCEPTED),
        ("broker_only", BrokerPositionImportResolution.Action.ADD_TRACKED),
        ("tracked_only", BrokerPositionImportResolution.Action.CLOSE_TRACKED),
    ):
        rows = []
        for item in summary.get(key, []):
            row = dict(item)
            url = row.get("apply_action_url")
            symbol = (row.get("symbol") or "").strip().upper()
            if url and symbol:
                sep = "&" if "?" in url else "?"
                row["apply_and_resolve_url"] = f"{url}{sep}{urlencode(_build_reconciliation_apply_resolve_params(run=run, symbol=symbol, resolution_action=action))}"
            rows.append(row)
        summary[key] = rows
    return summary


def _build_reconciliation_resolution_stats(run) -> list[dict]:
    counts: dict[str, int] = {}
    labels = dict(BrokerPositionImportResolution.Action.choices)
    for item in run.resolutions.all():
        counts[item.action] = counts.get(item.action, 0) + 1
    stats = []
    for action, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        stats.append({"action": action, "label": labels.get(action, action), "count": count})
    return stats


def _filter_reconciliation_summary(*, summary: dict, bucket_filter: str = "", status_filter: str = "", action_filter: str = "") -> dict:
    bucket_filter = (bucket_filter or "").strip().lower()
    status_filter = (status_filter or "").strip().lower()
    action_filter = (action_filter or "").strip().upper()
    allowed_keys = {"quantity_mismatches", "broker_only", "tracked_only"}
    filtered = dict(summary)
    visible_keys = [bucket_filter] if bucket_filter in allowed_keys else list(allowed_keys)
    resolutions = filtered.get("resolutions", {}) or {}

    for key in allowed_keys:
        rows = []
        for item in filtered.get(key, []):
            symbol = (item.get("symbol") or "").strip().upper()
            resolution = resolutions.get(symbol)
            if key not in visible_keys:
                continue
            if status_filter == "resolved" and not resolution:
                continue
            if status_filter == "unresolved" and resolution:
                continue
            if action_filter and (not resolution or (resolution.action or "").strip().upper() != action_filter):
                continue
            rows.append(item)
        filtered[key] = rows
        filtered[f"{key}_visible_count"] = len(rows)

    filtered["visible_issue_count"] = sum(filtered.get(f"{key}_visible_count", 0) for key in allowed_keys)
    filtered["active_filters"] = {
        "bucket": bucket_filter,
        "status": status_filter,
        "action": action_filter,
    }
    return filtered


def _parse_optional_decimal(raw_value: str | None):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None, None
    try:
        return Decimal(raw_value), None
    except InvalidOperation:
        return None, f"Invalid numeric value: {raw_value}"



def _ordered_user_watchlists(user):
    return list(Watchlist.objects.filter(user=user).order_by("-is_active", "name", "id"))




@login_required
def watchlist_list(request):
    watchlist = ensure_active_watchlist(request.user)
    priority_filter = (request.GET.get("priority") or "").strip().upper()
    sector_filter = (request.GET.get("sector") or "").strip()
    latest_close_subquery = Subquery(
        PriceBar.objects.filter(
            instrument_id=OuterRef("instrument_id"),
            timeframe=PriceBar.Timeframe.D1,
        )
        .order_by("-ts")
        .values("close")[:1]
    )
    selections_qs = (
        InstrumentSelection.objects.select_related("instrument")
        .filter(watchlist=watchlist, is_active=True, instrument__is_active=True)
        .annotate(last_close=latest_close_subquery)
    )
    if priority_filter in {InstrumentSelection.Priority.HIGH, InstrumentSelection.Priority.NORMAL, InstrumentSelection.Priority.LOW}:
        selections_qs = selections_qs.filter(priority=priority_filter)
    if sector_filter:
        if sector_filter.upper() == "UNCATEGORIZED":
            selections_qs = selections_qs.filter(sector="")
        else:
            selections_qs = selections_qs.filter(sector__iexact=sector_filter)
    selections = list(selections_qs.order_by("instrument__asset_class", "instrument__symbol"))
    priority_rank = {
        InstrumentSelection.Priority.HIGH: 0,
        InstrumentSelection.Priority.NORMAL: 1,
        InstrumentSelection.Priority.LOW: 2,
    }
    selections.sort(key=lambda item: (priority_rank.get(item.priority, 9), item.instrument.asset_class, item.instrument.symbol))
    recent_signal_map = {}
    if selections:
        instrument_ids = [item.instrument_id for item in selections]
        recent_signals = (
            Signal.objects.select_related("strategy", "instrument")
            .filter(instrument_id__in=instrument_ids)
            .exclude(direction=Signal.Direction.FLAT)
            .order_by("instrument_id", "-generated_at", "-id")
        )
        seen = set()
        for sig in recent_signals:
            if sig.instrument_id in seen:
                continue
            recent_signal_map[sig.instrument_id] = sig
            seen.add(sig.instrument_id)
            if len(seen) == len(instrument_ids):
                break

    form = WatchlistSymbolForm(request.POST or None)
    create_form = WatchlistCreateForm()
    watchlists = _ordered_user_watchlists(request.user)
    watchlist_counts = {
        item.pk: item.selections.filter(is_active=True, instrument__is_active=True).count()
        for item in watchlists
    }
    priority_counts = {
        "HIGH": watchlist.selections.filter(is_active=True, instrument__is_active=True, priority=InstrumentSelection.Priority.HIGH).count(),
        "NORMAL": watchlist.selections.filter(is_active=True, instrument__is_active=True, priority=InstrumentSelection.Priority.NORMAL).count(),
        "LOW": watchlist.selections.filter(is_active=True, instrument__is_active=True, priority=InstrumentSelection.Priority.LOW).count(),
    }
    sector_summaries = summarize_watchlist_sectors(watchlist=watchlist, user=request.user)
    sector_options = [item["label"] for item in sector_summaries]
    return render(
        request,
        "portfolios/watchlist_list.html",
        {
            "watchlist": watchlist,
            "watchlists": watchlists,
            "watchlist_counts": watchlist_counts,
            "selections": selections,
            "selection_count": len(selections),
            "recent_signal_map": recent_signal_map,
            "watchlist_form": form,
            "watchlist_create_form": create_form,
            "priority_counts": priority_counts,
            "priority_filter": priority_filter,
            "sector_filter": sector_filter,
            "sector_summaries": sector_summaries,
            "sector_options": sector_options,
        },
    )




@login_required
def watchlist_create(request):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = WatchlistCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a name for the new watchlist.")
        return redirect("portfolios:watchlist")
    name = form.cleaned_data["name"].strip()
    watchlist, created = Watchlist.objects.get_or_create(
        user=request.user,
        name=name,
        defaults={"is_active": False},
    )
    if created:
        messages.success(request, f"Created watchlist '{watchlist.name}'.")
    else:
        messages.info(request, f"Watchlist '{watchlist.name}' already exists.")
    return redirect("portfolios:watchlist")


@login_required
def watchlist_set_active(request, pk: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    watchlist = get_object_or_404(Watchlist, pk=pk, user=request.user)
    activate_watchlist(user=request.user, watchlist=watchlist)
    messages.success(request, f"Active watchlist set to '{watchlist.name}'.")
    next_url = request.POST.get("next") or reverse("portfolios:watchlist")
    return redirect(next_url)




@login_required
def watchlist_selection_edit(request, instrument_id: int):
    watchlist = ensure_active_watchlist(request.user)
    selection = get_object_or_404(
        InstrumentSelection.objects.select_related("instrument", "watchlist"),
        watchlist=watchlist,
        instrument_id=instrument_id,
    )
    form = WatchlistSelectionForm(request.POST or None, instance=selection)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {selection.instrument.symbol} watchlist priority.")
            return redirect("portfolios:watchlist")
        messages.error(request, "Fix the watchlist priority form and try again.")
    return render(request, "portfolios/watchlist_selection_edit.html", {
        "watchlist": watchlist,
        "selection": selection,
        "form": form,
    })


@login_required
def watchlist_import(request):
    watchlist = ensure_active_watchlist(request.user)
    preview_rows = []
    parse_errors = []
    preview_ready = False
    reconciliation = None

    if request.method == "POST" and request.POST.get("confirm_import") == "1":
        preview_rows = deserialize_watchlist_import_rows(request.session.get("watchlist_import_preview_rows", []))
        if not preview_rows:
            messages.error(request, "No watchlist import preview found. Upload or paste symbols again.")
            return redirect("portfolios:watchlist_import")
        replace_missing = bool(request.session.get("watchlist_import_replace_missing", False))
        result = apply_watchlist_import_rows(watchlist=watchlist, rows=preview_rows, replace_missing=replace_missing)
        request.session.pop("watchlist_import_preview_rows", None)
        request.session.pop("watchlist_import_replace_missing", None)
        messages.success(
            request,
            f"Watchlist import complete. Added: {result['created']}. Reactivated: {result['reactivated']}. Kept active: {result['kept']}. Skipped: {result['skipped']}. Deactivated: {result['deactivated']}."
        )
        return redirect("portfolios:watchlist")

    form = WatchlistImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and request.POST.get("confirm_import") != "1":
        if form.is_valid():
            parsed = parse_watchlist_import(
                file_obj=form.cleaned_data.get("csv_file"),
                symbols_text=form.cleaned_data.get("symbols_text") or "",
            )
            preview_rows = parsed["rows"]
            parse_errors = parsed["errors"]
            request.session["watchlist_import_preview_rows"] = serialize_watchlist_import_rows(preview_rows)
            request.session["watchlist_import_replace_missing"] = bool(form.cleaned_data.get("replace_missing"))
            preview_ready = bool(preview_rows) and all(item.status == "ready" for item in preview_rows) and not parse_errors
            reconciliation = build_watchlist_import_reconciliation(watchlist=watchlist, rows=preview_rows)
            if preview_rows and not parse_errors:
                messages.info(request, f"Parsed {len(preview_rows)} watchlist import rows. Review the preview, then confirm the import.")
        else:
            parse_errors = ["Upload a CSV or paste symbols to import the watchlist."]

    sample_headers = "symbol"
    return render(request, "portfolios/watchlist_import.html", {
        "watchlist": watchlist,
        "form": form,
        "preview_rows": preview_rows,
        "parse_errors": parse_errors,
        "preview_ready": preview_ready,
        "sample_headers": sample_headers,
        "reconciliation": reconciliation,
    })


@login_required
def watchlist_add_symbol(request):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = WatchlistSymbolForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a symbol to add to the watchlist.")
        return redirect("portfolios:watchlist")
    symbol = form.cleaned_data["symbol"].strip().upper()
    instrument = Instrument.objects.filter(symbol__iexact=symbol, is_active=True).first()
    if not instrument:
        messages.error(request, f"Symbol '{symbol}' is not in the instrument universe yet.")
        return redirect("portfolios:watchlist")
    watchlist = ensure_active_watchlist(request.user)
    selection, created = InstrumentSelection.objects.get_or_create(
        watchlist=watchlist,
        instrument=instrument,
        defaults={"is_active": True},
    )
    if not created and not selection.is_active:
        selection.is_active = True
        selection.save(update_fields=["is_active"])
        created = True
    if created:
        messages.success(request, f"Added {instrument.symbol} to the active watchlist.")
    else:
        messages.info(request, f"{instrument.symbol} is already active in the active watchlist.")
    return redirect("portfolios:watchlist")


@login_required
def watchlist_add_instrument(request, instrument_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    instrument = get_object_or_404(Instrument, pk=instrument_id, is_active=True)
    watchlist = ensure_active_watchlist(request.user)
    selection, created = InstrumentSelection.objects.get_or_create(
        watchlist=watchlist,
        instrument=instrument,
        defaults={"is_active": True},
    )
    if not created and not selection.is_active:
        selection.is_active = True
        selection.save(update_fields=["is_active"])
        created = True
    messages.success(request, f"{'Added' if created else 'Kept'} {instrument.symbol} in the active watchlist.")
    next_url = request.POST.get("next") or reverse("portfolios:watchlist")
    return redirect(next_url)


@login_required
def watchlist_remove_instrument(request, instrument_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    watchlist = ensure_active_watchlist(request.user)
    selection = InstrumentSelection.objects.filter(watchlist=watchlist, instrument_id=instrument_id).first()
    instrument = Instrument.objects.filter(pk=instrument_id).first()
    symbol = instrument.symbol if instrument else "Instrument"
    if selection and selection.is_active:
        selection.is_active = False
        selection.save(update_fields=["is_active"])
        messages.success(request, f"Removed {symbol} from the active watchlist.")
    else:
        messages.info(request, f"{symbol} was not active in the active watchlist.")
    next_url = request.POST.get("next") or reverse("portfolios:watchlist")
    return redirect(next_url)


@login_required
def holdings_performance(request):
    performance = summarize_holding_performance(user=request.user)
    exposure = summarize_portfolio_exposure(user=request.user, account_label=("" if account_filter in ("", "__UNLABELED__") else account_filter))
    return render(
        request,
        "portfolios/holdings_performance.html",
        {
            "performance": performance,
            "exposure": exposure,
        },
    )

@login_required
def holdings_sector_exposure(request):
    sector_exposure = summarize_holding_sector_exposure(user=request.user)
    return render(
        request,
        "portfolios/holdings_sector_exposure.html",
        {
            "sector_exposure": sector_exposure,
        },
    )


@login_required
def holdings_list(request):
    preset_id = (request.GET.get("preset") or "").strip()
    active_preset = None
    if preset_id.isdigit():
        active_preset = SavedFilterPreset.objects.filter(
            pk=int(preset_id),
            user=request.user,
            scope=SavedFilterPreset.Scope.HOLDINGS,
        ).first()
        if active_preset:
            query = request.GET.copy()
            changed = False
            for key, value in active_preset.filters.items():
                if not query.get(key):
                    query[key] = str(value)
                    changed = True
            if changed:
                redirect_url = reverse("portfolios:holdings")
                query_string = query.urlencode()
                return redirect(f"{redirect_url}?{query_string}")

    qs = (
        HeldPosition.objects.select_related("instrument")
        .filter(user=request.user)
        .annotate(display_price=Coalesce("last_price", "average_entry_price"))
        .order_by("status", "instrument__symbol")
    )
    min_price_raw = request.GET.get("min_price")
    max_price_raw = request.GET.get("max_price")
    min_price, min_error = _parse_optional_decimal(min_price_raw)
    max_price, max_error = _parse_optional_decimal(max_price_raw)
    filter_error = min_error or max_error
    if min_price is not None:
        qs = qs.filter(display_price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(display_price__lte=max_price)

    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    source_filter = request.GET.get("source")
    if source_filter:
        qs = qs.filter(source=source_filter)

    reconciliation_filter = request.GET.get("reconciliation")
    if reconciliation_filter == "MISSING_IMPORT":
        qs = qs.filter(missing_from_latest_import=True)
    elif reconciliation_filter == "IN_SYNC_IMPORT":
        qs = qs.filter(missing_from_latest_import=False)

    account_filter = (request.GET.get("account") or "").strip()
    if account_filter == "__UNLABELED__":
        qs = qs.filter(account_label="")
    elif account_filter:
        qs = qs.filter(account_label__iexact=account_filter)

    positions = list(qs)
    snapshots = [build_holding_health_snapshot(item) for item in positions if item.status == HeldPosition.Status.OPEN]
    snapshot_map = {item.position.id: item for item in snapshots}

    recommendation_filter = request.GET.get("recommendation")
    if recommendation_filter:
        allowed_ids = {item.position.id for item in snapshots if item.recommendation_code == recommendation_filter}
        positions = [item for item in positions if item.status == HeldPosition.Status.CLOSED or item.id in allowed_ids]

    holdings_paginator = Paginator(positions, 50)
    holdings_page_obj = holdings_paginator.get_page(request.GET.get("page", 1))
    positions = list(holdings_page_obj)

    watchlist_instrument_ids = active_watchlist_instrument_ids(request.user)
    selected_account_label = account_filter or ""
    summary = summarize_open_holdings(user=request.user, account_label=selected_account_label)
    exposure = summarize_portfolio_exposure(user=request.user, account_label=selected_account_label)
    holding_risk_guardrails = summarize_holding_risk_guardrails(user=request.user, account_label=selected_account_label)
    current_filters = _extract_holding_filter_params(request.GET)
    saved_presets = list(
        SavedFilterPreset.objects.filter(user=request.user, scope=SavedFilterPreset.Scope.HOLDINGS)
    )
    saved_preset_form = SavedFilterPresetForm()
    holding_account_options = []
    seen_account_keys = set()
    for label in HeldPosition.objects.filter(user=request.user).values_list("account_label", flat=True):
        normalized = (label or "").strip()
        key = normalized.lower() if normalized else "__unlabeled__"
        if key in seen_account_keys:
            continue
        seen_account_keys.add(key)
        holding_account_options.append({"value": normalized or "__UNLABELED__", "label": normalized or "Unlabeled holdings"})
    holding_account_options.sort(key=lambda item: item["label"].lower())
    return render(
        request,
        "portfolios/holdings_list.html",
        {
            "positions": positions,
            "page_obj": holdings_page_obj,
            "snapshot_map": snapshot_map,
            "summary": summary,
            "min_price": min_price_raw or "",
            "max_price": max_price_raw or "",
            "status_filter": status_filter or "",
            "source_filter": source_filter or "",
            "recommendation_filter": recommendation_filter or "",
            "reconciliation_filter": reconciliation_filter or "",
            "account_filter": account_filter or "",
            "holding_account_options": holding_account_options,
            "filter_error": filter_error,
            "status_choices": HeldPosition.Status.choices,
            "source_choices": HeldPosition.Source.choices,
            "recommendation_choices": [
                ("SELL_NOW", "Sell now"),
                ("REVIEW_URGENT", "Urgent review"),
                ("REVIEW", "Review"),
                ("TRIM_OR_EXIT", "Trim / exit"),
                ("HOLD", "Hold"),
            ],
            "reconciliation_choices": [
                ("MISSING_IMPORT", "Missing from latest import"),
                ("IN_SYNC_IMPORT", "In sync with latest import"),
            ],
            "current_filters": current_filters,
            "current_filter_querystring": _holding_filter_querystring(current_filters),
            "saved_presets": saved_presets,
            "saved_preset_form": saved_preset_form,
            "active_preset": active_preset,
            "exposure": exposure,
            "reconciliation_form": ReconciliationResolveForm(),
            "watchlist_instrument_ids": watchlist_instrument_ids,
            "holding_risk_guardrails": holding_risk_guardrails,
        },
    )


@login_required
def save_holding_filter_preset(request):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = SavedFilterPresetForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a preset name before saving.")
        redirect_query = _holding_filter_querystring(_extract_holding_filter_params(request.POST))
        if redirect_query:
            return redirect(f"{reverse('portfolios:holdings')}?{redirect_query}")
        return redirect("portfolios:holdings")

    filters = _extract_holding_filter_params(request.POST)
    if not filters:
        messages.error(request, "Choose at least one filter before saving a preset.")
        return redirect("portfolios:holdings")

    preset, created = SavedFilterPreset.objects.update_or_create(
        user=request.user,
        scope=SavedFilterPreset.Scope.HOLDINGS,
        name=form.cleaned_data["name"].strip(),
        defaults={
            "filters": filters,
            "is_dashboard_widget": form.cleaned_data.get("pin_to_dashboard", False),
        },
    )
    messages.success(request, f"{'Created' if created else 'Updated'} holding preset '{preset.name}'.")
    redirect_query = _holding_filter_querystring(filters)
    return redirect(f"{reverse('portfolios:holdings')}?{redirect_query}")


@login_required
def toggle_holding_filter_preset_dashboard(request, pk: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    preset = get_object_or_404(
        SavedFilterPreset,
        pk=pk,
        user=request.user,
        scope=SavedFilterPreset.Scope.HOLDINGS,
    )
    preset.is_dashboard_widget = not preset.is_dashboard_widget
    preset.save(update_fields=["is_dashboard_widget", "updated_at"])
    state = "Pinned" if preset.is_dashboard_widget else "Unpinned"
    messages.success(request, f"{state} holding preset '{preset.name}' {'to' if preset.is_dashboard_widget else 'from'} the dashboard.")
    return redirect("portfolios:holdings")


@login_required
def delete_holding_filter_preset(request, pk: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    preset = get_object_or_404(
        SavedFilterPreset,
        pk=pk,
        user=request.user,
        scope=SavedFilterPreset.Scope.HOLDINGS,
    )
    preset_name = preset.name
    preset.delete()
    messages.success(request, f"Deleted holding preset '{preset_name}'.")
    return redirect("portfolios:holdings")


@login_required
def holding_detail(request, pk: int):
    position = get_object_or_404(HeldPosition.objects.select_related("instrument"), pk=pk, user=request.user)
    snapshot = build_holding_health_snapshot(position) if position.status == HeldPosition.Status.OPEN else None
    recent_alerts = list(position.alerts.order_by("-created_at")[:12])
    recent_transactions = list(position.transactions.order_by("-created_at", "-id")[:12])
    add_shares_initial, partial_sell_initial, close_form_initial = _prefilled_holding_detail_forms(position, request)
    add_shares_form = AddSharesForm(initial=add_shares_initial)
    partial_sell_form = PartialSellForm(initial=partial_sell_initial)
    transfer_account_form = AccountTransferForm(initial={
        "account_label": position.account_label or "",
        "note": "",
    })
    recent_signals = list(
        Signal.objects.select_related("strategy")
        .filter(instrument=position.instrument, generated_at__gte=position.opened_at)
        .exclude(direction=Signal.Direction.FLAT)
        .order_by("-generated_at", "-id")[:12]
    )
    watchlist_instrument_ids = active_watchlist_instrument_ids(request.user)
    return render(
        request,
        "portfolios/holding_detail.html",
        {
            "position": position,
            "snapshot": snapshot,
            "recent_alerts": recent_alerts,
            "recent_signals": recent_signals,
            "recent_transactions": recent_transactions,
            "add_shares_form": add_shares_form,
            "partial_sell_form": partial_sell_form,
            "transfer_account_form": transfer_account_form,
            "close_form_initial": close_form_initial,
            "reconciliation_form": ReconciliationResolveForm(initial={"close_price": close_form_initial.get("price") or position.last_price or position.average_entry_price, "note": close_form_initial.get("note") or ""}),
            "broker_apply_context": _build_broker_apply_resolution_context(request),
            "in_watchlist": position.instrument_id in watchlist_instrument_ids,
        },
    )


@login_required
def holding_import(request):
    preview_rows = []
    parse_errors = []
    preview_ready = False
    reconciliation = None

    if request.method == "POST" and request.POST.get("confirm_import") == "1":
        preview_rows = deserialize_import_rows(request.session.get("holding_import_preview_rows", []))
        if not preview_rows:
            messages.error(request, "No import preview found. Upload the CSV again.")
            return redirect("portfolios:holding_import")
        mark_missing_review = bool(request.session.get("holding_import_mark_missing_review", True))
        import_account_label = (request.session.get("holding_import_account_label") or "").strip()
        result = apply_holding_import_rows(user=request.user, rows=preview_rows, mark_missing_review=mark_missing_review, account_label=import_account_label)
        request.session.pop("holding_import_preview_rows", None)
        request.session.pop("holding_import_mark_missing_review", None)
        request.session.pop("holding_import_account_label", None)
        messages.success(request, f"Holding import complete. Created: {result['created']}. Updated: {result['updated']}. Skipped: {result['skipped']}. Missing-from-import flagged: {result['flagged_missing']}.")
        return redirect("portfolios:holdings")

    form = HoldingImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and request.POST.get("confirm_import") != "1":
        if form.is_valid():
            parsed = parse_holding_import_csv(form.cleaned_data["csv_file"])
            preview_rows = parsed["rows"]
            parse_errors = parsed["errors"]
            request.session["holding_import_preview_rows"] = serialize_import_rows(preview_rows)
            request.session["holding_import_mark_missing_review"] = bool(form.cleaned_data.get("mark_missing_positions_for_review"))
            request.session["holding_import_account_label"] = (form.cleaned_data.get("account_label") or "").strip()
            preview_ready = bool(preview_rows) and all(item.status == "ready" for item in preview_rows) and not parse_errors
            reconciliation = build_holding_import_reconciliation(user=request.user, rows=preview_rows, account_label=form.cleaned_data.get("account_label") or "")
            if preview_rows and not parse_errors:
                messages.info(request, f"Parsed {len(preview_rows)} CSV rows. Review the preview, then confirm the import.")
        else:
            parse_errors = ["Upload a CSV file to import held positions."]

    sample_headers = "symbol,quantity,average_entry_price,opened_at,stop_price,target_price,thesis,notes"
    return render(request, "portfolios/holding_import.html", {
        "form": form,
        "preview_rows": preview_rows,
        "parse_errors": parse_errors,
        "preview_ready": preview_ready,
        "sample_headers": sample_headers,
        "reconciliation": reconciliation,
    })


@login_required
def holding_create(request):
    if request.method == "POST":
        form = HeldPositionForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.account_label = (obj.account_label or "").strip()
            obj.user = request.user
            obj.source = HeldPosition.Source.MANUAL
            obj.save()
            record_holding_transaction(position=obj, event_type=HoldingTransaction.EventType.OPEN, quantity=obj.quantity, price=obj.average_entry_price, notes="Manual open position.", created_at=obj.opened_at)
            resolved_run_id = _maybe_record_broker_apply_resolution(
                request,
                user=request.user,
                tracked_position=obj,
                note=f"Applied from broker reconciliation by creating a tracked holding for {obj.instrument.symbol}.",
            )
            if resolved_run_id:
                messages.success(request, f"Added held position for {obj.instrument.symbol} and marked the broker reconciliation item resolved.")
                return redirect("portfolios:broker_position_reconciliation_run_detail", pk=resolved_run_id)
            messages.success(request, f"Added held position for {obj.instrument.symbol}.")
            return redirect("portfolios:holdings")
    else:
        form = HeldPositionForm(initial=_prefilled_holding_create_initial(request))
    return render(request, "portfolios/holding_form.html", {"form": form, "title": "Add held position", "broker_apply_context": _build_broker_apply_resolution_context(request)})


@login_required
def holding_edit(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method == "POST":
        form = HeldPositionForm(request.POST, instance=position)
        if form.is_valid():
            prior_stop = position.stop_price
            obj = form.save(commit=False)
            obj.account_label = (obj.account_label or "").strip()
            obj.save()
            resolved = resolve_pending_stop_policy_events(position=obj, changed_at=timezone.now(), prior_stop=prior_stop, new_stop=obj.stop_price)
            if resolved:
                messages.success(request, f"Updated held position for {position.instrument.symbol} and resolved {resolved} pending stop-policy item(s).")
            else:
                messages.success(request, f"Updated held position for {position.instrument.symbol}.")
            return redirect("portfolios:holding_detail", pk=position.pk)
    else:
        form = HeldPositionForm(instance=position)
    return render(request, "portfolios/holding_form.html", {"form": form, "title": f"Edit {position.instrument.symbol}"})


@login_required
def holding_resolve_reconciliation(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = ReconciliationResolveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid reconciliation note or leave the fields blank.")
        return redirect("portfolios:holding_detail", pk=position.pk)
    note = (form.cleaned_data.get("note") or "").strip() or "Reviewed and kept open after import reconciliation."
    position.missing_from_latest_import = False
    position.reconciliation_note = note
    position.reconciliation_resolved_at = timezone.now()
    position.save(update_fields=["missing_from_latest_import", "reconciliation_note", "reconciliation_resolved_at", "updated_at"])
    messages.success(request, f"Marked {position.instrument.symbol} as reviewed for the latest import mismatch.")
    return redirect("portfolios:holding_detail", pk=position.pk)


@login_required
def holding_close_from_reconciliation(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    if position.status != HeldPosition.Status.OPEN:
        messages.error(request, "Only open positions can be closed from reconciliation.")
        return redirect("portfolios:holding_detail", pk=position.pk)
    form = ReconciliationResolveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid close price or leave it blank to use the latest price.")
        return redirect("portfolios:holding_detail", pk=position.pk)
    close_price = form.cleaned_data.get("close_price") or position.last_price or position.average_entry_price
    note = (form.cleaned_data.get("note") or "").strip()
    note = f"Closed from import reconciliation review. {note}".strip()
    try:
        apply_partial_sale(
            position=position,
            sell_quantity=Decimal(position.quantity),
            sale_price=Decimal(close_price),
            notes=note,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("portfolios:holding_detail", pk=position.pk)
    position.missing_from_latest_import = False
    position.reconciliation_note = note
    position.reconciliation_resolved_at = timezone.now()
    position.save(update_fields=["missing_from_latest_import", "reconciliation_note", "reconciliation_resolved_at", "updated_at"])
    messages.success(request, f"Closed {position.instrument.symbol} from the import reconciliation queue.")
    return redirect("portfolios:holdings")


@login_required
def holding_transfer_account(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = AccountTransferForm(request.POST)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
        return redirect("portfolios:holding_detail", pk=position.pk)
    prior_label = (position.account_label or "").strip() or "Unlabeled / blended"
    new_label = (form.cleaned_data.get("account_label") or "").strip()
    try:
        apply_account_transfer(
            position=position,
            new_account_label=new_label,
            notes=form.cleaned_data.get("note") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("portfolios:holding_detail", pk=position.pk)
    target_label = new_label or "Unlabeled / blended"
    messages.success(request, f"Moved {position.instrument.symbol} from {prior_label} to {target_label}.")
    return redirect("portfolios:holding_detail", pk=position.pk)


@login_required
def holding_add_shares(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    if position.status != HeldPosition.Status.OPEN:
        messages.error(request, "Only open positions can receive added shares.")
        return redirect("portfolios:holding_detail", pk=position.pk)
    form = AddSharesForm(request.POST)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
        return redirect("portfolios:holding_detail", pk=position.pk)
    try:
        tx = apply_buy_add(
            position=position,
            buy_quantity=form.cleaned_data["quantity"],
            buy_price=form.cleaned_data.get("buy_price"),
            stop_price=form.cleaned_data.get("stop_price"),
            notes=form.cleaned_data.get("notes") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("portfolios:holding_detail", pk=position.pk)
    resolved_run_id = _maybe_record_broker_apply_resolution(
        request,
        user=request.user,
        tracked_position=position,
        note=f"Applied from broker reconciliation by recording added shares for {position.instrument.symbol}.",
    )
    if resolved_run_id:
        messages.success(request, f"Recorded an added buy of {tx.quantity} {position.instrument.symbol} at {tx.price} and marked the broker reconciliation item resolved.")
        return redirect("portfolios:broker_position_reconciliation_run_detail", pk=resolved_run_id)
    messages.success(request, f"Recorded an added buy of {tx.quantity} {position.instrument.symbol} at {tx.price}. New quantity: {position.quantity}. New average entry: {position.average_entry_price}.")
    return redirect("portfolios:holding_detail", pk=position.pk)


@login_required
def holding_close(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    raw_price = (request.POST.get("close_price") or "").strip()
    close_price = position.last_price or position.average_entry_price
    if raw_price:
        try:
            close_price = Decimal(raw_price)
        except InvalidOperation:
            messages.error(request, "Invalid close price.")
            return redirect("portfolios:holding_detail", pk=position.pk)
    try:
        apply_partial_sale(
            position=position,
            sell_quantity=Decimal(position.quantity),
            sale_price=Decimal(close_price),
            notes=(request.POST.get("close_notes") or "").strip(),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("portfolios:holding_detail", pk=position.pk)
    resolved_run_id = _maybe_record_broker_apply_resolution(
        request,
        user=request.user,
        tracked_position=position,
        note=f"Applied from broker reconciliation by closing the tracked holding for {position.instrument.symbol}.",
    )
    if resolved_run_id:
        messages.success(request, f"Closed held position for {position.instrument.symbol} and marked the broker reconciliation item resolved.")
        return redirect("portfolios:broker_position_reconciliation_run_detail", pk=resolved_run_id)
    messages.success(request, f"Closed held position for {position.instrument.symbol}.")
    return redirect("portfolios:holdings")


@login_required
def holding_partial_sell(request, pk: int):
    position = get_object_or_404(HeldPosition, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    if position.status != HeldPosition.Status.OPEN:
        messages.error(request, "Only open positions can be reduced.")
        return redirect("portfolios:holding_detail", pk=position.pk)
    form = PartialSellForm(request.POST)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
        return redirect("portfolios:holding_detail", pk=position.pk)
    try:
        tx = apply_partial_sale(
            position=position,
            sell_quantity=form.cleaned_data["quantity"],
            sale_price=form.cleaned_data.get("sale_price"),
            notes=form.cleaned_data.get("notes") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("portfolios:holding_detail", pk=position.pk)
    resolved_run_id = _maybe_record_broker_apply_resolution(
        request,
        user=request.user,
        tracked_position=position,
        note=f"Applied from broker reconciliation by recording a sale adjustment for {position.instrument.symbol}.",
    )
    if position.status == HeldPosition.Status.CLOSED:
        if resolved_run_id:
            messages.success(request, f"Sold the full remaining {tx.quantity} of {position.instrument.symbol} and marked the broker reconciliation item resolved.")
            return redirect("portfolios:broker_position_reconciliation_run_detail", pk=resolved_run_id)
        messages.success(request, f"Sold the full remaining {tx.quantity} of {position.instrument.symbol} and closed the holding.")
        return redirect("portfolios:holdings")
    if resolved_run_id:
        messages.success(request, f"Recorded a partial sale of {tx.quantity} {position.instrument.symbol} at {tx.price} and marked the broker reconciliation item resolved.")
        return redirect("portfolios:broker_position_reconciliation_run_detail", pk=resolved_run_id)
    messages.success(request, f"Recorded a partial sale of {tx.quantity} {position.instrument.symbol} at {tx.price}. Remaining quantity: {position.quantity}.")
    return redirect("portfolios:holding_detail", pk=position.pk)


@login_required
def holding_check_now(request):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    alerts = check_open_held_positions(user=request.user, dry_run=False)
    sent = sum(1 for item in alerts if item.status == item.Status.SENT)
    messages.success(request, f"Held-position check complete. Alerts sent: {sent}. Records created: {len(alerts)}.")
    return redirect("portfolios:holdings")




@login_required
def broker_position_reconciliation(request):
    preview_rows = []
    parse_errors = []
    preview_ready = False
    reconciliation = None
    saved_run = None
    account_filter = (request.GET.get("account") or "").strip()

    if request.method == "POST" and request.POST.get("clear_preview") == "1":
        request.session.pop("broker_position_preview_rows", None)
        request.session.pop("broker_position_preview_account_label", None)
        messages.success(request, "Cleared the broker reconciliation preview.")
        return redirect("portfolios:broker_position_reconciliation")

    if request.method == "GET":
        preview_rows = deserialize_broker_position_import_rows(request.session.get("broker_position_preview_rows", []))
        preview_account_label = (request.session.get("broker_position_preview_account_label") or "").strip()
        if preview_rows:
            preview_ready = all(item.status == "ready" for item in preview_rows)
            reconciliation = build_broker_position_reconciliation(user=request.user, rows=preview_rows, account_label=preview_account_label)

    form = BrokerPositionImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and request.POST.get("clear_preview") != "1":
        if form.is_valid():
            parsed = parse_broker_position_import_csv(form.cleaned_data["csv_file"])
            preview_rows = parsed["rows"]
            parse_errors = parsed["errors"]
            request.session["broker_position_preview_rows"] = serialize_broker_position_import_rows(preview_rows)
            request.session["broker_position_preview_account_label"] = (form.cleaned_data.get("account_label") or "").strip()
            preview_ready = bool(preview_rows) and all(item.status == "ready" for item in preview_rows) and not parse_errors
            reconciliation = build_broker_position_reconciliation(user=request.user, rows=preview_rows, account_label=form.cleaned_data.get("account_label") or "")
            if preview_rows and not parse_errors:
                saved_run = create_broker_position_import_run(
                    user=request.user,
                    source_label=form.cleaned_data.get("source_label") or "Broker CSV",
                    account_label=form.cleaned_data.get("account_label") or "",
                    uploaded_filename=getattr(form.cleaned_data.get("csv_file"), "name", "") or "",
                    preview_rows=preview_rows,
                    reconciliation=reconciliation,
                )
                messages.info(request, f"Parsed {len(preview_rows)} broker-position rows. Review the mismatch summary below. Saved review run #{saved_run.pk} for audit history.")
        else:
            parse_errors = ["Upload a CSV file to review broker/account positions."]

    recent_runs_qs = BrokerPositionImportRun.objects.filter(user=request.user)
    if account_filter:
        recent_runs_qs = recent_runs_qs.filter(account_label__iexact=account_filter)
    recent_runs = list(recent_runs_qs.order_by("-created_at", "-id")[:8])
    account_options = []
    seen = set()
    for label in list(BrokerPositionImportRun.objects.filter(user=request.user).values_list("account_label", flat=True)) + list(HeldPosition.objects.filter(user=request.user).values_list("account_label", flat=True)):
        normalized = (label or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_options.append(normalized)
    account_options.sort(key=str.lower)
    sample_headers = "symbol,quantity,market_price,market_value,average_entry_price"
    return render(request, "portfolios/broker_position_reconciliation.html", {
        "form": form,
        "preview_rows": preview_rows,
        "parse_errors": parse_errors,
        "preview_ready": preview_ready,
        "sample_headers": sample_headers,
        "reconciliation": reconciliation,
        "saved_run": saved_run,
        "recent_runs": recent_runs,
        "account_filter": account_filter,
        "account_options": account_options,
    })


@login_required
def broker_position_reconciliation_run_detail(request, pk: int):
    run = get_object_or_404(BrokerPositionImportRun.objects.prefetch_related("resolutions", "resolutions__tracked_position", "resolutions__tracked_position__instrument"), pk=pk, user=request.user)
    bucket_filter = (request.GET.get("bucket") or "").strip().lower()
    status_filter = (request.GET.get("status") or "").strip().lower()
    action_filter = (request.GET.get("action") or "").strip().upper()
    base_summary = summarize_broker_reconciliation_run(run)
    summary = _attach_reconciliation_apply_shortcuts(summary=base_summary, run=run)
    summary = _filter_reconciliation_summary(summary=summary, bucket_filter=bucket_filter, status_filter=status_filter, action_filter=action_filter)
    resolution_form = BrokerPositionResolutionForm()
    resolution_stats = _build_reconciliation_resolution_stats(run)
    recent_resolutions = list(run.resolutions.select_related("tracked_position", "tracked_position__instrument").order_by("-resolved_at", "-id")[:10])
    return render(request, "portfolios/broker_position_reconciliation_run_detail.html", {
        "run": run,
        "summary": summary,
        "preview_rows": deserialize_broker_position_import_rows(run.preview_rows or []),
        "resolution_form": resolution_form,
        "resolution_stats": resolution_stats,
        "recent_resolutions": recent_resolutions,
        "bucket_filter": bucket_filter,
        "status_filter": status_filter,
        "action_filter": action_filter,
        "resolution_action_choices": BrokerPositionResolutionForm.base_fields["action"].choices,
    })


@login_required
def broker_position_reconciliation_resolve(request, pk: int, symbol: str):
    run = get_object_or_404(BrokerPositionImportRun, pk=pk, user=request.user)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = BrokerPositionResolutionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Fix the resolution form and try again.")
        return redirect("portfolios:broker_position_reconciliation_run_detail", pk=run.pk)
    symbol_value = (symbol or "").strip().upper()
    tracked_position = HeldPosition.objects.filter(user=request.user, instrument__symbol__iexact=symbol_value).order_by("-updated_at", "-id").first()
    record_broker_reconciliation_resolution(
        run=run,
        user=request.user,
        symbol=symbol_value,
        action=form.cleaned_data["action"],
        note=(form.cleaned_data.get("combined_note") or form.cleaned_data.get("note") or "").strip(),
        tracked_position=tracked_position,
    )
    messages.success(request, f"Saved reconciliation resolution for {symbol_value}.")
    return redirect("portfolios:broker_position_reconciliation_run_detail", pk=run.pk)




@login_required
def portfolio_health_score(request):
    if request.method == "POST" and request.POST.get("save_portfolio_health_snapshot") == "1":
        snapshot = save_portfolio_health_snapshot(user=request.user)
        messages.success(request, f"Saved portfolio health snapshot at score {snapshot.overall_score}.")
        return redirect("portfolios:portfolio_health_score")

    portfolio_health = summarize_portfolio_health_score(user=request.user)
    portfolio_health_history = summarize_portfolio_health_history(user=request.user)
    return render(request, "portfolios/portfolio_health_score.html", {
        "portfolio_health": portfolio_health,
        "portfolio_health_history": portfolio_health_history,
    })


@login_required
def ops_command_center(request):
    if request.method == "POST" and request.POST.get("save_portfolio_health_snapshot") == "1":
        snapshot = save_portfolio_health_snapshot(user=request.user)
        messages.success(request, f"Saved portfolio health snapshot at score {snapshot.overall_score}.")
        return redirect("portfolios:ops_command_center")

    if request.method == "POST" and request.POST.get("save_evidence_lifecycle_run") == "1":
        archive_mode = (request.POST.get("lifecycle_mode") or "SCAN").strip().upper() == "ARCHIVE_EXPIRED"
        result = run_evidence_lifecycle_automation(user=request.user, archive_expired=archive_mode)
        if archive_mode:
            messages.success(request, f"Evidence lifecycle automation archived {result['archived_count']} expired attachment(s).")
        else:
            messages.success(request, f"Evidence lifecycle scan complete: {result['expiring_soon_count']} expiring soon, {result['expired_count']} expired, {result['missing_retention_count']} missing retention.")
        return redirect("portfolios:ops_command_center")

    followup = summarize_stop_policy_followup_queue(user=request.user, status_filter="ACTIONABLE", limit=25)
    stop_policy_timeliness = summarize_stop_policy_timeliness(user=request.user)
    stop_policy_exception_trends = summarize_stop_policy_exception_trends(user=request.user)
    evidence_lifecycle_automation = summarize_evidence_lifecycle_automation(user=request.user)
    broker_snapshot_posture = summarize_broker_snapshot_posture(user=request.user)
    account_holding_queues = summarize_account_holding_queues(user=request.user)
    account_drawdown_monitoring = summarize_account_drawdown_monitoring(user=request.user)
    account_risk_posture = summarize_account_risk_posture(user=request.user)
    account_stop_guardrails = summarize_account_stop_guardrails(user=request.user)
    portfolio_health = summarize_portfolio_health_score(user=request.user)
    portfolio_health_history = summarize_portfolio_health_history(user=request.user, limit=8)

    delivery_channels = get_enabled_delivery_channels()
    delivery_health = get_delivery_health_summary()
    recent_failed_alerts = list(AlertDelivery.objects.filter(status=AlertDelivery.Status.FAILED).order_by("-created_at")[:8])
    recent_operator_notifications = list(
        OperatorNotification.objects.filter(
            kind__in=[
                OperatorNotification.Kind.DELIVERY_HEALTH,
                OperatorNotification.Kind.DELIVERY_RECOVERY,
                OperatorNotification.Kind.PORTFOLIO_HEALTH,
            ]
        )
        .order_by("-created_at")[:8]
    )
    recent_broker_runs = list(BrokerPositionImportRun.objects.filter(user=request.user).order_by("-created_at", "-id")[:8])

    context = {
        "followup": followup,
        "stop_policy_timeliness": stop_policy_timeliness,
        "stop_policy_exception_trends": stop_policy_exception_trends,
        "evidence_lifecycle_automation": evidence_lifecycle_automation,
        "broker_snapshot_posture": broker_snapshot_posture,
        "account_holding_queues": account_holding_queues,
        "account_drawdown_monitoring": account_drawdown_monitoring,
        "account_risk_posture": account_risk_posture,
        "account_stop_guardrails": account_stop_guardrails,
        "portfolio_health": portfolio_health,
        "portfolio_health_history": portfolio_health_history,
        "delivery_channels": delivery_channels,
        "delivery_health": delivery_health,
        "recent_failed_alerts": recent_failed_alerts,
        "recent_operator_notifications": recent_operator_notifications,
        "recent_broker_runs": recent_broker_runs,
    }
    return render(request, "portfolios/ops_command_center.html", context)




@login_required
def stop_policy_followup(request):
    account_filter = (request.GET.get("account") or request.POST.get("account") or "").strip()
    status_filter = (request.GET.get("policy_status") or request.POST.get("policy_status") or "ACTIONABLE").strip().upper()
    event_filter = (request.GET.get("event_type") or request.POST.get("event_type") or "").strip().upper()
    symbol_filter = (request.GET.get("symbol") or request.POST.get("symbol") or "").strip().upper()
    reason_filter = (request.GET.get("reason") or request.POST.get("reason") or "").strip().upper()
    evidence_filter = (request.GET.get("evidence_status") or request.POST.get("evidence_status") or "").strip().upper()
    evidence_type_filter = (request.GET.get("evidence_type") or request.POST.get("evidence_type") or "").strip().upper()
    evidence_quality_filter = (request.GET.get("evidence_quality") or request.POST.get("evidence_quality") or "").strip().upper()
    retention_filter = (request.GET.get("retention") or request.POST.get("retention") or "").strip().upper()


    def _current_query():
        query = {}
        if account_filter:
            query["account"] = account_filter
        if status_filter:
            query["policy_status"] = status_filter
        if event_filter:
            query["event_type"] = event_filter
        if symbol_filter:
            query["symbol"] = symbol_filter
        if reason_filter:
            query["reason"] = reason_filter
        if evidence_filter:
            query["evidence_status"] = evidence_filter
        if evidence_type_filter:
            query["evidence_type"] = evidence_type_filter
        if evidence_quality_filter:
            query["evidence_quality"] = evidence_quality_filter
        if retention_filter:
            query["retention"] = retention_filter
        return query

    def _apply_retention_action_to_tx(tx, action: str):
        action = (action or "").strip().lower()
        now = timezone.now()
        symbol = tx.position.instrument.symbol
        if not getattr(tx, "execution_evidence_attachment", None):
            return False, f"No evidence attachment is stored for {symbol}."
        if action == "extend_90":
            base = tx.execution_evidence_retention_until or now
            if base < now:
                base = now
            tx.execution_evidence_retention_until = base + timedelta(days=90)
            tx.save(update_fields=["execution_evidence_retention_until"])
            return True, f"Extended evidence retention 90 days for {symbol}."
        if action == "extend_365":
            base = tx.execution_evidence_retention_until or now
            if base < now:
                base = now
            tx.execution_evidence_retention_until = base + timedelta(days=365)
            tx.save(update_fields=["execution_evidence_retention_until"])
            return True, f"Extended evidence retention 365 days for {symbol}."
        if action == "archive_clear":
            attachment_name = tx.execution_evidence_attachment.name.split("/")[-1] if tx.execution_evidence_attachment.name else "attachment"
            tx.execution_evidence_attachment.delete(save=False)
            tx.execution_evidence_attachment = None
            archive_note = (tx.execution_evidence_note or "").strip()
            archive_suffix = f"Archived attachment removed on {timezone.localtime(now).strftime('%Y-%m-%d %H:%M')}"
            tx.execution_evidence_note = (archive_note + "\n" + archive_suffix).strip() if archive_note else archive_suffix
            tx.execution_evidence_retention_until = None
            tx.execution_evidence_recorded_at = now
            tx.save(update_fields=["execution_evidence_attachment", "execution_evidence_note", "execution_evidence_retention_until", "execution_evidence_recorded_at"])
            return True, f"Archived and cleared {attachment_name} for {symbol}."
        return False, "Unknown retention action."

    if request.method == "POST" and request.POST.get("save_retention_action") == "1":
        tx = get_object_or_404(
            HoldingTransaction,
            pk=request.POST.get("tx_id"),
            position__user=request.user,
            event_type__in={HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD},
        )
        ok, msg = _apply_retention_action_to_tx(tx, request.POST.get("retention_action"))
        (messages.success if ok else messages.error)(request, msg)
        query = _current_query()
        url = reverse("portfolios:stop_policy_followup")
        return redirect(f"{url}?{urlencode(query)}" if query else url)


    if request.method == "POST" and request.POST.get("save_evidence_lifecycle_run") == "1":
        archive_mode = (request.POST.get("lifecycle_mode") or "SCAN").strip().upper() == "ARCHIVE_EXPIRED"
        result = run_evidence_lifecycle_automation(user=request.user, archive_expired=archive_mode)
        if archive_mode:
            messages.success(request, f"Evidence lifecycle automation archived {result['archived_count']} expired attachment(s).")
        else:
            messages.success(request, f"Evidence lifecycle scan complete: {result['expiring_soon_count']} expiring soon, {result['expired_count']} expired, {result['missing_retention_count']} missing retention.")
        query = _current_query()
        url = reverse("portfolios:stop_policy_followup")
        return redirect(f"{url}?{urlencode(query)}" if query else url)

    if request.method == "POST" and request.POST.get("save_bulk_retention_action") == "1":
        action = (request.POST.get("retention_action") or "").strip().lower()
        bulk_scope = (request.POST.get("bulk_scope") or "CURRENT_RESULTS").strip().upper()
        scoped_retention_filter = retention_filter
        if bulk_scope in {"ATTACHMENT", "EXPIRING_SOON", "EXPIRED", "MISSING_RETENTION"}:
            scoped_retention_filter = bulk_scope
        followup_bulk = summarize_stop_policy_followup_queue(
            user=request.user,
            account_label=account_filter,
            status_filter=status_filter,
            event_filter=event_filter,
            symbol_filter=symbol_filter,
            reason_filter=reason_filter,
            evidence_filter=evidence_filter,
            evidence_type_filter=evidence_type_filter,
            evidence_quality_filter=evidence_quality_filter,
            retention_filter=scoped_retention_filter,
            limit=300,
        )
        rows = [row for row in followup_bulk["rows"] if row.get("has_execution_evidence_attachment")]
        if action not in {"extend_90", "extend_365", "archive_clear"}:
            messages.error(request, "Unknown bulk retention action.")
        elif not rows:
            messages.warning(request, "No attachment-backed stop-policy rows matched that bulk retention scope.")
        else:
            success_count = 0
            error_count = 0
            last_error = ""
            for row in rows:
                ok, msg = _apply_retention_action_to_tx(row["tx"], action)
                if ok:
                    success_count += 1
                else:
                    error_count += 1
                    last_error = msg
            scope_labels = {
                "CURRENT_RESULTS": "current filtered attachment rows",
                "ATTACHMENT": "attachment-backed rows",
                "EXPIRING_SOON": "expiring-soon rows",
                "EXPIRED": "expired rows",
                "MISSING_RETENTION": "missing-retention rows",
            }
            action_labels = {
                "extend_90": "extended 90 days",
                "extend_365": "extended 365 days",
                "archive_clear": "archived / cleared",
            }
            if success_count:
                messages.success(request, f"Bulk retention update complete: {success_count} {scope_labels.get(bulk_scope, 'rows')} {action_labels.get(action, 'updated')}.")
            if error_count:
                messages.warning(request, f"{error_count} rows could not be updated. {last_error}".strip())
        query = _current_query()
        url = reverse("portfolios:stop_policy_followup")
        return redirect(f"{url}?{urlencode(query)}" if query else url)

    if request.method == "POST" and request.POST.get("save_stop_policy_note") == "1":
        note_form = StopPolicyResolutionNoteForm(request.POST, request.FILES)
        if note_form.is_valid():
            tx = get_object_or_404(
                HoldingTransaction,
                pk=note_form.cleaned_data["tx_id"],
                position__user=request.user,
                event_type__in={HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD},
            )
            tx.stop_policy_reason_code = (note_form.cleaned_data.get("reason_code") or "").strip()
            tx.stop_policy_note = (note_form.cleaned_data.get("note") or "").strip()
            evidence_type = (note_form.cleaned_data.get("evidence_type") or "").strip()
            evidence_quality = (note_form.cleaned_data.get("evidence_quality") or "").strip()
            evidence_reference = (note_form.cleaned_data.get("evidence_reference") or "").strip()
            evidence_note = (note_form.cleaned_data.get("evidence_note") or "").strip()
            evidence_attachment = note_form.cleaned_data.get("evidence_attachment")
            snapshot_id = note_form.cleaned_data.get("broker_confirmation_snapshot_id")
            run_id = note_form.cleaned_data.get("broker_confirmation_run_id")
            resolution_id = note_form.cleaned_data.get("broker_confirmation_resolution_id")
            update_fields = ["stop_policy_reason_code", "stop_policy_note"]
            if snapshot_id is not None or run_id is not None or resolution_id is not None:
                tx.broker_confirmation_snapshot = ImportedBrokerSnapshot.objects.filter(user=request.user, pk=snapshot_id).first() if snapshot_id else None
                tx.broker_confirmation_run = BrokerPositionImportRun.objects.filter(user=request.user, pk=run_id).first() if run_id else None
                tx.broker_confirmation_resolution = BrokerPositionImportResolution.objects.filter(user=request.user, pk=resolution_id).first() if resolution_id else None
                tx.broker_confirmation_linked_at = timezone.now() if (tx.broker_confirmation_snapshot or tx.broker_confirmation_run or tx.broker_confirmation_resolution) else None
                update_fields.extend(["broker_confirmation_snapshot", "broker_confirmation_run", "broker_confirmation_resolution", "broker_confirmation_linked_at"])
            if evidence_type or evidence_quality or evidence_reference or evidence_note or evidence_attachment:
                tx.execution_evidence_type = evidence_type
                tx.execution_evidence_quality = evidence_quality
                tx.execution_evidence_reference = evidence_reference
                tx.execution_evidence_note = evidence_note
                tx.execution_evidence_recorded_at = timezone.now()
                update_fields.extend([
                    "execution_evidence_type",
                    "execution_evidence_quality",
                    "execution_evidence_reference",
                    "execution_evidence_note",
                    "execution_evidence_recorded_at",
                ])
                if evidence_attachment:
                    tx.execution_evidence_attachment = evidence_attachment
                    risk_profile = UserRiskProfile.objects.filter(user=request.user).first() or UserRiskProfile(user=request.user)
                    retention_days, _ = resolve_evidence_retention_days(
                        risk_profile=risk_profile,
                        evidence_type=evidence_type,
                        evidence_quality=evidence_quality,
                        has_attachment=True,
                        user=request.user,
                        account_label=(tx.account_label_snapshot or tx.position.account_label or ""),
                    )
                    tx.execution_evidence_retention_until = timezone.now() + timedelta(days=retention_days or 365)
                    update_fields.extend(["execution_evidence_attachment", "execution_evidence_retention_until"])
                messages.success(request, f"Saved stop-policy audit note and execution evidence for {tx.position.instrument.symbol}.")
            else:
                messages.success(request, f"Saved stop-policy audit note for {tx.position.instrument.symbol}.")
            tx.save(update_fields=update_fields)
        else:
            messages.error(request, "Could not save the stop-policy audit note.")
        query = _current_query()
        url = reverse("portfolios:stop_policy_followup")
        return redirect(f"{url}?{urlencode(query)}" if query else url)

    account_options = []
    seen = set()
    saw_unlabeled = False
    for label in list(HeldPosition.objects.filter(user=request.user).values_list("account_label", flat=True)) + list(HoldingTransaction.objects.filter(position__user=request.user).values_list("account_label_snapshot", flat=True)):
        normalized = (label or "").strip()
        if not normalized:
            saw_unlabeled = True
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_options.append(normalized)
    account_options.sort(key=str.lower)
    if saw_unlabeled:
        account_options.insert(0, "__UNLABELED__")

    reason_filter_label = ""
    if reason_filter == "__UNSPECIFIED__":
        reason_filter_label = "No reason saved"
    elif reason_filter:
        choice_map = dict(HoldingTransaction.StopPolicyReasonCode.choices)
        reason_filter_label = choice_map.get(reason_filter, reason_filter.replace("_", " ").title())

    evidence_type_filter_label = ""
    if evidence_type_filter == "__NONE__":
        evidence_type_filter_label = "No evidence type saved"
    elif evidence_type_filter:
        evidence_choice_map = dict(HoldingTransaction.ExecutionEvidenceType.choices)
        evidence_type_filter_label = evidence_choice_map.get(evidence_type_filter, evidence_type_filter.replace("_", " ").title())

    evidence_quality_filter_label = ""
    if evidence_quality_filter == "__UNRATED__":
        evidence_quality_filter_label = "Unrated evidence"
    elif evidence_quality_filter:
        quality_choice_map = dict(HoldingTransaction.ExecutionEvidenceQuality.choices)
        evidence_quality_filter_label = quality_choice_map.get(evidence_quality_filter, evidence_quality_filter.replace("_", " ").title())

    retention_filter_label = ""
    retention_filter_map = {
        "ATTACHMENT": "Has attachment",
        "TEXT_ONLY": "Text-only evidence",
        "EXPIRING_SOON": "Expiring soon",
        "EXPIRED": "Expired",
        "MISSING_RETENTION": "Missing retention",
    }
    if retention_filter:
        retention_filter_label = retention_filter_map.get(retention_filter, retention_filter.replace("_", " ").title())

    followup = summarize_stop_policy_followup_queue(
        user=request.user,
        account_label=account_filter or "",
        status_filter=status_filter,
        event_filter=event_filter,
        symbol_filter=symbol_filter,
        reason_filter=reason_filter,
        evidence_filter=evidence_filter,
        evidence_type_filter=evidence_type_filter,
        evidence_quality_filter=evidence_quality_filter,
        retention_filter=retention_filter,
        limit=150,
    )
    stop_policy_timeliness = summarize_stop_policy_timeliness(user=request.user, account_label=account_filter or "")
    stop_policy_exception_trends = summarize_stop_policy_exception_trends(user=request.user, account_label=account_filter or "")
    risk_profile = UserRiskProfile.objects.filter(user=request.user).first() or UserRiskProfile(user=request.user)
    evidence_lifecycle_automation = summarize_evidence_lifecycle_automation(user=request.user)
    retention_presets = [
        {"label": "Default attachment", "days": risk_profile.evidence_retention_default_days},
        {"label": "Verified quality", "days": risk_profile.evidence_retention_verified_days},
        {"label": "Strong quality", "days": risk_profile.evidence_retention_strong_days},
        {"label": "Weak quality", "days": risk_profile.evidence_retention_weak_days},
        {"label": "Placeholder quality", "days": risk_profile.evidence_retention_placeholder_days},
        {"label": "Broker confirmation / order reference", "days": risk_profile.evidence_retention_confirmation_days},
        {"label": "Later broker/import match", "days": risk_profile.evidence_retention_import_match_days},
    ]
    return render(
        request,
        "portfolios/stop_policy_followup.html",
        {
            "account_filter": account_filter,
            "status_filter": status_filter,
            "event_filter": event_filter,
            "symbol_filter": symbol_filter,
            "reason_filter": reason_filter,
            "reason_filter_label": reason_filter_label,
            "evidence_type_filter_label": evidence_type_filter_label,
            "evidence_quality_filter_label": evidence_quality_filter_label,
            "retention_filter_label": retention_filter_label,
            "evidence_filter": evidence_filter,
            "evidence_type_filter": evidence_type_filter,
            "evidence_quality_filter": evidence_quality_filter,
            "retention_filter": retention_filter,
            "account_options": account_options,
            "followup": followup,
            "stop_policy_timeliness": stop_policy_timeliness,
            "stop_policy_exception_trends": stop_policy_exception_trends,
            "stop_policy_note_form": StopPolicyResolutionNoteForm(),
            "retention_presets": retention_presets,
            "evidence_lifecycle_automation": evidence_lifecycle_automation,
        },
    )

@login_required
def risk_settings(request):
    profile, _ = UserRiskProfile.objects.get_or_create(user=request.user)
    account_filter = (request.GET.get("account") or "").strip()
    account_override_id = (request.GET.get("edit_override") or "").strip()
    clone_source_id = (request.GET.get("clone_override") or "").strip()
    template_id = (request.GET.get("edit_template") or "").strip()
    template_from_override_id = (request.GET.get("template_from_override") or "").strip()
    apply_template_id = (request.GET.get("apply_template") or "").strip()
    apply_accounts_prefill = (request.GET.get("apply_accounts") or "").strip()

    override_instance = None
    if account_override_id.isdigit():
        override_instance = AccountRetentionPolicyOverride.objects.filter(user=request.user, pk=int(account_override_id)).first()

    template_instance = None
    if template_id.isdigit():
        template_instance = AccountRetentionPolicyTemplate.objects.filter(user=request.user, pk=int(template_id)).first()

    clone_initial = {}
    if clone_source_id.isdigit():
        source_override = AccountRetentionPolicyOverride.objects.filter(user=request.user, pk=int(clone_source_id)).first()
        if source_override:
            clone_initial["source_override"] = source_override

    template_apply_initial = {}
    if apply_template_id.isdigit():
        apply_template = AccountRetentionPolicyTemplate.objects.filter(user=request.user, pk=int(apply_template_id)).first()
        if apply_template:
            template_apply_initial["template"] = apply_template
    if apply_accounts_prefill:
        template_apply_initial["account_labels"] = apply_accounts_prefill.replace("|", "\n")

    template_initial = {}
    if template_from_override_id.isdigit():
        source_override = AccountRetentionPolicyOverride.objects.filter(user=request.user, pk=int(template_from_override_id)).first()
        if source_override:
            template_initial.update({
                "family_label": source_override.account_label,
                "template_name": f"{source_override.account_label} template",
                "evidence_retention_default_days": source_override.evidence_retention_default_days,
                "evidence_retention_verified_days": source_override.evidence_retention_verified_days,
                "evidence_retention_strong_days": source_override.evidence_retention_strong_days,
                "evidence_retention_weak_days": source_override.evidence_retention_weak_days,
                "evidence_retention_placeholder_days": source_override.evidence_retention_placeholder_days,
                "evidence_retention_confirmation_days": source_override.evidence_retention_confirmation_days,
                "evidence_retention_import_match_days": source_override.evidence_retention_import_match_days,
            })

    if request.method == "POST" and request.POST.get("save_broker_snapshot") == "1":
        snapshot_form = BrokerSnapshotForm(request.POST)
        form = UserRiskProfileForm(instance=profile)
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        if snapshot_form.is_valid():
            snapshot = snapshot_form.save(commit=False)
            snapshot.user = request.user
            snapshot.save()
            messages.success(request, "Saved broker/account snapshot for reconciliation posture.")
            return redirect("portfolios:risk_settings")
    elif request.method == "POST" and request.POST.get("save_account_retention_override") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_pk = (request.POST.get("override_id") or "").strip()
        override_target = AccountRetentionPolicyOverride.objects.filter(user=request.user, pk=override_pk).first() if override_pk.isdigit() else None
        override_form = AccountRetentionPolicyOverrideForm(request.POST, instance=override_target)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        if override_form.is_valid():
            override = override_form.save(commit=False)
            override.user = request.user
            override.save()
            messages.success(request, f"Saved per-account retention override for {override.account_label}.")
            return redirect("portfolios:risk_settings")
    elif request.method == "POST" and request.POST.get("clone_account_retention_override") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(request.POST, initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        if clone_form.is_valid():
            source_override = clone_form.cleaned_data["source_override"]
            target_account_label = clone_form.cleaned_data["target_account_label"]
            overwrite_existing = bool(clone_form.cleaned_data.get("overwrite_existing"))
            target_override = AccountRetentionPolicyOverride.objects.filter(user=request.user, account_label__iexact=target_account_label).first()
            created_new = target_override is None
            if target_override is None:
                target_override = AccountRetentionPolicyOverride(user=request.user, account_label=target_account_label)
            source_fields = (
                "evidence_retention_default_days",
                "evidence_retention_verified_days",
                "evidence_retention_strong_days",
                "evidence_retention_weak_days",
                "evidence_retention_placeholder_days",
                "evidence_retention_confirmation_days",
                "evidence_retention_import_match_days",
            )
            for field_name in source_fields:
                setattr(target_override, field_name, getattr(source_override, field_name, None))
            target_override.account_label = target_account_label
            target_override.user = request.user
            target_override.source_template = source_override.source_template
            target_override.save()
            action_label = "created" if created_new else "updated"
            messages.success(request, f"Copied retention override from {source_override.account_label} to {target_account_label} ({action_label}).")
            return redirect("portfolios:risk_settings")
        messages.error(request, "Could not copy that account retention override. Review the highlighted fields.")
    elif request.method == "POST" and request.POST.get("delete_account_retention_override") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        override_pk = (request.POST.get("override_id") or "").strip()
        override_target = AccountRetentionPolicyOverride.objects.filter(user=request.user, pk=override_pk).first() if override_pk.isdigit() else None
        if override_target:
            label = override_target.account_label
            override_target.delete()
            messages.success(request, f"Removed per-account retention override for {label}.")
            return redirect("portfolios:risk_settings")
        messages.error(request, "Could not find that account retention override.")
    elif request.method == "POST" and request.POST.get("save_account_retention_template") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_pk = (request.POST.get("template_id") or "").strip()
        template_target = AccountRetentionPolicyTemplate.objects.filter(user=request.user, pk=template_pk).first() if template_pk.isdigit() else None
        template_form = AccountRetentionPolicyTemplateForm(request.POST, instance=template_target, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        if template_form.is_valid():
            template = template_form.save(commit=False)
            template.user = request.user
            template.save()
            messages.success(request, f"Saved retention template {template.template_name}.")
            return redirect("portfolios:risk_settings")
    elif request.method == "POST" and request.POST.get("apply_account_retention_template") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(request.POST, initial=template_apply_initial, user=request.user)
        if template_apply_form.is_valid():
            source_template = template_apply_form.cleaned_data["template"]
            overwrite_existing = bool(template_apply_form.cleaned_data.get("overwrite_existing"))
            labels = template_apply_form.cleaned_data["account_labels"]
            source_fields = (
                "evidence_retention_default_days",
                "evidence_retention_verified_days",
                "evidence_retention_strong_days",
                "evidence_retention_weak_days",
                "evidence_retention_placeholder_days",
                "evidence_retention_confirmation_days",
                "evidence_retention_import_match_days",
            )
            applied_count = 0
            for target_account_label in labels:
                target_override = AccountRetentionPolicyOverride.objects.filter(user=request.user, account_label__iexact=target_account_label).first()
                if target_override is None:
                    target_override = AccountRetentionPolicyOverride(user=request.user, account_label=target_account_label)
                elif not overwrite_existing:
                    continue
                for field_name in source_fields:
                    setattr(target_override, field_name, getattr(source_template, field_name, None))
                target_override.account_label = target_account_label
                target_override.user = request.user
                target_override.source_template = source_template
                target_override.save()
                applied_count += 1
            messages.success(request, f"Applied template {source_template.template_name} to {applied_count} account override(s).")
            return redirect("portfolios:risk_settings")
    elif request.method == "POST" and request.POST.get("delete_account_retention_template") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        template_pk = (request.POST.get("template_id") or "").strip()
        template_target = AccountRetentionPolicyTemplate.objects.filter(user=request.user, pk=template_pk).first() if template_pk.isdigit() else None
        if template_target:
            label = template_target.template_name
            template_target.delete()
            messages.success(request, f"Removed retention template {label}.")
            return redirect("portfolios:risk_settings")
        messages.error(request, "Could not find that retention template.")
    elif request.method == "POST" and request.POST.get("reset_account_retention_override_to_template") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        override_pk = (request.POST.get("override_id") or "").strip()
        override_target = AccountRetentionPolicyOverride.objects.select_related("source_template").filter(user=request.user, pk=override_pk).first() if override_pk.isdigit() else None
        if override_target and override_target.source_template:
            source_fields = (
                "evidence_retention_default_days",
                "evidence_retention_verified_days",
                "evidence_retention_strong_days",
                "evidence_retention_weak_days",
                "evidence_retention_placeholder_days",
                "evidence_retention_confirmation_days",
                "evidence_retention_import_match_days",
            )
            for field_name in source_fields:
                setattr(override_target, field_name, getattr(override_target.source_template, field_name, None))
            override_target.save(update_fields=[*source_fields, "updated_at"])
            messages.success(request, f"Reset {override_target.account_label} back to template {override_target.source_template.template_name}.")
            return redirect("portfolios:risk_settings")
        messages.error(request, "Could not reset that override because no source template is linked.")
    elif request.method == "POST" and request.POST.get("detach_account_retention_override_template") == "1":
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        override_pk = (request.POST.get("override_id") or "").strip()
        override_target = AccountRetentionPolicyOverride.objects.select_related("source_template").filter(user=request.user, pk=override_pk).first() if override_pk.isdigit() else None
        if override_target and override_target.source_template:
            template_name = override_target.source_template.template_name
            override_target.source_template = None
            override_target.save(update_fields=["source_template", "updated_at"])
            messages.success(request, f"Detached {override_target.account_label} from template {template_name}. Existing override windows were kept.")
            return redirect("portfolios:risk_settings")
        messages.error(request, "Could not detach template lineage for that override.")

    elif request.method == "POST":
        form = UserRiskProfileForm(request.POST, instance=profile)
        snapshot_form = BrokerSnapshotForm()
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Updated allocation controls.")
            return redirect("portfolios:risk_settings")
    else:
        form = UserRiskProfileForm(instance=profile)
        snapshot_form = BrokerSnapshotForm(initial={"source_label": "Broker CSV", "as_of": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        override_form = AccountRetentionPolicyOverrideForm(instance=override_instance)
        clone_form = AccountRetentionPolicyOverrideCloneForm(initial=clone_initial, user=request.user)
        template_form = AccountRetentionPolicyTemplateForm(instance=template_instance, initial=template_initial)
        template_apply_form = AccountRetentionPolicyTemplateApplyForm(initial=template_apply_initial, user=request.user)

    exposure_account_label = account_filter or ""
    exposure = summarize_portfolio_exposure(user=request.user, account_label=exposure_account_label)
    broker_posture = summarize_broker_snapshot_posture(user=request.user, account_label=exposure_account_label)
    recent_snapshots_qs = ImportedBrokerSnapshot.objects.filter(user=request.user)
    recent_broker_runs_qs = BrokerPositionImportRun.objects.filter(user=request.user)
    if account_filter == "__UNLABELED__":
        recent_snapshots_qs = recent_snapshots_qs.filter(account_label="")
        recent_broker_runs_qs = recent_broker_runs_qs.filter(account_label="")
    elif account_filter:
        recent_snapshots_qs = recent_snapshots_qs.filter(account_label__iexact=account_filter)
        recent_broker_runs_qs = recent_broker_runs_qs.filter(account_label__iexact=account_filter)
    recent_snapshots = list(recent_snapshots_qs.order_by("-as_of", "-id")[:5])
    recent_broker_runs = list(recent_broker_runs_qs.order_by("-created_at", "-id")[:5])
    account_options = []
    seen = set()
    saw_unlabeled = False
    for label in list(ImportedBrokerSnapshot.objects.filter(user=request.user).values_list("account_label", flat=True)) + list(BrokerPositionImportRun.objects.filter(user=request.user).values_list("account_label", flat=True)) + list(HeldPosition.objects.filter(user=request.user).values_list("account_label", flat=True)):
        normalized = (label or "").strip()
        if not normalized:
            saw_unlabeled = True
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_options.append(normalized)
    account_options.sort(key=str.lower)
    if saw_unlabeled:
        account_options.insert(0, "__UNLABELED__")
    account_risk_posture = summarize_account_risk_posture(user=request.user)
    account_exposure_heatmap = summarize_account_exposure_heatmap(user=request.user)
    account_drawdown_monitoring = summarize_account_drawdown_monitoring(user=request.user)
    account_stop_guardrails = summarize_account_stop_guardrails(user=request.user)
    account_holding_queues = summarize_account_holding_queues(user=request.user)
    holding_risk_guardrails = summarize_holding_risk_guardrails(user=request.user, account_label=exposure_account_label)
    stop_discipline_history = summarize_stop_discipline_history(user=request.user, account_label=exposure_account_label)
    stop_discipline_trends = summarize_stop_discipline_trends(user=request.user, account_label=exposure_account_label)
    stop_policy_timeliness = summarize_stop_policy_timeliness(user=request.user, account_label=exposure_account_label)
    stop_policy_exception_trends = summarize_stop_policy_exception_trends(user=request.user, account_label=exposure_account_label)
    account_retention_overrides = summarize_account_retention_overrides(user=request.user)
    account_retention_template_recommendations = summarize_account_retention_template_recommendations(user=request.user)
    account_retention_templates = summarize_account_retention_templates(user=request.user)
    account_retention_template_drift = summarize_account_retention_template_drift(user=request.user)
    evidence_lifecycle_automation = summarize_evidence_lifecycle_automation(user=request.user)
    return render(
        request,
        "portfolios/risk_settings.html",
        {
            "form": form,
            "snapshot_form": snapshot_form,
            "profile": profile,
            "exposure": exposure,
            "broker_posture": broker_posture,
            "recent_snapshots": recent_snapshots,
            "recent_broker_runs": recent_broker_runs,
            "account_filter": account_filter,
            "account_options": account_options,
            "account_risk_posture": account_risk_posture,
            "account_exposure_heatmap": account_exposure_heatmap,
            "account_drawdown_monitoring": account_drawdown_monitoring,
            "account_stop_guardrails": account_stop_guardrails,
            "account_holding_queues": account_holding_queues,
            "holding_risk_guardrails": holding_risk_guardrails,
            "stop_discipline_history": stop_discipline_history,
            "stop_discipline_trends": stop_discipline_trends,
            "stop_policy_timeliness": stop_policy_timeliness,
            "stop_policy_exception_trends": stop_policy_exception_trends,
            "account_retention_overrides": account_retention_overrides,
            "account_retention_template_recommendations": account_retention_template_recommendations,
            "account_retention_templates": account_retention_templates,
            "account_retention_template_drift": account_retention_template_drift,
            "evidence_lifecycle_automation": evidence_lifecycle_automation,
            "account_retention_override_form": override_form,
            "account_retention_override_clone_form": clone_form,
            "account_retention_template_form": template_form,
            "account_retention_template_apply_form": template_apply_form,
            "editing_account_retention_override": override_instance,
            "editing_account_retention_template": template_instance,
        },
    )


    exposure_account_label = account_filter or ""
    exposure = summarize_portfolio_exposure(user=request.user, account_label=exposure_account_label)
    broker_posture = summarize_broker_snapshot_posture(user=request.user, account_label=exposure_account_label)
    recent_snapshots_qs = ImportedBrokerSnapshot.objects.filter(user=request.user)
    recent_broker_runs_qs = BrokerPositionImportRun.objects.filter(user=request.user)
    if account_filter == "__UNLABELED__":
        recent_snapshots_qs = recent_snapshots_qs.filter(account_label="")
        recent_broker_runs_qs = recent_broker_runs_qs.filter(account_label="")
    elif account_filter:
        recent_snapshots_qs = recent_snapshots_qs.filter(account_label__iexact=account_filter)
        recent_broker_runs_qs = recent_broker_runs_qs.filter(account_label__iexact=account_filter)
    recent_snapshots = list(recent_snapshots_qs.order_by("-as_of", "-id")[:5])
    recent_broker_runs = list(recent_broker_runs_qs.order_by("-created_at", "-id")[:5])
    account_options = []
    seen = set()
    saw_unlabeled = False
    for label in list(ImportedBrokerSnapshot.objects.filter(user=request.user).values_list("account_label", flat=True)) + list(BrokerPositionImportRun.objects.filter(user=request.user).values_list("account_label", flat=True)) + list(HeldPosition.objects.filter(user=request.user).values_list("account_label", flat=True)):
        normalized = (label or "").strip()
        if not normalized:
            saw_unlabeled = True
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_options.append(normalized)
    account_options.sort(key=str.lower)
    if saw_unlabeled:
        account_options.insert(0, "__UNLABELED__")
    account_risk_posture = summarize_account_risk_posture(user=request.user)
    account_exposure_heatmap = summarize_account_exposure_heatmap(user=request.user)
    account_drawdown_monitoring = summarize_account_drawdown_monitoring(user=request.user)
    account_stop_guardrails = summarize_account_stop_guardrails(user=request.user)
    account_holding_queues = summarize_account_holding_queues(user=request.user)
    holding_risk_guardrails = summarize_holding_risk_guardrails(user=request.user, account_label=exposure_account_label)
    stop_discipline_history = summarize_stop_discipline_history(user=request.user, account_label=exposure_account_label)
    stop_discipline_trends = summarize_stop_discipline_trends(user=request.user, account_label=exposure_account_label)
    stop_policy_timeliness = summarize_stop_policy_timeliness(user=request.user, account_label=exposure_account_label)
    stop_policy_exception_trends = summarize_stop_policy_exception_trends(user=request.user, account_label=exposure_account_label)
    account_retention_overrides = summarize_account_retention_overrides(user=request.user)
    account_retention_template_recommendations = summarize_account_retention_template_recommendations(user=request.user)
    account_retention_templates = summarize_account_retention_templates(user=request.user)
    account_retention_template_drift = summarize_account_retention_template_drift(user=request.user)
    return render(
        request,
        "portfolios/risk_settings.html",
        {
            "form": form,
            "snapshot_form": snapshot_form,
            "profile": profile,
            "exposure": exposure,
            "broker_posture": broker_posture,
            "recent_snapshots": recent_snapshots,
            "recent_broker_runs": recent_broker_runs,
            "account_filter": account_filter,
            "account_options": account_options,
            "account_risk_posture": account_risk_posture,
            "account_exposure_heatmap": account_exposure_heatmap,
            "account_drawdown_monitoring": account_drawdown_monitoring,
            "account_stop_guardrails": account_stop_guardrails,
            "account_holding_queues": account_holding_queues,
            "holding_risk_guardrails": holding_risk_guardrails,
            "stop_discipline_history": stop_discipline_history,
            "stop_discipline_trends": stop_discipline_trends,
            "stop_policy_timeliness": stop_policy_timeliness,
            "stop_policy_exception_trends": stop_policy_exception_trends,
            "account_retention_overrides": account_retention_overrides,
            "account_retention_template_recommendations": account_retention_template_recommendations,
            "account_retention_templates": account_retention_templates,
            "account_retention_template_drift": account_retention_template_drift,
            "account_retention_override_form": override_form,
            "account_retention_override_clone_form": clone_form,
            "account_retention_template_form": template_form,
            "account_retention_template_apply_form": template_apply_form,
            "editing_account_retention_override": override_instance,
            "editing_account_retention_template": template_instance,
        },
    )
