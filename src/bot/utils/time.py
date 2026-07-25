"""Shared relative-time rendering for system prompt blocks.

Single canonical helper used by every block that surfaces "when did this
happen" — `[RECENT OPERATIONS]`, `[OBSERVATIONS]`,
interaction render. Granularity is fine enough for continuity signals
(seconds → days) without paginating into months/years (use the date-based
helper in `tools/_helpers.py:_relative_age` for that — different shape).
"""

from datetime import UTC, datetime, timedelta


def humanize_duration(delta: timedelta) -> str:
    """Render a timedelta compactly: '3d 20h', '45m', '12s', etc.

    For positive deltas only. Drops zero-leading units. Returns '0s' for
    zero or negative.
    """
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "0s"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    if seconds and not (days or hours):
        parts.append(f"{seconds}s")
    return " ".join(parts) or f"{seconds}s"


def relative_when(iso_ts: str) -> str:
    """Render an ISO timestamp as a human-readable age.

    Granularity slides with the size of the gap so callers don't have to
    paginate ages: seconds → minutes → hours (with one decimal under 10) →
    days (one decimal under 10) → months → years.

    Returns '' on parse failure or future timestamps. Handles both tz-aware
    ('2026-04-20T15:00:00+00:00' / '...Z') and tz-naive ('2026-04-20T15:00:00')
    inputs — naive values are treated as UTC (which is how legacy rows in
    turbopuffer were written: `datetime.now().isoformat()` without tz info).
    """
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = (datetime.now(UTC) - ts).total_seconds()
    if delta < 0:
        return ""
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        hours = delta / 3600
        return f"{hours:.1f}h ago" if hours < 10 else f"{int(hours)}h ago"
    days = delta / 86400
    if days < 30:
        return f"{days:.1f}d ago" if days < 10 else f"{int(days)}d ago"
    if days < 365:
        return f"{int(days // 30)}mo ago"
    return f"{int(days // 365)}y ago"
