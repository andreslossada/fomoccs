"""Reusable loading indicator with animated spinner."""

from __future__ import annotations

from textual.widgets import Static


class LoadingIndicator(Static):
    """Animated braille spinner with configurable label.

    Usage:
        yield LoadingIndicator("Loading sources...", id="my-loader")
        # later, when done:
        loader = self.query_one("#my-loader", LoadingIndicator)
        loader.remove()
    """

    _frames = [
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
    ]
    _frame_idx: int = 0
    _label: str = ""

    def __init__(
        self,
        label: str = "Loading...",
        id: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(id=id, **kwargs)
        self._label = label

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        frame = self._frames[self._frame_idx]
        self._frame_idx = (self._frame_idx + 1) % len(self._frames)
        self.update(f"[dim]{frame} {self._label}[/dim]")
