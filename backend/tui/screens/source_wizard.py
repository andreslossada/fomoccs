"""New Source wizard screen — guided form to add event sources."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
)

from tui.db import get_session


class SourceWizardScreen(Screen[None]):
    """Guided form to create or edit a source with URLs."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Cancel"),
    ]

    _type_options: list[tuple[str, str]] = [
        ("crawler", "crawler"),
        ("api", "api"),
        ("user_submission", "user_submission"),
        ("partner_feed", "partner_feed"),
    ]

    _urls: list[str] = []

    def __init__(
        self,
        source_id: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._source_id = source_id
        self._existing_data = data

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        title = "Edit Source" if self._source_id else "New Source"
        yield Label(f"[bold]{title}[/bold]", id="breadcrumb")
        with Vertical(id="wizard-form"):
            yield Label("Name:")
            yield Input(placeholder="e.g. Teatro Chacao", id="sw-name")

            yield Label("Type:")
            yield Select(self._type_options, id="sw-type")

            yield Label("Tier (1=fastest, 3=slowest):")
            yield Select(
                [("1 — every 6h", 1), ("2 — every 12h", 2), ("3 — every 24h", 3)],
                id="sw-tier",
            )

            yield Label("Trust level (0.1-1.0, optional):")
            yield Input(placeholder="e.g. 0.8", id="sw-trust")

            yield Label("")
            with Horizontal(id="url-row"):
                yield Input(placeholder="https://...", id="sw-url")
                yield Button("Add URL", id="btn-add-url", variant="primary")
            yield DataTable(id="sw-urls-table")

            with Horizontal(id="wizard-actions"):
                label = "Update Source" if self._source_id else "Save Source"
                yield Button(label, id="btn-save", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="default")

            yield Label("", id="sw-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sw-urls-table", DataTable)
        table.add_columns("#", "URL")

        if self._existing_data is not None:
            self._prepopulate()

        self.query_one("#sw-name", Input).focus()

    def _prepopulate(self) -> None:
        data = self._existing_data
        if data is None:
            return
        self.query_one("#sw-name", Input).value = str(data.get("name", ""))
        self.query_one("#sw-trust", Input).value = str(
            data.get("trust_level") or ""
        )

        # Pre-select type
        src_type = str(data.get("type", "crawler"))
        type_select = self.query_one("#sw-type", Select)
        for i, (val, label) in enumerate(self._type_options):
            if val == src_type:
                type_select.value = val
                break

        # Pre-select tier
        tier = data.get("tier", 1)
        tier_select = self.query_one("#sw-tier", Select)
        # Set tier value by finding matching option
        for label, val in tier_select._options:
            if val == tier:
                tier_select.value = val
                break

        # Load existing URLs
        self.run_worker(self._load_existing_urls())

    async def _load_existing_urls(self) -> None:
        if self._source_id is None:
            return
        from sqlalchemy import text

        session = await get_session()
        try:
            result = await session.execute(
                text(
                    "SELECT url FROM source_urls "
                    "WHERE source_id = :sid AND deleted_at IS NULL "
                    "ORDER BY sort_order"
                ),
                {"sid": self._source_id},
            )
            self._urls = [row[0] for row in result.fetchall()]
        finally:
            await session.close()
        self._render_urls()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-add-url":
            self._add_url()
        elif bid == "btn-save":
            self.run_worker(self._save_source())
        elif bid == "btn-cancel":
            self.dismiss(None)

    def _add_url(self) -> None:
        url_input = self.query_one("#sw-url", Input)
        url = url_input.value.strip()
        if not url:
            return
        self._urls.append(url)
        self._render_urls()
        url_input.clear()
        url_input.focus()

    def _render_urls(self) -> None:
        table = self.query_one("#sw-urls-table", DataTable)
        table.clear()
        for i, url in enumerate(self._urls, 1):
            table.add_row(str(i), url)

    async def _save_source(self) -> None:
        name = self.query_one("#sw-name", Input).value.strip()
        if not name:
            self._status("[red]Name is required[/red]")
            return
        if not self._urls and not self._source_id:
            self._status("[red]At least one URL is required[/red]")
            return

        type_sel = self.query_one("#sw-type", Select)
        source_type = str(type_sel.value) if type_sel.value else "crawler"

        tier_sel = self.query_one("#sw-tier", Select)
        tier = int(tier_sel.value) if tier_sel.value else 1

        trust_text = self.query_one("#sw-trust", Input).value.strip()
        trust_level: float | None = None
        if trust_text:
            try:
                trust_level = float(trust_text)
            except ValueError:
                self._status("[red]Invalid trust level[/red]")
                return

        session: AsyncSession = await get_session()
        try:
            if self._source_id is not None:
                await self._update_source(
                    session, name, source_type, tier, trust_level
                )
            else:
                await self._insert_source(
                    session, name, source_type, tier, trust_level
                )
            await session.commit()
        except Exception as e:
            await session.rollback()
            self._status(f"[red]Error: {e}[/red]")
            return
        finally:
            await session.close()

        action = "updated" if self._source_id else "saved"
        self._status(f"[green]Source {action}![/green]")
        self.set_timer(1, lambda: self.dismiss(True))

    async def _insert_source(
        self,
        session: AsyncSession,
        name: str,
        source_type: str,
        tier: int,
        trust_level: float | None,
    ) -> None:
        from sqlalchemy import text

        result = await session.execute(
            text(
                "INSERT INTO sources (name, type, trust_level, tier) "
                "VALUES (:name, :type, :trust, :tier) RETURNING id"
            ),
            {"name": name, "type": source_type, "trust": trust_level, "tier": tier},
        )
        source_id = result.scalar_one()

        for i, url in enumerate(self._urls):
            await session.execute(
                text(
                    "INSERT INTO source_urls (source_id, url, sort_order) "
                    "VALUES (:sid, :url, :order)"
                ),
                {"sid": source_id, "url": url, "order": i},
            )

        await session.execute(
            text(
                "INSERT INTO crawl_configs (source_id, crawl_frequency) "
                "VALUES (:sid, 10080)"
            ),
            {"sid": source_id},
        )

    async def _update_source(
        self,
        session: AsyncSession,
        name: str,
        source_type: str,
        tier: int,
        trust_level: float | None,
    ) -> None:
        from sqlalchemy import text

        await session.execute(
            text(
                "UPDATE sources SET name=:name, type=:type, trust_level=:trust, "
                "tier=:tier WHERE id=:sid"
            ),
            {
                "name": name,
                "type": source_type,
                "trust": trust_level,
                "tier": tier,
                "sid": self._source_id,
            },
        )

        # Replace URLs: soft-delete existing, insert new
        await session.execute(
            text(
                "UPDATE source_urls SET deleted_at = now() "
                "WHERE source_id = :sid AND deleted_at IS NULL"
            ),
            {"sid": self._source_id},
        )
        for i, url in enumerate(self._urls):
            await session.execute(
                text(
                    "INSERT INTO source_urls (source_id, url, sort_order) "
                    "VALUES (:sid, :url, :order)"
                ),
                {"sid": self._source_id, "url": url, "order": i},
            )

    def _status(self, msg: str) -> None:
        self.query_one("#sw-status", Label).update(msg)
