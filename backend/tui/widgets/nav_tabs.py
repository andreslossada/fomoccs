"""Shared navigation tabs mixin — eliminates duplicated _nav_tabs across screens."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button

_SCREEN_MAP: dict[str, str] = {
    "d": "dashboard",
    "s": "sources",
    "e": "events",
    "l": "locations",
    "t": "tag_rules",
    "o": "operations",
    "g": "logs",
}

_SCREENS: list[tuple[str, str]] = [
    ("d", "Dashboard"),
    ("s", "Sources"),
    ("e", "Events"),
    ("l", "Locations"),
    ("t", "Rules"),
    ("o", "Ops"),
    ("g", "Logs"),
]


def compose_nav_tabs() -> ComposeResult:
    """Yield navigation tab buttons wrapped in a Horizontal."""
    with Horizontal(id="nav-tabs"):
        for key, label in _SCREENS:
            yield Button(f"[dim]{key}[/dim] {label}", id=f"nav-{key}")


def handle_nav_press(button_id: str) -> str | None:
    """Return the screen name for a nav button id, or None if not a nav button."""
    if not button_id.startswith("nav-"):
        return None
    key = button_id[4:]
    return _SCREEN_MAP.get(key)


def active_tab(screen: object, tab_key: str) -> None:
    """Mark a nav tab button as active. Call from on_mount or after screen switch."""
    try:

        if hasattr(screen, "query_one"):
            btn = screen.query_one(f"#nav-{tab_key}", Button)  # type: ignore[union-attr]
            btn.add_class("-active")
    except Exception:
        pass
