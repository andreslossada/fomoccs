"""Status badge widget — consistent color-coded status display with pill styling."""

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
    "active": "\u25cf",
    "enabled": "\u25cf",
    "completed": "\u25cf",
    "success": "\u25cf",
    "connected": "\u25cf",
    "disabled": "\u25cb",
    "disconnected": "\u25cb",
    "error": "\u2716",
    "failed": "\u2716",
    "deleted": "\u2716",
    "cancelled": "\u2716",
    "running": "\u25cc",
    "pending": "\u25cc",
    "draft": "\u25cc",
    "archived": "\u00b7",
    "none": "\u00b7",
}


class StatusBadge(Static):
    """A colored pill-style badge for status display.

    Usage:
        yield StatusBadge("active")
        yield StatusBadge("failed", label="Error")
        yield StatusBadge("completed", icon=False)
    """

    DEFAULT_CSS = """
    StatusBadge {
        width: auto;
        height: 1;
        padding: 0 2;
        content-align: center middle;
        text-style: bold;
    }
    StatusBadge.badge-success {
        background: $success 20%;
        color: $success;
        border: solid $success 40%;
    }
    StatusBadge.badge-error {
        background: $error 20%;
        color: $error;
        border: solid $error 40%;
    }
    StatusBadge.badge-warning {
        background: $warning 20%;
        color: $warning;
        border: solid $warning 50%;
    }
    StatusBadge.badge-dim {
        background: $panel;
        color: $text-muted;
        border: solid $text-muted 30%;
    }
    StatusBadge.badge-default {
        background: $primary 15%;
        color: $primary-lighten-1;
        border: solid $primary 40%;
    }
    """

    _BADGE_CLASSES: dict[str, str] = {
        "green": "badge-success",
        "red": "badge-error",
        "yellow": "badge-warning",
        "dim": "badge-dim",
    }

    def __init__(
        self,
        status: str,
        label: str | None = None,
        icon: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._render(status, label, icon)

    def _render(self, status: str, label: str | None, icon: bool) -> None:
        normalized = status.lower().strip()
        color = _STATUS_COLORS.get(normalized, "")
        icon_str = f"{_STATUS_ICONS.get(normalized, ' ')} " if icon else ""
        text = label if label is not None else status.capitalize()
        css_class = self._BADGE_CLASSES.get(color, "badge-default")
        self.set_class(True, css_class)
        if color:
            self.update(f"[{color}]{icon_str}{text}[/{color}]")
        else:
            self.update(f"{icon_str}{text}")

    def update_badge(
        self, status: str, label: str | None = None, icon: bool = True
    ) -> None:
        """Update an existing badge to a new status."""
        for cls in self._BADGE_CLASSES.values():
            self.set_class(False, cls)
        self.set_class(False, "badge-default")
        self._render(status, label, icon)


def status_color(status: str) -> str:
    return _STATUS_COLORS.get(status.lower().strip(), "")


def status_icon(status: str) -> str:
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
