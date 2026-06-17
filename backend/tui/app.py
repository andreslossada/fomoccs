"""FomoCCS Admin TUI — main application entry point."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult, Screen
from textual.binding import Binding
from textual.theme import Theme
from textual.widgets import Footer, Header

from tui.screens.dashboard import DashboardScreen
from tui.screens.events import EventsScreen
from tui.screens.locations import LocationsScreen
from tui.screens.logs import LogsScreen
from tui.screens.operations import OperationsScreen
from tui.screens.sources import SourcesScreen
from tui.screens.tag_rules import TagRulesScreen

FOMO_THEME = Theme(
    name="fomo",
    primary="#00bcd4",
    secondary="#7c4dff",
    accent="#ffab40",
    warning="#ffd54f",
    error="#ef5350",
    success="#66bb6a",
    surface="#0d1117",
    panel="#161b22",
    dark=True,
)


class FomoCCSApp(App[None]):
    """Terminal-based admin panel for FomoCCS."""

    TITLE = "FomoCCS Admin"
    SUB_TITLE = "Event Discovery Platform — Caracas"

    CSS_PATH = "app.tcss"
    DEFAULT_THEME = "fomo"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("backspace", "pop_screen", "Back", priority=True),
        Binding("d", "push_screen('dashboard')", "Dashboard", priority=True),
        Binding("s", "push_screen('sources')", "Sources", priority=True),
        Binding("e", "push_screen('events')", "Events", priority=True),
        Binding("l", "push_screen('locations')", "Locations", priority=True),
        Binding("t", "push_screen('tag_rules')", "Tag Rules", priority=True),
        Binding("o", "push_screen('operations')", "Operations", priority=True),
        Binding("g", "push_screen('logs')", "Logs", priority=True),
    ]

    SCREENS: dict[str, Callable[[], Screen[object]]] = {
        "dashboard": lambda: DashboardScreen(),
        "sources": lambda: SourcesScreen(),
        "events": lambda: EventsScreen(),
        "locations": lambda: LocationsScreen(),
        "tag_rules": lambda: TagRulesScreen(),
        "operations": lambda: OperationsScreen(),
        "logs": lambda: LogsScreen(),
    }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(FOMO_THEME)
        self.theme = "fomo"
        self.push_screen("dashboard")


def main() -> None:
    """Entry point for the fomoccs-tui command."""
    app = FomoCCSApp()
    app.run()
