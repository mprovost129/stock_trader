from __future__ import annotations

from .watchlists import ensure_active_watchlist


def active_watchlist(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'active_watchlist': None}
    return {'active_watchlist': ensure_active_watchlist(request.user)}
