"""Sources list screen — browse, filter, and manage data sources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label

from tui.db import count_sources, get_session, list_sources
from tui.screens.pipeline_run import PipelineRunScreen
from tui.screens.source_wizard import SourceWizardScreen
from tui.screens.sources_detail import SourceDetailScreen

VET_TZ = timezone(timedelta(hours=-4))


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
        ("e", "edit_source", "Edit"),
        ("d", "delete_source", "Delete"),
        ("space", "toggle_source", "Toggle"),
        ("c", "crawl_source", "Crawl"),
        ("f", "force_crawl", "Force"),
        ("a", "toggle_active_only", "Active"),
        ("t", "cycle_tier", "Tier"),
        ("n", "new_source", "New"),
        ("r", "refresh", "Refresh"),
        ("m", "load_more", "More"),
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
        table.add_columns(
            "ID", "Name", "Type", "Tier", "Website", "Last Crawl", "Events", "Status"
        )
        self.run_worker(self._load_data())

    def _render_table(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.clear()
        if self._loading:
            table.add_row("", "[dim]Loading...[/dim]", "", "", "", "", "", "")
            self.query_one("#counter", Label).update("[dim]Loading...[/dim]")
            return
        if not self._data:
            table.add_row("", "[dim]No results[/dim]", "", "", "", "", "", "")
            self.query_one("#counter", Label).update("0 / 0")
            return
        for src in self._data:
            status = (
                "[red]Disabled[/red]"
                if src.get("disabled")
                else "[green]Active[/green]"
            )
            website = str(src.get("website", ""))[:40]
            last = src.get("last_crawled_at")
            if last is not None:
                last_local = last.replace(tzinfo=UTC).astimezone(VET_TZ)
                last_str = str(last_local)[:10]
            else:
                last_str = "-"
            events = str(src.get("event_count", 0))
            table.add_row(
                str(src.get("id", "")),
                str(src.get("name", ""))[:45],
                str(src.get("type", "")),
                str(src.get("tier", "")),
                website,
                last_str,
                events,
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
            self._data = sources
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
        src = self._get_selected()
        if src is not None and isinstance(src["id"], int):
            self.app.push_screen(SourceDetailScreen(src["id"]))

    def action_toggle_source(self) -> None:
        src = self._get_selected()
        if src is not None:
            sid = src["id"]
            disabled = src["disabled"]
            if isinstance(sid, int) and isinstance(disabled, bool):
                self.run_worker(self._toggle_active_source(sid, disabled))
                src["disabled"] = not disabled
                self._render_table()

    def action_new_source(self) -> None:
        self.app.push_screen(SourceWizardScreen())

    def action_edit_source(self) -> None:
        src = self._get_selected()
        if src is None:
            return
        source_id = src["id"]
        if not isinstance(source_id, int):
            return
        self.app.push_screen(SourceWizardScreen(source_id=source_id, data=src))

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
            ConfirmDialog(f"Delete source '{source_name}'?\n\nThis soft-deletes the source. It can be restored from the DB.")
        )
        if not confirmed:
            return

        from sqlalchemy import text

        session = await get_session()
        try:
            await session.execute(
                text("UPDATE sources SET deleted_at = :now WHERE id = :sid"),
                {"now": datetime.now(UTC).replace(tzinfo=None), "sid": source_id},
            )
            await session.commit()
            self.app.notify(f"'{source_name}' deleted", severity="information")
        finally:
            await session.close()
        await self._load_data()

    def _get_selected(self) -> dict[str, Any] | None:
        table = self.query_one("#sources-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if not self._data or row_key is None or row_key >= len(self._data):
            return None
        return self._data[row_key]

    def action_force_crawl(self) -> None:
        src = self._get_selected()
        if src is not None and isinstance(src["id"], int):
            self.run_worker(self._toggle_force_crawl(src["id"], str(src["name"])))

    async def _toggle_force_crawl(self, source_id: int, source_name: str) -> None:
        from sqlalchemy import text

        from tui.widgets.confirm_dialog import ConfirmDialog

        session = await get_session()
        try:
            # Check current force_crawl status
            result = await session.execute(
                text("SELECT force_crawl FROM crawl_configs WHERE source_id = :sid"),
                {"sid": source_id},
            )
            row = result.fetchone()
            current = bool(row[0]) if row and row[0] else False
            new_val = not current
            action = "ENABLE" if new_val else "DISABLE"

            confirmed = await self.app.push_screen_wait(
                ConfirmDialog(f"{action} force crawl for '{source_name}'?\n\nWhen enabled, this source will be crawled on the next pipeline run regardless of schedule.")
            )
            if not confirmed:
                return

            if row is None:
                await session.execute(
                    text(
                        "INSERT INTO crawl_configs (source_id, crawl_frequency, force_crawl) "
                        "VALUES (:sid, 10080, TRUE)"
                    ),
                    {"sid": source_id},
                )
            else:
                await session.execute(
                    text(
                        "UPDATE crawl_configs SET force_crawl = :val WHERE source_id = :sid"
                    ),
                    {"val": new_val, "sid": source_id},
                )
            await session.commit()
        finally:
            await session.close()

    def action_crawl_source(self) -> None:
        src = self._get_selected()
        if src is not None and isinstance(src["id"], int):
            self.run_worker(self._do_crawl(src["id"], str(src["name"])))

    async def _do_crawl(self, source_id: int, source_name: str) -> None:
        from tui.widgets.confirm_dialog import ConfirmDialog

        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(f"Crawl '{source_name}'?\n\nThis visits the source website and extracts events.\nMay take 1-3 minutes.")
        )
        if not confirmed:
            return

        import os
        import sys

        from api.config import get_settings

        settings = get_settings()

        # Use pipeline's own venv Python — avoids uv VIRTUAL_ENV conflicts
        pipeline_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "pipeline"
        )
        pipeline_dir = os.path.abspath(pipeline_dir)

        if sys.platform == "win32":
            python = os.path.join(pipeline_dir, ".venv", "Scripts", "python.exe")
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
