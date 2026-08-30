from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, TabbedContent, TabPane
from textual.widgets.data_table import RowDoesNotExist

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import ProjectLogBufferManager, TextualLogHandler
from orchestrator.quota import QuotaManager
from orchestrator.ui.widgets import (
    AnomalyAlertsWidget,
    HarnessQuotaWidget,
    SDLCProgressWidget,
    _apply_keyed_diff,
)


class DashboardApp(App):
    """
    Read-only Async Textual TUI Observability Dashboard for graph-orchestrator watch.
    Displays a live alphabetically-sorted projects status table and multi-pane bottom split
    with SDLCProgressWidget and TabbedContent hosting RichLog, HarnessQuotaWidget, and AnomalyAlertsWidget.
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
        Binding("space", "toggle_auto_scroll", "Toggle Auto-Scroll"),
        Binding("ctrl+l", "clear_logs", "Clear Logs"),
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
        quota_manager: Optional[QuotaManager] = None,
        buffer_manager: Optional[ProjectLogBufferManager] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.config = config or GlobalConfig()
        self.state_manager = state_manager
        self.log_handler = log_handler
        self.quota_manager = quota_manager
        self.buffer_manager = (
            buffer_manager
            or (log_handler.buffer_manager if log_handler and getattr(log_handler, "buffer_manager", None) else None)
            or ProjectLogBufferManager()
        )
        self.selected_project: Optional[str] = None
        self.selected_node: Optional[str] = None
        self._last_bottom_pane_fingerprint: Optional[str] = None
        self.is_draining: bool = False
        self._drain_task: Optional[asyncio.Task] = None
        self.auto_scroll: bool = True
        self.title = "Graph Orchestrator - TUI Dashboard"
        self._update_sub_title()

    def compose(self) -> ComposeResult:
        """Compose the TUI layout with Header, DataTable, Horizontal split (SDLCProgressWidget + TabbedContent), and Footer."""
        yield Header(show_clock=True)
        yield DataTable(id="projects_table")
        with Horizontal(id="bottom_container"):
            yield SDLCProgressWidget(id="sdlc_widget", state_manager=self.state_manager)
            with TabbedContent(id="tabs"):
                with TabPane("Logs", id="tab_logs"):
                    yield RichLog(id="log_view", highlight=True, markup=True, max_lines=1000, auto_scroll=self.auto_scroll)
                with TabPane("Quota Limits", id="tab_quotas"):
                    yield HarnessQuotaWidget(
                        id="quota_widget",
                        config=self.config,
                        state_manager=self.state_manager,
                        quota_manager=self.quota_manager,
                    )
                with TabPane("Alerts (24h)", id="tab_alerts"):
                    yield AnomalyAlertsWidget(id="alerts_widget", state_manager=self.state_manager, hours=24.0)
        yield Footer()

    async def on_mount(self) -> None:
        """Initializes widgets, binds log stream, and schedules periodic refresh."""
        table = self.query_one("#projects_table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns(*self.TABLE_COLUMNS)

        if self.log_handler:
            self.log_handler.callback = self._handle_log_record

        # Register dashboard to receive live harness stream lines
        AsyncHarnessAdapter.register_stream_listener(self._handle_harness_stream_line)

        # Initial render of project status table
        await self.update_projects_table()

        if self.selected_project:
            await self.hydrate_project_logs(self.selected_project, node_name=self.selected_node)
        elif self.log_handler:
            log_view = self.query_one(RichLog)
            for rec in self.log_handler.records:
                formatted = self.log_handler.format(rec)
                log_view.write(formatted)

        # Set non-blocking 2.0s refresh interval
        self.set_interval(2.0, self.update_projects_table)

    async def hydrate_project_logs(
        self,
        project_name: Optional[str],
        node_name: Optional[str] = None,
    ) -> None:
        """
        Clears the RichLog pane and populates it with scoped logs from ProjectLogBufferManager.
        Falls back to disk tailing if in-memory buffer is empty.
        """
        try:
            log_view = self.query_one("#log_view", RichLog)
        except Exception:
            return

        if project_name:
            if node_name:
                log_view.border_title = f"Live Output [{project_name} | {node_name}]"
            else:
                log_view.border_title = f"Live Output [{project_name}]"
        else:
            log_view.border_title = "Live Output"

        log_dir = self.config.settings.resolved_log_dir if (self.config and self.config.settings) else None

        lines = ProjectLogBufferManager.get_project_logs(
            project_name=project_name,
            log_dir=log_dir,
            max_lines=100,
            node_name=node_name,
        )
        if not lines and self.log_handler and self.log_handler.records:
            lines = [
                self.log_handler.format(rec)
                for rec in self.log_handler.records
                if (
                    not project_name
                    or ProjectLogBufferManager.extract_project_name(rec) in (None, project_name)
                )
                and (
                    not node_name
                    or ProjectLogBufferManager.extract_node_name(rec) in (None, node_name)
                )
            ]

        log_view.clear()
        for line in lines:
            log_view.write(line)

    def _handle_log_record(self, record: logging.LogRecord, formatted: str) -> None:
        """Callback invoked by TextualLogHandler on new log emissions."""
        rec_project = ProjectLogBufferManager.extract_project_name(record)
        rec_node = ProjectLogBufferManager.extract_node_name(record)
        if self.selected_project and rec_project and rec_project != self.selected_project:
            return
        if self.selected_node and rec_node and rec_node != self.selected_node:
            return

        try:
            log_view = self.query_one(RichLog)
            self.call_from_thread(log_view.write, formatted)
        except Exception:
            try:
                log_view = self.query_one(RichLog)
                log_view.write(formatted)
            except Exception:
                pass

    def _handle_harness_stream_line(
        self,
        project_name: Optional[str] = None,
        node_name: Optional[str] = None,
        line: Optional[str] = None,
    ) -> None:
        """Callback invoked by AsyncHarnessAdapter on live subprocess stream emissions."""
        if line is None and node_name is not None:
            # Fallback for 2-argument calls where (project_name, line) was passed
            line = node_name
            node_name = None
        elif line is None and node_name is None and project_name is not None:
            # Fallback for 1-argument calls where (line,) was passed
            line = str(project_name)
            project_name = None

        if line is None:
            line = ""

        line_project = project_name or ProjectLogBufferManager.extract_project_name(line)
        line_node = node_name or ProjectLogBufferManager.extract_node_name(line)
        self.buffer_manager.add_line(line, project_name=line_project, node_name=line_node)

        if self.selected_project and line_project and line_project != self.selected_project:
            return
        if self.selected_node and line_node and line_node != self.selected_node:
            return

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
        Queries state_manager / in-memory config and refreshes DataTable rows in-place
        sorted alphabetically by project name with compound multi-node row keys without clearing the table.
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

        sorted_projects = sorted(self.config.projects, key=lambda p: p.name.lower())
        now_str = datetime.datetime.now().strftime("%H:%M:%S")

        target_rows: List[Tuple[str, Tuple[Any, ...]]] = []
        for p in sorted_projects:
            if not p.enabled:
                status = "[dim red]Disabled[/dim red]"
            elif p.name in paused_projects:
                status = "[bold yellow]Paused[/bold yellow]"
            else:
                status = "[bold green]Active[/bold green]"

            # Check if active lock exists for repo
            matching_jobs = [j for j in active_jobs if j.get("repo") == p.repo and j.get("status") == "RUNNING"]
            if matching_jobs:
                # Sort matching jobs alphabetically by node_type for deterministic rendering
                matching_jobs.sort(key=lambda j: str(j.get("node_type", "")))
                for idx, job in enumerate(matching_jobs):
                    node_type = job.get("node_type", "Working")
                    row_key = f"{p.name}::{node_type}"
                    display_name = p.name if idx == 0 else "  └─"
                    active_node = f"[bold cyan]{node_type}[/bold cyan]"
                    locks_info = f"Issue #{job.get('issue_id')}"
                    target_rows.append(
                        (
                            row_key,
                            (
                                display_name,
                                p.repo,
                                active_node,
                                status,
                                now_str,
                                locks_info,
                            ),
                        )
                    )
            else:
                failed_jobs = [j for j in active_jobs if j.get("repo") == p.repo and j.get("status") == "FAILED"]
                locks_info = "None"
                if failed_jobs:
                    locks_info = f"[bold red]Failed: #{failed_jobs[0].get('issue_id')}[/bold red]"

                row_key = f"{p.name}::Idle"
                target_rows.append(
                    (
                        row_key,
                        (
                            p.name,
                            p.repo,
                            "Idle",
                            status,
                            now_str,
                            locks_info,
                        ),
                    )
                )

        _apply_keyed_diff(table, target_rows)

        # Preserve cursor within valid bounds / selected project & node
        if table.row_count > 0:
            target_cursor_index = None
            if self.selected_project:
                for idx, (rk, _) in enumerate(target_rows):
                    parts = rk.split("::", 1)
                    p_name = parts[0]
                    n_name = parts[1] if len(parts) > 1 and parts[1] != "Idle" else None
                    if p_name == self.selected_project:
                        if getattr(self, "selected_node", None) and n_name == self.selected_node:
                            target_cursor_index = idx
                            break
                        elif target_cursor_index is None:
                            target_cursor_index = idx

            if target_cursor_index is not None:
                try:
                    table.move_cursor(row=target_cursor_index)
                except Exception:
                    pass
            elif table.cursor_row is not None and table.cursor_row >= table.row_count:
                try:
                    table.move_cursor(row=max(0, table.row_count - 1))
                except Exception:
                    pass
            elif table.cursor_row is None:
                try:
                    table.move_cursor(row=0)
                except Exception:
                    pass

        if self.selected_project:
            await self._update_bottom_panes(self.selected_project, force=False)
        else:
            try:
                quota_widget = self.query_one(HarnessQuotaWidget)
                if self.state_manager:
                    fp = await self.state_manager.get_project_state_fingerprint(None)
                    if fp != self._last_bottom_pane_fingerprint:
                        self._last_bottom_pane_fingerprint = fp
                        await quota_widget.update_quotas(
                            config=self.config,
                            state_manager=self.state_manager,
                            quota_manager=self.quota_manager,
                        )
                else:
                    await quota_widget.update_quotas(
                        config=self.config,
                        state_manager=self.state_manager,
                        quota_manager=self.quota_manager,
                    )
            except Exception:
                pass

    @on(DataTable.RowHighlighted, "#projects_table")
    async def on_project_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """
        Handles row highlight event on projects table to reactively filter bottom panes and log view.
        """
        table = self.query_one("#projects_table", DataTable)
        project_name = None
        node_name = None

        row_key_str = None
        if event.row_key is not None:
            row_key_str = event.row_key.value if hasattr(event.row_key, "value") else str(event.row_key)

        if row_key_str and "::" in row_key_str:
            parts = row_key_str.split("::", 1)
            project_name = parts[0]
            node_name = parts[1] if parts[1] != "Idle" else None
        elif row_key_str:
            project_name = row_key_str

        if not project_name:
            try:
                if event.row_key is not None:
                    row_data = table.get_row(event.row_key)
                    if row_data:
                        raw_name = str(row_data[0]).strip()
                        if not raw_name.startswith("└─"):
                            project_name = raw_name
            except (RowDoesNotExist, KeyError):
                pass

        if not project_name:
            try:
                if event.cursor_row is not None and 0 <= event.cursor_row < table.row_count:
                    row_data = table.get_row_at(event.cursor_row)
                    if row_data:
                        raw_name = str(row_data[0]).strip()
                        if not raw_name.startswith("└─"):
                            project_name = raw_name
            except (RowDoesNotExist, KeyError, IndexError):
                pass

        if project_name:
            if project_name != self.selected_project or node_name != getattr(self, "selected_node", None):
                self.selected_project = project_name
                self.selected_node = node_name
                await self.hydrate_project_logs(project_name, node_name=node_name)
                await self._update_bottom_panes(project_name, force=True)

    async def _update_bottom_panes(self, project_name: Optional[str], force: bool = False) -> None:
        """
        Asynchronously updates SDLCProgressWidget, AnomalyAlertsWidget, and HarnessQuotaWidget for the selected project.
        Uses lightweight state fingerprinting to avoid redundant SQLite re-queries when state is unchanged.
        """
        if not force and self.state_manager:
            try:
                fp = await self.state_manager.get_project_state_fingerprint(project_name)
                if fp == self._last_bottom_pane_fingerprint:
                    return
                self._last_bottom_pane_fingerprint = fp
            except Exception:
                pass
        elif self.state_manager:
            try:
                self._last_bottom_pane_fingerprint = await self.state_manager.get_project_state_fingerprint(project_name)
            except Exception:
                pass

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

    def _update_sub_title(self) -> None:
        """Updates dashboard sub_title with current operational status and auto-scroll indicator."""
        scroll_indicator = f"[Auto-Scroll: {'ON' if self.auto_scroll else 'OFF'}]"
        if self.is_draining:
            self.sub_title = (
                f"⏳ DRAINING - Waiting for active agents to finish (Press 'Q' again to Force Quit) {scroll_indicator}"
            )
        else:
            self.sub_title = f"Real-time Autonomous SDLC Observability {scroll_indicator}"

    def action_toggle_auto_scroll(self) -> None:
        """Toggles log auto-scrolling between ON and OFF and synchronizes with RichLog widget."""
        self.auto_scroll = not self.auto_scroll
        try:
            log_view = self.query_one("#log_view", RichLog)
            log_view.auto_scroll = self.auto_scroll
        except Exception:
            pass
        self._update_sub_title()

    def action_clear_logs(self) -> None:
        """Clears the RichLog buffer on explicit demand."""
        try:
            log_view = self.query_one("#log_view", RichLog)
            log_view.clear()
        except Exception:
            pass

    async def action_refresh(self) -> None:
        """Manual refresh trigger via 'R' key."""
        await self.update_projects_table()
        if self.selected_project:
            await self._update_bottom_panes(self.selected_project, force=True)

    async def action_quit(self) -> None:
        """
        Two-stage exit handler:
        1. If already draining: immediately force-terminates subprocesses and exits.
        2. If active harness subprocesses or running jobs exist: initiates graceful draining mode
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
        self._update_sub_title()

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
