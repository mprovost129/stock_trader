from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.signals.models import Signal

from .forms import JournalEntryForm
from .models import JournalEntry


@login_required
def list_entries(request):
    entries_qs = (
        JournalEntry.objects.select_related("signal", "signal__instrument", "signal__strategy")
        .filter(user=request.user)
        .order_by("-decided_at")
    )

    decision = (request.GET.get("decision") or "").strip().upper()
    if decision:
        entries_qs = entries_qs.filter(decision=decision)

    outcome = (request.GET.get("outcome") or "").strip().upper()
    if outcome:
        entries_qs = entries_qs.filter(outcome=outcome)

    tag = (request.GET.get("tag") or "").strip()
    if tag:
        entries_qs = entries_qs.filter(tags__icontains=tag)

    raw_stats = JournalEntry.objects.filter(user=request.user).aggregate(
        total=Count("id"),
        yes=Count("id", filter=Q(decision=JournalEntry.Decision.YES)),
        no=Count("id", filter=Q(decision=JournalEntry.Decision.NO)),
        wins=Count("id", filter=Q(outcome=JournalEntry.Outcome.WIN)),
        losses=Count("id", filter=Q(outcome=JournalEntry.Outcome.LOSS)),
        known_outcomes=Count("id", filter=~Q(outcome=JournalEntry.Outcome.UNKNOWN)),
    )
    stats = dict(raw_stats)
    known = stats.get("known_outcomes") or 0
    wins = stats.get("wins") or 0
    stats["win_rate"] = round((wins / known) * 100, 1) if known else None

    entries = entries_qs[:200]
    return render(
        request,
        "journal/list.html",
        {
            "entries": entries,
            "stats": stats,
            "decision_filter": decision,
            "outcome_filter": outcome,
            "tag_filter": tag,
        },
    )


@login_required
def new_for_signal(request, signal_id: int):
    signal = get_object_or_404(Signal.objects.select_related("instrument", "strategy"), pk=signal_id)

    if request.method == "POST":
        form = JournalEntryForm(request.POST)
        if form.is_valid():
            entry: JournalEntry = form.save(commit=False)
            entry.user = request.user
            entry.signal = signal
            entry.save()
            if entry.decision == JournalEntry.Decision.YES:
                signal.status = Signal.Status.CONFIRMED
                signal.save(update_fields=["status"])
            elif entry.decision == JournalEntry.Decision.NO:
                signal.status = Signal.Status.REJECTED
                signal.save(update_fields=["status"])
            next_url = request.POST.get("next")
            return redirect(next_url) if next_url else redirect("journal:list")
    else:
        form = JournalEntryForm()

    return render(request, "journal/new_for_signal.html", {"form": form, "signal": signal})
