"""Sources list screen — browse, filter, and manage data sources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Label

from api.models.source import Source
from tui.db import count_sources, get_session, list_sources
from tui.screens.base import BaseListScreen
from tui.screens.pipeline_run import PipelineRunScreen
from tui.screens.source_wizard import SourceWizardScreen
from tui.screens.sources_detail import SourceDetailScreen
from tui.widgets.status_badge import format_status
from tui.utils import relative_time

VET_TZ = timezone(timedelta(hours=-4))


class SourcesScreen(BaseListScreen):
    """Browse and manage sources."""

    _table_id = "sources-table"
    _columns = [
        "ID", "Name", "Type", "Tier", "Website",
        "Last Crawl", "Events", "Status",
    ]
    _title = "Sources"
    _page_size = 50

    _tier_filter: int | None = None
    _active_only: bool = False
    _empty_message = "No sources found. Press n to create one."

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
        Binding("/", "focus_search", "Search"),
        Binding("enter", "view_source", "Detail"),
        Binding("e", "edit_source", "Edit", show=False),
        Binding("d", "delete_source", "Delete", show=False),
        Binding("space", "toggle_source", "Toggle", show=False),
        Binding("c", "crawl_source", "Crawl", show=False),
        Binding("f", "force_crawl", "Force", show=False),
        Binding("a", "toggle_active_only", "Active", show=False),
        Binding("t", "cycle_tier", "Tier", show=False),
        Binding("n", "new_source", "New", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("m", "load_more", "More"),
        Binding("p", "load_less", "Prev"),
    ]

    # ── filters ──────────────────────────────────────────────────────
    def _extra_filters(self) -> ComposeResult:
        yield Label("Tier: all", id="tier-label")

    # ── data loading (BaseListScreen overrides) ────────────────────
    async def _load_page(
        self, session: AsyncSession, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        return await list_sources(
            session,
            search=self._search,
            tier=self._tier_filter,
            active_only=self._active_only,
            offset=offset,
            limit=limit,
        )

    async def _count_total(self, session: AsyncSession) -> int:
        return await count_sources(
            session,
            search=self._search,
            tier=self._tier_filter,
            active_only=self._active_only,
        )

    def _render_row(self, item: dict[str, Any]) -> list[str]:
        disabled = item.get("disabled")
        status = format_status(
            "disabled" if disabled else "active",
            label="Disabled" if disabled else "Active",
        )
        website = str(item.get("website", ""))[:40]
        last = item.get("last_crawled_at")
        last_str = relative_time(last)
        tier = item.get("tier", "")
        tier_color = {1: "green", 2: "yellow", 3: "dim"}.get(tier, "")
        tier_str = f"[{tier_color}]T{tier}[/{tier_color}]" if tier_color else str(tier)
        return [
            str(item.get("id", "")),
            str(item.get("name", ""))[:45],
            str(item.get("type", "")),
            tier_str,
            website,
            last_str,
            str(item.get("event_count", 0)),
            status,
        ]

    def _row_style(self, item: dict[str, Any]) -> str | None:
        disabled = item.get("disabled")
        return "color: #ef5350" if disabled else "color: #66bb6a"

    # ── source actions ───────────────────────────────────────────────
    def _get_selected(self) -> dict[str, Any] | None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        row_key = (
            table.cursor_coordinate.row if table.cursor_coordinate else None
        )
        if not self._data or row_key is None or row_key >= len(self._data):
            return None
        return self._data[row_key]

    def action_view_source(self) -> None:
        src = self._get_selected()
        if src is not None and isinstance(src["id"], int):
            self.app.push_screen(SourceDetailScreen(src["id"]))

    def action_toggle_source(self) -> None:
        src = self._get_selected()
        if src is None:
            return
        sid, disabled = src["id"], src["disabled"]
        if isinstance(sid, int) and isinstance(disabled, bool):
            self.run_worker(self._toggle_active_source(sid, disabled))
            src["disabled"] = not disabled
            self._render_table()

    async def _toggle_active_source(
        self, source_id: int, disabled: bool
    ) -> None:
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
        new_state = "disabled" if not disabled else "enabled"
        self.app.notify(f"Source {new_state}", severity="information")

    def action_new_source(self) -> None:
        self.app.push_screen(SourceWizardScreen())

    def action_edit_source(self) -> None:
        src = self._get_selected()
        if src is None:
            return
        source_id = src["id"]
        if not isinstance(source_id, int):
            return
        self.app.push_screen(
            SourceWizardScreen(source_id=source_id, data=src)
        )

    def action_delete_source(self) -> None:
        src = self._get_selected()
        if src is None:
            return
        source_id = src["id"]
        source_name = src["name"]
        if not isinstance(source_id, int):
            return
        self.run_worker(self._do_delete(source_id, str(source_name)))

    async def _do_delete(self, source_id: int, source_name: str) -> None:
        from tui.widgets.confirm_dialog import ConfirmDialog

        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(
                f"Delete source '{source_name}'?\n\n"
                "This soft-deletes the source. It can be restored from the DB."
            )
        )
        if not confirmed:
            return
        session = await get_session()
        try:
            await session.execute(
                text(
                    "UPDATE sources SET deleted_at = :now WHERE id = :sid"
                ),
                {
                    "now": datetime.now(UTC).replace(tzinfo=None),
                    "sid": source_id,
                },
            )
            await session.commit()
            self.app.notify(
                f"'{source_name}' deleted", severity="information"
            )
        finally:
            await session.close()
        await self._load_data()

    def action_force_crawl(self) -> None:
        src = self._get_selected()
        if src is not None and isinstance(src["id"], int):
            self.run_worker(
                self._toggle_force_crawl(src["id"], str(src["name"]))
            )

    async def _toggle_force_crawl(
        self, source_id: int, source_name: str
    ) -> None:
        from tui.widgets.confirm_dialog import ConfirmDialog

        session = await get_session()
        try:
            result = await session.execute(
                text(
                    "SELECT force_crawl FROM crawl_configs "
                    "WHERE source_id = :sid"
                ),
                {"sid": source_id},
            )
            row = result.fetchone()
            current = bool(row[0]) if row and row[0] else False
            new_val = not current
            action = "ENABLE" if new_val else "DISABLE"
            confirmed = await self.app.push_screen_wait(
                ConfirmDialog(
                    f"{action} force crawl for '{source_name}'?\n\n"
                    "When enabled, this source will be crawled on the next "
                    "pipeline run regardless of schedule."
                )
            )
            if not confirmed:
                return
            if row is None:
                await session.execute(
                    text(
                        "INSERT INTO crawl_configs "
                        "(source_id, crawl_frequency, force_crawl) "
                        "VALUES (:sid, 10080, TRUE)"
                    ),
                    {"sid": source_id},
                )
            else:
                await session.execute(
                    text(
                        "UPDATE crawl_configs SET force_crawl = :val "
                        "WHERE source_id = :sid"
                    ),
                    {"val": new_val, "sid": source_id},
                )
            await session.commit()
        finally:
            await session.close()
        state = "ENABLED" if new_val else "DISABLED"
        self.app.notify(
            f"Force crawl {state.lower()} for '{source_name}'",
            severity="information",
        )

    def action_crawl_source(self) -> None:
        src = self._get_selected()
        if src is not None and isinstance(src["id"], int):
            self.run_worker(self._do_crawl(src["id"], str(src["name"])))

    async def _do_crawl(self, source_id: int, source_name: str) -> None:
        import os
        import sys

        from api.config import get_settings
        from tui.widgets.confirm_dialog import ConfirmDialog

        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(
                f"Crawl '{source_name}'?\n\n"
                "This visits the source website and extracts events.\n"
                "May take 1-3 minutes."
            )
        )
        if not confirmed:
            return
        settings = get_settings()
        pipeline_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "pipeline"
            )
        )
        if sys.platform == "win32":
            python = os.path.join(
                pipeline_dir, ".venv", "Scripts", "python.exe"
            )
        else:
            python = os.path.join(pipeline_dir, ".venv", "bin", "python")
        env: dict[str, str] = {
            "PYTHONUTF8": "1",
            "API_BASE_URL": settings.api_base_url,
            "SYNC_API_KEY": settings.sync_api_key,
            "REDIS_URL": settings.redis_url,
        }
        if not os.path.isfile(python):
            self.app.notify(
                "Pipeline venv not found. Run: cd pipeline && uv sync",
                severity="error",
                timeout=10,
            )
            return
        self.app.push_screen(
            PipelineRunScreen(
                cmd=[python, "main.py", "--ids", str(source_id)],
                cwd=pipeline_dir,
                env=env,
                source_name=source_name,
            )
        )

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
        label = (
            f"Tier: {self._tier_filter}" if self._tier_filter else "Tier: all"
        )
        self.query_one("#tier-label", Label).update(label)
        self._offset = 0
        self.run_worker(self._load_data())
