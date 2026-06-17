"""Event detail screen."""

from __future__ import annotations

from rich.rule import Rule as RichRule
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.db import get_event_with_relations, get_session
from tui.screens.help import HelpModal
from tui.widgets.loading import LoadingIndicator
from tui.widgets.status_badge import format_status


class EventDetailScreen(Screen[object]):
    """Detailed view of a single event."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
    ]

    def action_show_help(self) -> None:
        self.app.push_screen(
            HelpModal("Event Detail", self.BINDINGS)
        )

    def __init__(self, event_id: int) -> None:
        super().__init__()
        self._event_id = event_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label(
            "[bold reverse #00bcd4]  Event Detail  [/]", id="screen-title"
        )
        yield Label("[bold]▸ Event Detail[/bold]", id="breadcrumb")
        with Vertical(id="event-detail"):
            yield LoadingIndicator("Loading event...", id="detail-spinner")
            yield Static("", id="event-name")
            yield Static("", id="event-meta")
            yield Static("", id="event-desc")
            yield Static("", id="event-occurrences")
            yield Static("", id="event-tags")
            yield Static("", id="event-urls")
            yield Static("", id="event-sources")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#detail-spinner", LoadingIndicator).display = True
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
        self.query_one("#detail-spinner", LoadingIndicator).display = False

    def _render_detail(self, event: object) -> None:
        from api.models.event import Event

        ev: Event = event  # type: ignore[assignment]
        emoji = ev.emoji or ""
        status = format_status(str(ev.status))
        self.query_one("#event-name", Static).update(
            f"[bold reverse]  {emoji} {ev.name}  [/bold reverse]"
        )
        self.query_one("#event-meta", Static).update(
            f"[bold]Status:[/bold] {status}    "
            f"[bold]ID:[/bold] {ev.id}\n"
            f"[bold]Location:[/bold] "
            f"{ev.location.name if ev.location else '[dim]N/A[/dim]'}\n"
            f"[bold]Sublocation:[/bold] {ev.sublocation or '[dim]—[/dim]'}\n"
            f"[bold]Created:[/bold] [dim]{ev.created_at}[/dim]    "
            f"[bold]Updated:[/bold] [dim]{ev.updated_at}[/dim]"
        )

        desc = ev.description or "[dim]No description[/dim]"
        self.query_one("#event-desc", Static).update(
            f"{RichRule('Description', style='cyan', align='left')}\n{desc}"
        )

        occurrences: list[object] = getattr(ev, "occurrences", [])
        if occurrences:
            lines: list[str] = [
                str(RichRule("Occurrences", style="cyan", align="left"))
            ]
            for occ in occurrences:
                start = getattr(occ, "start_date", "")
                end = getattr(occ, "end_date", "")
                start_t = getattr(occ, "start_time", "") or ""
                end_t = getattr(occ, "end_time", "") or ""
                detail = f"  📅 {start}"
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
                f"{RichRule('Occurrences', style='cyan', align='left')}\n"
                "  [dim]No occurrences[/dim]"
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
            lines = [str(RichRule("URLs", style="cyan", align="left"))]
            for u in urls:
                lines.append(f"  🔗 {getattr(u, 'url', '')}")
            self.query_one("#event-urls", Static).update("\n".join(lines))
        else:
            self.query_one("#event-urls", Static).update(
                f"{RichRule('URLs', style='cyan', align='left')}\n"
                "  [dim]No URLs[/dim]"
            )

        sources: list[object] = getattr(ev, "sources", [])
        if sources:
            lines = [
                str(RichRule("Source Lineage", style="cyan", align="left"))
            ]
            for es in sources:
                src_id = getattr(es, "source_id", "")
                trust = getattr(es, "trust_score", "")
                primary = (
                    " [green]★ primary[/green]"
                    if getattr(es, "is_primary", False)
                    else ""
                )
                lines.append(f"  Source #{src_id}  trust={trust}{primary}")
            self.query_one("#event-sources", Static).update("\n".join(lines))
        else:
            self.query_one("#event-sources", Static).update(
                f"{RichRule('Source Lineage', style='cyan', align='left')}\n"
                "  [dim]No source lineage[/dim]"
            )
