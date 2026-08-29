from __future__ import annotations

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
        """Clean graceful teardown trigger via 'Q' key."""
        await self.teardown()
        self.exit()

    async def teardown(self) -> None:
        """
        Executes graceful resource cleanup: terminates active harness subprocesses
        and unregisters daemon PID from state.db.
        """
        AsyncHarnessAdapter.terminate_all_active()
        if self.state_manager:
            try:
                await self.state_manager.unregister_daemon()
            except Exception:
                pass
