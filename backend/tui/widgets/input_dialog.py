"""Text input dialog (modal with input field)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class InputDialog(ModalScreen[str | None]):
    """A modal dialog with a text input. Returns the entered value or None on cancel."""

    def __init__(
        self, title: str, *, initial: str = "", placeholder: str = ""
    ) -> None:
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"[bold]{self._title}[/bold]")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="dialog_input",
            )
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
