from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, TabbedContent, TabPane

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import TextualLogHandler
from orchestrator.ui.widgets import AnomalyAlertsWidget, HarnessQuotaWidget, SDLCProgressWidget


class DashboardApp(App):
    """
    Read-only Async Textual TUI Observability Dashboard for graph-orchestrator watch.
    Displays a live alphabetically-sorted projects status table and multi-pane bottom split
    with SDLCProgressWidget and TabbedContent hosting RichLog, AnomalyAlertsWidget, and HarnessQuotaWidget.
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    #projects_table {
        height: 40%;
        border: solid green;
    }
    #bottom_container {
        height: 60%;
        layout: horizontal;
    }
    #sdlc_widget {
        width: 50%;
        border: solid yellow;
    }
    #tabs {
        width: 50%;
        border: solid cyan;
    }
    #log_view {
        height: 100%;
    }
    #alerts_widget {
        height: 100%;
    }
    #quota_widget {
        height: 100%;
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
        self.selected_project: Optional[str] = None
        self.is_draining: bool = False
        self._drain_task: Optional[asyncio.Task] = None
        self.title = "Graph Orchestrator - TUI Dashboard"
        self.sub_title = "Real-time Autonomous SDLC Observability"

    def compose(self) -> ComposeResult:
        """Compose the TUI layout with Header, DataTable, Horizontal split (SDLCProgressWidget + TabbedContent), and Footer."""
        yield Header(show_clock=True)
        yield DataTable(id="projects_table")
        with Horizontal(id="bottom_container"):
            yield SDLCProgressWidget(id="sdlc_widget", state_manager=self.state_manager)
            with TabbedContent(id="tabs"):
                with TabPane("Logs", id="tab_logs"):
                    yield RichLog(id="log_view", highlight=True, markup=True, max_lines=1000)
                with TabPane("Alerts (24h)", id="tab_alerts"):
                    yield AnomalyAlertsWidget(id="alerts_widget", state_manager=self.state_manager, hours=24.0)
                with TabPane("Quotas", id="tab_quotas"):
                    yield HarnessQuotaWidget(id="quota_widget", config=self.config, state_manager=self.state_manager)
        yield Footer()

    async def on_mount(self) -> None:
        """Initializes widgets, binds log stream, and schedules periodic refresh."""
        table = self.query_one("#projects_table", DataTable)
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
            table = self.query_one("#projects_table", DataTable)
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
                key=p.name,
            )

        if self.selected_project:
            await self._update_bottom_panes(self.selected_project)
        else:
            try:
                quota_widget = self.query_one(HarnessQuotaWidget)
                await quota_widget.update_quotas()
            except Exception:
                pass

    @on(DataTable.RowHighlighted, "#projects_table")
    async def on_project_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """
        Handles row highlight event on projects table to reactively filter bottom panes.
        """
        table = self.query_one("#projects_table", DataTable)
        project_name = None
        try:
            if event.row_key is not None:
                row_data = table.get_row(event.row_key)
                if row_data:
                    project_name = str(row_data[0])
        except Exception:
            pass

        if not project_name:
            try:
                if event.cursor_row is not None and 0 <= event.cursor_row < table.row_count:
                    row_data = table.get_row_at(event.cursor_row)
                    if row_data:
                        project_name = str(row_data[0])
            except Exception:
                pass

        if project_name:
            self.selected_project = project_name
            await self._update_bottom_panes(project_name)

    async def _update_bottom_panes(self, project_name: Optional[str]) -> None:
        """
        Asynchronously updates SDLCProgressWidget, AnomalyAlertsWidget, and HarnessQuotaWidget for the selected project.
        """
        try:
            sdlc_widget = self.query_one(SDLCProgressWidget)
            await sdlc_widget.update_project(project_name)
        except Exception:
            pass

        try:
            alerts_widget = self.query_one(AnomalyAlertsWidget)
            await alerts_widget.update_project(project_name, hours=24.0)
        except Exception:
            pass

        try:
            quota_widget = self.query_one(HarnessQuotaWidget)
            await quota_widget.update_project(project_name)
        except Exception:
            pass

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
