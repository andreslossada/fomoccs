"""Tag Rules screen — manage tag rewrite, exclude, and remove rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from textual.binding import Binding
from textual.widgets import DataTable

from api.models.tag import TagRule
from tui.db import count_tag_rules, get_session, list_tag_rules
from tui.screens.base import BaseListScreen
from tui.widgets.confirm_dialog import ConfirmDialog
from tui.widgets.input_dialog import InputDialog
from tui.widgets.status_badge import format_status


class TagRulesScreen(BaseListScreen):
    """Manage tag transformation rules."""

    _table_id = "tagrules-table"
    _columns = ["ID", "Type", "Pattern", "Replacement", "Active"]
    _title = "Tag Rules"
    _has_search = False
    _empty_message = "No tag rules defined. Press n to create one."

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
        Binding("enter", "edit_rule", "Edit", show=False),
        Binding("n", "new_rule", "New Rule", show=False),
        Binding("d", "delete_rule", "Delete", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("m", "load_more", "More"),
        Binding("p", "load_less", "Prev"),
    ]

    # ── data loading (BaseListScreen overrides) ────────────────────
    async def _load_page(
        self, session: AsyncSession, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        rules = await list_tag_rules(session, offset=offset, limit=limit)
        return [
            {
                "id": r.id,
                "type": str(r.rule_type),
                "pattern": r.pattern,
                "replacement": r.replacement,
                "deleted": r.deleted_at is not None,
            }
            for r in rules
        ]

    async def _count_total(self, session: AsyncSession) -> int:
        return await count_tag_rules(session)

    def _render_row(self, item: dict[str, Any]) -> list[str]:
        deleted = item.get("deleted")
        status = format_status("deleted" if deleted else "active",
                              label="Deleted" if deleted else "Active")
        return [
            str(item.get("id", "")),
            str(item.get("type", "")),
            str(item.get("pattern", "")),
            str(item.get("replacement") or "-"),
            status,
        ]

    # ── rule actions ─────────────────────────────────────────────────
    def action_new_rule(self) -> None:
        self.run_worker(self._do_new_rule())

    async def _do_new_rule(self) -> None:
        rule_type = await self.app.push_screen_wait(
            InputDialog("Rule type (rewrite/exclude/remove):")
        )
        if not rule_type or rule_type not in (
            "rewrite",
            "exclude",
            "remove",
        ):
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
        session = await get_session()
        try:
            rule = TagRule(
                rule_type=rule_type,
                pattern=pattern,
                replacement=replacement,
            )
            session.add(rule)
            await session.commit()
        finally:
            await session.close()
        self.app.notify("Rule created", severity="information")
        await self._load_data()

    def action_edit_rule(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        row_key = (
            table.cursor_coordinate.row if table.cursor_coordinate else None
        )
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
                InputDialog(
                    "Replacement:",
                    initial=str(rule.get("replacement") or ""),
                )
            )
            if not replacement:
                return
        session = await get_session()
        try:
            values: dict[str, Any] = {"pattern": pattern}
            if replacement is not None:
                values["replacement"] = replacement
            await session.execute(
                update(TagRule)
                .where(TagRule.id == rule["id"])
                .values(**values)
            )
            await session.commit()
        finally:
            await session.close()
        self.app.notify("Rule updated", severity="information")
        await self._load_data()

    def action_delete_rule(self) -> None:
        table = self.query_one(f"#{self._table_id}", DataTable)
        row_key = (
            table.cursor_coordinate.row if table.cursor_coordinate else None
        )
        if not self._data or row_key is None or row_key >= len(self._data):
            return
        rule = self._data[row_key]
        if rule["deleted"]:
            return
        if isinstance(rule["pattern"], str):
            self.run_worker(
                self._do_delete_rule(int(rule["id"]), rule["pattern"])
            )

    async def _do_delete_rule(self, rule_id: int, pattern: str) -> None:
        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(f"Delete rule '{pattern}'?")
        )
        if not confirmed:
            return
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
        self.app.notify(f"Rule '{pattern}' deleted", severity="information")
        await self._load_data()
