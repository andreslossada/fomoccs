"""Logs screen — recent crawl activity and errors."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from tui.db import get_session, recent_crawl_errors, recent_crawl_results
from tui.screens.help import HelpModal
from tui.widgets.loading import LoadingIndicator


class LogsScreen(Screen[object]):
    """View recent crawl results and errors."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
        Binding("e", "show_errors", "Errors", show=False),
        Binding("a", "show_all", "All", show=False),
        Binding("r", "refresh", "Refresh"),
    ]

    def action_show_help(self) -> None:
        self.app.push_screen(HelpModal("Logs", self.BINDINGS))

    _mode: str = "all"

    MODE_LABELS = {
        "errors": "[bold red]Recent Errors[/bold red]",
        "all": "[bold]Recent Crawl Activity[/bold]",
    }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold reverse #00bcd4]  Logs  [/]", id="screen-title")
        yield from self._nav_tabs()
        yield Label("[bold]Logs[/bold]", id="breadcrumb")
        yield Container(
            Static(
                "[bold]Recent Crawl Activity[/bold] [dim](r:refresh e:errors a:all)[/dim]",
                id="logs-title",
            ),
            LoadingIndicator("Loading logs...", id="logs-spinner"),
            Static("", id="logs-content"),
            id="logs-container",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._load_logs()

    def _show_loading(self) -> None:
        label = self.MODE_LABELS.get(self._mode, self.MODE_LABELS["all"])
        self.query_one("#logs-title", Static).update(
            f"{label} [dim](refreshing...)[/dim]"
        )
        self.query_one("#logs-spinner", LoadingIndicator).display = True

    async def _load_logs(self) -> None:
        self._show_loading()
        try:
            session: AsyncSession = await get_session()
            try:
                if self._mode == "errors":
                    results = await recent_crawl_errors(session, limit=50)
                    if results:
                        lines = [f"[bold red]Last {len(results)} errors:[/bold red]"]
                        for cr in results:
                            src_name = cr.source.name if cr.source else "unknown"
                            lines.append(
                                f"[red]#{cr.id}[/red] "
                                f"source=[bold]{src_name}[/bold] "
                                f"{cr.error_message or ''}"
                            )
                    else:
                        lines = ["[green]No recent errors![/green]"]
                else:
                    results = await recent_crawl_results(session, limit=100)
                    lines = [f"[bold]Last {len(results)} crawl results:[/bold]"]
                    for cr in results:
                        src_name = cr.source.name if cr.source else "unknown"
                        color: str = {
                            "processed": "green",
                            "extracted": "yellow",
                            "crawled": "blue",
                            "failed": "red",
                            "pending": "dim",
                        }.get(str(cr.status), "")
                        lines.append(
                            f"[{color}]#{cr.id}[/{color}] "
                            f"[{color}]{cr.status}[/{color}] "
                            f"source=[bold]{src_name}[/bold] "
                            f"provider={cr.extraction_provider or '-'} "
                            f"model={cr.extraction_model or '-'} "
                            f"attempts={cr.extraction_attempts}"
                        )
                self.query_one("#logs-content", Static).update("\n".join(lines))
            finally:
                await session.close()
        except Exception as e:
            self.query_one("#logs-content", Static).update(
                f"[bold red]Error loading logs:[/bold red]\n{e}"
            )
        label = self.MODE_LABELS.get(self._mode, self.MODE_LABELS["all"])
        self.query_one("#logs-title", Static).update(
            f"{label} [dim](r:refresh e:errors a:all)[/dim]"
        )
        self.query_one("#logs-spinner", LoadingIndicator).display = False

    def action_show_errors(self) -> None:
        self._mode = "errors"
        self.run_worker(self._load_logs())

    def action_show_all(self) -> None:
        self._mode = "all"
        self.run_worker(self._load_logs())

    def action_refresh(self) -> None:
        self.run_worker(self._load_logs())

    def _nav_tabs(self) -> ComposeResult:
        screens = [
            ("d", "Dashboard"), ("s", "Sources"), ("e", "Events"),
            ("l", "Locations"), ("t", "Rules"), ("o", "Ops"), ("g", "Logs"),
        ]
        with Horizontal(id="nav-tabs"):
            for key, label in screens:
                yield Button(label, id=f"nav-{key}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("nav-"):
            return
        screen_map = {
            "d": "dashboard", "s": "sources", "e": "events",
            "l": "locations", "t": "tag_rules", "o": "operations", "g": "logs",
        }
        key = bid[4:]
        if key in screen_map:
            self.app.switch_screen(screen_map[key])
