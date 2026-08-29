from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from textual.widgets import DataTable

from orchestrator.db import StateManager


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
