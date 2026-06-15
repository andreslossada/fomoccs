"""Source detail screen."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.db import get_session, get_source_with_relations


class SourceDetailScreen(Screen[object]):
    """Detailed view of a single source."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, source_id: int) -> None:
        super().__init__()
        self._source_id = source_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Source Detail[/bold]", id="breadcrumb")
        yield Container(
            Static("Loading...", id="source-name"),
            Static("", id="source-meta"),
            Static("", id="source-urls"),
            Static("", id="source-config"),
            id="source-detail",
        )
        yield Footer()

    def on_mount(self) -> None:
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

    def _render_detail(self, source: object) -> None:
        from api.models.source import Source

        s: Source = source  # type: ignore[assignment]

        status = "[red]Disabled[/red]" if s.disabled else "[green]Active[/green]"
        self.query_one("#source-name", Static).update(
            f"[bold reverse]  {s.name}  [/bold reverse]"
        )
        self.query_one("#source-meta", Static).update(
            f"[bold]Type:[/bold] {s.type}    "
            f"[bold]Tier:[/bold] {s.tier}    "
            f"[bold]Status:[/bold] {status}\n"
            f"[bold]Trust Level:[/bold] {s.trust_level or '-'}    "
            f"[bold]Rate Limit:[/bold] {s.min_request_interval_seconds or '-'}s\n"
            f"[bold]Created:[/bold] {s.created_at}    "
            f"[bold]Updated:[/bold] {s.updated_at}"
        )

        urls: list[object] = getattr(s, "urls", [])
        if urls:
            lines: list[str] = ["[bold]URLs:[/bold]"]
            for u in sorted(urls, key=lambda x: getattr(x, "sort_order", 0)):
                url_val = getattr(u, "url", "")
                deleted = getattr(u, "deleted_at", None)
                prefix = "  [dim]" if deleted else "  "
                lines.append(f"{prefix}{url_val}")
            self.query_one("#source-urls", Static).update("\n".join(lines))
        else:
            self.query_one("#source-urls", Static).update("[dim]No URLs[/dim]")

        config: object | None = getattr(s, "crawl_config", None)
        if config is not None:
            fields: list[tuple[str, object]] = [
                ("Mode", getattr(config, "crawl_mode", "-")),
                ("Frequency", getattr(config, "crawl_frequency", "-")),
                ("Max Pages", getattr(config, "max_pages", "-")),
                ("Selector", getattr(config, "selector") or "-"),
                ("Keywords", getattr(config, "keywords") or "-"),
                ("Text Mode", str(getattr(config, "text_mode", False))),
                ("Stealth", str(getattr(config, "use_stealth", False))),
                ("Timeout", getattr(config, "crawl_timeout") or "-"),
                ("Image Process", str(getattr(config, "process_images", False))),
            ]
            lines = ["[bold]Crawl Config:[/bold]"]
            for label, value in fields:
                lines.append(f"  {label}: {value}")
            notes = getattr(config, "notes", None)
            if notes:
                lines.append(f"  Notes: {notes}")
            self.query_one("#source-config", Static).update("\n".join(lines))
        else:
            self.query_one("#source-config", Static).update(
                "[dim]No crawl config[/dim]"
            )
