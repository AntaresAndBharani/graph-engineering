from __future__ import annotations

import asyncio
import datetime
from typing import Any, List, Optional, Tuple

from textual.widgets import DataTable
from textual.widgets.data_table import (
    CellDoesNotExist,
    ColumnDoesNotExist,
    DuplicateKey,
    RowDoesNotExist,
)

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.quota import QuotaManager


def _apply_keyed_diff(
    table: DataTable,
    target_rows: List[Tuple[str, Tuple[Any, ...]]],
) -> None:
    """
    Applies non-destructive keyed in-place cell diffing to a DataTable.
    - Drops deleted rows individually.
    - Inserts newly added rows with explicit row keys.
    - Updates modified cells in-place with bounds-validated column keys.
    Prevents cursor coordinate resets and DOM reconstruction flicker.
    """
    if not table.columns:
        return

    existing_keys = {
        k.value if hasattr(k, "value") else str(k)
        for k in table.rows.keys()
    }
    target_keys = {rk for rk, _ in target_rows}

    # 1. Remove deleted rows
    for k in existing_keys - target_keys:
        try:
            table.remove_row(k)
        except (RowDoesNotExist, KeyError):
            pass

    # 2. Add or update rows in-place
    col_keys = list(table.columns.keys())
    for row_key, values in target_rows:
        if row_key not in existing_keys and row_key not in table.rows:
            try:
                table.add_row(*values, key=row_key)
            except (DuplicateKey, KeyError):
                pass
        else:
            for col_idx, new_val in enumerate(values):
                if 0 <= col_idx < len(col_keys):
                    col_key = col_keys[col_idx]
                    try:
                        curr_val = table.get_cell(row_key, col_key)
                    except (CellDoesNotExist, RowDoesNotExist, ColumnDoesNotExist, KeyError):
                        curr_val = None
                    if curr_val != new_val:
                        try:
                            table.update_cell(row_key, col_key, new_val)
                        except (CellDoesNotExist, RowDoesNotExist, ColumnDoesNotExist, KeyError):
                            pass

    # 3. Re-align row locations to preserve exact target_rows order
    key_map = {
        (k.value if hasattr(k, "value") else str(k)): k
        for k in table.rows.keys()
    }
    target_row_key_order = [rk for rk, _ in target_rows]
    ordered_row_keys = [
        key_map[rk]
        for rk in target_row_key_order
        if rk in key_map
    ]
    if ordered_row_keys and len(ordered_row_keys) == len(table.rows):
        from textual._two_way_dict import TwoWayDict
        table._row_locations = TwoWayDict(
            {row_key: new_index for new_index, row_key in enumerate(ordered_row_keys)}
        )
        table._update_count += 1
        table.refresh()


def format_pr_status_badge(
    linked_pr: Optional[int],
    pr_status: Optional[str] = None,
    pr_ci_details: Optional[str] = None,
) -> str:
    """
    Formats PR status badge with Rich color tags:
    - PASS: #<pr> [green]PASS[/green]
    - FAIL: #<pr> [red]FAIL[/red]
    - RUNNING: #<pr> [yellow]RUNNING[/yellow]
    - MERGED: #<pr> [blue]MERGED[/blue]
    - If linked_pr present without specific CI: #<pr>
    - If no linked_pr: '-'
    """
    if not linked_pr:
        return "-"

    status_upper = str(pr_status).upper() if pr_status else ""
    ci_upper = str(pr_ci_details).upper() if pr_ci_details else ""

    if status_upper == "MERGED":
        return f"#{linked_pr} [blue]MERGED[/blue]"

    if ci_upper == "PASS":
        return f"#{linked_pr} [green]PASS[/green]"
    elif ci_upper == "FAIL":
        return f"#{linked_pr} [red]FAIL[/red]"
    elif ci_upper == "RUNNING":
        return f"#{linked_pr} [yellow]RUNNING[/yellow]"

    if status_upper and status_upper != "OPEN":
        return f"#{linked_pr} [{status_upper}]"

    return f"#{linked_pr}"


