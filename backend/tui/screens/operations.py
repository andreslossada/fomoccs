"""Operations screen — trigger admin actions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, RichLog, Static

from api.config import get_settings
from api.models.crawl import CrawlJob
from tui.db import (
    count_extracted_results,
    get_jobs_with_extracted_results,
    get_session,
)
from tui.widgets.confirm_dialog import ConfirmDialog
from tui.widgets.input_dialog import InputDialog

_project_root = Path(__file__).resolve().parents[3]


class OperationsScreen(Screen[object]):
    """Trigger admin operations like crawl jobs, reprocessing, and backfills."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def on_mount(self) -> None:
        self.query_one("#btn-crawl-all", Button).focus()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("[bold]Operations[/bold]", id="breadcrumb")
        yield Container(
            Static("[bold $accent]── Crawl Operations ──[/]", classes="ops-section"),
            Horizontal(
                Button("Crawl: All due sources", id="btn-crawl-all"),
                Button("Crawl: Tier 1 sources", id="btn-crawl-tier1"),
                classes="ops-button-row",
            ),
            Static("[bold $accent]── Process Operations ──[/]", classes="ops-section"),
            Horizontal(
                Button("Reprocess latest crawl job", id="btn-reprocess"),
                Button("Process all stuck crawl results", id="btn-process-stuck"),
                Button("Process crawl job #N ...", id="btn-process-job"),
                classes="ops-button-row",
            ),
            Static("[bold $accent]── Geocode Operations ──[/]", classes="ops-section"),
            Horizontal(
                Button("Backfill geocode locations (dry run)", id="btn-geocode-dry"),
                Button("Backfill geocode locations (apply)", id="btn-geocode-apply"),
                classes="ops-button-row",
            ),
            Static("Ready.", id="ops-status"),
            RichLog(highlight=True, markup=True, id="ops-output"),
            id="operations-container",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "btn-crawl-all":
            self.run_worker(self._run_pipeline())
        elif action == "btn-crawl-tier1":
            self.run_worker(self._run_pipeline("--tier 1"))
        elif action == "btn-reprocess":
            self.run_worker(self._reprocess_latest())
        elif action == "btn-process-stuck":
            self.run_worker(self._process_all_stuck())
        elif action == "btn-process-job":
            self.run_worker(self._process_specific_job())
        elif action == "btn-geocode-dry":
            self.run_worker(
                self._run_command("uv run python scripts/backfill_geocode.py")
            )
        elif action == "btn-geocode-apply":
            self.run_worker(self._geocode_apply())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self) -> RichLog:
        return self.query_one("#ops-output", RichLog)

    def _status(self) -> Static:
        return self.query_one("#ops-status", Static)

    # ------------------------------------------------------------------
    # Pipeline subprocess with handoff env vars
    # ------------------------------------------------------------------

    async def _run_pipeline(self, extra_args: str = "") -> None:
        out = self._log()
        status = self._status()
        settings = get_settings()

        python_exe = str(
            _project_root / "pipeline" / ".venv" / "Scripts" / "python.exe"
        )
        cmd = f'"{python_exe}" main.py {extra_args}'.strip()

        env_vars: dict[str, str] = {}
        env_vars["API_BASE_URL"] = settings.api_base_url
        env_vars["SYNC_API_KEY"] = settings.sync_api_key
        env_vars["PYTHONUTF8"] = "1"
        env_vars["USE_CELERY"] = "false"

        out.clear()
        out.write(
            f"[yellow]Running pipeline in {_project_root / 'pipeline'}[/yellow]\n"
            f"[dim]{cmd}[/dim]\n"
            f"[dim]API_BASE_URL={env_vars['API_BASE_URL']}[/dim]\n"
        )
        status.update("[yellow]Running: Crawl pipeline...[/yellow]")

        try:
            env = {**os.environ, **env_vars}
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(_project_root / "pipeline"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()

            if stdout:
                out.write(f"[green]stdout:[/green]\n{stdout.decode()[:2000]}\n")
            if stderr:
                out.write(f"[yellow]stderr:[/yellow]\n{stderr.decode()[:2000]}\n")
            out.write(f"[bold]Exit code: {proc.returncode}[/bold]\n")

            if proc.returncode == 0:
                out.write(
                    "[green]Pipeline completed. Handoff triggered via "
                    f"{env_vars['API_BASE_URL']}.[/green]"
                )
                status.update("[green]Completed[/green]")
            else:
                out.write("[red]Pipeline failed — check output above.[/red]")
                status.update("[red]Failed[/red]")
        except Exception as e:
            out.write(f"[red]Pipeline error: {e}[/red]")
            status.update("[red]Failed[/red]")

    # ------------------------------------------------------------------
    # Shell command runner
    # ------------------------------------------------------------------

    async def _run_command(self, cmd: str) -> None:
        out = self._log()
        status = self._status()

        out.clear()
        out.write(f"[yellow]Running: {cmd}[/yellow]\n")
        status.update("[yellow]Running command...[/yellow]")

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if stdout:
                out.write(f"[green]stdout:[/green]\n{stdout.decode()[:2000]}\n")
            if stderr:
                out.write(f"[yellow]stderr:[/yellow]\n{stderr.decode()[:2000]}\n")
            out.write(f"[bold]Exit code: {proc.returncode}[/bold]\n")

            if proc.returncode == 0:
                status.update("[green]Completed[/green]")
            else:
                status.update("[red]Failed[/red]")
        except Exception as e:
            out.write(f"[red]Error: {e}[/red]")
            status.update("[red]Failed[/red]")

    # ------------------------------------------------------------------
    # HTTP processing helpers
    # ------------------------------------------------------------------

    async def _call_process_endpoint(
        self, job_id: int, client: httpx.AsyncClient | None = None
    ) -> tuple[bool, str]:
        settings = get_settings()
        url = (
            f"{settings.api_base_url}"
            f"/api/v1/admin/process-crawl-job/{job_id}"
            f"?api_key={settings.sync_api_key}"
        )
        try:
            if client is not None:
                resp = await client.post(url)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as cl:
                    resp = await cl.post(url)
            if resp.status_code == 200:
                return True, f"Job #{job_id} processed OK"
            return (
                False,
                f"Job #{job_id}: HTTP {resp.status_code} — {resp.text[:200]}",
            )
        except Exception as exc:
            return False, f"Job #{job_id}: connection error — {exc}"

    # ------------------------------------------------------------------
    # Individual operations
    # ------------------------------------------------------------------

    async def _reprocess_latest(self) -> None:
        out = self._log()
        status = self._status()

        session: AsyncSession = await get_session()
        try:
            result = await session.execute(
                select(CrawlJob.id).order_by(CrawlJob.started_at.desc()).limit(1)
            )
            job_id = result.scalar()
            if job_id is None:
                out.clear()
                out.write("[yellow]No crawl jobs found[/yellow]")
                return

            confirmed = await self.app.push_screen_wait(
                ConfirmDialog(f"Process crawl job #{job_id}?")
            )
            if not confirmed:
                return

            out.clear()
            out.write(f"[yellow]Processing crawl job #{job_id}...[/yellow]\n")
            status.update(f"[yellow]Processing crawl job #{job_id}...[/yellow]")

            ok, msg = await self._call_process_endpoint(job_id)
            if ok:
                out.write(f"[green]{msg}[/green]")
                status.update("[green]Completed[/green]")
            else:
                out.write(f"[red]{msg}[/red]")
                status.update("[red]Failed[/red]")
        finally:
            await session.close()

    async def _process_all_stuck(self) -> None:
        out = self._log()
        status = self._status()

        session: AsyncSession = await get_session()
        try:
            count = await count_extracted_results(session)
            if count == 0:
                out.clear()
                out.write("[green]No stuck crawl results — everything is processed.[/green]")
                return

            job_ids = await get_jobs_with_extracted_results(session)
        finally:
            await session.close()

        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(
                f"Process [bold]{count}[/bold] extracted results across "
                f"[bold]{len(job_ids)}[/bold] crawl jobs? "
                f"(4 concurrent)"
            )
        )
        if not confirmed:
            return

        total = len(job_ids)
        out.clear()
        out.write(
            f"[yellow]Processing {total} jobs ({count} results) — 4 at a time...[/yellow]\n"
        )
        status.update(f"[yellow]Processing {total} jobs...[/yellow]")

        ok_count = 0
        fail_count = 0
        done = 0
        lock = asyncio.Lock()
        lines: list[str] = []

        async def update_output() -> None:
            out.clear()
            out.write(
                f"[yellow]Processing... ({done}/{total}) "
                f"[green]{ok_count} ok[/green] "
                f"[red]{fail_count} fail[/red][/yellow]\n"
                + "\n".join(lines[-20:])
            )

        sem = asyncio.Semaphore(1)

        async def process_one_under_sem(job_id: int) -> None:
            nonlocal ok_count, fail_count, done
            async with sem:
                ok, msg = await self._call_process_endpoint(job_id, client=httpx_client)
            async with lock:
                done += 1
                if ok:
                    ok_count += 1
                    lines.append(f"  [green]OK[/green] Job #{job_id}")
                else:
                    fail_count += 1
                    lines.append(
                        f"  [red]FAIL[/red] Job #{job_id}: "
                        f"{msg[msg.find(' — ') + 3:] if ' — ' in msg else msg}"
                    )
                await update_output()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120), limits=httpx.Limits(max_connections=1)
        ) as httpx_client:
            await asyncio.gather(
                *(process_one_under_sem(jid) for jid in job_ids)
            )

        out.write(
            f"\n[bold]Done: {ok_count} ok, {fail_count} failed[/bold]\n"
            + "\n".join(lines[-20:])
        )
        status.update("[green]Completed[/green]")

    async def _process_specific_job(self) -> None:
        out = self._log()
        status = self._status()

        job_str = await self.app.push_screen_wait(InputDialog("Enter crawl job ID:"))
        if not job_str:
            return
        try:
            job_id = int(job_str)
        except ValueError:
            out.clear()
            out.write(f"[red]Invalid job ID: {job_str}[/red]")
            return

        out.clear()
        out.write(f"[yellow]Processing crawl job #{job_id}...[/yellow]\n")
        status.update(f"[yellow]Processing crawl job #{job_id}...[/yellow]")

        ok, msg = await self._call_process_endpoint(job_id)
        if ok:
            out.write(f"[green]{msg}[/green]")
            status.update("[green]Completed[/green]")
        else:
            out.write(f"[red]{msg}[/red]")
            status.update("[red]Failed[/red]")

    async def _geocode_apply(self) -> None:
        confirmed = await self.app.push_screen_wait(
            ConfirmDialog("Apply geocode changes to database?")
        )
        if confirmed:
            await self._run_command("uv run python scripts/backfill_geocode.py --apply")
