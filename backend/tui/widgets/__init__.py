"""Reusable TUI widgets."""

from tui.widgets.confirm_dialog import ConfirmDialog
from tui.widgets.input_dialog import InputDialog
from tui.widgets.loading import LoadingIndicator
from tui.widgets.nav_tabs import compose_nav_tabs, handle_nav_press
from tui.widgets.status_badge import (
    StatusBadge,
    format_status,
    status_color,
    status_icon,
)

__all__ = [
    "ConfirmDialog",
    "InputDialog",
    "LoadingIndicator",
    "StatusBadge",
    "compose_nav_tabs",
    "format_status",
    "handle_nav_press",
    "status_color",
    "status_icon",
]
