"""Status badge widget — consistent color-coded status display."""

from __future__ import annotations

from textual.widgets import Static

_STATUS_COLORS: dict[str, str] = {
    "active": "green",
    "enabled": "green",
    "completed": "green",
    "success": "green",
    "connected": "green",
    "disabled": "red",
    "disconnected": "red",
    "error": "red",
    "failed": "red",
    "deleted": "red",
    "cancelled": "red",
    "running": "yellow",
    "pending": "yellow",
    "draft": "yellow",
    "archived": "dim",
    "none": "dim",
}

_STATUS_ICONS: dict[str, str] = {
    "active": "●",
    "enabled": "●",
    "completed": "●",
    "success": "●",
    "connected": "●",
    "disabled": "○",
    "disconnected": "○",
    "error": "✖",
    "failed": "✖",
    "deleted": "✖",
    "cancelled": "✖",
    "running": "◌",
    "pending": "◌",
    "draft": "◌",
    "archived": "·",
    "none": "·",
}


class StatusBadge(Static):
    """A colored badge for status display.

    Usage:
        yield StatusBadge("active")
        yield StatusBadge("failed", label="Error")
        yield StatusBadge("completed", icon=False)  # icon-less
    """

    def __init__(
        self,
        status: str,
        label: str | None = None,
        icon: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.update(format_status(status, label, icon))


def status_color(status: str) -> str:
    """Return the Textual color name for a status string."""
    return _STATUS_COLORS.get(status.lower().strip(), "")


def status_icon(status: str) -> str:
    """Return a unicode icon for a status string."""
    return _STATUS_ICONS.get(status.lower().strip(), " ")


def format_status(
    status: str,
    label: str | None = None,
    icon: bool = True,
) -> str:
    """Format a status string as a colored Textual markup string."""
    normalized = status.lower().strip()
    color = _STATUS_COLORS.get(normalized, "")
    icon_str = f"{_STATUS_ICONS.get(normalized, ' ')} " if icon else ""
    text = label if label is not None else status.capitalize()
    if color:
        return f"[{color}]{icon_str}{text}[/{color}]"
    return f"{icon_str}{text}"
