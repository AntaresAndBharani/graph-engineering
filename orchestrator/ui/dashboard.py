from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, RichLog

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import TextualLogHandler


class DashboardApp(App):
    """
    Read-only Async Textual TUI Observability Dashboard for graph-orchestrator watch.
    Displays a live alphabetically-sorted projects status table and bounded orchestrator log stream.
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    #projects_table {
        height: 40%;
        border: solid green;
    }
    #log_view {
        height: 60%;
        border: solid cyan;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh Status"),
    ]

    TABLE_COLUMNS = [
        "Project Name",
        "Repository",
        "Active Node",
        "Status",
        "Last Updated",
        "Locks/Anomalies",
    ]

    def __init__(
        self,
        config: Optional[GlobalConfig] = None,
        state_manager: Optional[StateManager] = None,
        log_handler: Optional[TextualLogHandler] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.config = config or GlobalConfig()
        self.state_manager = state_manager
        self.log_handler = log_handler
        self.is_draining: bool = False
        self._drain_task: Optional[asyncio.Task] = None
        self.title = "Graph Orchestrator - TUI Dashboard"
        self.sub_title = "Real-time Autonomous SDLC Observability"

    def compose(self) -> ComposeResult:
        """Compose the TUI layout with Header, DataTable, RichLog, and Footer."""
        yield Header(show_clock=True)
        yield DataTable(id="projects_table")
        yield RichLog(id="log_view", highlight=True, markup=True, max_lines=1000)
        yield Footer()

    async def on_mount(self) -> None:
        """Initializes widgets, binds log stream, and schedules periodic refresh."""
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns(*self.TABLE_COLUMNS)

        if self.log_handler:
            self.log_handler.callback = self._handle_log_record
            log_view = self.query_one(RichLog)
            for rec in self.log_handler.records:
                formatted = self.log_handler.format(rec)
                log_view.write(formatted)

        # Register dashboard to receive live harness stream lines
        AsyncHarnessAdapter.register_stream_listener(self._handle_harness_stream_line)

        # Initial render of project status table
        await self.update_projects_table()

        # Set non-blocking 2.0s refresh interval
        self.set_interval(2.0, self.update_projects_table)

    def _handle_log_record(self, record: logging.LogRecord, formatted: str) -> None:
        """Callback invoked by TextualLogHandler on new log emissions."""
        try:
            log_view = self.query_one(RichLog)
            self.call_from_thread(log_view.write, formatted)
        except Exception:
            try:
                log_view = self.query_one(RichLog)
                log_view.write(formatted)
            except Exception:
                pass

    def _handle_harness_stream_line(self, line: str) -> None:
        """Callback invoked by AsyncHarnessAdapter on live subprocess stream emissions."""
        try:
            log_view = self.query_one(RichLog)
            self.call_from_thread(log_view.write, line)
        except Exception:
            try:
                log_view = self.query_one(RichLog)
                log_view.write(line)
            except Exception:
                pass

    async def update_projects_table(self) -> None:
        """
        Queries state_manager / in-memory config and refreshes DataTable rows
        sorted alphabetically by project name.
        """
        try:
            table = self.query_one(DataTable)
        except Exception:
            return

        paused_projects = set()
        active_jobs: List[Dict[str, Any]] = []
        if self.state_manager:
            try:
                paused = await self.state_manager.get_paused_projects()
                paused_projects = set(paused)
                active_jobs = await self.state_manager.get_active_jobs()
            except Exception:
                pass

        table.clear()
        sorted_projects = sorted(self.config.projects, key=lambda p: p.name.lower())

        now_str = datetime.datetime.now().strftime("%H:%M:%S")

        for p in sorted_projects:
            if not p.enabled:
                status = "[dim red]Disabled[/dim red]"
            elif p.name in paused_projects:
                status = "[bold yellow]Paused[/bold yellow]"
            else:
                status = "[bold green]Active[/bold green]"

            # Check if active lock exists for repo
            active_node = "Idle"
            locks_info = "None"
            matching_jobs = [j for j in active_jobs if j.get("repo") == p.repo and j.get("status") == "RUNNING"]
            if matching_jobs:
                job = matching_jobs[0]
                active_node = f"[bold cyan]{job.get('node_type', 'Working')}[/bold cyan]"
                locks_info = f"Issue #{job.get('issue_id')}"
            else:
                failed_jobs = [j for j in active_jobs if j.get("repo") == p.repo and j.get("status") == "FAILED"]
                if failed_jobs:
                    locks_info = f"[bold red]Failed: #{failed_jobs[0].get('issue_id')}[/bold red]"

            table.add_row(
                p.name,
                p.repo,
                active_node,
                status,
                now_str,
                locks_info,
            )

    async def action_refresh(self) -> None:
        """Manual refresh trigger via 'R' key."""
        await self.update_projects_table()

    async def action_quit(self) -> None:
        """
        Two-stage exit handler:
        1. If already draining: immediately force-terminates subprocesses and exits.
        2. If active harness subprocesses or running jobs exist: initiates graceful draining mode
           and notifies the operator.
        3. If idle: performs clean immediate exit.
        """
        if self.is_draining:
            # Stage 2: Second 'Q' press -> Emergency Force Quit
            try:
                log_view = self.query_one(RichLog)
                log_view.write("[bold red]⚡ Force-quit requested. Terminating all active subprocesses...[/bold red]")
            except Exception:
                pass
            await self.teardown(force=True)
            self.exit()
            return

        # Check if there are active running subprocesses or jobs
        has_active = AsyncHarnessAdapter.has_active_processes()
        if self.state_manager:
            try:
                active_jobs = await self.state_manager.get_active_jobs()
                running_jobs = [j for j in active_jobs if j.get("status") == "RUNNING"]
                if running_jobs:
                    has_active = True
            except Exception:
                pass

        if not has_active:
            # Idle: immediate clean exit
            await self.teardown(force=False)
            self.exit()
            return

        # Active processes exist -> Enter graceful draining mode
        self.is_draining = True
        self.sub_title = "⏳ DRAINING - Waiting for active agents to finish (Press 'Q' again to Force Quit)"

        # Request daemon stop in state DB so worker loops do not start new passes
        if self.state_manager:
            try:
                await self.state_manager.request_stop()
            except Exception:
                pass

        try:
            log_view = self.query_one(RichLog)
            log_view.write(
                "\n[bold yellow]════════════════════════════════════════════════════════════════════[/bold yellow]\n"
                "[bold yellow]⏳ Graceful Drain Initiated:[/bold yellow] Waiting for active agent tasks to complete...\n"
                "[dim]• No new node passes will be started.[/dim]\n"
                "[bold cyan]• Press 'Q' or Ctrl+C again to Force Quit immediately.[/bold cyan]\n"
                "[bold yellow]════════════════════════════════════════════════════════════════════[/bold yellow]\n"
            )
        except Exception:
            pass

        # Start asynchronous drain waiter with a 30s safety timeout
        self._drain_task = asyncio.create_task(self._wait_for_drain(timeout=30.0))

    async def _wait_for_drain(self, timeout: float = 30.0) -> None:
        """Asynchronously monitors active harness processes until drained or timed out."""
        finished_cleanly = await AsyncHarnessAdapter.wait_all_active(timeout=timeout)
        if not finished_cleanly:
            try:
                log_view = self.query_one(RichLog)
                log_view.write("[bold red]⚠️ Drain timeout (30s) reached. Force-terminating remaining subprocesses...[/bold red]")
            except Exception:
                pass
            await self.teardown(force=True)
        else:
            try:
                log_view = self.query_one(RichLog)
                log_view.write("[bold green]✅ All active agents completed cleanly. Exiting dashboard.[/bold green]")
            except Exception:
                pass
            await self.teardown(force=False)

        self.exit()

    async def teardown(self, force: bool = False) -> None:
        """
        Executes resource cleanup.
        If force=True, forcefully kills all child harness processes.
        Unregisters stream listeners and unregisters daemon PID from state.db.
        """
        AsyncHarnessAdapter.unregister_stream_listener(self._handle_harness_stream_line)
        if force:
            AsyncHarnessAdapter.terminate_all_active()
        if self.state_manager:
            try:
                await self.state_manager.unregister_daemon()
            except Exception:
                pass
