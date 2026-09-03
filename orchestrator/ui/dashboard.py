from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple

import rich.markup
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, TabbedContent, TabPane
from textual.widgets.data_table import RowDoesNotExist

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import (
    ProjectLogBufferManager,
    TextualLogHandler,
    matches_node_scope,
    strip_ansi,
)
from orchestrator.quota import QuotaManager
from orchestrator.reloader import ConfigHolder
from orchestrator.ui.widgets import (
    AnomalyAlertsWidget,
    ConfigStatusBanner,
    HarnessQuotaWidget,
    SDLCProgressWidget,
    _apply_keyed_diff,
    format_node_agent_spec,
)


class DashboardApp(App):
    """
    Read-only Async Textual TUI Observability Dashboard for graph-orchestrator watch.
    Displays a live alphabetically-sorted projects status table with 7th column Agent Model,
    ConfigStatusBanner, and multi-pane bottom split with SDLCProgressWidget and TabbedContent.
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    ConfigStatusBanner, #config_status_banner {
        height: 3;
    }
    #projects_table {
        height: 1fr;
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
        "Agent Model",
    ]

    def __init__(
        self,
        config: Optional[GlobalConfig | ConfigHolder] = None,
        state_manager: Optional[StateManager] = None,
        log_handler: Optional[TextualLogHandler] = None,
        quota_manager: Optional[QuotaManager] = None,
        buffer_manager: Optional[ProjectLogBufferManager] = None,
        selected_project: Optional[str] = None,
        selected_node: Optional[str] = None,
        selected_issue_id: Optional[int] = None,
        config_holder: Optional[ConfigHolder] = None,
        config_path: Optional[str | Path] = None,
        issue_id: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if isinstance(config, ConfigHolder):
            self.config_holder: Optional[ConfigHolder] = config
            self.config: GlobalConfig = config.config
        else:
            self.config_holder = config_holder
            self.config = config or GlobalConfig()

        self.config_path: Optional[Path] = (
            Path(config_path).resolve() if config_path else (
                Path(self.config.resolved_path).resolve() if getattr(self.config, "resolved_path", None) else (
                    Path(self.config_holder.config.resolved_path).resolve() if self.config_holder and getattr(self.config_holder.config, "resolved_path", None) else None
                )
            )
        )
        if self.config_path and not getattr(self.config, "resolved_path", None):
            self.config.resolved_path = self.config_path

        self.state_manager = state_manager
        self.log_handler = log_handler
        self.quota_manager = quota_manager
        self.buffer_manager = (
            buffer_manager
            or (log_handler.buffer_manager if log_handler and getattr(log_handler, "buffer_manager", None) else None)
            or ProjectLogBufferManager()
        )
        self.selected_project = selected_project
        self.selected_node = selected_node
        self.selected_issue_id: Optional[int] = selected_issue_id if selected_issue_id is not None else issue_id
        self._active_node_identity: Optional[Tuple[Optional[str], Optional[str]]] = (
            (selected_project, selected_node) if selected_project else None
        )
        self._last_bottom_pane_fingerprint: Optional[str] = None
        self._last_reload_at_epoch: Optional[float] = None
        self._last_tail_file: Optional[Path] = None
        self._last_tail_offset: int = 0
        self._placeholder_active: bool = False
        self.is_draining: bool = False
        self._drain_task: Optional[asyncio.Task] = None
        self.auto_scroll: bool = True
        self.title = "Graph Orchestrator - TUI Dashboard"
        self._update_sub_title()

    @property
    def issue_id(self) -> Optional[int]:
        return self.selected_issue_id

    @issue_id.setter
    def issue_id(self, value: Optional[int]) -> None:
        self.selected_issue_id = value

    def compose(self) -> ComposeResult:
        """Compose the TUI layout with Header, ConfigStatusBanner, DataTable, Horizontal split (SDLCProgressWidget + TabbedContent), and Footer."""
        yield Header(show_clock=True)
        yield ConfigStatusBanner(
            id="config_status_banner",
            config=self.config,
            state_manager=self.state_manager,
            config_path=self.config_path,
        )
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
            await self.hydrate_project_logs(
                self.selected_project,
                node_name=self.selected_node,
                issue_id=self.selected_issue_id,
            )
        elif self.log_handler:
            log_view = self.query_one(RichLog)
            for rec in self.log_handler.records:
                formatted = self.log_handler.format(rec)
                log_view.write(rich.markup.escape(formatted))

        # Set non-blocking 2.0s refresh interval
        self.set_interval(2.0, self.update_projects_table)

    async def hydrate_project_logs(
        self,
        project_name: Optional[str],
        node_name: Optional[str] = None,
        issue_id: Optional[int] = None,
    ) -> None:
        """
        Clears the RichLog pane and populates it with scoped logs from ProjectLogBufferManager.
        Falls back to disk tailing if in-memory buffer is empty.
        """
        try:
            log_view = self.query_one("#log_view", RichLog)
        except Exception:
            return

        if issue_id is not None:
            try:
                self.selected_issue_id = int(issue_id)
            except (ValueError, TypeError):
                self.selected_issue_id = issue_id
        elif project_name != self.selected_project or node_name != getattr(self, "selected_node", None):
            self.selected_issue_id = None

        if project_name:
            if node_name:
                title_text = f"Live Output [{project_name} | {node_name}*]"
                log_view.border_title = Text(title_text)
                try:
                    log_view.title = title_text
                except Exception:
                    pass
            else:
                title_text = f"Live Output [{project_name}]"
                log_view.border_title = Text(title_text)
                try:
                    log_view.title = title_text
                except Exception:
                    pass
        else:
            log_view.border_title = Text("Live Output")
            try:
                log_view.title = "Live Output"
            except Exception:
                pass

        log_dir = self.config.settings.resolved_log_dir if (self.config and self.config.settings) else None

        query_result = self.buffer_manager.get_project_logs(
            project_name=project_name,
            log_dir=log_dir,
            max_lines=100,
            node_name=node_name,
        )
        lines = query_result.lines if hasattr(query_result, "lines") else query_result

        if not lines and self.log_handler and self.log_handler.records:
            lines = [
                self.log_handler.format(rec)
                for rec in self.log_handler.records
                if (
                    not project_name
                    or self.buffer_manager.extract_project_name(rec) in (None, project_name)
                )
                and (
                    not node_name
                    or (
                        self.buffer_manager.extract_node_name(rec) is not None
                        and matches_node_scope(node_name, self.buffer_manager.extract_node_name(rec))
                    )
                )
            ]

        target_file = getattr(query_result, "target_file", None)
        file_size = getattr(query_result, "file_size", 0)

        if target_file is None and project_name:
            try:
                disk_res = self.buffer_manager.tail_latest_project_logs(
                    project_name=project_name,
                    log_dir=log_dir,
                    max_lines=100,
                    node_name=node_name,
                )
                if disk_res.target_file is not None:
                    target_file = disk_res.target_file
                    file_size = disk_res.file_size
            except Exception:
                pass

        self._last_tail_file = target_file
        self._last_tail_offset = file_size

        if not lines:
            if node_name:
                if target_file is not None and file_size == 0:
                    active_issue = issue_id if issue_id is not None else self.selected_issue_id
                    if active_issue is not None:
                        clean_issue = str(active_issue).lstrip("#")
                        placeholder = f"⚡ Initializing {node_name} harness on Issue #{clean_issue}... Awaiting output."
                    else:
                        placeholder = f"⚡ Initializing {node_name} harness... Awaiting output."
                    log_view.clear()
                    log_view.write(rich.markup.escape(placeholder))
                    self._placeholder_active = True
                else:
                    placeholder = f"No execution logs found yet for node '{node_name}'"
                    log_view.clear()
                    log_view.write(rich.markup.escape(placeholder))
                    self._placeholder_active = True
            else:
                log_view.clear()
                self._placeholder_active = False
        else:
            self._placeholder_active = False
            log_view.clear()
            for line in lines:
                if isinstance(line, str):
                    log_view.write(rich.markup.escape(line))
                else:
                    log_view.write(line)

    def _handle_log_record(self, record: logging.LogRecord, formatted: str) -> None:
        """Callback invoked by TextualLogHandler on new log emissions."""
        rec_project = self.buffer_manager.extract_project_name(record)
        rec_node = self.buffer_manager.extract_node_name(record)
        if self.selected_project and rec_project and rec_project != self.selected_project:
            return
        if self.selected_node and rec_node and not matches_node_scope(self.selected_node, rec_node):
            return

        try:
            log_view = self.query_one("#log_view", RichLog)
            if self._placeholder_active:
                self._placeholder_active = False
                if getattr(self, "_thread_id", None) is not None and threading.get_ident() != self._thread_id:
                    self.call_from_thread(log_view.clear)
                else:
                    log_view.clear()

            escaped = rich.markup.escape(formatted)
            if getattr(self, "_thread_id", None) is not None and threading.get_ident() != self._thread_id:
                self.call_from_thread(log_view.write, escaped)
            else:
                log_view.write(escaped)
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

        line_project = project_name or self.buffer_manager.extract_project_name(line)
        line_node = node_name or self.buffer_manager.extract_node_name(line)
        self.buffer_manager.add_line(line, project_name=line_project, node_name=line_node)

        if self.selected_project and line_project and line_project != self.selected_project:
            return
        if self.selected_node and line_node and not matches_node_scope(self.selected_node, line_node):
            return

        try:
            log_view = self.query_one("#log_view", RichLog)
            if self._placeholder_active:
                self._placeholder_active = False
                if getattr(self, "_thread_id", None) is not None and threading.get_ident() != self._thread_id:
                    self.call_from_thread(log_view.clear)
                else:
                    log_view.clear()

            escaped = rich.markup.escape(line)
            if getattr(self, "_thread_id", None) is not None and threading.get_ident() != self._thread_id:
                self.call_from_thread(log_view.write, escaped)
            else:
                log_view.write(escaped)

            if self._last_tail_file and self._last_tail_file.exists():
                try:
                    self._last_tail_offset = self._last_tail_file.stat().st_size
                except Exception:
                    pass
        except Exception:
            pass

    async def _rebind_config(
        self,
        new_config: Optional[GlobalConfig] = None,
        trigger: Optional[str] = None,
        timestamp: Optional[str] = None,
        config_path: Optional[str | Path] = None,
    ) -> None:
        """
        Asynchronously reactively re-binds all 4 configuration holders:
        1. DashboardApp.config (self.config)
        2. QuotaManager.config
        3. QuotaManager.quota_settings
        4. HarnessQuotaWidget (and calls update_quotas)
        Also updates ConfigStatusBanner and refreshes #projects_table immediately.
        """
        if new_config is None:
            if self.config_holder is not None:
                new_config = self.config_holder.config
            elif self.config_path is not None:
                from orchestrator.config import load_config
                new_config = load_config(self.config_path)
            else:
                new_config = self.config

        if new_config is not None:
            if self.config_holder is not None and self.config_holder.config is not new_config:
                self.config_holder.update(new_config)
            self.config = new_config

            if self.quota_manager is not None:
                self.quota_manager.config = new_config
                self.quota_manager.quota_settings = new_config.quota

            # 4th holder: HarnessQuotaWidget
            try:
                quota_widget = self.query_one(HarnessQuotaWidget)
                quota_widget.config = new_config
                await quota_widget.update_quotas(
                    config=new_config,
                    state_manager=self.state_manager,
                    quota_manager=self.quota_manager,
                )
            except Exception:
                pass

            # Update ConfigStatusBanner
            try:
                banner = self.query_one(ConfigStatusBanner)
                await banner.update_status(
                    config=new_config,
                    trigger=trigger,
                    timestamp=timestamp,
                    config_path=config_path or self.config_path,
                )
            except Exception:
                pass

            # Update projects table
            try:
                await self.update_projects_table()
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

        # Check if config was updated via ConfigHolder or StateManager
        if self.config_holder and self.config_holder.config is not self.config:
            await self._rebind_config(self.config_holder.config)
        elif self.state_manager:
            try:
                info = await self.state_manager.get_daemon_info()
                epoch_str = info.get("last_reload_at_epoch")
                if epoch_str:
                    epoch = float(epoch_str)
                    if self._last_reload_at_epoch is None:
                        self._last_reload_at_epoch = epoch
                    elif epoch > self._last_reload_at_epoch:
                        self._last_reload_at_epoch = epoch
                        await self._rebind_config(
                            trigger=info.get("last_reload_trigger"),
                            timestamp=info.get("last_reload_timestamp"),
                            config_path=info.get("last_reload_config_path"),
                        )
            except Exception:
                pass

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

                    # Determine agent model for active node
                    node_key = str(node_type).lower()
                    node_cfg = p.nodes.get(node_key)
                    if not node_cfg:
                        for base_node in ("architect", "devtest", "reviewer", "supervisor", "bau"):
                            if node_key.startswith(base_node):
                                node_cfg = p.nodes.get(base_node)
                                break
                    agent_model = format_node_agent_spec(node_cfg.model, node_cfg.effort) if node_cfg else "—"

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
                                agent_model,
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
                            "—",
                        ),
                    )
                )

        _apply_keyed_diff(table, target_rows)

        # Determine highlighted / selected project
        current_proj_name = self.selected_project
        if not current_proj_name and table.row_count > 0:
            cursor_idx = table.cursor_row if table.cursor_row is not None else 0
            if 0 <= cursor_idx < len(target_rows):
                current_proj_name = target_rows[cursor_idx][0].split("::", 1)[0]
                self.selected_project = current_proj_name

        # Identity-based state transition diffing
        if current_proj_name:
            p = next((proj for proj in self.config.projects if proj.name == current_proj_name), None)
            if p:
                p_active_jobs = [j for j in active_jobs if j.get("repo") == p.repo and j.get("status") == "RUNNING"]
                p_active_jobs.sort(key=lambda j: str(j.get("node_type", "")))

                if p_active_jobs:
                    # If current selected_node is one of running jobs, preserve it; otherwise pick first active job
                    matching_node_job = next(
                        (j for j in p_active_jobs if j.get("node_type") == getattr(self, "selected_node", None)),
                        None,
                    )
                    if matching_node_job:
                        active_node = matching_node_job.get("node_type")
                        raw_issue = matching_node_job.get("issue_id")
                    else:
                        active_node = p_active_jobs[0].get("node_type")
                        raw_issue = p_active_jobs[0].get("issue_id")
                    try:
                        active_issue_id = int(raw_issue) if raw_issue is not None else None
                    except (ValueError, TypeError):
                        active_issue_id = raw_issue
                else:
                    active_node = None
                    active_issue_id = None

                current_identity = (current_proj_name, active_node)
                prev_identity = getattr(self, "_active_node_identity", None)

                if prev_identity is None:
                    self._active_node_identity = current_identity
                    if active_node is not None:
                        self.selected_node = active_node
                        self.selected_issue_id = active_issue_id
                        await self.hydrate_project_logs(
                            current_proj_name,
                            node_name=active_node,
                            issue_id=active_issue_id,
                        )
                elif current_identity != prev_identity:
                    self._active_node_identity = current_identity
                    self.selected_node = active_node
                    self.selected_issue_id = active_issue_id
                    await self.hydrate_project_logs(
                        current_proj_name,
                        node_name=active_node,
                        issue_id=active_issue_id,
                    )

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
            await self._poll_active_log_file()
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

    async def _poll_active_log_file(self) -> None:
        """
        Incremental log tailer executed on the 2.0s table refresh tick.
        Performs offset handoff from hydrate_project_logs, reading strictly from _last_tail_offset.
        Recovers gracefully on log rotation (FileNotFoundError or size truncation) by resetting
        _last_tail_file and _last_tail_offset to None and 0, re-discovering the active log file on the next tick.
        Cleanly retires any active startup placeholder when live output bytes arrive.
        """
        if not self.selected_project:
            return

        try:
            log_view = self.query_one("#log_view", RichLog)
        except Exception:
            return

        log_dir = self.config.settings.resolved_log_dir if (self.config and self.config.settings) else None

        # Re-discovery on next tick if currently not tracking a target file
        if self._last_tail_file is None:
            if not self.selected_node:
                return

            try:
                disk_res = self.buffer_manager.tail_latest_project_logs(
                    project_name=self.selected_project,
                    log_dir=log_dir,
                    max_lines=100,
                    node_name=self.selected_node,
                )
            except Exception:
                return

            if disk_res.target_file is not None and disk_res.target_file.exists():
                self._last_tail_file = disk_res.target_file
                if disk_res.file_size == 0:
                    self._last_tail_offset = 0
                    if self.selected_node:
                        active_issue = self.selected_issue_id
                        if active_issue is not None:
                            clean_issue = str(active_issue).lstrip("#")
                            placeholder = f"⚡ Initializing {self.selected_node} harness on Issue #{clean_issue}... Awaiting output."
                        else:
                            placeholder = f"⚡ Initializing {self.selected_node} harness... Awaiting output."
                        log_view.clear()
                        log_view.write(rich.markup.escape(placeholder))
                        self._placeholder_active = True
                else:
                    if self._placeholder_active:
                        log_view.clear()
                        self._placeholder_active = False
                    for line in disk_res.lines:
                        log_view.write(rich.markup.escape(line))
                    self._last_tail_offset = disk_res.file_size
            return

        target_file = self._last_tail_file

        # Check for rotation / deletion / size truncation
        try:
            stat = target_file.stat()
            current_size = stat.st_size
        except FileNotFoundError:
            self._last_tail_file = None
            self._last_tail_offset = 0
            return
        except OSError:
            if not target_file.exists():
                self._last_tail_file = None
                self._last_tail_offset = 0
            return

        # Detect size truncation (e.g. log rotated in place or file truncated)
        if current_size < self._last_tail_offset:
            self._last_tail_file = None
            self._last_tail_offset = 0
            return

        # No new bytes written
        if current_size == self._last_tail_offset:
            return

        # Incremental read strictly from byte offset N
        offset = self._last_tail_offset
        try:
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                new_content = f.read()
                self._last_tail_offset = f.tell()
        except FileNotFoundError:
            self._last_tail_file = None
            self._last_tail_offset = 0
            return
        except OSError:
            if not target_file.exists():
                self._last_tail_file = None
                self._last_tail_offset = 0
            return

        if new_content:
            if self._placeholder_active:
                log_view.clear()
                self._placeholder_active = False

            for line in new_content.splitlines():
                clean_line = strip_ansi(line).rstrip("\r\n")
                log_view.write(rich.markup.escape(clean_line))

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
                self._active_node_identity = (project_name, node_name)
                issue_id = None
                if self.state_manager and node_name:
                    try:
                        active_jobs = await self.state_manager.get_active_jobs()
                        p = next((proj for proj in self.config.projects if proj.name == project_name), None)
                        if p:
                            matching = [
                                j for j in active_jobs
                                if j.get("repo") == p.repo
                                and j.get("node_type") == node_name
                                and j.get("status") == "RUNNING"
                            ]
                            if matching:
                                issue_id = matching[0].get("issue_id")
                    except Exception:
                        pass
                self.selected_issue_id = issue_id
                await self.hydrate_project_logs(project_name, node_name=node_name, issue_id=issue_id)
                await self._update_bottom_panes(project_name, force=True)

    @on(DataTable.RowSelected, "#sdlc_widget")
    def on_sdlc_row_selected(self, event: DataTable.RowSelected) -> None:
        """Traps SDLC row selection to prevent bubbling and preserve active log view."""
        event.stop()

    @on(DataTable.RowHighlighted, "#sdlc_widget")
    def on_sdlc_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Traps SDLC row highlighting to prevent bubbling and preserve active log view."""
        event.stop()

    @on(TabbedContent.TabActivated)
    async def on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """
        Synchronously hydrates the newly activated tab pane immediately on tab click/selection.
        """
        pane_id = event.pane.id if event.pane else None
        if pane_id == "tab_quotas":
            try:
                quota_widget = self.query_one(HarnessQuotaWidget)
                await quota_widget.update_quotas(
                    config=self.config,
                    state_manager=self.state_manager,
                    quota_manager=self.quota_manager,
                )
            except Exception:
                pass
        elif pane_id == "tab_logs":
            await self.hydrate_project_logs(
                self.selected_project,
                node_name=self.selected_node,
                issue_id=getattr(self, "selected_issue_id", None),
            )
        elif pane_id == "tab_alerts":
            try:
                alerts_widget = self.query_one(AnomalyAlertsWidget)
                await alerts_widget.update_project(self.selected_project, hours=24.0)
            except Exception:
                pass


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
