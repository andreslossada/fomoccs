"""FomoCCS Admin TUI — main application entry point."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult, Screen
from textual.binding import Binding
from textual.widgets import Footer, Header

from tui.screens.dashboard import DashboardScreen
from tui.screens.events import EventsScreen
from tui.screens.locations import LocationsScreen
from tui.screens.logs import LogsScreen
from tui.screens.operations import OperationsScreen
from tui.screens.sources import SourcesScreen
from tui.screens.tag_rules import TagRulesScreen


class FomoCCSApp(App[None]):
    """Terminal-based admin panel for FomoCCS."""

    TITLE = "FomoCCS Admin"
    SUB_TITLE = "Event Discovery Platform — Caracas"

    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
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
        self.push_screen("dashboard")


def main() -> None:
    """Entry point for the fomoccs-tui command."""
    app = FomoCCSApp()
    app.run()
