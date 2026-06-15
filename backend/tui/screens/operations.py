"""Operations screen — trigger admin actions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from tui.db import get_session
from tui.widgets.confirm_dialog import ConfirmDialog


class OperationsScreen(Screen[object]):
    """Trigger admin operations like crawl jobs, reprocessing, and backfills."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Operations[/bold]", id="breadcrumb")
        yield Container(
            Static("[bold]Available Operations[/bold]", id="ops-title"),
            Static("", id="ops-output"),
            Button("Crawl: All due sources", id="btn-crawl-all"),
            Button("Crawl: Tier 1 sources", id="btn-crawl-tier1"),
            Button("Reprocess latest crawl job", id="btn-reprocess"),
            Button("Backfill geocode locations (dry run)", id="btn-geocode-dry"),
            Button("Backfill geocode locations (apply)", id="btn-geocode-apply"),
            id="operations-container",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "btn-crawl-all":
            self.run_worker(self._run_command("uv run python pipeline/main.py"))
        elif action == "btn-crawl-tier1":
            self.run_worker(
                self._run_command("uv run python pipeline/main.py --tier 1")
            )
        elif action == "btn-reprocess":
            self.run_worker(self._reprocess_latest())
        elif action == "btn-geocode-dry":
            self.run_worker(
                self._run_command("uv run python scripts/backfill_geocode.py")
            )
        elif action == "btn-geocode-apply":
            self.run_worker(self._geocode_apply())

    async def _geocode_apply(self) -> None:
        confirmed = await self.app.push_screen_wait(
            ConfirmDialog("Apply geocode changes to database?")
        )
        if confirmed:
            await self._run_command("uv run python scripts/backfill_geocode.py --apply")

    async def _run_command(self, cmd: str) -> None:
        import asyncio

        output_widget = self.query_one("#ops-output", Static)
        output_widget.update(f"[yellow]Running: {cmd}[/yellow]\n")

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            lines: list[str] = []
            if stdout:
                lines.append(f"[green]stdout:[/green]\n{stdout.decode()[:2000]}")
            if stderr:
                lines.append(f"[yellow]stderr:[/yellow]\n{stderr.decode()[:2000]}")
            lines.append(f"\n[bold]Exit code: {proc.returncode}[/bold]")
            output_widget.update("\n".join(lines))
        except Exception as e:
            output_widget.update(f"[red]Error: {e}[/red]")

    async def _reprocess_latest(self) -> None:
        from sqlalchemy import select

        from api.models.crawl import CrawlJob

        session: AsyncSession = await get_session()
        try:
            result = await session.execute(
                select(CrawlJob.id).order_by(CrawlJob.started_at.desc()).limit(1)
            )
            job_id = result.scalar()
            if job_id is None:
                self.query_one("#ops-output", Static).update(
                    "[yellow]No crawl jobs found[/yellow]"
                )
                return

            confirmed = await self.app.push_screen_wait(
                ConfirmDialog(f"Re-process crawl job #{job_id}?")
            )
            if not confirmed:
                return

            self.query_one("#ops-output", Static).update(
                f"[yellow]Re-processing crawl job #{job_id}...[/yellow]\n"
                "[dim]This requires the Celery worker to be running.[/dim]\n"
                f"[dim]Call POST /api/v1/admin/process-crawl-job/{job_id}[/dim]"
            )
        finally:
            await session.close()
