"""Dashboard screen — system overview and KPIs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import plotext as _plt
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from tui.db import (
    active_crawl_jobs,
    count_stuck_jobs,
    db_ping,
    events_by_status,
    get_session,
    hourly_events_last_24h,
    hourly_llm_cost_last_24h,
    llm_usage_today,
    recent_crawl_jobs_for_dashboard,
    recent_events_count,
    recent_events_for_dashboard,
    sources_by_tier,
    sources_summary,
)
from tui.screens.help import HelpModal
from tui.widgets.loading import LoadingIndicator
from tui.widgets.status_badge import format_status

VET_TZ = timezone(timedelta(hours=-4))


def _card(body: str, title: str, border: str = "cyan") -> Panel:
    return Panel(
        Text.from_markup(body),
        title=title,
        border_style=border,
        padding=(1, 2),
    )


def _section(title: str) -> Rule:
    return Rule(title, style="cyan", align="left")


def _sparkline(values: list[float] | list[int], color: str = "green") -> str:
    """Generate a sparkline bar chart string from a list of numeric values."""
    if not values or sum(values) == 0:
        return ""
    try:
        _plt.clear_data()
        _plt.bar(list(values), fill_color=color, marker="")
        _plt.frame(False)
        _plt.canvas_color("none")
        _plt.axes_color("none")
        _plt.ticks_color("none")
        _plt.plotsize(100, 25)
        return _plt.build().strip()
    except Exception:
        return ""


class DashboardScreen(Screen[object]):
    """Main dashboard with live system stats."""

    BINDINGS = [
        Binding("s", "app.push_screen('sources')", "Sources", show=False),
        Binding("e", "app.push_screen('events')", "Events", show=False),
        Binding("l", "app.push_screen('locations')", "Locations", show=False),
        Binding("t", "app.push_screen('tag_rules')", "Tag Rules", show=False),
        Binding("o", "app.push_screen('operations')", "Operations", show=False),
        Binding("g", "app.push_screen('logs')", "Logs", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("?", "show_help", "Help"),
    ]

    def action_show_help(self) -> None:
        self.app.push_screen(HelpModal("Dashboard", self.BINDINGS))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold reverse #00bcd4]  Dashboard  [/]", id="screen-title")
        yield from self._nav_tabs()
        yield Label("[bold]▸ Dashboard[/bold]", id="breadcrumb")
        with Grid(id="dashboard-grid"):
            yield Static("", id="health")
            yield Static("", id="crawls")
            yield Static("", id="events-stats")
            yield Static("", id="sources")
            yield Static("", id="llm")
            yield Static("", id="recent-events")
        yield Static("", id="last-crawls")
        yield LoadingIndicator("Loading dashboard...", id="dash-spinner")
        yield Footer()

    def on_mount(self) -> None:
        self._show_spinner(True)
        self.call_later(self.refresh_data)
        self.set_interval(30, self.refresh_data)

    def refresh_data(self) -> None:
        self.run_worker(self._do_refresh())

    async def _do_refresh(self) -> None:
        self._show_spinner(True)
        session = await get_session()
        try:
            await self._update_health(session)
            await self._update_crawls(session)
            await self._update_events(session)
            await self._update_sources(session)
            await self._update_llm(session)
            await self._update_recent(session)
            await self._update_last_crawls(session)
        finally:
            await session.close()
        self._show_spinner(False)

    def _show_spinner(self, show: bool) -> None:
        self.query_one("#dash-spinner", LoadingIndicator).display = show

    # ── cards ─────────────────────────────────────────────────────────

    async def _update_health(self, session: AsyncSession) -> None:
        ok = await db_ping(session)
        status = format_status("connected" if ok else "disconnected")
        self.query_one("#health", Static).update(
            _card(
                f"{status}\n[dim]Supabase PostgreSQL[/dim]",
                title="🗄️  DB Status",
                border="green" if ok else "red",
            )
        )

    async def _update_crawls(self, session: AsyncSession) -> None:
        jobs = await active_crawl_jobs(session)
        stuck = await count_stuck_jobs(session)
        if not jobs and not stuck:
            self.query_one("#crawls", Static).update(
                _card(
                    "[dim]No active crawls[/dim]",
                    title="🔄  Active Crawls",
                    border="#666666",
                )
            )
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        lines: list[str] = [f"[bold]{len(jobs) or 0} running[/bold]\n"]
        for job in (jobs or [])[:5]:
            elapsed = ""
            if job.started_at is not None:
                delta = int((now - job.started_at).total_seconds())
                elapsed = f" ({delta}s)"
            lines.append(
                f"  {format_status('running')} #{job.id}{elapsed}"
            )
        if stuck:
            lines.append(
                f"\n[bold dim]⚠  Stuck (>2h): {stuck}[/bold dim]"
            )
        self.query_one("#crawls", Static).update(
            _card("\n".join(lines), title="🔄  Active Crawls",
                  border="yellow" if jobs else "#666666")
        )

    async def _update_events(self, session: AsyncSession) -> None:
        today = await recent_events_count(session)
        by_status = await events_by_status(session)
        status_parts = "  ".join(
            f"{format_status(k)} {v}" for k, v in sorted(by_status.items())
        )
        sparkline = ""
        hourly = await hourly_events_last_24h(session)
        if hourly:
            sparkline = _sparkline(hourly, "green")
        body = f"Today: [bold]{today}[/bold] new\n{status_parts}"
        if sparkline:
            body += f"\n\n{sparkline}"
        self.query_one("#events-stats", Static).update(
            _card(body, title="📅  Events", border="green")
        )

    async def _update_sources(self, session: AsyncSession) -> None:
        summary = await sources_summary(session)
        by_tier = await sources_by_tier(session)
        tier_parts = "  ".join(
            f"[bold]T{t}[/bold]: {c}" for t, c in sorted(by_tier.items())
        )
        self.query_one("#sources", Static).update(
            _card(
                f"Total: [bold]{summary['total']}[/bold]  "
                f"{format_status('active')} {summary['active']}  "
                f"{format_status('disabled', icon=False)} {summary['disabled']}\n\n"
                f"{tier_parts}",
                title="📡  Sources",
                border="cyan",
            )
        )

    async def _update_llm(self, session: AsyncSession) -> None:
        usage = await llm_usage_today(session)
        cost = float(usage["cost"])
        calls = int(usage["api_calls"])
        inp = int(usage["input_tokens"])
        out = int(usage["output_tokens"])
        sparkline = ""
        costs = await hourly_llm_cost_last_24h(session)
        if costs:
            sparkline = _sparkline(costs, "#ffab40")
        body = (
            f"Calls: [bold]{calls:,}[/bold]  "
            f"In: {inp:,}  Out: {out:,}\n"
            f"Est. Cost: [bold accent]${cost:.4f}[/bold accent]"
        )
        if sparkline:
            body += f"\n\n{sparkline}"
        self.query_one("#llm", Static).update(
            _card(body, title="🤖  LLM Today", border="#ffab40")
        )

    async def _update_recent(self, session: AsyncSession) -> None:
        events = await recent_events_for_dashboard(session, limit=10)
        if not events:
            self.query_one("#recent-events", Static).update(
                _card("[dim]No events yet[/dim]",
                      title="🆕  Recent Events", border="#666666")
            )
            return
        lines: list[str] = []
        for ev in events:
            emoji = ev.get("emoji") or "•"
            name = str(ev.get("name", ""))[:40]
            loc = str(ev.get("location_name", ""))[:20]
            lines.append(f"{emoji} {name}")
            if loc:
                lines.append(f"  [dim]@{loc}[/dim]")
        self.query_one("#recent-events", Static).update(
            _card("\n".join(lines), title="🆕  Recent Events", border="#7c4dff")
        )

    async def _update_last_crawls(self, session: AsyncSession) -> None:
        jobs = await recent_crawl_jobs_for_dashboard(session, limit=3)
        if not jobs:
            self.query_one("#last-crawls", Static).update(
                _card("[dim]No crawl history[/dim]",
                      title="📊  Last Crawls", border="#666666")
            )
            return
        lines: list[str] = []
        for job in jobs:
            status = format_status(str(job.status))
            extra = ""
            if job.summary is not None:
                calls = job.summary.api_calls
                cost = float(job.summary.estimated_cost)
                extra = f" — {calls} calls, ${cost:.4f}"
            if job.started_at is not None:
                local = job.started_at.replace(tzinfo=UTC).astimezone(VET_TZ)
                started = str(local)[:16]
            else:
                started = "?"
            lines.append(
                f"{status} #{job.id} [dim]{started}[/dim]{extra}"
            )
        self.query_one("#last-crawls", Static).update(
            _card("\n".join(lines), title="📊  Last Crawls", border="cyan")
        )

    def action_refresh(self) -> None:
        self.refresh_data()

    def _nav_tabs(self) -> ComposeResult:
        screens = [
            ("d", "Dashboard"), ("s", "Sources"), ("e", "Events"),
            ("l", "Locations"), ("t", "Rules"), ("o", "Ops"), ("g", "Logs"),
        ]
        with Horizontal(id="nav-tabs"):
            for key, label in screens:
                yield Button(label, id=f"nav-{key}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("nav-"):
            return
        screen_map = {
            "d": "dashboard", "s": "sources", "e": "events",
            "l": "locations", "t": "tag_rules", "o": "operations", "g": "logs",
        }
        key = bid[4:]
        if key in screen_map:
            self.app.switch_screen(screen_map[key])
