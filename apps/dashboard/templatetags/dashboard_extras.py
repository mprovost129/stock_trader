from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    return mapping.get(key)


@register.filter
def score_color_class(score):
    """Return a Bootstrap text color class for a 0-100 signal score."""
    if score is None:
        return "text-muted"
    try:
        v = float(score)
    except (TypeError, ValueError):
        return "text-muted"
    if v >= 80:
        return "text-success fw-bold"
    if v >= 60:
        return "text-warning-emphasis fw-semibold"
    return "text-muted"


@register.filter
def hours_old(dt):
    """Return how many whole hours old a datetime is (None-safe)."""
    if dt is None:
        return None
    try:
        delta = timezone.now() - dt
        return int(delta.total_seconds() // 3600)
    except Exception:
        return None
