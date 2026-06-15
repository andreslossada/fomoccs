"""Events list screen — browse, filter, and manage events."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label

from tui.db import count_events, get_session, list_events
from tui.screens.events_detail import EventDetailScreen


class EventsScreen(Screen[object]):
    """Browse and manage events."""

    _data: list[dict[str, Any]] = []
    _offset: int = 0
    _total: int = 0
    _search: str = ""
    _status_filter: str = ""
    _search_timer: object | None = None
    _loading: bool = False

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("/", "focus_search", "Search"),
        ("enter", "view_event", "Detail"),
        ("a", "toggle_archive", "Archive"),
        ("c", "cycle_status", "Status"),
        ("r", "refresh", "Refresh"),
        ("n", "load_more", "More"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Events[/bold]", id="breadcrumb")
        with Horizontal(id="filters"):
            yield Input(placeholder="Search events...", id="search-input")
            yield Label("Status: all", id="status-label")
            yield Label("", id="counter")
        yield DataTable(id="events-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#events-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Name", "Status", "Location", "Occ.", "Created")
        self.run_worker(self._load_data())

    def _render_table(self) -> None:
        table = self.query_one("#events-table", DataTable)
        table.clear()
        if self._loading:
            table.add_row("", "[dim]Loading...[/dim]", "", "", "", "")
            self.query_one("#counter", Label).update("[dim]Loading...[/dim]")
            return
        if not self._data:
            table.add_row("", "[dim]No results[/dim]", "", "", "", "")
            self.query_one("#counter", Label).update("0 / 0")
            return
        for ev in self._data:
            status = str(ev.get("status", ""))
            color = {
                "active": "[green]",
                "archived": "[dim]",
                "draft": "[yellow]",
                "cancelled": "[red]",
            }.get(status, "")
            end_tag = f"[/{color[1:]}" if color.startswith("[") else ""
            table.add_row(
                str(ev.get("id", "")),
                str(ev.get("name", ""))[:60],
                f"{color}{status}{end_tag}",
                str(ev.get("location_name", ""))[:30],
                str(ev.get("occurrences", "")),
                str(ev.get("created_at", ""))[:16] if ev.get("created_at") else "",
            )
        self.query_one("#counter", Label).update(
            f"{self._offset + len(self._data)} / {self._total}"
        )

    async def _load_data(self) -> None:
        self._loading = True
        self._render_table()
        session: AsyncSession = await get_session()
        try:
            self._total = await count_events(
                session, search=self._search, status=self._status_filter
            )
            events = await list_events(
                session,
                search=self._search,
                status=self._status_filter,
                offset=self._offset,
                limit=50,
            )
            self._data = [dict(row) for row in events]
        finally:
            await session.close()
        self._loading = False
        self._render_table()

    def action_view_event(self) -> None:
        table = self.query_one("#events-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if self._data and row_key is not None and row_key < len(self._data):
            event_id = self._data[row_key]["id"]
            if isinstance(event_id, int):
                self.app.push_screen(EventDetailScreen(event_id))

    def action_toggle_archive(self) -> None:
        table = self.query_one("#events-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if self._data and row_key is not None and row_key < len(self._data):
            ev = self._data[row_key]
            new_status = "active" if ev["status"] == "archived" else "archived"
            self.run_worker(self._do_toggle_archive(int(ev["id"]), new_status))

    async def _do_toggle_archive(self, event_id: int, new_status: str) -> None:
        from api.models.event import Event

        session = await get_session()
        try:
            await session.execute(
                update(Event).where(Event.id == event_id).values(status=new_status)
            )
            await session.commit()
        finally:
            await session.close()
        await self._load_data()

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

    def action_cycle_status(self) -> None:
        statuses = ["", "active", "archived", "draft", "cancelled"]
        current_idx = (
            statuses.index(self._status_filter)
            if self._status_filter in statuses
            else 0
        )
        next_idx = (current_idx + 1) % len(statuses)
        self._status_filter = statuses[next_idx]
        label = f"Status: {self._status_filter or 'all'}"
        self.query_one("#status-label", Label).update(label)
        self._offset = 0
        self.run_worker(self._load_data())

    def action_refresh(self) -> None:
        self.run_worker(self._load_data())

    def action_load_more(self) -> None:
        if self._offset + len(self._data) < self._total:
            self._offset += len(self._data)
            self.run_worker(self._load_data())
