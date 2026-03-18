from __future__ import annotations

from apps.portfolios.models import InstrumentSelection, Watchlist


def ensure_active_watchlist(user) -> Watchlist:
    active = Watchlist.objects.filter(user=user, is_active=True).order_by('name', '-created_at').first()
    if active:
        return active
    default, created = Watchlist.objects.get_or_create(user=user, name='Default', defaults={'is_active': True})
    if not default.is_active:
        Watchlist.objects.filter(user=user, is_active=True).exclude(pk=default.pk).update(is_active=False)
        default.is_active = True
        default.save(update_fields=['is_active', 'updated_at'])
    return default


def activate_watchlist(*, user, watchlist: Watchlist) -> Watchlist:
    Watchlist.objects.filter(user=user, is_active=True).exclude(pk=watchlist.pk).update(is_active=False)
    if not watchlist.is_active:
        watchlist.is_active = True
        watchlist.save(update_fields=['is_active', 'updated_at'])
    return watchlist


def active_watchlist_instrument_ids(user) -> set[int]:
    watchlist = ensure_active_watchlist(user)
    return set(
        InstrumentSelection.objects.filter(
            watchlist=watchlist,
            is_active=True,
            instrument__is_active=True,
        ).values_list('instrument_id', flat=True)
    )
