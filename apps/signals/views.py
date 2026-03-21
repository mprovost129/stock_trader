from decimal import Decimal, InvalidOperation

from django.db import models
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseForbidden
from django.urls import reverse
from django.utils.http import urlencode
from django.shortcuts import get_object_or_404, redirect, render

from apps.marketdata.models import PriceBar
from apps.portfolios.forms import SavedFilterPresetForm
from apps.portfolios.models import HeldPosition, SavedFilterPreset, UserRiskProfile
from apps.portfolios.watchlists import active_watchlist_instrument_ids
from apps.portfolios.services import assess_signal_guardrails, build_signal_correlation_context, summarize_holding_sector_exposure, summarize_portfolio_exposure

from .models import PaperTrade, PositionAlert, Signal, SignalOutcome
from .services.alerts import explain_alert_eligibility
from .services.lifecycle import sync_trade_lifecycle
from .services.paper_trading import close_paper_trade, open_paper_trade_from_signal


SIGNAL_FILTER_FIELDS = (
    "status",
    "strategy",
    "instrument",
    "direction",
    "timeframe",
    "ownership_state",
    "outcome_status",
    "review_queue",
    "min_price",
    "max_price",
    "min_score",
    "max_score",
)


def _clean_filter_value(value):
    value = (value or "").strip()
    return value


def _extract_signal_filter_params(source):
    filters = {}
    for field in SIGNAL_FILTER_FIELDS:
        value = _clean_filter_value(source.get(field))
        if value:
            filters[field] = value
    return filters


def _signal_filter_querystring(filters: dict) -> str:
    clean_filters = {key: value for key, value in filters.items() if value not in (None, "")}
    return urlencode(clean_filters)




def _parse_optional_decimal(raw_value: str | None):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None, None
    try:
        return Decimal(raw_value), None
    except InvalidOperation:
        return None, f"Invalid numeric value: {raw_value}"


