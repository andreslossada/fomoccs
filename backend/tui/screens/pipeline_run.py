"""Pipeline run screen — runs the pipeline and shows clean output."""

from __future__ import annotations

import json
import re
import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from tui.screens.help import HelpModal


class PipelineRunScreen(Screen[None]):
    """Run the event discovery pipeline and show real-time output."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "rerun", "Re-run"),
        Binding("?", "show_help", "Help"),
    ]

    def action_show_help(self) -> None:
        self.app.push_screen(HelpModal("Pipeline Run", self.BINDINGS))

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
        yield Label(
            f"[bold reverse #00bcd4]  Crawling: {label}  [/]",
            id="screen-title",
        )
        yield Label(f"[bold]Crawling: {label}[/bold]", id="breadcrumb")
        yield Container(
            Static("", id="pipeline-status"),
            Static("[dim]Launching pipeline...[/dim]", id="pipeline-output"),
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
        status_bar.update("[yellow]Running pipeline...[/yellow]")

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
            source_count = 0
            current_step = "booting"
            t0 = time.time()

            while proc.stdout is not None:
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=600
                    )
                except TimeoutError:
                    status_bar.update(
                        "[red]Timed out after 10 min — killing...[/red]"
                    )
                    proc.kill()
                    await proc.wait()
                    break

                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").rstrip()

                # Skip truly noisy lines
                if any(skip in text for skip in [
                    "RequestsDependencyWarning",
                    "VIRTUAL_ENV",
                    "deprecated",
                    "net::ERR_",
                    "[ERROR]...",
                    "Invalid URL:",
                ]):
                    continue

                # Detect step transitions
                if "Finding Sources" in text or "STEP 1" in text:
                    current_step = "scanning"
                    status_bar.update("[yellow]Scanning for sources...[/yellow]")
                elif "Crawling Sources" in text or "STEP 2" in text:
                    current_step = "crawling"
                    status_bar.update("[yellow]Crawling...[/yellow]")
                elif "Extracting Events" in text or "STEP 3" in text:
                    current_step = "extracting"
                    status_bar.update("[yellow]Extracting events...[/yellow]")
                elif "STEP 4" in text:
                    status_bar.update("[yellow]Saving...[/yellow]")
                elif "PIPELINE COMPLETED" in text:
                    status_bar.update("[green]Pipeline finished![/green]")

                # Parse JSON event lines for progress
                if text.startswith('{"event":'):
                    try:
                        evt = json.loads(text)
                        if evt.get("event") == "source_complete":
                            source_count += 1
                            src = evt.get("source_name", "")
                            elapsed = time.time() - t0
                            status_bar.update(
                                f"[yellow]Crawled {source_count} sources "
                                f"({int(elapsed)}s) — latest: {src[:30]}[/yellow]"
                            )
                        elif evt.get("event") == "source_extracted":
                            cnt = evt.get("events_extracted", 0)
                            event_count += cnt
                            src = evt.get("source_name", "")
                            status_bar.update(
                                f"[yellow]Extracted {event_count} events "
                                f"so far — latest: {src[:30]}[/yellow]"
                            )
                        elif evt.get("event") == "source_extract_error":
                            src = evt.get("source_name", "")
                            err = (evt.get("error", "") or "")[:80]
                            lines.append(
                                f"[red]Extraction failed for {src}: {err}[/red]"
                            )
                        continue  # don't show raw JSON
                    except (json.JSONDecodeError, KeyError):
                        pass

                # Count events from summary line
                m = re.search(r"(\d+) event", text)
                if m:
                    event_count = max(event_count, int(m.group(1)))

                # Show meaningful lines
                if current_step == "crawling" and any(kw in text for kw in [
                    "Crawling", "[FETCH]", "[BROWSER]", "JSON API", "crawled",
                    "source_complete",
                ]):
                    pass  # too noisy, skip raw crawl lines
                elif any(kw in text for kw in [
                    "STEP 0", "STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5",
                    "Finding Sources", "Crawling Sources", "Extracting Events",
                    "Crawl job ID:", "PIPELINE", "Summary:", "events extracted",
                    "total events:", "API calls", "processed", "prepped",
                    "extraction error", "timeout", "rate",
                ]):
                    lines.append(text)
                elif len(text.strip()) > 0 and (
                    "[" not in text or current_step == "extracting"
                ):
                    lines.append(text)

                if len(lines) > 200:
                    lines = lines[-200:]
                output.update("\n".join(lines))

            await proc.wait()
            elapsed = int(time.time() - t0)

            if proc.returncode == 0:
                status_bar.update(
                    f"[green]Done in {elapsed}s — "
                    f"{event_count} events extracted[/green]"
                )
                self.app.notify(
                    f"Crawl of '{self._source_name}' finished "
                    f"({event_count} events)",
                    severity="information",
                    timeout=8,
                )
            else:
                status_bar.update(
                    f"[red]Failed (exit {proc.returncode})[/red]"
                )
                if not lines:
                    output.update(
                        "[red]Pipeline produced no output. "
                        "Common causes:\n"
                        "- Playwright/Chromium not installed\n"
                        "- Python encoding issue (need PYTHONUTF8=1)\n"
                        "- Pipeline .venv missing[/red]"
                    )
                self.app.notify(
                    f"Crawl of '{self._source_name}' failed",
                    severity="error",
                    timeout=10,
                )

        except FileNotFoundError:
            output.update(
                "[red]Python not found. Is the pipeline venv set up?[/red]\n"
                "[dim]Run: cd pipeline && uv sync[/dim]"
            )
            status_bar.update("[red]Venv not found[/red]")
        except Exception as e:
            output.update(f"[red]Error launching pipeline: {e}[/red]")
            status_bar.update("[red]Launch error[/red]")

    def action_rerun(self) -> None:
        output = self.query_one("#pipeline-output", Static)
        output.update("[dim]Re-running...[/dim]")
        self.run_worker(self._run_pipeline())
