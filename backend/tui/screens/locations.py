"""Locations list screen — browse, filter, and manage physical venues."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Label

from tui.db import count_locations, list_locations
from tui.screens.base import BaseListScreen
from tui.screens.locations_detail import LocationDetailScreen


class LocationsScreen(BaseListScreen):
    """Browse and manage locations."""

    _table_id = "locations-table"
    _columns = ["ID", "Name", "Type", "Lat", "Lng", "Events"]
    _title = "Locations"

    _type_filter: str = ""
    _empty_message = "No locations found. Add a source to auto-discover venues."

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
        Binding("/", "focus_search", "Search"),
        Binding("enter", "view_location", "Detail"),
        Binding("c", "cycle_type", "Type", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("n", "load_more", "More"),
        Binding("p", "load_less", "Prev"),
    ]

    # ── filters ──────────────────────────────────────────────────────
    def _extra_filters(self) -> ComposeResult:
        yield Label("Type: all", id="type-label")

    # ── data loading (BaseListScreen overrides) ────────────────────
    async def _load_page(
        self, session: AsyncSession, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        locations = await list_locations(
            session,
            search=self._search,
            location_type=self._type_filter,
            offset=offset,
            limit=limit,
        )
        return [dict(row) for row in locations]

    async def _count_total(self, session: AsyncSession) -> int:
        return await count_locations(
            session, search=self._search, location_type=self._type_filter
        )

    def _render_row(self, item: dict[str, Any]) -> list[str]:
        lat = item.get("lat")
        lng = item.get("lng")
        no_coords = lat is None or lng is None
        lat_str = f"{lat:.4f}" if isinstance(lat, float) else "-"
        lng_str = f"{lng:.4f}" if isinstance(lng, float) else "-"
        if no_coords:
            lat_str = f"[dim]{lat_str}[/dim]"
            lng_str = f"[dim]{lng_str}[/dim]"
        return [
            str(item.get("id", "")),
            str(item.get("name", ""))[:40],
            str(item.get("type", "")),
            lat_str,
            lng_str,
            str(item.get("event_count", "")),
        ]

    def _row_style(self, item: dict[str, Any]) -> str | None:
        no_coords = item.get("lat") is None or item.get("lng") is None
        return "color: #888888" if no_coords else None

    # ── location actions ─────────────────────────────────────────────
    def action_view_location(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        row_key = (
            table.cursor_coordinate.row if table.cursor_coordinate else None
        )
        if self._data and row_key is not None and row_key < len(self._data):
            loc_id = self._data[row_key]["id"]
            if isinstance(loc_id, int):
                self.app.push_screen(LocationDetailScreen(loc_id))

    def action_cycle_type(self) -> None:
        types = ["", "venue", "area", "meeting_point"]
        current_idx = (
            types.index(self._type_filter)
            if self._type_filter in types
            else 0
        )
        next_idx = (current_idx + 1) % len(types)
        self._type_filter = types[next_idx]
        label = f"Type: {self._type_filter or 'all'}"
        self.query_one("#type-label", Label).update(label)
        self._offset = 0
        self.run_worker(self._load_data())
