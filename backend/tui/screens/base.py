"""Base screen classes for the FomoCCS Admin TUI.

Eliminates code duplication across list screens (sources, events, locations,
tag_rules) and detail screens (sources_detail, events_detail, locations_detail).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label

from tui.db import get_session
from tui.screens.help import HelpModal
from tui.widgets.loading import LoadingIndicator


class BaseListScreen(Screen[object]):
    """Reusable base for any screen that shows a paginated, searchable DataTable.

    Subclasses must override:
      - _table_id: CSS id for the DataTable widget
      - _columns: list of column header strings
      - _title: breadcrumb label text
      - _load_page(session, offset, limit) → list[dict]
      - _count_total(session) → int
      - _render_row(item: dict) → list[str]  (values matching _columns order)

    Optional overrides:
      - _page_size (default 50)
      - _has_search (default True)
      - _search_placeholder (default "Search...")
      - _empty_message (default auto-generated)
      - BINDINGS (extend with screen-specific shortcuts)
    """

    _data: list[dict[str, Any]] = []
    _offset: int = 0
    _total: int = 0
    _search: str = ""
    _search_timer: object | None = None
    _loading: bool = False

    # ── overridable constants ──────────────────────────────────────────
    _table_id: str = ""
    _columns: list[str] = []
    _title: str = ""
    _page_size: int = 50
    _has_search: bool = True
    _search_placeholder: str = "Search..."
    _empty_message: str = ""

    # ── abstract ───────────────────────────────────────────────────────
    async def _load_page(
        self, session: AsyncSession, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def _count_total(self, session: AsyncSession) -> int:
        raise NotImplementedError

    def _render_row(self, item: dict[str, Any]) -> list[str]:
        raise NotImplementedError

    def _row_style(self, item: dict[str, Any]) -> str | None:
        """Optional: return a CSS style string for the row."""
        return None

    # ── layout ─────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label(
            f"[bold reverse #00bcd4]  {self._title}  [/]",
            id="screen-title",
        )
        yield from self._nav_tabs()
        yield Label(f"[bold]▸ {self._title}[/bold]", id="breadcrumb")
        with Horizontal(id="filters"):
            if self._has_search:
                yield Input(
                    placeholder=self._search_placeholder, id="search-input"
                )
            yield from self._extra_filters()
            yield Label("", id="counter")
        yield DataTable(id=self._table_id)
        yield LoadingIndicator("", id="spinner")
        yield Label("", id="empty-state")
        yield Footer()

    def _nav_tabs(self) -> ComposeResult:
        screens = [
            ("d", "Dashboard"),
            ("s", "Sources"),
            ("e", "Events"),
            ("l", "Locations"),
            ("t", "Rules"),
            ("o", "Ops"),
            ("g", "Logs"),
        ]
        with Horizontal(id="nav-tabs"):
            for key, label in screens:
                yield Button(label, id=f"nav-{key}", classes="nav-tab")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("nav-"):
            key = bid[4:]
            screen_map = {
                "d": "dashboard",
                "s": "sources",
                "e": "events",
                "l": "locations",
                "t": "tag_rules",
                "o": "operations",
                "g": "logs",
            }
            if key in screen_map:
                self.app.switch_screen(screen_map[key])

    def _extra_filters(self) -> ComposeResult:
        yield from ()

    # ── lifecycle ──────────────────────────────────────────────────────
    def on_mount(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        table.cursor_type = "row"
        table.add_columns(*self._columns)
        self._show_spinner(True)
        self.run_worker(self._load_data())

    # ── data loading ───────────────────────────────────────────────────
    async def _load_data(self) -> None:
        self._loading = True
        self._show_spinner(True)
        session: AsyncSession = await get_session()
        try:
            self._total = await self._count_total(session)
            self._data = await self._load_page(
                session, offset=self._offset, limit=self._page_size
            )
        finally:
            await session.close()
        self._loading = False
        self._show_spinner(False)
        self._render_table()

    # ── table rendering ────────────────────────────────────────────────
    def _render_table(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        table.clear()
        empty_label = self.query_one("#empty-state", Label)

        if self._loading:
            empty_label.update("")
            table.display = False
            return

        if not self._data and self._offset == 0:
            table.display = False
            msg = self._empty_message or f"No {self._title.lower()} found."
            empty_label.update(f"[dim italic]{msg}[/dim italic]")
            self.query_one("#counter", Label).update("0 / 0")
            return

        table.display = True
        empty_label.update("")
        for item in self._data:
            table.add_row(*self._render_row(item))
        shown = self._offset + len(self._data)
        self.query_one("#counter", Label).update(f"{shown} / {self._total}")

    def _show_spinner(self, show: bool) -> None:
        spinner = self.query_one("#spinner", LoadingIndicator)
        if show:
            spinner._label = f"Loading {self._title.lower()}..."
            spinner.display = True
        else:
            spinner.display = False
        empty = self.query_one("#empty-state", Label)
        if show:
            empty.update("")

    # ── shared actions ─────────────────────────────────────────────────
    def action_refresh(self) -> None:
        self.run_worker(self._load_data())

    def action_load_more(self) -> None:
        if self._offset + len(self._data) < self._total:
            self._offset += len(self._data)
            self.run_worker(self._load_data())

    def action_load_less(self) -> None:
        if self._offset > 0:
            self._offset = max(0, self._offset - self._page_size)
            self.run_worker(self._load_data())

    def action_focus_search(self) -> None:
        if self._has_search:
            self.query_one("#search-input", Input).focus()

    # ── search (debounced) ─────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if not self._has_search:
            return
        if event.input.id == "search-input":
            self._search = event.value
            self._offset = 0
            if self._search_timer is not None:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.3, self._debounced_search)

    def _debounced_search(self) -> None:
        self._search_timer = None
        self.run_worker(self._load_data())

    def action_show_help(self) -> None:
        self.app.push_screen(HelpModal(self._title, list(self.BINDINGS)))


class BaseDetailScreen(Screen[object]):
    """Reusable base for any screen that shows a single entity's detail."""

    _title: str = ""
    _entity_id: str = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label(
            f"[bold reverse #00bcd4]  {self._title}  [/]",
            id="screen-title",
        )
        yield from self._nav_tabs()
        yield Label(f"[bold]▸ {self._title}[/bold]", id="breadcrumb")
        yield from self._detail_widgets()
        yield Footer()

    def _detail_widgets(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        self.run_worker(self._load())

    async def _load(self) -> None:
        session = await get_session()
        try:
            entity = await self._load_entity(session)
            if entity is None:
                self.notify(f"{self._title} not found", severity="error")
                return
            self._render(entity)
        finally:
            await session.close()

    async def _load_entity(self, session: AsyncSession) -> Any:
        raise NotImplementedError

    def _render(self, entity: Any) -> None:
        raise NotImplementedError

    def action_show_help(self) -> None:
        self.app.push_screen(HelpModal(self._title, list(self.BINDINGS)))
