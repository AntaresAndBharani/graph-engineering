from __future__ import annotations

import asyncio
import datetime
from typing import Any, Dict, List, Optional

from textual.widgets import DataTable

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.quota import QuotaManager


class SDLCProgressWidget(DataTable):
    """
    Read-only DataTable widget rendering active SDLC items (Issues, Subtasks, PRs)
    for a selected project from local SQLite StateManager.
    """

    TABLE_COLUMNS = [
        "ID",
        "Title",
        "Status/Label",
        "Linked PR",
    ]

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        project_name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.state_manager = state_manager
        self.project_name = project_name

    async def on_mount(self) -> None:
        """Initializes table configuration and renders initial project items."""
        self.cursor_type = "row"
        self.zebra_stripes = True
        if not self.columns:
            self.add_columns(*self.TABLE_COLUMNS)
        await self._render_rows()

    async def update_project(self, project_name: Optional[str] = None) -> None:
        """
        Asynchronously queries StateManager for active SDLC items and updates the table.
        Non-blocking to the Textual UI event loop.
        """
        self.project_name = project_name
        await self._render_rows()

    async def _render_rows(self) -> None:
        """Renders SDLC item rows into the DataTable."""
        if not self.columns:
            return
        self.clear()

        if not self.project_name or not self.state_manager:
            self.add_row("-", "No active SDLC items", "-", "-")
            return

        try:
            items = await self.state_manager.get_sdlc_items(self.project_name)
        except Exception:
            items = []

        if not items:
            self.add_row("-", "No active SDLC items", "-", "-")
            return

        for item in items:
            issue_num = item.get("issue_number") or item.get("id", "-")
            issue_id = f"#{issue_num}" if issue_num != "-" else "-"
            title = str(item.get("title", ""))

            raw_labels = item.get("labels")
            raw_state = item.get("state") or item.get("status")
            status_label = str(raw_labels) if raw_labels else (str(raw_state) if raw_state else "-")

            linked_pr = item.get("linked_pr")
            pr_str = f"#{linked_pr}" if linked_pr else "-"

            self.add_row(issue_id, title, status_label, pr_str)


class AnomalyAlertsWidget(DataTable):
    """
    Read-only DataTable widget rendering 24-hour anomaly and retry events for a project
    from local SQLite StateManager.
    """

    TABLE_COLUMNS = [
        "Timestamp",
        "Node",
        "Error Type",
        "Error Message",
    ]

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        project_name: Optional[str] = None,
        hours: float = 24.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.state_manager = state_manager
        self.project_name = project_name
        self.hours = hours

    async def on_mount(self) -> None:
        """Initializes table configuration and renders initial anomaly events."""
        self.cursor_type = "row"
        self.zebra_stripes = True
        if not self.columns:
            self.add_columns(*self.TABLE_COLUMNS)
        await self._render_rows()

    async def update_project(
        self,
        project_name: Optional[str] = None,
        hours: Optional[float] = None,
    ) -> None:
        """
        Asynchronously queries StateManager for recent anomalies and updates the table.
        Non-blocking to the Textual UI event loop.
        """
        self.project_name = project_name
        if hours is not None:
            self.hours = hours
        await self._render_rows()

    async def _render_rows(self) -> None:
        """Renders anomaly event rows into the DataTable."""
        if not self.columns:
            return
        self.clear()

        if not self.project_name or not self.state_manager:
            self.add_row("-", "-", "No anomalies in last 24h", "-")
            return

        try:
            anomalies = await self.state_manager.get_recent_anomalies(
                project_name=self.project_name,
                hours=self.hours,
            )
        except Exception:
            anomalies = []

        if not anomalies:
            self.add_row("-", "-", "No anomalies in last 24h", "-")
            return

        for row in anomalies:
            created_at = row.get("created_at")
            if isinstance(created_at, (int, float)):
                time_str = datetime.datetime.fromtimestamp(created_at).strftime("%H:%M:%S")
            else:
                time_str = str(created_at or "-")

            node_name = str(row.get("node_name", "-"))
            error_type = str(row.get("error_type", "-"))
            error_msg = str(row.get("error_message", "-"))

            self.add_row(time_str, node_name, error_type, error_msg)


