"""Pipeline run screen — runs the pipeline and shows clean output."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static


class PipelineRunScreen(Screen[None]):
    """Run the event discovery pipeline and show real-time output."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "rerun", "Re-run"),
    ]

    _source_name: str = ""

    def __init__(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
        source_name: str = "",
    ) -> None:
        super().__init__()
        self._cmd = cmd
        self._cwd = cwd
        self._env = env
        self._source_name = source_name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        label = self._source_name or "Pipeline"
        yield Label(f"[bold]Crawling: {label}[/bold]", id="breadcrumb")
        yield Container(
            Static("", id="pipeline-status"),
            Static("[dim]Starting pipeline...[/dim]", id="pipeline-output"),
            id="pipeline-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._run_pipeline())

    async def _run_pipeline(self) -> None:
        import asyncio
        import os

        output = self.query_one("#pipeline-output", Static)
        status_bar = self.query_one("#pipeline-status", Static)
        status_bar.update("[yellow]Running...[/yellow]")

        merged_env = os.environ.copy()
        merged_env.update(self._env)
        merged_env["PYTHONUNBUFFERED"] = "1"
        merged_env["PYTHONUTF8"] = "1"
        merged_env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc = await asyncio.create_subprocess_exec(
                *self._cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=merged_env,
                cwd=self._cwd,
            )

            lines: list[str] = []
            event_count = 0
            while proc.stdout is not None:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").rstrip()

                # Filter noise
                if any(skip in text for skip in [
                    "RequestsDependencyWarning",
                    "VIRTUAL_ENV",
                    "deprecated",
                    "CrawlJob ID:",
                    "STEP",
                    "======",
                ]):
                    continue

                # Simplify known lines
                if "processed successfully" in text.lower():
                    import re
                    m = re.search(r"(\d+) event", text)
                    if m:
                        event_count = int(m.group(1))
                    status_bar.update("[green]Crawl complete![/green]")

                lines.append(text)
                if len(lines) > 300:
                    lines = lines[-300:]
                output.update("\n".join(lines))

            await proc.wait()

            if proc.returncode == 0:
                status_bar.update(
                    f"[green]Done — {event_count} events extracted[/green]"
                )
                self.app.notify(
                    f"Crawl of '{self._source_name}' finished successfully",
                    severity="information",
                    timeout=8,
                )
            else:
                status_bar.update(
                    f"[red]Failed (exit code {proc.returncode})[/red]"
                )
                last_line = ""
                for line in reversed(lines):
                    if line.strip():
                        last_line = line.strip()[:100]
                        break
                self.app.notify(
                    f"Crawl of '{self._source_name}' failed: {last_line}",
                    severity="error",
                    timeout=10,
                )

            if not lines:
                output.update("[dim]No output from pipeline[/dim]")

        except FileNotFoundError:
            output.update(
                "[red]Python not found. Is the pipeline venv set up?[/red]"
            )
            status_bar.update("[red]Error[/red]")
        except Exception as e:
            output.update(f"[red]Error: {e}[/red]")
            status_bar.update("[red]Error[/red]")

    def action_rerun(self) -> None:
        output = self.query_one("#pipeline-output", Static)
        output.update("[dim]Re-running...[/dim]")
        self.run_worker(self._run_pipeline())