@login_required
def list_signals(request):
    preset_id = (request.GET.get("preset") or "").strip()
    active_preset = None
    if preset_id.isdigit():
        active_preset = SavedFilterPreset.objects.filter(
            pk=int(preset_id),
            user=request.user,
            scope=SavedFilterPreset.Scope.SIGNALS,
        ).first()
        if active_preset:
            query = request.GET.copy()
            changed = False
            for key, value in active_preset.filters.items():
                if not query.get(key):
                    query[key] = str(value)
                    changed = True
            if changed:
                redirect_url = reverse("signals:list")
                query_string = query.urlencode()
                return redirect(f"{redirect_url}?{query_string}")

    latest_close_subquery = Subquery(
        PriceBar.objects.filter(
            instrument_id=OuterRef("instrument_id"),
            timeframe=OuterRef("timeframe"),
        )
        .order_by("-ts")
        .values("close")[:1]
    )
    user_signals = Signal.objects.filter(created_by=request.user)
    qs = (
        user_signals.select_related("instrument", "strategy")
        .annotate(display_price=Coalesce("trade_plan__entry_price", latest_close_subquery))
        .order_by("-generated_at")
    )

    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)

    strategy = request.GET.get("strategy")
    if strategy:
        qs = qs.filter(strategy__slug=strategy)

    instrument = request.GET.get("instrument")
    if instrument:
        qs = qs.filter(instrument__symbol=instrument)

    direction = request.GET.get("direction")
    if direction:
        qs = qs.filter(direction=direction)

    timeframe = request.GET.get("timeframe")
    if timeframe:
        qs = qs.filter(timeframe=timeframe)

    ownership_state = request.GET.get("ownership_state")
    watchlist_instrument_ids = active_watchlist_instrument_ids(request.user)
    held_instrument_ids = list(
        HeldPosition.objects.filter(user=request.user, status=HeldPosition.Status.OPEN).values_list("instrument_id", flat=True).distinct()
    )
    if ownership_state == "HELD_OPEN":
        qs = qs.filter(instrument_id__in=held_instrument_ids)
    elif ownership_state == "NOT_HELD":
        qs = qs.exclude(instrument_id__in=held_instrument_ids)

    outcome_status = request.GET.get("outcome_status")
    if outcome_status:
        qs = qs.filter(outcome__status=outcome_status)

    review_queue = request.GET.get("review_queue")
    if review_queue:
        qs = qs.filter(status=Signal.Status.NEW).exclude(outcome__status=SignalOutcome.Status.EVALUATED)

    min_price_raw = request.GET.get("min_price")
    max_price_raw = request.GET.get("max_price")
    min_price, min_error = _parse_optional_decimal(min_price_raw)
    max_price, max_error = _parse_optional_decimal(max_price_raw)

    min_score_raw = request.GET.get("min_score")
    max_score_raw = request.GET.get("max_score")
    min_score, min_score_error = _parse_optional_decimal(min_score_raw)
    max_score, max_score_error = _parse_optional_decimal(max_score_raw)

    filter_error = min_error or max_error or min_score_error or max_score_error
    if min_price is not None:
        qs = qs.filter(display_price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(display_price__lte=max_price)
    if min_score is not None:
        qs = qs.filter(score__gte=min_score)
    if max_score is not None:
        qs = qs.filter(score__lte=max_score)

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    signals = list(page_obj)
    risk_profile = UserRiskProfile.objects.filter(user=request.user).first()
    account_equity = Decimal(risk_profile.account_equity) if risk_profile and risk_profile.account_equity is not None else None
    risk_pct = Decimal(risk_profile.risk_per_trade_pct) if risk_profile and risk_profile.risk_per_trade_pct is not None else None
    portfolio_exposure = summarize_portfolio_exposure(user=request.user)
    sector_exposure = summarize_holding_sector_exposure(user=request.user)
    total_held_market_value = Decimal(portfolio_exposure.get("total_market_value") or 0).quantize(Decimal("0.01"))
    cash_headroom = portfolio_exposure.get("cash_headroom")

    score_summary = {
        "high_conviction": user_signals.exclude(direction=Signal.Direction.FLAT).filter(status=Signal.Status.NEW, score__gte=80).count(),
        "review_zone": user_signals.exclude(direction=Signal.Direction.FLAT).filter(status=Signal.Status.NEW, score__gte=60, score__lt=80).count(),
        "below_review": user_signals.exclude(direction=Signal.Direction.FLAT).filter(status=Signal.Status.NEW).filter(models.Q(score__lt=60) | models.Q(score__isnull=True)).count(),
    }

    allocation_preview = {}
    correlation_context = build_signal_correlation_context(user=request.user, risk_profile=risk_profile)
    guardrail_summary = {"OK": 0, "NEAR": 0, "OVER": 0, "NO_PROFILE": 0, "NO_PLAN": 0}
    for signal in signals:
        plan = getattr(signal, "trade_plan", None)
        entry_price = getattr(signal, "display_price", None)
        suggested_qty = getattr(plan, "suggested_qty", None) if plan else None
        suggested_cost = None
        suggested_weight_pct = None
        fits_headroom = None
        if entry_price is not None and suggested_qty:
            suggested_cost = (Decimal(entry_price) * Decimal(suggested_qty)).quantize(Decimal("0.01"))
            if account_equity and account_equity > 0:
                suggested_weight_pct = ((suggested_cost / account_equity) * Decimal("100")).quantize(Decimal("0.01"))
            if cash_headroom is not None:
                fits_headroom = suggested_cost <= cash_headroom
        guardrails = assess_signal_guardrails(
            user=request.user,
            signal=signal,
            entry_price=entry_price,
            suggested_qty=suggested_qty,
            portfolio_exposure=portfolio_exposure,
            sector_exposure=sector_exposure,
            correlation_context=correlation_context,
        )
        guardrail_summary[guardrails["overall_posture"]] = guardrail_summary.get(guardrails["overall_posture"], 0) + 1
        allocation_preview[signal.pk] = {
            "suggested_qty": suggested_qty,
            "suggested_cost": suggested_cost,
            "suggested_weight_pct": suggested_weight_pct,
            "fits_headroom": fits_headroom,
            "guardrails": guardrails,
        }
    guardrail_summary["MISSING"] = guardrail_summary.get("NO_PROFILE", 0) + guardrail_summary.get("NO_PLAN", 0)
    timeframe_choices = list(user_signals.order_by().values_list("timeframe", flat=True).distinct())
    current_filters = _extract_signal_filter_params(request.GET)
    saved_presets = list(
        SavedFilterPreset.objects.filter(user=request.user, scope=SavedFilterPreset.Scope.SIGNALS)
    )
    saved_preset_form = SavedFilterPresetForm()
    return render(
        request,
        "signals/list.html",
        {
            "signals": signals,
            "page_obj": page_obj,
            "outcome_status": outcome_status,
            "review_queue": bool(review_queue),
            "min_price": min_price_raw or "",
            "max_price": max_price_raw or "",
            "min_score": min_score_raw or "",
            "max_score": max_score_raw or "",
            "direction": direction or "",
            "timeframe": timeframe or "",
            "ownership_state": ownership_state or "",
            "filter_error": filter_error,
            "score_summary": score_summary,
            "direction_choices": Signal.Direction.choices,
            "timeframe_choices": timeframe_choices,
            "current_filters": current_filters,
            "current_filter_querystring": _signal_filter_querystring(current_filters),
            "saved_presets": saved_presets,
            "saved_preset_form": saved_preset_form,
            "active_preset": active_preset,
            "allocation_preview": allocation_preview,
            "risk_profile": risk_profile,
            "account_equity": account_equity,
            "risk_pct": risk_pct,
            "cash_headroom": cash_headroom,
            "total_held_market_value": total_held_market_value.quantize(Decimal("0.01")),
            "guardrail_summary": guardrail_summary,
            "portfolio_exposure": portfolio_exposure,
            "sector_exposure": sector_exposure,
            "watchlist_instrument_ids": watchlist_instrument_ids,
        },
    )


@login_required
def save_filter_preset(request):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    form = SavedFilterPresetForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a preset name before saving.")
        redirect_query = _signal_filter_querystring(_extract_signal_filter_params(request.POST))
        if redirect_query:
            return redirect(f"{reverse('signals:list')}?{redirect_query}")
        return redirect("signals:list")

    filters = _extract_signal_filter_params(request.POST)
    if not filters:
        messages.error(request, "Choose at least one filter before saving a preset.")
        return redirect("signals:list")

    preset, created = SavedFilterPreset.objects.update_or_create(
        user=request.user,
        scope=SavedFilterPreset.Scope.SIGNALS,
        name=form.cleaned_data["name"].strip(),
        defaults={
            "filters": filters,
            "is_dashboard_widget": form.cleaned_data.get("pin_to_dashboard", False),
        },
    )
    messages.success(request, f"{'Created' if created else 'Updated'} signal preset '{preset.name}'.")
    redirect_query = _signal_filter_querystring(filters)
    return redirect(f"{reverse('signals:list')}?{redirect_query}")


@login_required
def toggle_filter_preset_dashboard(request, pk: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    preset = get_object_or_404(
        SavedFilterPreset,
        pk=pk,
        user=request.user,
        scope=SavedFilterPreset.Scope.SIGNALS,
    )
    preset.is_dashboard_widget = not preset.is_dashboard_widget
    preset.save(update_fields=["is_dashboard_widget", "updated_at"])
    state = "Pinned" if preset.is_dashboard_widget else "Unpinned"
    messages.success(request, f"{state} signal preset '{preset.name}' {'to' if preset.is_dashboard_widget else 'from'} the dashboard.")
    return redirect("signals:list")


@login_required
def delete_filter_preset(request, pk: int):
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    preset = get_object_or_404(
        SavedFilterPreset,
        pk=pk,
        user=request.user,
        scope=SavedFilterPreset.Scope.SIGNALS,
    )
    preset_name = preset.name
    preset.delete()
    messages.success(request, f"Deleted signal preset '{preset_name}'.")
    return redirect("signals:list")


@login_required
def detail(request, pk: int):
    try:
        signal = Signal.objects.select_related("instrument", "strategy").prefetch_related(
            "alert_deliveries",
            "journal_entries",
        ).get(pk=pk)
    except Signal.DoesNotExist as exc:
        raise Http404 from exc

    latest_journal = signal.journal_entries.order_by("-decided_at").first()
    alert_deliveries = signal.alert_deliveries.order_by("-created_at")[:10]
    outcome = getattr(signal, "outcome", None)
    alert_explanation = explain_alert_eligibility(signal=signal) if hasattr(signal, "trade_plan") else None
    paper_trade = getattr(signal, "paper_trade", None)
    position_alerts = paper_trade.position_alerts.order_by("-created_at")[:10] if paper_trade else []
    lifecycle_snapshot = None

    checklist = [
        ("Plan has entry / stop / targets", bool(getattr(signal, "trade_plan", None))),
        ("Signal is still NEW or awaiting review", signal.status in {Signal.Status.NEW, Signal.Status.REVIEWED, Signal.Status.TAKEN}),
        ("Operator reviewed rationale and timeframe", bool(signal.rationale)),
        ("Journal decision recorded", latest_journal is not None),
    ]

    watchlist_instrument_ids = active_watchlist_instrument_ids(request.user)
    return render(
        request,
        "signals/detail.html",
        {
            "signal": signal,
            "latest_journal": latest_journal,
            "alert_deliveries": alert_deliveries,
            "checklist": checklist,
            "outcome": outcome,
            "alert_explanation": alert_explanation,
            "paper_trade": paper_trade,
            "position_alerts": position_alerts,
            "lifecycle_snapshot": lifecycle_snapshot,
            "in_watchlist": signal.instrument_id in watchlist_instrument_ids,
        },
    )


@login_required
def mark_reviewed(request, pk: int):
    signal = get_object_or_404(Signal, pk=pk)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    signal.status = Signal.Status.REVIEWED
    signal.save(update_fields=["status"])
    messages.success(request, f"Marked {signal.instrument.symbol} as reviewed.")
    return redirect("signals:detail", pk=pk)


@login_required
def skip_signal(request, pk: int):
    signal = get_object_or_404(Signal, pk=pk)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    signal.status = Signal.Status.SKIPPED
    signal.save(update_fields=["status"])
    messages.success(request, f"Marked {signal.instrument.symbol} as skipped.")
    return redirect("signals:detail", pk=pk)


@login_required
def open_paper_trade_view(request, pk: int):
    signal = get_object_or_404(Signal, pk=pk)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    result = open_paper_trade_from_signal(signal=signal, user=request.user, notes=request.POST.get("notes", ""))
    if result.created:
        messages.success(request, f"Opened paper trade for {signal.instrument.symbol}.")
    else:
        messages.info(request, f"Paper trade already exists for {signal.instrument.symbol}.")
    return redirect("signals:detail", pk=pk)


@login_required
def close_paper_trade_view(request, trade_id: int):
    trade = get_object_or_404(PaperTrade.objects.select_related("signal", "signal__instrument"), pk=trade_id)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    exit_price = None
    raw_exit = (request.POST.get("exit_price") or "").strip()
    if raw_exit:
        try:
            exit_price = Decimal(raw_exit)
        except InvalidOperation:
            messages.error(request, "Invalid exit price.")
            return redirect("signals:detail", pk=trade.signal_id)
    try:
        result = close_paper_trade(
            trade=trade,
            exit_price=exit_price,
            notes=request.POST.get("notes", ""),
            closed_reason=request.POST.get("closed_reason") or PaperTrade.ClosedReason.MANUAL,
        )
        messages.success(request, f"Closed paper trade for {trade.signal.instrument.symbol}. PnL {result.realized_pnl_amount} ({result.realized_pnl_pct}%).")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("signals:detail", pk=trade.signal_id)


@login_required
def sync_paper_trade_view(request, trade_id: int):
    trade = get_object_or_404(PaperTrade.objects.select_related("signal", "signal__instrument"), pk=trade_id)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")
    result = sync_trade_lifecycle(trade)
    if result.changed:
        messages.success(request, f"Synced {trade.signal.instrument.symbol}: {result.headline}.")
    else:
        messages.info(request, f"No lifecycle change for {trade.signal.instrument.symbol}: {result.headline}.")
    return redirect("signals:detail", pk=trade.signal_id)


@login_required
def update_paper_trade_management_view(request, trade_id: int):
    trade = get_object_or_404(PaperTrade.objects.select_related("signal", "signal__instrument"), pk=trade_id)
    if request.method != "POST":
        return HttpResponseForbidden("POST required")

    changed_fields: list[str] = []

    def _parse_decimal(name: str):
        raw = (request.POST.get(name) or "").strip()
        if not raw:
            return None, False
        try:
            return Decimal(raw), True
        except InvalidOperation:
            raise ValueError(f"Invalid value for {name.replace('_', ' ')}.")

    try:
        active_stop, has_stop = _parse_decimal("active_stop_price")
        active_target, has_target = _parse_decimal("active_target_price")
        trailing_stop, has_trailing = _parse_decimal("trailing_stop_pct")
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("signals:detail", pk=trade.signal_id)

    if has_stop and trade.active_stop_price != active_stop:
        trade.active_stop_price = active_stop
        changed_fields.append("active_stop_price")
    if has_target and trade.active_target_price != active_target:
        trade.active_target_price = active_target
        changed_fields.append("active_target_price")
    if has_trailing and trade.trailing_stop_pct != trailing_stop:
        trade.trailing_stop_pct = trailing_stop
        changed_fields.append("trailing_stop_pct")

    notes = (request.POST.get("management_note") or "").strip()
    if notes:
        trade.notes = ((trade.notes or "") + "\n" + f"[management] {notes}").strip()
        changed_fields.append("notes")

    if changed_fields:
        trade.save(update_fields=sorted(set(changed_fields + ["updated_at"])))
        messages.success(request, f"Updated paper-trade management for {trade.signal.instrument.symbol}.")
    else:
        messages.info(request, f"No paper-trade management changes detected for {trade.signal.instrument.symbol}.")

    return redirect("signals:detail", pk=trade.signal_id)
