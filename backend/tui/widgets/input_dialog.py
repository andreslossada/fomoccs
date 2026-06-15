"""Text input dialog (modal with input field)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class InputDialog(ModalScreen[str | None]):
    """A modal dialog with a text input. Returns the entered value or None on cancel."""

    DEFAULT_CSS = """
    InputDialog {
        align: center middle;
    }
    InputDialog > Static {
        width: 50;
        height: auto;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    InputDialog Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    InputDialog Button {
        margin: 0 1;
    }
    InputDialog Input {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, title: str, *, initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        yield Static(self._title)
        yield Input(value=self._initial, id="dialog_input")
        with Horizontal():
            yield Button("OK", variant="primary", id="input_ok")
            yield Button("Cancel", variant="default", id="input_cancel")

    def on_mount(self) -> None:
        self.query_one("#dialog_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "input_ok":
            value = self.query_one("#dialog_input", Input).value.strip()
            self.dismiss(value or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)
