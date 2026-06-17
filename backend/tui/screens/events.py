"""Events list screen — browse, filter, and manage events."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Label

from api.models.event import Event
from tui.db import count_events, list_events
from tui.screens.base import BaseListScreen
from tui.screens.events_detail import EventDetailScreen
from tui.widgets.status_badge import format_status
from tui.utils import relative_time


class EventsScreen(BaseListScreen):
    """Browse and manage events."""

    _table_id = "events-table"
    _columns = ["ID", "Name", "Status", "Location", "Occ.", "Created"]
    _title = "Events"

    _status_filter: str = ""
    _empty_message = "No events match this filter. Press c to cycle status."

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
        Binding("/", "focus_search", "Search"),
        Binding("enter", "view_event", "Detail"),
        Binding("a", "toggle_archive", "Archive", show=False),
        Binding("c", "cycle_status", "Status", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("n", "load_more", "More"),
        Binding("p", "load_less", "Prev"),
    ]

    # ── filters ──────────────────────────────────────────────────────
    def _extra_filters(self) -> ComposeResult:
        yield Label("Status: all", id="status-label")

    # ── data loading (BaseListScreen overrides) ────────────────────
    async def _load_page(
        self, session: AsyncSession, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        events = await list_events(
            session,
            search=self._search,
            status=self._status_filter,
            offset=offset,
            limit=limit,
        )
        return [dict(row) for row in events]

    async def _count_total(self, session: AsyncSession) -> int:
        return await count_events(
            session, search=self._search, status=self._status_filter
        )

    def _render_row(self, item: dict[str, Any]) -> list[str]:
        status = str(item.get("status", ""))
        return [
            str(item.get("id", "")),
            str(item.get("name", ""))[:60],
            format_status(status),
            str(item.get("location_name", ""))[:30],
            str(item.get("occurrences", "")),
            relative_time(item.get("created_at")),
        ]

    def _row_style(self, item: dict[str, Any]) -> str | None:
        color_map = {
            "active": "#66bb6a", "archived": "#888888",
            "draft": "#ffd54f", "cancelled": "#ef5350",
        }
        color = color_map.get(str(item.get("status", "")), "")
        return f"color: {color}" if color else None

    # ── event actions ────────────────────────────────────────────────
    def action_view_event(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        row_key = (
            table.cursor_coordinate.row if table.cursor_coordinate else None
        )
        if self._data and row_key is not None and row_key < len(self._data):
            event_id = self._data[row_key]["id"]
            if isinstance(event_id, int):
                self.app.push_screen(EventDetailScreen(event_id))

    def action_toggle_archive(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        row_key = (
            table.cursor_coordinate.row if table.cursor_coordinate else None
        )
        if self._data and row_key is not None and row_key < len(self._data):
            ev = self._data[row_key]
            new_status = (
                "active" if ev["status"] == "archived" else "archived"
            )
            self.run_worker(
                self._do_toggle_archive(int(ev["id"]), new_status)
            )

    async def _do_toggle_archive(
        self, event_id: int, new_status: str
    ) -> None:
        from tui.db import get_session

        session = await get_session()
        try:
            await session.execute(
                update(Event)
                .where(Event.id == event_id)
                .values(status=new_status)
            )
            await session.commit()
        finally:
            await session.close()
        self.app.notify(
            f"Event {new_status}", severity="information"
        )
        await self._load_data()

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

    def action_load_less(self) -> None:
        if self._offset > 0:
            self._offset = max(0, self._offset - self._page_size)
            self.run_worker(self._load_data())
