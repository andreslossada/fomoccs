"""Tag Rules screen — manage tag rewrite, exclude, and remove rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from tui.db import count_tag_rules, get_session, list_tag_rules
from tui.widgets.confirm_dialog import ConfirmDialog
from tui.widgets.input_dialog import InputDialog


class TagRulesScreen(Screen[object]):
    """Manage tag transformation rules."""

    _data: list[dict[str, Any]] = []
    _offset: int = 0
    _total: int = 0

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("enter", "edit_rule", "Edit"),
        ("n", "new_rule", "New Rule"),
        ("d", "delete_rule", "Delete"),
        ("r", "refresh", "Refresh"),
        ("m", "load_more", "More"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Tag Rules[/bold]", id="breadcrumb")
        with Horizontal(id="filters"):
            yield Label("", id="counter")
        yield DataTable(id="tagrules-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#tagrules-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Type", "Pattern", "Replacement", "Active")
        self.run_worker(self._load_data())

    def _render_table(self) -> None:
        table = self.query_one("#tagrules-table", DataTable)
        table.clear()
        for rule in self._data:
            deleted = rule.get("deleted")
            status = "[dim]Deleted[/dim]" if deleted else "[green]Active[/green]"
            table.add_row(
                str(rule.get("id", "")),
                str(rule.get("type", "")),
                str(rule.get("pattern", "")),
                str(rule.get("replacement") or "-"),
                status,
            )
        self.query_one("#counter", Label).update(
            f"{self._offset + len(self._data)} / {self._total}"
        )

    async def _load_data(self) -> None:
        session: AsyncSession = await get_session()
        try:
            self._total = await count_tag_rules(session)
            rules = await list_tag_rules(session, offset=self._offset, limit=50)
            self._data = [
                {
                    "id": r.id,
                    "type": str(r.rule_type),
                    "pattern": r.pattern,
                    "replacement": r.replacement,
                    "deleted": r.deleted_at is not None,
                }
                for r in rules
            ]
        finally:
            await session.close()
        self._render_table()

    def action_new_rule(self) -> None:
        self.run_worker(self._do_new_rule())

    async def _do_new_rule(self) -> None:
        rule_type = await self.app.push_screen_wait(
            InputDialog("Rule type (rewrite/exclude/remove):")
        )
        if not rule_type or rule_type not in ("rewrite", "exclude", "remove"):
            return
        pattern = await self.app.push_screen_wait(
            InputDialog("Pattern (regex or exact match):")
        )
        if not pattern:
            return
        replacement = None
        if rule_type == "rewrite":
            replacement = await self.app.push_screen_wait(
                InputDialog("Replacement text:")
            )
            if not replacement:
                return

        from api.models.tag import TagRule

        session = await get_session()
        try:
            rule = TagRule(
                rule_type=rule_type, pattern=pattern, replacement=replacement
            )
            session.add(rule)
            await session.commit()
        finally:
            await session.close()
        await self._load_data()

    def action_edit_rule(self) -> None:
        table = self.query_one("#tagrules-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if not self._data or row_key is None or row_key >= len(self._data):
            return
        rule = self._data[row_key]
        if rule["deleted"]:
            return
        self.run_worker(self._do_edit_rule(rule))

    async def _do_edit_rule(self, rule: dict[str, Any]) -> None:
        pattern = await self.app.push_screen_wait(
            InputDialog("Pattern:", initial=str(rule["pattern"]))
        )
        if not pattern:
            return
        replacement = None
        if rule["type"] == "rewrite":
            replacement = await self.app.push_screen_wait(
                InputDialog("Replacement:", initial=str(rule.get("replacement") or ""))
            )
            if not replacement:
                return

        from api.models.tag import TagRule

        session = await get_session()
        try:
            values: dict[str, Any] = {"pattern": pattern}
            if replacement is not None:
                values["replacement"] = replacement
            await session.execute(
                update(TagRule).where(TagRule.id == rule["id"]).values(**values)
            )
            await session.commit()
        finally:
            await session.close()
        await self._load_data()

    def action_delete_rule(self) -> None:
        table = self.query_one("#tagrules-table", DataTable)
        row_key = table.cursor_coordinate.row if table.cursor_coordinate else None
        if not self._data or row_key is None or row_key >= len(self._data):
            return
        rule = self._data[row_key]
        if rule["deleted"]:
            return
        if isinstance(rule["pattern"], str):
            self.run_worker(self._do_delete_rule(int(rule["id"]), rule["pattern"]))

    async def _do_delete_rule(self, rule_id: int, pattern: str) -> None:
        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(f"Delete rule '{pattern}'?")
        )
        if not confirmed:
            return

        from api.models.tag import TagRule

        session = await get_session()
        try:
            await session.execute(
                update(TagRule)
                .where(TagRule.id == rule_id)
                .values(deleted_at=datetime.now(UTC).replace(tzinfo=None))
            )
            await session.commit()
        finally:
            await session.close()
        await self._load_data()

    def action_refresh(self) -> None:
        self.run_worker(self._load_data())

    def action_load_more(self) -> None:
        if self._offset + len(self._data) < self._total:
            self._offset += len(self._data)
            self.run_worker(self._load_data())
