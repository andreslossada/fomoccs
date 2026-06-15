"""Sources list screen — browse, filter, and manage data sources."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label

from tui.db import count_sources, get_session, list_sources
from tui.screens.sources_detail import SourceDetailScreen


class SourcesScreen(Screen[object]):
    """Browse and manage sources."""

    _data: list[dict[str, Any]] = []
    _offset: int = 0
    _total: int = 0
    _search: str = ""
    _tier_filter: int | None = None
    _active_only: bool = False
    _search_timer: object | None = None
    _loading: bool = False

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("/", "focus_search", "Search"),
        ("enter", "view_source", "Detail"),
        ("space", "toggle_source", "Toggle"),
        ("a", "toggle_active_only", "Active"),
        ("t", "cycle_tier", "Tier"),
        ("r", "refresh", "Refresh"),
        ("n", "load_more", "More"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Sources[/bold]", id="breadcrumb")
        with Horizontal(id="filters"):
            yield Input(placeholder="Search sources...", id="search-input")
            yield Label("Tier: all", id="tier-label")
            yield Label("", id="counter")
        yield DataTable(id="sources-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Name", "Type", "Tier", "Trust", "Status")
        self.run_worker(self._load_data())

    def _render_table(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.clear()
        if self._loading:
            table.add_row("", "[dim]Loading...[/dim]", "", "", "", "")
            self.query_one("#counter", Label).update("[dim]Loading...[/dim]")
            return
        if not self._data:
            table.add_row("", "[dim]No results[/dim]", "", "", "", "")
            self.query_one("#counter", Label).update("0 / 0")
            return
        for src in self._data:
            status = (
                "[red]Disabled[/red]"
                if src.get("disabled")
                else "[green]Active[/green]"
            )
            table.add_row(
                str(src.get("id", "")),
                str(src.get("name", ""))[:50],
                str(src.get("type", "")),
                str(src.get("tier", "")),
                str(src.get("trust_level") or "-"),
                status,
            )
        self.query_one("#counter", Label).update(
            f"{self._offset + len(self._data)} / {self._total}"
        )

    async def _load_data(self) -> None:
        self._loading = True
        self._render_table()
        session: AsyncSession = await get_session()
        try:
            self._total = await count_sources(
                session,
                search=self._search,
                tier=self._tier_filter,
                active_only=self._active_only,
            )
            sources = await list_sources(
                session,
                search=self._search,
                tier=self._tier_filter,
                active_only=self._active_only,
                offset=self._offset,
                limit=50,
            )
            self._data = [
                {
                    "id": s.id,
                    "name": s.name,
                    "type": str(s.type),
                    "tier": s.tier,
                    "trust_level": s.trust_level,
                    "disabled": s.disabled,
                }
                for s in sources
            ]
        finally:
            await session.close()
        self._loading = False
        self._render_table()

    async def _toggle_active_source(self, source_id: int, disabled: bool) -> None:
        from api.models.source import Source

        session = await get_session()
        try:
            await session.execute(
                update(Source)
                .where(Source.id == source_id)
                .values(disabled=not disabled)
            )
            await session.commit()
        finally:
            await session.close()

    def action_view_source(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if self._data and row_key is not None:
            idx = row_key
            if idx < len(self._data):
                source_id = self._data[idx]["id"]
                if isinstance(source_id, int):
                    self.app.push_screen(SourceDetailScreen(source_id))

    def action_toggle_source(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if self._data and row_key is not None:
            idx = row_key
            if idx < len(self._data):
                src = self._data[idx]
                sid = src["id"]
                disabled = src["disabled"]
                if isinstance(sid, int) and isinstance(disabled, bool):
                    self.run_worker(self._toggle_active_source(sid, disabled))
                    src["disabled"] = not disabled
                    self._render_table()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._search = event.value
            self._offset = 0
            if self._search_timer is not None:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.3, self._debounced_search)

    def _debounced_search(self) -> None:
        self._search_timer = None
        self.run_worker(self._load_data())

    def action_toggle_active_only(self) -> None:
        self._active_only = not self._active_only
        self._offset = 0
        self.run_worker(self._load_data())

    def action_cycle_tier(self) -> None:
        if self._tier_filter is None:
            self._tier_filter = 1
        elif self._tier_filter == 1:
            self._tier_filter = 2
        elif self._tier_filter == 2:
            self._tier_filter = 3
        else:
            self._tier_filter = None
        label = f"Tier: {self._tier_filter}" if self._tier_filter else "Tier: all"
        self.query_one("#tier-label", Label).update(label)
        self._offset = 0
        self.run_worker(self._load_data())

    def action_refresh(self) -> None:
        self.run_worker(self._load_data())

    def action_load_more(self) -> None:
        if self._offset + len(self._data) < self._total:
            self._offset += len(self._data)
            self.run_worker(self._load_data())
