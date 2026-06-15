"""Event detail screen."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.db import get_event_with_relations, get_session


class EventDetailScreen(Screen[object]):
    """Detailed view of a single event."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, event_id: int) -> None:
        super().__init__()
        self._event_id = event_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Event Detail[/bold]", id="breadcrumb")
        yield Container(
            Static("Loading...", id="event-name"),
            Static("", id="event-meta"),
            Static("", id="event-desc"),
            Static("", id="event-occurrences"),
            Static("", id="event-tags"),
            Static("", id="event-urls"),
            Static("", id="event-sources"),
            id="event-detail",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load())

    async def _load(self) -> None:
        session: AsyncSession = await get_session()
        try:
            event = await get_event_with_relations(session, self._event_id)
            if event is None:
                self.query_one("#event-name", Static).update(
                    "[red]Event not found[/red]"
                )
                return
            self._render_detail(event)
        finally:
            await session.close()

    def _render_detail(self, event: object) -> None:
        from api.models.event import Event

        ev: Event = event  # type: ignore[assignment]
        emoji = ev.emoji or ""
        status_color = {
            "active": "green",
            "archived": "dim",
            "draft": "yellow",
            "cancelled": "red",
        }.get(str(ev.status), "")
        self.query_one("#event-name", Static).update(
            f"[bold reverse]  {emoji} {ev.name}  [/bold reverse]"
        )
        self.query_one("#event-meta", Static).update(
            f"[bold]Status:[/bold] [{status_color}]{ev.status}[/{status_color}]    "
            f"[bold]ID:[/bold] {ev.id}\n"
            f"[bold]Location:[/bold] {ev.location.name if ev.location else 'N/A'}\n"
            f"[bold]Sublocation:[/bold] {ev.sublocation or '-'}\n"
            f"[bold]Created:[/bold] {ev.created_at}    "
            f"[bold]Updated:[/bold] {ev.updated_at}"
        )

        desc = ev.description or "[dim]No description[/dim]"
        self.query_one("#event-desc", Static).update(
            f"[bold]Description:[/bold]\n{desc}"
        )

        occurrences: list[object] = getattr(ev, "occurrences", [])
        if occurrences:
            lines: list[str] = ["[bold]Occurrences:[/bold]"]
            for occ in occurrences:
                start = getattr(occ, "start_date", "")
                end = getattr(occ, "end_date", "")
                start_t = getattr(occ, "start_time", "") or ""
                end_t = getattr(occ, "end_time", "") or ""
                detail = f"  {start}"
                if start_t:
                    detail += f" {start_t}"
                if end:
                    detail += f"  →  {end}"
                    if end_t:
                        detail += f" {end_t}"
                lines.append(detail)
            self.query_one("#event-occurrences", Static).update("\n".join(lines))
        else:
            self.query_one("#event-occurrences", Static).update(
                "[dim]No occurrences[/dim]"
            )

        tags: list[object] = getattr(ev, "tags", [])
        if tags:
            tag_names = ", ".join(getattr(t, "name", str(t)) for t in tags)
            self.query_one("#event-tags", Static).update(
                f"[bold]Tags:[/bold] {tag_names}"
            )
        else:
            self.query_one("#event-tags", Static).update("[dim]No tags[/dim]")

        urls: list[object] = getattr(ev, "urls", [])
        if urls:
            lines = ["[bold]URLs:[/bold]"]
            for u in urls:
                lines.append(f"  {getattr(u, 'url', '')}")
            self.query_one("#event-urls", Static).update("\n".join(lines))
        else:
            self.query_one("#event-urls", Static).update("[dim]No URLs[/dim]")

        sources: list[object] = getattr(ev, "sources", [])
        if sources:
            lines = ["[bold]Sources (lineage):[/bold]"]
            for es in sources:
                src_id = getattr(es, "source_id", "")
                trust = getattr(es, "trust_score", "")
                primary = "[green]*[/green]" if getattr(es, "is_primary", False) else ""
                lines.append(f"  #{src_id} trust={trust} {primary}")
            self.query_one("#event-sources", Static).update("\n".join(lines))
        else:
            self.query_one("#event-sources", Static).update(
                "[dim]No source lineage[/dim]"
            )
