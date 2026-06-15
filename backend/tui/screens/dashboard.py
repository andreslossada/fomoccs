"""Dashboard screen — system overview and KPIs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.db import (
    active_crawl_jobs,
    db_ping,
    events_by_status,
    get_session,
    llm_usage_today,
    recent_events_count,
    sources_by_tier,
    sources_summary,
)


class DashboardScreen(Screen[object]):
    """Main dashboard with live system stats."""

    BINDINGS = [
        ("s", "app.push_screen('sources')", "Sources"),
        ("e", "app.push_screen('events')", "Events"),
        ("l", "app.push_screen('locations')", "Locations"),
        ("t", "app.push_screen('tag_rules')", "Tag Rules"),
        ("o", "app.push_screen('operations')", "Operations"),
        ("g", "app.push_screen('logs')", "Logs"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Dashboard[/bold]", id="breadcrumb")
        with Grid(id="dashboard-grid"):
            yield Static("Loading...", id="health")
            yield Static("Loading...", id="crawls")
            yield Static("Loading...", id="events-stats")
            yield Static("Loading...", id="sources")
            yield Static("Loading...", id="llm")
        yield Footer()

    def on_mount(self) -> None:
        self.call_later(self.refresh_data)
        self.set_interval(10, self.refresh_data)

    def refresh_data(self) -> None:
        """Schedule async data refresh via call_later."""
        self.query_one("#health", Static).update("[dim]Refreshing...[/dim]")
        self.run_worker(self._do_refresh())

    async def _do_refresh(self) -> None:
        session = await get_session()
        try:
            await self._update_health(session)
            await self._update_crawls(session)
            await self._update_events(session)
            await self._update_sources(session)
            await self._update_llm(session)
        finally:
            await session.close()

    async def _update_health(self, session: AsyncSession) -> None:
        ok = await db_ping(session)
        self.query_one("#health", Static).update(
            f"[bold]DB Status[/bold]\n  {'[green]Connected' if ok else '[red]Disconnected'}"
        )

    async def _update_crawls(self, session: AsyncSession) -> None:
        jobs = await active_crawl_jobs(session)
        if not jobs:
            self.query_one("#crawls", Static).update(
                "[bold]Active Crawls[/bold]\n  [dim]None running[/dim]"
            )
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        lines: list[str] = [f"[bold]Active Crawls ({len(jobs)})[/bold]"]
        for job in jobs[:5]:
            elapsed = ""
            if job.started_at is not None:
                delta = int((now - job.started_at).total_seconds())
                elapsed = f" ({delta}s)"
            lines.append(f"  #{job.id} [yellow]running[/yellow]{elapsed}")
        self.query_one("#crawls", Static).update("\n".join(lines))

    async def _update_events(self, session: AsyncSession) -> None:
        today = await recent_events_count(session)
        by_status = await events_by_status(session)
        status_lines = "  ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))
        self.query_one("#events-stats", Static).update(
            f"[bold]Events[/bold]\n  Today: [bold]{today}[/bold] new\n  {status_lines}"
        )

    async def _update_sources(self, session: AsyncSession) -> None:
        summary = await sources_summary(session)
        by_tier = await sources_by_tier(session)
        tier_lines = "  ".join(f"T{t}: {c}" for t, c in sorted(by_tier.items()))
        self.query_one("#sources", Static).update(
            f"[bold]Sources[/bold]\n"
            f"  Total: {summary['total']}  Active: {summary['active']}  "
            f"Disabled: {summary['disabled']}\n"
            f"  {tier_lines}"
        )

    async def _update_llm(self, session: AsyncSession) -> None:
        usage = await llm_usage_today(session)
        cost = float(usage["cost"])
        calls = int(usage["api_calls"])
        inp = int(usage["input_tokens"])
        out = int(usage["output_tokens"])
        self.query_one("#llm", Static).update(
            f"[bold]LLM Usage (today)[/bold]\n"
            f"  Calls: {calls:,}  In: {inp:,}  Out: {out:,}\n"
            f"  Est. Cost: [bold]${cost:.4f}[/bold]"
        )

    def action_refresh(self) -> None:
        self.refresh_data()
