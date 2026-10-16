"""Source detail screen."""

from __future__ import annotations

from rich.rule import Rule as RichRule
from rich.table import Table as RichTable
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.db import get_session, get_source_with_relations
from tui.screens.help import HelpModal
from tui.widgets.loading import LoadingIndicator
from tui.widgets.status_badge import format_status


class SourceDetailScreen(Screen[object]):
    """Detailed view of a single source."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("?", "show_help", "Help"),
    ]

    def action_show_help(self) -> None:
        self.app.push_screen(
            HelpModal("Source Detail", self.BINDINGS)
        )

    def __init__(self, source_id: int) -> None:
        super().__init__()
        self._source_id = source_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label(
            "[bold reverse #00bcd4]  Source Detail  [/]", id="screen-title"
        )
        yield Label("[bold]\u25b8 Source Detail[/bold]", id="breadcrumb")
        with Vertical(id="source-detail"):
            yield LoadingIndicator("Loading source...", id="detail-spinner")
            yield Static("", id="source-name")
            yield Static("", id="source-meta")
            yield Static("", id="source-urls")
            yield Static("", id="source-config")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#detail-spinner", LoadingIndicator).display = True
        self.run_worker(self._load())

    async def _load(self) -> None:
        session: AsyncSession = await get_session()
        try:
            source = await get_source_with_relations(session, self._source_id)
            if source is None:
                self.query_one("#source-name", Static).update(
                    "[red]Source not found[/red]"
                )
                return
            self._render_detail(source)
        finally:
            await session.close()
        self.query_one("#detail-spinner", LoadingIndicator).display = False

    def _render_detail(self, source: object) -> None:
        from api.models.source import Source

        s: Source = source  # type: ignore[assignment]

        status = format_status(
            "disabled" if s.disabled else "active",
            label="Disabled" if s.disabled else "Active",
        )
        self.query_one("#source-name", Static).update(
            f"[bold reverse]  {s.name}  [/bold reverse]"
        )

        table = RichTable(show_header=False, box=None, padding=(0, 2), expand=False)
        table.add_column(style="bold cyan", width=16)
        table.add_column(width=22)
        table.add_column(style="bold cyan", width=16)
        table.add_column()
        table.add_row("Type", str(s.type), "Tier", f"T{s.tier}")
        table.add_row("Status", status, "Trust Level", str(s.trust_level or "\u2014"))
        table.add_row(
            "Rate Limit", f"{s.min_request_interval_seconds or '\u2014'}s",
            "", "",
        )
        table.add_row(
            "Created", f"[dim]{s.created_at}[/dim]",
            "Updated", f"[dim]{s.updated_at}[/dim]",
        )
        self.query_one("#source-meta", Static).update(str(table))

        # URLs
        urls: list[object] = getattr(s, "urls", [])
        if urls:
            lines: list[str] = [str(RichRule("URLs", style="cyan", align="left"))]
            for u in sorted(urls, key=lambda x: getattr(x, "sort_order", 0)):
                url_val = getattr(u, "url", "")
                deleted = getattr(u, "deleted_at", None)
                if deleted:
                    lines.append(f"  [dim]\U0001f517 {url_val} (deleted)[/dim]")
                else:
                    lines.append(f"  \U0001f517 {url_val}")
            self.query_one("#source-urls", Static).update("\n".join(lines))
        else:
            self.query_one("#source-urls", Static).update(
                f"{RichRule('URLs', style='cyan', align='left')}\n"
                "  [dim]No URLs configured[/dim]"
            )

        # Crawl config as Rich Table
        config: object | None = getattr(s, "crawl_config", None)
        if config is not None:
            mode = str(getattr(config, "crawl_mode", "-"))
            table = RichTable(
                show_header=False,
                box=None,
                padding=(0, 2),
                expand=False,
            )
            table.add_column(style="bold cyan", width=16)
            table.add_column()
            table.add_row("Mode", mode)
            if mode == "instagram":
                ig_cfg = getattr(config, "json_api_config", None) or {}
                table.add_row("IG Username", str(ig_cfg.get("username", "\u2014")))
                table.add_row("IG Max Posts", str(ig_cfg.get("max_posts", 20)))
            table.add_row("Frequency", f"{getattr(config, 'crawl_frequency', '-')} days")
            table.add_row("Max Pages", str(getattr(config, "max_pages", "-")))
            table.add_row("Selector", str(getattr(config, "selector") or "\u2014"))
            table.add_row("Keywords", str(getattr(config, "keywords") or "\u2014"))
            table.add_row("Text Mode", str(getattr(config, "text_mode", False)))
            table.add_row("Stealth", str(getattr(config, "use_stealth", False)))
            table.add_row("Timeout", f"{getattr(config, 'crawl_timeout') or '-'}s")
            table.add_row("Image Process", str(getattr(config, "process_images", False)))

            notes = getattr(config, "notes", None)
            output = f"{RichRule('Crawl Config', style='cyan', align='left')}\n{table}"
            if notes:
                output += f"\n[dim]Notes: {notes}[/dim]"
            self.query_one("#source-config", Static).update(output)
        else:
            self.query_one("#source-config", Static).update(
                f"{RichRule('Crawl Config', style='cyan', align='left')}\n"
                "  [dim]No crawl config[/dim]"
            )
