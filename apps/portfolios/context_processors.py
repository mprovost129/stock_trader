from __future__ import annotations

from .watchlists import ensure_active_watchlist


def active_watchlist(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'active_watchlist': None, 'active_watchlist_count': 0}
    wl = ensure_active_watchlist(request.user)
    count = wl.selections.filter(is_active=True, instrument__is_active=True).count() if wl else 0
    return {'active_watchlist': wl, 'active_watchlist_count': count}
