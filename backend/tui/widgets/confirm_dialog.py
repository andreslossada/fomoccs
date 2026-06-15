"""Confirmation dialog (Yes/No modal)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmDialog(ModalScreen[bool]):
    """A Yes/No confirmation modal. Returns True for Yes, False for No."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    ConfirmDialog > Static {
        width: 50;
        height: auto;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    ConfirmDialog Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    ConfirmDialog Button {
        margin: 0 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Static(self._message)
        with Horizontal():
            yield Button("Yes", variant="primary", id="confirm_yes")
            yield Button("No", variant="default", id="confirm_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm_yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