class SDLCProgressWidget(DataTable):
    """
    Read-only DataTable widget rendering active SDLC items (Stories, Subtasks, PRs)
    in a hierarchical tree with live PR and CI check statuses for a selected project
    from local SQLite StateManager.
    """

    TABLE_COLUMNS = [
        "ID",
        "Title",
        "Status/Label",
        "PR Status",
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
        self._render_lock = asyncio.Lock()

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
        """Renders SDLC hierarchy into DataTable with tree prefixes and keyed in-place diffing."""
        async with self._render_lock:
            if not self.columns:
                return

            if not self.project_name or not self.state_manager:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "No active SDLC items", "-", "-"))],
                )
                return

            try:
                hierarchy = await self.state_manager.get_active_sdlc_hierarchy(self.project_name)
            except Exception:
                hierarchy = []

            try:
                active_locked_id = await self.state_manager.get_active_locked_story_id(self.project_name)
            except Exception:
                active_locked_id = None

            if not hierarchy:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "No active SDLC items", "-", "-"))],
                )
                return

            target_rows: List[Tuple[str, Tuple[Any, ...]]] = []
            for root in hierarchy:
                root_num = root.get("issue_number") or root.get("id") or root.get("number")
                root_id = f"#{root_num}" if (root_num is not None and str(root_num) != "-") else "-"
                root_title = str(root.get("title", ""))

                raw_labels = root.get("labels")
                raw_state = root.get("state") or root.get("status")
                base_status = str(raw_labels) if raw_labels else (str(raw_state) if raw_state else "-")

                is_locked = (
                    active_locked_id is not None
                    and root_num is not None
                    and str(root_num).isdigit()
                    and int(root_num) == int(active_locked_id)
                )
                if is_locked:
                    status_label = f"[LOCKED] {base_status}" if base_status != "-" else "[LOCKED]"
                else:
                    status_label = base_status

                linked_pr = root.get("linked_pr")
                pr_status = root.get("pr_status")
                pr_ci = root.get("pr_ci_details")
                pr_badge = format_pr_status_badge(linked_pr, pr_status, pr_ci)

                row_key = str(root_num) if root_num is not None else f"story_{root_title}"
                target_rows.append((row_key, (root_id, root_title, status_label, pr_badge)))

                subtasks = root.get("subtasks") or root.get("children") or []
                total_subtasks = len(subtasks)
                for idx, sub in enumerate(subtasks):
                    sub_num = sub.get("issue_number") or sub.get("id") or sub.get("number")
                    sub_id = f"#{sub_num}" if (sub_num is not None and str(sub_num) != "-") else "-"
                    sub_title = str(sub.get("title", ""))
                    prefix = "  └─ " if idx == total_subtasks - 1 else "  ├─ "
                    display_title = f"{prefix}{sub_title}"

                    sub_labels = sub.get("labels")
                    sub_state = sub.get("state") or sub.get("status")
                    sub_status_label = str(sub_labels) if sub_labels else (str(sub_state) if sub_state else "-")

                    sub_pr = sub.get("linked_pr")
                    sub_pr_status = sub.get("pr_status")
                    sub_pr_ci = sub.get("pr_ci_details")
                    sub_pr_badge = format_pr_status_badge(sub_pr, sub_pr_status, sub_pr_ci)

                    sub_row_key = str(sub_num) if sub_num is not None else f"sub_{sub_title}"
                    target_rows.append((sub_row_key, (sub_id, display_title, sub_status_label, sub_pr_badge)))

            _apply_keyed_diff(self, target_rows)


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
        self._render_lock = asyncio.Lock()

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
        """Renders anomaly event rows into the DataTable with keyed in-place diffing."""
        async with self._render_lock:
            if not self.columns:
                return

            if not self.project_name or not self.state_manager:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "-", "No anomalies in last 24h", "-"))],
                )
                return

            try:
                anomalies = await self.state_manager.get_recent_anomalies(
                    project_name=self.project_name,
                    hours=self.hours,
                )
            except Exception:
                anomalies = []

            if not anomalies:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "-", "No anomalies in last 24h", "-"))],
                )
                return

            target_rows: List[Tuple[str, Tuple[Any, ...]]] = []
            for row in anomalies:
                created_at = row.get("created_at")
                if isinstance(created_at, (int, float)):
                    time_str = datetime.datetime.fromtimestamp(created_at).strftime("%H:%M:%S")
                else:
                    time_str = str(created_at or "-")

                node_name = str(row.get("node_name", "-"))
                error_type = str(row.get("error_type", "-"))
                error_msg = str(row.get("error_message", "-"))

                anomaly_id = row.get("id")
                if anomaly_id is not None:
                    row_key = f"anomaly_{anomaly_id}"
                else:
                    row_key = f"anomaly_{created_at}_{node_name}_{error_type}_{error_msg}"

                target_rows.append((row_key, (time_str, node_name, error_type, error_msg)))

            _apply_keyed_diff(self, target_rows)


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
        quota_manager: Optional[QuotaManager] = None,
        project_name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.config = config or GlobalConfig()
        self.state_manager = state_manager
        self.quota_manager = quota_manager
        self.project_name = project_name
        self._render_lock = asyncio.Lock()

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
        quota_manager: Optional[QuotaManager] = None,
    ) -> None:
        """
        Asynchronously queries QuotaManager for quota capacity and breakdowns,
        and updates the table rows. Non-blocking to the Textual UI event loop.
        """
        if config is not None:
            self.config = config
        if state_manager is not None:
            self.state_manager = state_manager
        if quota_manager is not None:
            self.quota_manager = quota_manager
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
        """Renders harness quota rows into the DataTable with keyed in-place diffing."""
        async with self._render_lock:
            if not self.columns:
                return

            if not self.quota_manager:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "No quota data", "-", "-", "-", "-"))],
                )
                return

            harnesses = getattr(self.config, "quota", None)
            harness_dict = harnesses.harnesses if harnesses else {}
            if not harness_dict:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "No harnesses configured", "-", "-", "-", "-"))],
                )
                return

            target_rows: List[Tuple[str, Tuple[Any, ...]]] = []
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

                target_rows.append(
                    (
                        harness_name,
                        (
                            harness_name,
                            capacity_str,
                            w_str,
                            status_str,
                            project_str,
                            node_str,
                        ),
                    )
                )

            _apply_keyed_diff(self, target_rows)

