from __future__ import annotations

import asyncio
import datetime
import math
from pathlib import Path
from typing import Any, List, Optional, Tuple

from rich.text import Text
from textual.widgets import DataTable, Static
from textual.widgets.data_table import (
    CellDoesNotExist,
    ColumnDoesNotExist,
    DuplicateKey,
    RowDoesNotExist,
)

from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.quota import QuotaManager


def format_node_agent_spec(
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> str:
    """
    Pure, harness-agnostic formatter for node agent model and optional reasoning effort.
    Returns '<model> (<effort>)' when effort is non-empty, '<model>' when effort is omitted,
    or '—' (em-dash) when model is None or empty (idle/unassigned).
    Zero harness-specific branching or hardcoded harness names.
    """
    if not model or not model.strip():
        return "—"
    clean_model = model.strip()
    if effort and effort.strip():
        return f"{clean_model} ({effort.strip()})"
    return clean_model


class ConfigStatusBanner(Static):
    """
    Read-only Static banner widget displaying the canonical configuration file path,
    last reload local timestamp, and trigger source above #projects_table.
    """

    DEFAULT_CSS = """
    ConfigStatusBanner {
        height: 3;
    }
    """

    def __init__(
        self,
        config: Optional[GlobalConfig] = None,
        state_manager: Optional[StateManager] = None,
        config_path: Optional[str | Path] = None,
        id: Optional[str] = "config_status_banner",
        **kwargs,
    ) -> None:
        super().__init__(id=id, **kwargs)
        self.config = config
        self.state_manager = state_manager
        self.config_path = config_path
        self.canonical_config_path: str = "-"
        self.last_reload_timestamp: str = "-"
        self.last_reload_trigger: str = "-"
        self.last_reload_status: str = "SUCCESS"

    @property
    def resolved_config_path(self) -> str:
        return self.canonical_config_path

    @property
    def timestamp(self) -> str:
        return self.last_reload_timestamp

    @property
    def trigger(self) -> str:
        return self.last_reload_trigger

    @property
    def trigger_source(self) -> str:
        return self.last_reload_trigger

    @property
    def renderable(self) -> Any:
        if hasattr(self, "_renderable") and self._renderable is not None:
            return self._renderable
        return self.render()

    @property
    def text(self) -> str:
        r = self.renderable
        return r.plain if hasattr(r, "plain") else str(r)

    async def on_mount(self) -> None:
        """Initializes banner content on widget mount."""
        await self.refresh_status()

    async def update_status(
        self,
        config: Optional[GlobalConfig] = None,
        trigger: Optional[str] = None,
        timestamp: Optional[str] = None,
        config_path: Optional[str | Path] = None,
    ) -> None:
        """Asynchronously updates the status banner with fresh configuration and reload telemetry."""
        await self.refresh_status(config=config, trigger=trigger, timestamp=timestamp, config_path=config_path)

    async def refresh_status(
        self,
        config: Optional[GlobalConfig] = None,
        trigger: Optional[str] = None,
        timestamp: Optional[str] = None,
        config_path: Optional[str | Path] = None,
    ) -> None:
        """Queries StateManager or in-memory config to refresh path, timestamp, and trigger."""
        if config is not None:
            self.config = config
        if config_path is not None:
            self.config_path = config_path

        resolved_p = None
        if self.config and getattr(self.config, "resolved_path", None):
            resolved_p = str(Path(self.config.resolved_path).resolve())
        elif self.config_path:
            resolved_p = str(Path(self.config_path).resolve())

        trig = trigger
        ts = timestamp
        status = "SUCCESS"

        if self.state_manager:
            try:
                info = await self.state_manager.get_daemon_info()
                if not trig and "last_reload_trigger" in info:
                    trig = info["last_reload_trigger"]
                if not ts and "last_reload_timestamp" in info:
                    ts = info["last_reload_timestamp"]
                if not resolved_p and "last_reload_config_path" in info:
                    resolved_p = str(Path(info["last_reload_config_path"]).resolve())
                if "last_reload_status" in info:
                    status = info["last_reload_status"]
            except Exception:
                pass

        if not resolved_p:
            from orchestrator.config import find_config_file
            try:
                found = find_config_file(self.config_path)
                resolved_p = str(found.resolve()) if found else "~/.orchestrator/config.yaml"
            except Exception:
                resolved_p = str(self.config_path or "~/.orchestrator/config.yaml")

        if not ts:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not trig:
            trig = "Daemon Startup"

        self.canonical_config_path = str(resolved_p)
        self.last_reload_timestamp = str(ts)
        self.last_reload_trigger = str(trig)
        self.last_reload_status = status

        status_color = "green" if status == "SUCCESS" else "red"
        markup = (
            f"[bold cyan]Config:[/bold cyan] {self.canonical_config_path}  │  "
            f"[bold cyan]Last Reload:[/bold cyan] {self.last_reload_timestamp}  │  "
            f"[bold cyan]Trigger:[/bold cyan] [{status_color}]{self.last_reload_trigger}[/{status_color}]"
        )
        self.update(Text.from_markup(markup))



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
    Read-only DataTable widget rendering dual progress gauges (5-hour and weekly limits)
    with visual threshold coloring, predictive operational runway forecasts, replenishment
    countdowns, and by-project / by-node percentage breakdowns from local SQLite StateManager.
    """

    TABLE_COLUMNS = [
        "Harness",
        "5-Hour Limit",
        "Weekly Limit",
        "Runway",
        "Reset Countdown",
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

    @classmethod
    def _render_gauge(cls, remaining: int, limit: int, width: int = 10) -> str:
        """
        Renders a progress gauge with visual status threshold coloring:
        - Remaining capacity >= 40%: [green]
        - Remaining capacity between 15% and 39%: [yellow]
        - Remaining capacity < 15%: [bold red]
        Displays exact tokens remaining, limit, and percentage (e.g. "3.8M / 5.0M (76%)").
        """
        if limit <= 0:
            return "-"
        fraction = min(1.0, max(0.0, remaining / limit))
        pct = round(fraction * 100)
        filled = int(round(fraction * width))
        empty = max(0, width - filled)
        bar = f"[{'█' * filled}{'░' * empty}]"

        if pct >= 40:
            color = "green"
        elif pct >= 15:
            color = "yellow"
        else:
            color = "bold red"

        return f"[{color}]{bar} {cls._format_tokens(remaining)} / {cls._format_tokens(limit)} ({pct}%)[/{color}]"

    @classmethod
    def _render_progress_bar(cls, used: int, limit: int, width: int = 16) -> str:
        """Backwards-compatible progress bar helper."""
        if limit <= 0:
            return f"[{'░' * width}]"
        fraction = min(1.0, max(0.0, used / limit))
        filled = int(round(fraction * width))
        empty = max(0, width - filled)
        return f"[{'█' * filled}{'░' * empty}]"

    @classmethod
    def _format_runway(cls, metrics: Any) -> str:
        """
        Formats operational runway forecast string:
        - Idle: "Runway: Idle (∞)"
        - Active: "~12.6h runway remaining @ 300k tok/hr"
        """
        forecast = getattr(metrics, "runway_forecast", None) if metrics else None
        if (
            not forecast
            or getattr(forecast, "is_idle", False)
            or forecast.burn_rate <= 0
            or math.isinf(forecast.runway_hours)
        ):
            return "Runway: Idle (∞)"

        burn_str = cls._format_tokens(int(round(forecast.burn_rate)))
        return f"~{forecast.formatted} runway remaining @ {burn_str} tok/hr"

    @classmethod
    def _format_countdown(cls, metrics: Any) -> str:
        """
        Formats reset countdown string:
        - Short window countdown or weekly window countdown (e.g. "Resets in 26 min", "Full Capacity (0s)")
        """
        if not metrics:
            return "Full Capacity (0s)"
        short_w = getattr(metrics, "short_window", None)
        weekly_w = getattr(metrics, "weekly_window", None)

        if weekly_w and getattr(weekly_w, "eta_seconds", 0) > getattr(short_w, "eta_seconds", 0):
            return str(weekly_w.formatted_countdown)
        if short_w and hasattr(short_w, "formatted_countdown"):
            return str(short_w.formatted_countdown)
        return "Full Capacity (0s)"

    async def _render_rows(self) -> None:
        """Renders dual-window harness quota rows into the DataTable with keyed in-place diffing."""
        async with self._render_lock:
            if not self.columns:
                return

            if not self.quota_manager:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "No quota data", "-", "-", "-", "-", "-"))],
                )
                return

            harnesses = getattr(self.config, "quota", None)
            harness_dict = harnesses.harnesses if harnesses else {}
            if not harness_dict:
                _apply_keyed_diff(
                    self,
                    [("-empty-", ("-", "No harnesses configured", "-", "-", "-", "-", "-"))],
                )
                return

            target_rows: List[Tuple[str, Tuple[Any, ...]]] = []
            for harness_name in sorted(harness_dict.keys()):
                try:
                    metrics = await self.quota_manager.calculate_dashboard_metrics(harness_name)
                    breakdown = await self.quota_manager.get_informative_breakdown(harness_name)
                except Exception:
                    metrics = None
                    breakdown = {"by_project": {}, "by_node": {}}

                if metrics and metrics.short_window:
                    short_gauge = self._render_gauge(
                        metrics.short_window.remaining,
                        metrics.short_window.limit,
                    )
                else:
                    short_gauge = "-"

                if metrics and metrics.weekly_window and metrics.weekly_window.limit > 0:
                    weekly_gauge = self._render_gauge(
                        metrics.weekly_window.remaining,
                        metrics.weekly_window.limit,
                    )
                else:
                    weekly_gauge = "-"

                runway_str = self._format_runway(metrics)
                countdown_str = self._format_countdown(metrics)

                # By-project breakdown
                by_project = breakdown.get("by_project", {})
                if by_project:
                    project_parts = [
                        f'"{p}": {int(pct)}%' if isinstance(pct, (int, float)) and (pct == int(pct)) else f'"{p}": {pct}%'
                        for p, pct in by_project.items()
                    ]
                    project_str = ", ".join(project_parts)
                else:
                    project_str = "-"

                # By-node breakdown
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
                            short_gauge,
                            weekly_gauge,
                            runway_str,
                            countdown_str,
                            project_str,
                            node_str,
                        ),
                    )
                )

            _apply_keyed_diff(self, target_rows)


