"""Help modal — shows keyboard shortcuts for the current screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static


class HelpModal(ModalScreen[None]):
    """Modal overlay listing all available keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("?", "dismiss", "Close"),
    ]

    def __init__(self, title: str, bindings: list[Binding]) -> None:
        super().__init__()
        self._screen_title = title
        self._bindings = bindings

    def compose(self) -> ComposeResult:
        lines = [
            f"[bold reverse #00bcd4]  {self._screen_title} — Shortcuts  [/]",
            "",
        ]
        for b in self._bindings:
            key = b.key_display or b.key
            desc = b.description or ""
            if not desc or b.show is False:
                continue
            lines.append(f"  [bold cyan]{key:<6}[/bold cyan]  {desc}")
        # Also show hidden bindings
        lines.append("")
        lines.append("[bold]All shortcuts:[/bold]")
        for b in self._bindings:
            key = b.key_display or b.key
            desc = b.description or ""
            if not desc or b.show is not False:
                continue
            lines.append(f"  [dim]{key:<6}  {desc}[/dim]")
        lines.append("")
        lines.append("[dim]ESC / q to close[/dim]")

        with Vertical(id="help-container"):
            yield Static("\n".join(lines))
        yield Footer()
