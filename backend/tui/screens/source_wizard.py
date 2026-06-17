"""New Source wizard screen — guided form to add event sources."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
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
from tui.screens.help import HelpModal


class SourceWizardScreen(Screen[None]):
    """Guided form to create or edit a source with URLs."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Cancel"),
        Binding("?", "show_help", "Help"),
    ]

    def action_show_help(self) -> None:
        title = (
            "Edit Source" if self._source_id else "New Source"
        )
        self.app.push_screen(HelpModal(title, self.BINDINGS))

    _type_options: list[tuple[str, str]] = [
        ("crawler", "crawler"),
        ("api", "api"),
        ("user_submission", "user_submission"),
        ("partner_feed", "partner_feed"),
    ]

    _mode_options: list[tuple[str, str]] = [
        ("browser (website scraping)", "browser"),
        ("json_api (API endpoint)", "json_api"),
        ("instagram (profile scraping)", "instagram"),
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
        yield Label(
            f"[bold reverse #00bcd4]  {title}  [/]",
            id="screen-title",
        )
        yield Label(f"[bold]{title}[/bold]", id="breadcrumb")
        with Vertical(id="wizard-form"):
            yield Label("Name:")
            yield Input(placeholder="e.g. Teatro Chacao", id="sw-name")

            yield Label("Type:")
            yield Select(self._type_options, id="sw-type")

            yield Label("Crawl mode:")
            yield Select(self._mode_options, id="sw-mode")

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

            # Instagram-specific fields (hidden by default)
            yield Label("Instagram username:", id="ig-label")
            yield Input(placeholder="@elgallocinefilo", id="sw-ig-username")

            yield Label("Max posts per crawl:", id="ig-max-label")
            yield Input(placeholder="20", id="sw-ig-max-posts")

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

        self._update_instagram_visibility()
        self.query_one("#sw-name", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sw-mode":
            self._update_instagram_visibility()

    def _update_instagram_visibility(self) -> None:
        mode_sel = self.query_one("#sw-mode", Select)
        is_instagram = str(mode_sel.value) == "instagram"
        display = "block" if is_instagram else "none"
        for wid_id in ("ig-label", "sw-ig-username", "ig-max-label", "sw-ig-max-posts"):
            self.query_one(f"#{wid_id}").display = display

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
        for val, label in self._type_options:
            if val == src_type:
                type_select.value = val
                break

        # Pre-select crawl mode
        crawl_mode = str(data.get("crawl_mode", "browser"))
        mode_select = self.query_one("#sw-mode", Select)
        for label, val in self._mode_options:
            if val == crawl_mode:
                mode_select.value = val
                break

        # Pre-select tier
        tier = data.get("tier", 1)
        tier_select = self.query_one("#sw-tier", Select)
        for _label, val in tier_select._options:
            if val == tier:
                tier_select.value = val
                break

        # Prepopulate Instagram fields
        if crawl_mode == "instagram":
            ig_config = data.get("json_api_config") or {}
            self.query_one("#sw-ig-username", Input).value = str(
                ig_config.get("username", "")
            )
            self.query_one("#sw-ig-max-posts", Input).value = str(
                ig_config.get("max_posts", 20)
            )

        self._update_instagram_visibility()

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

        # Instagram sources don't require URLs
        mode_sel = self.query_one("#sw-mode", Select)
        crawl_mode = str(mode_sel.value) if mode_sel.value else "browser"
        if crawl_mode != "instagram" and not self._urls and not self._source_id:
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
                    session, name, source_type, tier, trust_level, crawl_mode
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
        crawl_mode: str,
    ) -> None:
        from sqlalchemy import text

        result = await session.execute(
            text(
                "INSERT INTO sources (name, type, trust_level, tier) "
                "VALUES (:name, :type, :trust, :tier) RETURNING id"
            ),
            {
                "name": name,
                "type": source_type,
                "trust": trust_level,
                "tier": tier,
            },
        )
        source_id = result.scalar_one()

        if crawl_mode == "instagram":
            username = self.query_one("#sw-ig-username", Input).value.strip()
            max_posts_raw = self.query_one("#sw-ig-max-posts", Input).value.strip()
            max_posts = int(max_posts_raw) if max_posts_raw.isdigit() else 20
            ig_config = {"username": username, "max_posts": max_posts}
            await session.execute(
                text(
                    "INSERT INTO crawl_configs "
                    "(source_id, crawl_frequency, crawl_mode, json_api_config) "
                    "VALUES (:sid, 10080, :mode, :config::jsonb)"
                ),
                {
                    "sid": source_id,
                    "mode": crawl_mode,
                    "config": json.dumps(ig_config),
                },
            )
        else:
            await session.execute(
                text(
                    "INSERT INTO crawl_configs "
                    "(source_id, crawl_frequency, crawl_mode) "
                    "VALUES (:sid, 10080, :mode)"
                ),
                {"sid": source_id, "mode": crawl_mode},
            )
            for i, url in enumerate(self._urls):
                await session.execute(
                    text(
                        "INSERT INTO source_urls (source_id, url, sort_order) "
                        "VALUES (:sid, :url, :order)"
                    ),
                    {"sid": source_id, "url": url, "order": i},
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
