"""Locations list screen — browse, filter, and manage physical venues."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label

from tui.db import count_locations, get_session, list_locations
from tui.screens.locations_detail import LocationDetailScreen


class LocationsScreen(Screen[object]):
    """Browse and manage locations."""

    _data: list[dict[str, Any]] = []
    _offset: int = 0
    _total: int = 0
    _search: str = ""
    _type_filter: str = ""
    _search_timer: object | None = None
    _loading: bool = False

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("/", "focus_search", "Search"),
        ("enter", "view_location", "Detail"),
        ("c", "cycle_type", "Type"),
        ("r", "refresh", "Refresh"),
        ("n", "load_more", "More"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Locations[/bold]", id="breadcrumb")
        with Horizontal(id="filters"):
            yield Input(placeholder="Search locations...", id="search-input")
            yield Label("Type: all", id="type-label")
            yield Label("", id="counter")
        yield DataTable(id="locations-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#locations-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Name", "Type", "Lat", "Lng", "Events")
        self.run_worker(self._load_data())

    def _render_table(self) -> None:
        table = self.query_one("#locations-table", DataTable)
        table.clear()
        if self._loading:
            table.add_row("", "[dim]Loading...[/dim]", "", "", "", "")
            self.query_one("#counter", Label).update("[dim]Loading...[/dim]")
            return
        if not self._data:
            table.add_row("", "[dim]No results[/dim]", "", "", "", "")
            self.query_one("#counter", Label).update("0 / 0")
            return
        for loc in self._data:
            lat = loc.get("lat")
            lng = loc.get("lng")
            lat_str = f"{lat:.4f}" if isinstance(lat, float) else "-"
            lng_str = f"{lng:.4f}" if isinstance(lng, float) else "-"
            no_coords = lat is None or lng is None
            color = "[dim]" if no_coords else ""
            end_color = "[/dim]" if no_coords else ""
            table.add_row(
                str(loc.get("id", "")),
                str(loc.get("name", ""))[:40],
                str(loc.get("type", "")),
                f"{color}{lat_str}{end_color}",
                f"{color}{lng_str}{end_color}",
                str(loc.get("event_count", "")),
            )
        self.query_one("#counter", Label).update(
            f"{self._offset + len(self._data)} / {self._total}"
        )

    async def _load_data(self) -> None:
        self._loading = True
        self._render_table()
        session: AsyncSession = await get_session()
        try:
            self._total = await count_locations(
                session, search=self._search, location_type=self._type_filter
            )
            locations = await list_locations(
                session,
                search=self._search,
                location_type=self._type_filter,
                offset=self._offset,
                limit=50,
            )
            self._data = [dict(row) for row in locations]
        finally:
            await session.close()
        self._loading = False
        self._render_table()

    def action_view_location(self) -> None:
        table = self.query_one("#locations-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if self._data and row_key is not None and row_key < len(self._data):
            loc_id = self._data[row_key]["id"]
            if isinstance(loc_id, int):
                self.app.push_screen(LocationDetailScreen(loc_id))

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

    def action_cycle_type(self) -> None:
        types = ["", "venue", "area", "meeting_point"]
        current_idx = (
            types.index(self._type_filter) if self._type_filter in types else 0
        )
        next_idx = (current_idx + 1) % len(types)
        self._type_filter = types[next_idx]
        label = f"Type: {self._type_filter or 'all'}"
        self.query_one("#type-label", Label).update(label)
        self._offset = 0
        self.run_worker(self._load_data())

    def action_refresh(self) -> None:
        self.run_worker(self._load_data())

    def action_load_more(self) -> None:
        if self._offset + len(self._data) < self._total:
            self._offset += len(self._data)
            self.run_worker(self._load_data())