class HarnessQuotaWidget(DataTable):
    """
    Read-only DataTable widget rendering global harness quota capacities,
    rolling window limits, progress bars, OK/THROTTLED statuses with countdown,
    and by-project / by-node percentage breakdowns from local SQLite StateManager.
    """

    TABLE_COLUMNS = [
        "Harness",
        "Capacity",
        "Window",
        "Status",
        "By Project",
        "By Node",
    ]

    def __init__(
        self,
        config: Optional[GlobalConfig] = None,
        state_manager: Optional[StateManager] = None,
        project_name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.config = config or GlobalConfig()
        self.state_manager = state_manager
        self.project_name = project_name
        self.quota_manager: Optional[QuotaManager] = None
        self._render_lock = asyncio.Lock()
        if self.state_manager is not None:
            self.quota_manager = QuotaManager(self.config, self.state_manager)

    async def on_mount(self) -> None:
        """Initializes table configuration and renders initial quota metrics."""
        self.cursor_type = "row"
        self.zebra_stripes = True
        if not self.columns:
            self.add_columns(*self.TABLE_COLUMNS)
        await self._render_rows()

    async def update_quotas(
        self,
        config: Optional[GlobalConfig] = None,
        state_manager: Optional[StateManager] = None,
    ) -> None:
        """
        Asynchronously queries StateManager/QuotaManager for quota capacity and breakdowns,
        and updates the table rows. Non-blocking to the Textual UI event loop.
        """
        if config is not None:
            self.config = config
        if state_manager is not None:
            self.state_manager = state_manager
        if self.state_manager is not None:
            self.quota_manager = QuotaManager(self.config, self.state_manager)
        await self._render_rows()

    async def update_project(self, project_name: Optional[str] = None) -> None:
        """Optional hook to filter or update project context."""
        self.project_name = project_name
        await self.update_quotas()

    @staticmethod
    def _format_tokens(tokens: int) -> str:
        """Formats integer token count into compact string (e.g. 120k, 5.0M)."""
        if tokens >= 1_000_000:
            val = tokens / 1_000_000
            return f"{val:.1f}M"
        elif tokens >= 1_000:
            val = tokens / 1_000
            return f"{int(val)}k" if val.is_integer() else f"{val:.1f}k"
        else:
            return str(tokens)

    @staticmethod
    def _render_progress_bar(used: int, limit: int, width: int = 16) -> str:
        """Renders a visual capacity progress bar [████████░░░░░░░░]."""
        if limit <= 0:
            return f"[{'░' * width}]"
        fraction = min(1.0, max(0.0, used / limit))
        filled = int(round(fraction * width))
        empty = max(0, width - filled)
        return f"[{'█' * filled}{'░' * empty}]"

    async def _render_rows(self) -> None:
        """Renders harness quota rows into the DataTable."""
        async with self._render_lock:
            if not self.columns:
                return
            self.clear()

            if not self.state_manager:
                self.add_row("-", "No quota data", "-", "-", "-", "-")
                return

            if self.quota_manager is None:
                self.quota_manager = QuotaManager(self.config, self.state_manager)

            harnesses = getattr(self.config, "quota", None)
            harness_dict = harnesses.harnesses if harnesses else {}
            if not harness_dict:
                self.add_row("-", "No harnesses configured", "-", "-", "-", "-")
                return

            for harness_name in sorted(harness_dict.keys()):
                quota_cfg = harness_dict[harness_name]
                window_hours = quota_cfg.window_hours
                limit = quota_cfg.window_token_limit

                try:
                    res = await self.quota_manager.check_harness_capacity(harness_name)
                    breakdown = await self.quota_manager.get_informative_breakdown(harness_name)
                except Exception:
                    res = None
                    breakdown = {"by_project": {}, "by_node": {}}

                # 1. Window format
                w_str = f"{int(window_hours) if window_hours.is_integer() else window_hours}h Window"

                # 2. Capacity progress bar and token limits
                used = res.used if res else 0
                remaining = res.remaining if res else limit
                progress_bar = self._render_progress_bar(used, limit)
                capacity_str = f"{progress_bar} {self._format_tokens(remaining)} / {self._format_tokens(limit)}"

                # 3. Status string with badge & ETA
                if res and not res.allowed:
                    status_str = f"[bold red]THROTTLED ({self._format_tokens(res.remaining)} / {self._format_tokens(res.limit)} - Ready in {res.formatted_eta})[/bold red]"
                elif res:
                    pct_left = round((res.remaining / res.limit) * 100) if res.limit > 0 else 100
                    status_str = f"[bold green]OK ({self._format_tokens(res.remaining)} / {self._format_tokens(res.limit)} - {pct_left}% left)[/bold green]"
                else:
                    status_str = "[dim]UNKNOWN[/dim]"

                # 4. By-project breakdown
                by_project = breakdown.get("by_project", {})
                if by_project:
                    project_parts = [
                        f'"{p}": {int(pct)}%' if isinstance(pct, (int, float)) and (pct == int(pct)) else f'"{p}": {pct}%'
                        for p, pct in by_project.items()
                    ]
                    project_str = ", ".join(project_parts)
                else:
                    project_str = "-"

                # 5. By-node breakdown
                by_node = breakdown.get("by_node", {})
                if by_node:
                    node_parts = [
                        f'"{n}": {int(pct)}%' if isinstance(pct, (int, float)) and (pct == int(pct)) else f'"{n}": {pct}%'
                        for n, pct in by_node.items()
                    ]
                    node_str = ", ".join(node_parts)
                else:
                    node_str = "-"

                self.add_row(
                    harness_name,
                    capacity_str,
                    w_str,
                    status_str,
                    project_str,
                    node_str,
                    key=harness_name,
                )



