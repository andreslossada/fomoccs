"""Location detail screen."""

from __future__ import annotations

from rich.rule import Rule as RichRule
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.db import get_location_with_relations, get_session
from tui.screens.help import HelpModal
from tui.widgets.loading import LoadingIndicator
from tui.widgets.status_badge import format_status


class LocationDetailScreen(Screen[object]):
    """Detailed view of a single location."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
    ]

    def action_show_help(self) -> None:
        self.app.push_screen(
            HelpModal("Location Detail", self.BINDINGS)
        )

    def __init__(self, location_id: int) -> None:
        super().__init__()
        self._location_id = location_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label(
            "[bold reverse #00bcd4]  Location Detail  [/]", id="screen-title"
        )
        yield Label("[bold]▸ Location Detail[/bold]", id="breadcrumb")
        with Vertical(id="location-detail"):
            yield LoadingIndicator("Loading location...", id="detail-spinner")
            yield Static("", id="loc-name")
            yield Static("", id="loc-meta")
            yield Static("", id="loc-desc")
            yield Static("", id="loc-alt-names")
            yield Static("", id="loc-tags")
            yield Static("", id="loc-events")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#detail-spinner", LoadingIndicator).display = True
        self.run_worker(self._load())

    async def _load(self) -> None:
        session: AsyncSession = await get_session()
        try:
            location = await get_location_with_relations(
                session, self._location_id
            )
            if location is None:
                self.query_one("#loc-name", Static).update(
                    "[red]Location not found[/red]"
                )
                return
            self._render_detail(location)
        finally:
            await session.close()
        self.query_one("#detail-spinner", LoadingIndicator).display = False

    def _render_detail(self, location: object) -> None:
        from api.models.location import Location

        loc: Location = location  # type: ignore[assignment]
        emoji = loc.emoji or ""
        self.query_one("#loc-name", Static).update(
            f"[bold reverse]  {emoji} {loc.name}  [/bold reverse]"
        )

        lat = f"{loc.lat:.6f}" if loc.lat is not None else "[dim]N/A[/dim]"
        lng = f"{loc.lng:.6f}" if loc.lng is not None else "[dim]N/A[/dim]"
        self.query_one("#loc-meta", Static).update(
            f"[bold]Type:[/bold] {loc.type}    "
            f"[bold]ID:[/bold] {loc.id}\n"
            f"[bold]📍 Lat:[/bold] {lat}    "
            f"[bold]📍 Lng:[/bold] {lng}\n"
            f"[bold]Address:[/bold] {loc.address or '[dim]N/A[/dim]'}\n"
            f"[bold]Short:[/bold] {loc.short_name or '—'}    "
            f"[bold]Very Short:[/bold] {loc.very_short_name or '—'}\n"
            f"[bold]Website:[/bold] {loc.website_url or '[dim]N/A[/dim]'}\n"
            f"[bold]Created:[/bold] [dim]{loc.created_at}[/dim]    "
            f"[bold]Updated:[/bold] [dim]{loc.updated_at}[/dim]"
        )

        desc = loc.description or "[dim]No description[/dim]"
        self.query_one("#loc-desc", Static).update(
            f"{RichRule('Description', style='cyan', align='left')}\n{desc}"
        )

        alt_names: list[object] = getattr(loc, "alternate_names", [])
        if alt_names:
            names = ", ".join(
                getattr(a, "alternate_name", str(a)) for a in alt_names
            )
            self.query_one("#loc-alt-names", Static).update(
                f"{RichRule('Alternate Names', style='cyan', align='left')}\n"
                f"  {names}"
            )
        else:
            self.query_one("#loc-alt-names", Static).update(
                f"{RichRule('Alternate Names', style='cyan', align='left')}\n"
                "  [dim]No alternate names[/dim]"
            )

        tags: list[object] = getattr(loc, "tags", [])
        if tags:
            tag_names = ", ".join(getattr(t, "name", str(t)) for t in tags)
            self.query_one("#loc-tags", Static).update(
                f"[bold]Tags:[/bold] {tag_names}"
            )
        else:
            self.query_one("#loc-tags", Static).update("[dim]No tags[/dim]")

        events: list[object] = getattr(loc, "events", [])
        if events:
            lines: list[str] = [
                str(
                    RichRule(
                        f"Events at this location ({len(events)})",
                        style="cyan",
                        align="left",
                    )
                )
            ]
            for ev in events[:20]:
                ev_name = getattr(ev, "name", str(ev))
                ev_status = str(getattr(ev, "status", ""))
                badge = format_status(ev_status)
                lines.append(f"  {badge} {ev_name[:60]}")
            if len(events) > 20:
                lines.append(
                    f"  [dim]... and {len(events) - 20} more[/dim]"
                )
            self.query_one("#loc-events", Static).update("\n".join(lines))
        else:
            self.query_one("#loc-events", Static).update(
                f"{RichRule('Events', style='cyan', align='left')}\n"
                "  [dim]No events at this location[/dim]"
            )
