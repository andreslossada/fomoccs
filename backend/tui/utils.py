"""Shared TUI utilities — formatting, navigation, helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def relative_time(dt: object) -> str:
    """Format a datetime as a human-friendly relative string.

    Examples: "2h ago", "yesterday", "3d ago", "Jan 5"
    """
    if dt is None:
        return "-"
    if not isinstance(dt, datetime):
        return str(dt)[:16]

    now = datetime.now(UTC).replace(tzinfo=None)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)

    delta = now - dt
    seconds = delta.total_seconds()

    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        mins = int(seconds / 60)
        return f"{mins}m ago"
    if seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    if seconds < 172800:
        return "yesterday"
    if seconds < 604800:
        days = int(seconds / 86400)
        return f"{days}d ago"
    return dt.strftime("%b %d")
