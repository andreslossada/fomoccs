"""Confirmation dialog (Yes/No modal)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmDialog(ModalScreen[bool]):
    """A Yes/No confirmation modal. Returns True for Yes, False for No."""

    def __init__(self, message: str, *, title: str | None = None) -> None:
        super().__init__()
        self._message = message
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical():
            if self._title:
                yield Static(f"[bold]{self._title}[/bold]")
            yield Static(self._message)
            with Horizontal():
                yield Button("Yes", variant="primary", id="confirm_yes")
                yield Button("No", variant="default", id="confirm_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm_yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
