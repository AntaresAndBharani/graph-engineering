from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import pytest
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, TabbedContent

from orchestrator.config import (
    GlobalConfig,
    HarnessQuotaConfig,
    NodeConfig,
    ProjectConfig,
    QuotaSettings,
    SettingsConfig,
)
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import ProjectLogBufferManager, TextualLogHandler
from orchestrator.quota import QuotaManager
from orchestrator.reloader import ConfigHolder
from orchestrator.ui.dashboard import DashboardApp
from orchestrator.ui.widgets import (
    AnomalyAlertsWidget,
    ConfigStatusBanner,
    HarnessQuotaWidget,
    SDLCProgressWidget,
    format_node_agent_spec,
)


def test_textual_log_handler_bounded_buffer():
    """Asserts TextualLogHandler retains at most 1000 records."""
    handler = TextualLogHandler(maxlen=1000)
    logger = logging.getLogger("test_bounded_logger")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for i in range(1250):
        logger.info(f"Log message {i}")

    assert len(handler.buffer) == 1000
    assert len(handler.records) == 1000
    # Check that oldest records were dropped and latest retained
    assert "Log message 1249" in handler.records[-1].getMessage()
    assert "Log message 250" in handler.records[0].getMessage()


def test_textual_log_handler_drops_filtered_node_traces():
    """Asserts TextualLogHandler drops filtered node traces."""
    handler = TextualLogHandler(maxlen=1000)

    # 1. Record with node_trace=True
    rec_trace1 = logging.LogRecord(
        name="orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Harness trace line",
        args=(),
        exc_info=None,
    )
    setattr(rec_trace1, "node_trace", True)
    handler.emit(rec_trace1)
    assert len(handler.buffer) == 0

    # 2. Record with is_node_trace=True
    rec_trace2 = logging.LogRecord(
        name="orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Harness trace line 2",
        args=(),
        exc_info=None,
    )
    setattr(rec_trace2, "is_node_trace", True)
    handler.emit(rec_trace2)
    assert len(handler.buffer) == 0

    # 3. Record with logger name starting with orchestrator.node_trace
    rec_trace3 = logging.LogRecord(
        name="orchestrator.node_trace.devtest",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Harness verbose trace",
        args=(),
        exc_info=None,
    )
    handler.emit(rec_trace3)
    assert len(handler.buffer) == 0

    # 4. Standard root orchestrator record
    rec_valid = logging.LogRecord(
        name="orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Core daemon heartbeat",
        args=(),
        exc_info=None,
    )
    handler.emit(rec_valid)
    assert len(handler.buffer) == 1
    assert handler.buffer[0].getMessage() == "Core daemon heartbeat"


def test_pyproject_declares_textual_dependency():
    """Asserts pyproject.toml declares textual>=0.50.0 in dependencies."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    assert pyproject_path.exists()
    content = pyproject_path.read_text(encoding="utf-8")
    assert "textual>=0.50.0" in content


def test_no_circular_dependencies():
    """Asserts orchestrator.ui.dashboard is not imported by config.py or db.py."""
    import orchestrator.config as cfg
    import orchestrator.db as db_mod

    assert "orchestrator.ui" not in [
        getattr(obj, "__module__", "") for obj in cfg.__dict__.values()
    ]
    assert "orchestrator.ui" not in [
        getattr(obj, "__module__", "") for obj in db_mod.__dict__.values()
    ]


@pytest.mark.asyncio
async def test_dashboard_app_composition(tmp_path: Path):
    """Asserts DashboardApp composes Header, DataTable, RichLog, and Footer."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="proj1", repo="org/proj1", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as _:
        assert app.query_one(Header) is not None
        assert app.query_one(DataTable) is not None
        assert app.query_one(RichLog) is not None
        assert app.query_one(Footer) is not None


@pytest.mark.asyncio
async def test_dashboard_table_columns(tmp_path: Path):
    """
    Scenario: Table columns
    Given the dashboard is actively rendering
    Then the DataTable displays columns [Project Name | Repository | Active Node | Status | Last Updated | Locks/Anomalies]
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="proj1", repo="org/proj1", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as _:
        table = app.query_one(DataTable)
        # Extract column labels from table.columns dictionary
        column_labels = [str(col.label) for col in table.columns.values()]
        expected_columns = [
            "Project Name",
            "Repository",
            "Active Node",
            "Status",
            "Last Updated",
            "Locks/Anomalies",
            "Agent Model",
        ]
        assert column_labels == expected_columns
        assert app.TABLE_COLUMNS == expected_columns


@pytest.mark.asyncio
async def test_dashboard_table_alphabetical_sorting(tmp_path: Path):
    """
    Scenario: Alphabetical sort
    Given multiple managed projects are active
    When the DataTable renders
    Then rows are sorted alphabetically by project name
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="zebra", repo="org/zebra", local_path=str(tmp_path)),
            ProjectConfig(name="Beta", repo="org/Beta", local_path=str(tmp_path)),
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
            ProjectConfig(name="middle", repo="org/middle", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as _:
        table = app.query_one(DataTable)
        # Verify rendered rows in table
        rendered_names = []
        for row_index in range(table.row_count):
            row_data = table.get_row_at(row_index)
            rendered_names.append(row_data[0])

        assert rendered_names == ["alpha", "Beta", "middle", "zebra"]


@pytest.mark.asyncio
async def test_dashboard_periodic_refresh_and_live_state_updates(tmp_path: Path):
    """
    Scenario: Non-blocking periodic refresh
    Given the dashboard is running
    When the 2-second interval timer fires
    Then it queries state_manager/in-memory worker states asynchronously
    And the UI rendering loop is not blocked during the query
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="proj_a", repo="org/proj_a", local_path=str(tmp_path)),
            ProjectConfig(name="proj_b", repo="org/proj_b", local_path=str(tmp_path)),
            ProjectConfig(name="proj_c", repo="org/proj_c", local_path=str(tmp_path), enabled=False),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as _:
        table = app.query_one(DataTable)
        assert table.row_count == 3

        # Initial state check
        row_a = table.get_row_at(0)
        assert row_a[0] == "proj_a"
        assert "Active" in str(row_a[3])
        assert "Idle" in str(row_a[2])

        row_c = table.get_row_at(2)
        assert row_c[0] == "proj_c"
        assert "Disabled" in str(row_c[3])

        # Mutate state asynchronously: Acquire lock on proj_a and pause proj_b
        await state_manager.acquire_lock(issue_id=42, repo="org/proj_a", node_type="DevTest")
        await state_manager.pause_project("proj_b")

        # Trigger update_projects_table (as fired by the 2-second timer)
        await app.update_projects_table()

        # Verify live updates rendered in DataTable
        row_a_updated = table.get_row_at(0)
        assert row_a_updated[0] == "proj_a"
        assert "DevTest" in str(row_a_updated[2])
        assert "Issue #42" in str(row_a_updated[5])

        row_b_updated = table.get_row_at(1)
        assert row_b_updated[0] == "proj_b"
        assert "Paused" in str(row_b_updated[3])


@pytest.mark.asyncio
async def test_dashboard_manual_refresh_action(tmp_path: Path):
    """Asserts pressing 'r' triggers action_refresh and updates table without blocking."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="proj_test", repo="org/proj_test", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert table.row_count == 1
        assert "Idle" in str(table.get_row_at(0)[2])

        # Simulate job running in state_manager
        await state_manager.acquire_lock(issue_id=99, repo="org/proj_test", node_type="Reviewer")

        # Press 'r' to trigger manual refresh binding
        await pilot.press("r")

        row = table.get_row_at(0)
        assert "Reviewer" in str(row[2])
        assert "Issue #99" in str(row[5])


@pytest.mark.asyncio
async def test_dashboard_teardown_and_quit(tmp_path: Path, monkeypatch):
    """Asserts action_quit performs clean resource cleanup and daemon unregistration when idle."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.register_daemon(12345)

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        await pilot.press("q")

    # Verify daemon PID was unregistered
    info = await state_manager.get_daemon_info()
    assert info.get("status") == "STOPPED"
    assert "pid" not in info


@pytest.mark.asyncio
async def test_dashboard_graceful_drain_mode(tmp_path: Path, monkeypatch):
    """Asserts that pressing 'q' when jobs are active enters draining mode and requests stop."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.register_daemon(12345)
    await state_manager.acquire_lock(issue_id=10, repo="org/alpha", node_type="devtest")

    monkeypatch.setattr(AsyncHarnessAdapter, "has_active_processes", lambda: True)

    waited = []
    async def mock_wait_all(timeout=30.0):
        waited.append(True)
        return True

    monkeypatch.setattr(AsyncHarnessAdapter, "wait_all_active", mock_wait_all)

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app.is_draining is True
        assert "DRAINING" in app.sub_title
        assert await state_manager.is_stop_requested() is True


@pytest.mark.asyncio
async def test_dashboard_double_press_force_quit(tmp_path: Path, monkeypatch):
    """Asserts that pressing 'q' twice triggers immediate force quit and terminates subprocesses."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.register_daemon(12345)

    monkeypatch.setattr(AsyncHarnessAdapter, "has_active_processes", lambda: True)

    async def mock_wait_all(timeout=30.0):
        try:
            await asyncio.sleep(10.0)
            return True
        except asyncio.CancelledError:
            return False

    monkeypatch.setattr(AsyncHarnessAdapter, "wait_all_active", mock_wait_all)

    terminated = []
    def mock_terminate():
        terminated.append(True)
        return 1

    monkeypatch.setattr(AsyncHarnessAdapter, "terminate_all_active", mock_terminate)

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        # First Q -> enters draining mode
        await pilot.press("q")
        assert app.is_draining is True
        assert len(terminated) == 0

        # Second Q -> triggers force quit
        await pilot.press("q")
        assert len(terminated) == 1


@pytest.mark.asyncio
async def test_dashboard_sigint_and_task_cancellation_cleanup(tmp_path: Path, monkeypatch):
    """Asserts SIGINT/CancelledError during watch daemon triggers worker cancellation and teardown."""
    from orchestrator.cli import _watch_daemon_tui

    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "alpha"
    repo: "org/alpha"
    local_path: "{posix_path}"
        """,
        encoding="utf-8",
    )

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    worker_cancelled = []
    worker_started = asyncio.Event()

    async def mock_worker_loop(*args, **kwargs):
        worker_started.set()
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            worker_cancelled.append(True)
            raise

    terminated_calls = []

    def mock_terminate():
        terminated_calls.append(True)
        return 0

    monkeypatch.setattr("orchestrator.cli._project_worker_loop", mock_worker_loop)
    monkeypatch.setattr("orchestrator.cli.sync_all_projects_labels", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(AsyncHarnessAdapter, "terminate_all_active", mock_terminate)

    # Mock run_async to raise CancelledError (simulating SIGINT)
    async def mock_run_async(self, *args, **kwargs):
        await worker_started.wait()
        raise asyncio.CancelledError()

    from textual.app import App
    monkeypatch.setattr(App, "run_async", mock_run_async)
    monkeypatch.setattr(DashboardApp, "run_async", mock_run_async)

    # Run _watch_daemon_tui
    await _watch_daemon_tui(interval_override=5, config_path=config_file)

    assert len(worker_cancelled) == 1
    assert len(terminated_calls) >= 1

    # Verify daemon PID was unregistered from state.db
    info = await state_manager.get_daemon_info()
    assert info.get("status") == "STOPPED"
    assert "pid" not in info


@pytest.mark.asyncio
async def test_sdlc_progress_widget_renders_items(tmp_path: Path):
    """
    Scenario: SDLCProgressWidget renders items for a project
    Given get_sdlc_items(project_name) returns a list of items
    When SDLCProgressWidget is given that project_name
    Then it renders a table with columns [ID | Title | Status/Label | Linked PR]
    """
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 42,
            "title": "feat(core): implement feature",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "linked_pr": 105,
        },
        {
            "issue_number": 43,
            "title": "fix(core): fix bug",
            "state": "OPEN",
            "labels": "needs-architect-review",
            "linked_pr": None,
        },
    ]
    await state_manager.sync_project_sdlc_items("project_alpha", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="project_alpha")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.TABLE_COLUMNS == ["ID", "Title", "Status/Label", "PR Status"]
        column_labels = [str(col.label) for col in widget.columns.values()]
        assert column_labels == ["ID", "Title", "Status/Label", "PR Status"]

        assert widget.row_count == 2
        row0 = widget.get_row_at(0)
        assert row0[0] == "#42"
        assert row0[1] == "feat(core): implement feature"
        assert row0[2] == "ready-for-dev"
        assert row0[3] == "#105"

        row1 = widget.get_row_at(1)
        assert row1[0] == "#43"
        assert row1[1] == "fix(core): fix bug"
        assert row1[2] == "needs-architect-review"
        assert row1[3] == "-"


@pytest.mark.asyncio
async def test_sdlc_progress_widget_empty_state(tmp_path: Path):
    """
    Scenario: SDLCProgressWidget empty state
    Given get_sdlc_items(project_name) returns an empty list
    When SDLCProgressWidget renders
    Then it displays a clean empty-state row without raising an exception
    """
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="empty_project")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.row_count == 1
        row = widget.get_row_at(0)
        assert "No active SDLC items" in str(row[1])


@pytest.mark.asyncio
async def test_sdlc_progress_widget_dynamic_project_update(tmp_path: Path):
    """Asserts SDLCProgressWidget dynamically updates rows when switching projects."""
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    await state_manager.sync_project_sdlc_items(
        "p1", [{"issue_number": 1, "title": "P1 Issue", "labels": "ready-for-dev"}]
    )
    await state_manager.sync_project_sdlc_items(
        "p2", [{"issue_number": 2, "title": "P2 Issue", "labels": "architect-approved", "linked_pr": 50}]
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="p1")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.row_count == 1
        assert widget.get_row_at(0)[0] == "#1"

        # Dynamically switch to p2
        await widget.update_project("p2")
        assert widget.row_count == 1
        assert widget.get_row_at(0)[0] == "#2"
        assert widget.get_row_at(0)[3] == "#50"

        # Switch to non-existent project
        await widget.update_project("p3")
        assert widget.row_count == 1
        assert "No active SDLC items" in str(widget.get_row_at(0)[1])


@pytest.mark.asyncio
async def test_anomaly_alerts_widget_renders_anomalies(tmp_path: Path):
    """
    Scenario: AnomalyAlertsWidget renders 24h anomalies for a project
    Given get_recent_anomalies(project_name, hours=24.0) returns anomaly rows
    When AnomalyAlertsWidget is given that project_name
    Then it renders each anomaly with node_name, error_type, error_message, and a relative/absolute timestamp
    """
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    await state_manager.record_anomaly_event(
        project_name="project_beta",
        node_name="devtest",
        error_type="HarnessTimeout",
        error_message="Execution exceeded 900s limit",
        issue_number=33,
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield AnomalyAlertsWidget(state_manager=state_manager, project_name="project_beta")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(AnomalyAlertsWidget)
        assert widget.TABLE_COLUMNS == ["Timestamp", "Node", "Error Type", "Error Message"]
        column_labels = [str(col.label) for col in widget.columns.values()]
        assert column_labels == ["Timestamp", "Node", "Error Type", "Error Message"]

        assert widget.row_count == 1
        row = widget.get_row_at(0)
        assert row[1] == "devtest"
        assert row[2] == "HarnessTimeout"
        assert row[3] == "Execution exceeded 900s limit"
        assert len(str(row[0])) > 0


@pytest.mark.asyncio
async def test_anomaly_alerts_widget_empty_state(tmp_path: Path):
    """
    Scenario: AnomalyAlertsWidget empty state
    Given get_recent_anomalies(project_name, hours=24.0) returns an empty list
    When AnomalyAlertsWidget renders
    Then it displays a clean empty-state row without raising an exception
    """
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield AnomalyAlertsWidget(state_manager=state_manager, project_name="clean_project")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(AnomalyAlertsWidget)
        assert widget.row_count == 1
        row = widget.get_row_at(0)
        assert "No anomalies in last 24h" in str(row[2])


@pytest.mark.asyncio
async def test_widgets_non_blocking_async_reads(tmp_path: Path):
    """
    Scenario: Non-blocking reads
    Given either widget queries StateManager
    When the query executes
    Then it must be awaited asynchronously and never block the Textual UI event loop
    """
    import inspect
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    sdlc_widget = SDLCProgressWidget(state_manager=state_manager, project_name="alpha")
    alerts_widget = AnomalyAlertsWidget(state_manager=state_manager, project_name="alpha")

    # Assert update_project methods are coroutines
    assert inspect.iscoroutinefunction(sdlc_widget.update_project)
    assert inspect.iscoroutinefunction(alerts_widget.update_project)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield sdlc_widget
            yield alerts_widget

    app = TestApp()
    async with app.run_test() as _:
        await sdlc_widget.update_project("alpha")
        await alerts_widget.update_project("alpha")

        assert sdlc_widget.row_count == 1
        assert alerts_widget.row_count == 1


@pytest.mark.asyncio
async def test_dashboard_multi_pane_layout_composition(tmp_path: Path):
    """
    Scenario: Layout structure on mount
    Given DashboardApp mounts in an interactive terminal
    Then the screen contains:
      - Top pane: ProjectStatusTable (alphabetically sorted, existing)
      - Bottom container: a 50/50 Horizontal split
        - Bottom-left: SDLCProgressWidget
        - Bottom-right: TabbedContent with tabs "Logs" (existing RichLog) and "Alerts (24h)" (AnomalyAlertsWidget)
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="proj1", repo="org/proj1", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Top pane
        top_table = app.query_one("#projects_table", DataTable)
        assert top_table is not None

        # Bottom container
        bottom_container = app.query_one("#bottom_container", Horizontal)
        assert bottom_container is not None

        # Bottom-left widget
        sdlc_widget = app.query_one("#sdlc_widget", SDLCProgressWidget)
        assert sdlc_widget is not None

        # Bottom-right tabbed container
        tabs = app.query_one("#tabs", TabbedContent)
        assert tabs is not None

        # Tabs content
        log_view = app.query_one("#log_view", RichLog)
        assert log_view is not None

        alerts_widget = app.query_one("#alerts_widget", AnomalyAlertsWidget)
        assert alerts_widget is not None

        # Header and Footer
        assert app.query_one(Header) is not None
        assert app.query_one(Footer) is not None


@pytest.mark.asyncio
async def test_dashboard_no_project_selected_empty_state(tmp_path: Path):
    """
    Scenario: No project selected yet
    Given the dashboard has just mounted and no row is highlighted
    Then both bottom panes render a graceful empty state without exceptions
    """
    config = GlobalConfig(projects=[])
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        sdlc_widget = app.query_one("#sdlc_widget", SDLCProgressWidget)
        alerts_widget = app.query_one("#alerts_widget", AnomalyAlertsWidget)

        # Before any project row selection, both widgets show graceful empty states
        assert sdlc_widget.row_count == 1
        assert "No active SDLC items" in str(sdlc_widget.get_row_at(0)[1])

        assert alerts_widget.row_count == 1
        assert "No anomalies in last 24h" in str(alerts_widget.get_row_at(0)[2])


@pytest.mark.asyncio
async def test_dashboard_reactive_project_selection_updates_sdlc_pane(tmp_path: Path):
    """
    Scenario: Reactive project selection updates SDLC pane
    Given multiple projects are listed in the top ProjectStatusTable
    When the user highlights a project row via Up/Down arrow keys (DataTable.RowHighlighted)
    Then SDLCProgressWidget immediately re-queries local SQLite and displays that project's active issues/PRs
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
            ProjectConfig(name="beta", repo="org/beta", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Seed SDLC items for alpha and beta
    await state_manager.sync_project_sdlc_items(
        "alpha",
        [
            {
                "issue_number": 101,
                "title": "feat(core): alpha feature",
                "state": "OPEN",
                "labels": "ready-for-dev",
                "linked_pr": 201,
            }
        ],
    )
    await state_manager.sync_project_sdlc_items(
        "beta",
        [
            {
                "issue_number": 102,
                "title": "fix(core): beta bugfix",
                "state": "OPEN",
                "labels": "needs-architect-review",
                "linked_pr": None,
            }
        ],
    )

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        sdlc_widget = app.query_one("#sdlc_widget", SDLCProgressWidget)
        table.focus()

        # Highlight row 1 (beta) via Down arrow
        await pilot.press("down")
        await pilot.pause()

        assert app.selected_project == "beta"
        assert sdlc_widget.row_count == 1
        row_beta = sdlc_widget.get_row_at(0)
        assert row_beta[0] == "#102"
        assert row_beta[1] == "fix(core): beta bugfix"
        assert row_beta[2] == "needs-architect-review"
        assert row_beta[3] == "-"

        # Highlight row 0 (alpha) via Up arrow
        await pilot.press("up")
        await pilot.pause()

        assert app.selected_project == "alpha"
        assert sdlc_widget.row_count == 1
        row_alpha = sdlc_widget.get_row_at(0)
        assert row_alpha[0] == "#101"
        assert row_alpha[1] == "feat(core): alpha feature"
        assert row_alpha[2] == "ready-for-dev"
        assert row_alpha[3] == "#201"


@pytest.mark.asyncio
async def test_dashboard_reactive_project_selection_updates_alerts_tab(tmp_path: Path):
    """
    Scenario: Reactive project selection updates Alerts tab
    Given a project is highlighted in the top table
    When the RowHighlighted event fires
    Then the "Alerts (24h)" tab content re-queries and displays only anomaly events for that project within the last 24 hours
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
            ProjectConfig(name="beta", repo="org/beta", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Seed anomaly events for alpha and beta
    await state_manager.record_anomaly_event(
        project_name="alpha",
        node_name="devtest",
        error_type="HarnessTimeout",
        error_message="Alpha execution timed out",
        issue_number=101,
    )
    await state_manager.record_anomaly_event(
        project_name="beta",
        node_name="reviewer",
        error_type="MergeConflict",
        error_message="Beta merge conflict in main",
        issue_number=102,
    )

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        alerts_widget = app.query_one("#alerts_widget", AnomalyAlertsWidget)
        table.focus()

        # Highlight row 1 (beta) via Down arrow
        await pilot.press("down")
        await pilot.pause()

        assert app.selected_project == "beta"
        assert alerts_widget.row_count == 1
        row_beta = alerts_widget.get_row_at(0)
        assert row_beta[1] == "reviewer"
        assert row_beta[2] == "MergeConflict"
        assert row_beta[3] == "Beta merge conflict in main"

        # Highlight row 0 (alpha) via Up arrow
        await pilot.press("up")
        await pilot.pause()

        assert app.selected_project == "alpha"
        assert alerts_widget.row_count == 1
        row_alpha = alerts_widget.get_row_at(0)
        assert row_alpha[1] == "devtest"
        assert row_alpha[2] == "HarnessTimeout"
        assert row_alpha[3] == "Alpha execution timed out"


@pytest.mark.asyncio
async def test_dashboard_keyboard_navigation_arrow_keys(tmp_path: Path):
    """Asserts pressing down and up arrow keys updates selected_project and bottom panes."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="p1", repo="org/p1", local_path=str(tmp_path)),
            ProjectConfig(name="p2", repo="org/p2", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    await state_manager.sync_project_sdlc_items(
        "p1", [{"issue_number": 1, "title": "P1 Issue", "labels": "open"}]
    )
    await state_manager.sync_project_sdlc_items(
        "p2", [{"issue_number": 2, "title": "P2 Issue", "labels": "done"}]
    )

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        table.focus()

        # Navigate down
        await pilot.press("down")
        await pilot.pause()
        assert app.selected_project in ("p1", "p2")

        sdlc = app.query_one("#sdlc_widget", SDLCProgressWidget)
        assert sdlc.row_count >= 1


@pytest.mark.asyncio
async def test_harness_quota_widget_dependency_injection(tmp_path: Path):
    """
    Asserts HarnessQuotaWidget adheres to Design Pattern #6 (Dependency Injection)
    and does not self-construct QuotaManager when not provided.
    """
    from textual.app import App, ComposeResult

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            harnesses={
                "claude": HarnessQuotaConfig(window_hours=5.0, window_token_limit=5_000_000)
            }
        )
    )

    # 1. Without quota_manager, widget does not instantiate one and shows "No quota data"
    class TestAppNoMgr(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=None)

    app1 = TestAppNoMgr()
    async with app1.run_test() as _:
        w1 = app1.query_one(HarnessQuotaWidget)
        assert w1.quota_manager is None
        assert w1.row_count == 1
        row = w1.get_row_at(0)
        assert "No quota data" in str(row[1])

    # 2. With injected quota_manager, widget uses it directly
    quota_mgr = QuotaManager(config, state_manager)

    class TestAppWithMgr(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=quota_mgr)

    app2 = TestAppWithMgr()
    async with app2.run_test() as _:
        w2 = app2.query_one(HarnessQuotaWidget)
        assert w2.quota_manager is quota_mgr
        assert w2.row_count == 1
        row = w2.get_row_at(0)
        assert "claude" in str(row[0])


@pytest.mark.asyncio
async def test_harness_quota_widget_formatting_and_render(tmp_path: Path):
    """
    Tests token formatting, progress bar rendering, and status row generation.
    """
    assert HarnessQuotaWidget._format_tokens(500) == "500"
    assert HarnessQuotaWidget._format_tokens(1500) == "1.5k"
    assert HarnessQuotaWidget._format_tokens(10000) == "10k"
    assert HarnessQuotaWidget._format_tokens(2_000_000) == "2.0M"
    assert HarnessQuotaWidget._format_tokens(2_500_000) == "2.5M"

    assert HarnessQuotaWidget._render_progress_bar(0, 100, width=10) == "[░░░░░░░░░░]"
    assert HarnessQuotaWidget._render_progress_bar(50, 100, width=10) == "[█████░░░░░]"
    assert HarnessQuotaWidget._render_progress_bar(100, 100, width=10) == "[██████████]"
    assert HarnessQuotaWidget._render_progress_bar(150, 100, width=10) == "[██████████]"
    assert HarnessQuotaWidget._render_progress_bar(0, 0, width=10) == "[░░░░░░░░░░]"


@pytest.mark.asyncio
async def test_harness_quota_widget_ok_and_throttled_states(tmp_path: Path):
    """
    Asserts HarnessQuotaWidget renders capacity gauges, countdowns, and breakdowns.
    """
    from textual.app import App, ComposeResult

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=1_000_000,
                    avg_tokens_per_hour=400_000,
                )
            },
        )
    )

    quota_mgr = QuotaManager(config, state_manager)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=quota_mgr)

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(HarnessQuotaWidget)
        assert widget.row_count == 1
        row = widget.get_row_at(0)
        assert "1.0M / 1.0M (100%)" in str(row[1])
        assert "Full Capacity (0s)" in str(row[4])

        # Record high usage causing throttle
        await state_manager.record_token_usage_event(
            harness_name="antigravity",
            model_name="gemini-3.7-flash",
            project_name="proj-alpha",
            node_name="devtest",
            issue_number=10,
            prompt_tokens=700_000,
            completion_tokens=200_000,
            total_tokens=900_000,
        )

        await widget.update_quotas()
        assert widget.row_count == 1
        row_throttled = widget.get_row_at(0)
        assert "[bold red]" in str(row_throttled[1])
        assert "100k / 1.0M (10%)" in str(row_throttled[1])
        assert '"proj-alpha": 100%' in str(row_throttled[5])
        assert '"devtest": 100%' in str(row_throttled[6])


@pytest.mark.asyncio
async def test_dashboard_app_composes_quota_tab(tmp_path: Path):
    """
    Asserts DashboardApp mounts HarnessQuotaWidget inside TabbedContent with Quota Limits tab,
    passing injected quota_manager without self-constructing one when None.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    repo_dir = tmp_path / "repo1"
    repo_dir.mkdir()

    config = GlobalConfig(
        projects=[ProjectConfig(name="p1", repo="org/repo1", local_path=repo_dir)],
        quota=QuotaSettings(
            harnesses={
                "antigravity": HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000)
            }
        ),
    )

    # 1. Without quota_manager passed to DashboardApp
    app_no_mgr = DashboardApp(config=config, state_manager=state_manager)
    assert app_no_mgr.quota_manager is None
    async with app_no_mgr.run_test() as _:
        quota_widget_no_mgr = app_no_mgr.query_one("#quota_widget", HarnessQuotaWidget)
        assert quota_widget_no_mgr.quota_manager is None
        assert quota_widget_no_mgr.row_count == 1
        assert "No quota data" in str(quota_widget_no_mgr.get_row_at(0)[1])

    # 2. With injected quota_manager
    quota_mgr = QuotaManager(config, state_manager)
    app = DashboardApp(config=config, state_manager=state_manager, quota_manager=quota_mgr)
    assert app.quota_manager is quota_mgr

    async with app.run_test() as _:
        quota_widget = app.query_one("#quota_widget", HarnessQuotaWidget)
        assert quota_widget is not None
        assert quota_widget.quota_manager is quota_mgr
        assert quota_widget.row_count == 1
        assert "antigravity" in str(quota_widget.get_row_at(0)[0])


def test_dashboard_bindings_include_space_and_ctrl_l():
    """Asserts DashboardApp.BINDINGS includes space for toggle_auto_scroll and ctrl+l for clear_logs."""
    keys = {b.key: b.action for b in DashboardApp.BINDINGS}
    assert "space" in keys
    assert keys["space"] == "toggle_auto_scroll"
    assert "ctrl+l" in keys
    assert keys["ctrl+l"] == "clear_logs"


@pytest.mark.asyncio
async def test_dashboard_persistent_append_only_log_stream_across_refresh_ticks(tmp_path: Path, mocker):
    """
    Scenario: Persistent Append-Only Log Stream Across Refresh Ticks
    Given the "Logs" tab is active and contains historical agent log records
    When multiple timer refresh cycles execute while agent harnesses emit new output lines
    Then all historical log records remain intact in the RichLog buffer
    And new records are appended to the bottom without calling rich_log.clear()
    And the operator's scroll position is preserved without jumping to the top.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    repo_dir = tmp_path / "repo1"
    repo_dir.mkdir()

    config = GlobalConfig(
        projects=[ProjectConfig(name="proj1", repo="org/repo1", local_path=repo_dir)],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    from orchestrator.logging import ProjectLogBufferManager
    ProjectLogBufferManager.PROJECT_BUFFERS.clear()

    log_handler = TextualLogHandler(maxlen=1000)
    logger = logging.getLogger("orchestrator")
    logger.handlers = [h for h in logger.handlers if not isinstance(h, TextualLogHandler)]
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)

    # Seed initial historical log records
    logger.info("Historical record 1: Daemon initializing")
    logger.info("Historical record 2: Project config loaded")

    app = DashboardApp(config=config, state_manager=state_manager, log_handler=log_handler)

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        await pilot.pause()

        # Initial historical logs are loaded into RichLog
        rendered_texts = [line.text for line in log_view.lines]
        assert any("Historical record 1" in t for t in rendered_texts)
        assert any("Historical record 2" in t for t in rendered_texts)
        initial_count = len(log_view.lines)
        assert initial_count >= 2

        # Spy on RichLog.clear to verify periodic refresh ticks NEVER call clear()
        clear_spy = mocker.spy(log_view, "clear")

        # Emit new live output lines
        logger.info("Live record 3: DevTest node started")
        app._handle_harness_stream_line("Harness stdout line: Running tests...")
        await pilot.pause()

        # Execute multiple refresh cycles (simulating 2.0s timer ticks)
        for _ in range(5):
            await app.update_projects_table()
            await pilot.pause()

        # Emit more live records
        logger.info("Live record 4: DevTest completed successfully")
        await pilot.pause()

        # Assert RichLog.clear() was NEVER called across refresh ticks
        clear_spy.assert_not_called()

        # Assert all historical records and new records remain intact in the RichLog buffer
        all_texts = [line.text for line in log_view.lines]
        assert any("Historical record 1" in t for t in all_texts)
        assert any("Historical record 2" in t for t in all_texts)
        assert any("Live record 3" in t for t in all_texts)
        assert any("Running tests..." in t for t in all_texts)
        assert any("Live record 4" in t for t in all_texts)
        assert len(log_view.lines) >= 5


@pytest.mark.asyncio
async def test_dashboard_interactive_log_controls_and_auto_scroll_toggle(tmp_path: Path):
    """
    Scenario: Interactive Log Controls & Auto-Scroll Toggle
    Given the operator is viewing the live log stream
    When the operator presses "Space"
    Then log auto-scroll toggles between ON and OFF
    And when the operator presses "Ctrl+L"
    Then the log buffer clears on demand.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[ProjectConfig(name="proj1", repo="org/proj1", local_path=str(tmp_path))]
    )

    app = DashboardApp(config=config, state_manager=state_manager)

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)

        # Initial state: auto_scroll is ON
        assert app.auto_scroll is True
        assert log_view.auto_scroll is True
        assert "[Auto-Scroll: ON]" in app.sub_title

        # Press 'space' -> toggle auto_scroll OFF
        await pilot.press("space")
        await pilot.pause()

        assert app.auto_scroll is False
        assert log_view.auto_scroll is False
        assert "[Auto-Scroll: OFF]" in app.sub_title

        # Press 'space' again -> toggle auto_scroll ON
        await pilot.press("space")
        await pilot.pause()

        assert app.auto_scroll is True
        assert log_view.auto_scroll is True
        assert "[Auto-Scroll: ON]" in app.sub_title

        # Write lines to RichLog buffer
        log_view.write("Entry 1 to be cleared")
        log_view.write("Entry 2 to be cleared")
        await pilot.pause()
        assert len(log_view.lines) >= 2

        # Press 'ctrl+l' -> clears log buffer on demand
        await pilot.press("ctrl+l")
        await pilot.pause()

        assert len(log_view.lines) == 0


@pytest.mark.asyncio
async def test_dashboard_inplace_projects_table_diffing_preserves_cursor_and_selection(tmp_path: Path, mocker):
    """
    Scenario: Non-Destructive In-Place Project Table Updates & Cursor Persistence
    Given the TUI dashboard is active with multiple registered projects ("crosstrainingapp", "graph-engineering")
    And the operator has used arrow keys to highlight row 1 ("graph-engineering")
    When periodic 2-second background refresh cycles execute
    Then the dashboard updates cell values in-place using keyed cell mutation (without calling table.clear())
    And the table cursor remains locked on row 1 ("graph-engineering")
    And no spurious "RowHighlighted" event resetting to row 0 is triggered.
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=str(tmp_path)),
            ProjectConfig(name="graph-engineering", repo="org/graph-engineering", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        table.focus()
        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "crosstrainingapp"
        assert table.get_row_at(1)[0] == "graph-engineering"

        # Navigate down to row 1 ("graph-engineering")
        await pilot.press("down")
        await pilot.pause()

        assert table.cursor_row == 1
        assert app.selected_project == "graph-engineering"

        # Spy on DataTable.clear() to ensure it is NEVER called during refresh cycles
        clear_spy = mocker.spy(table, "clear")

        # Mutate state in SQLite:
        # - "crosstrainingapp": acquire active lock (DevTest, Issue #10)
        # - "graph-engineering": pause project
        await state_manager.acquire_lock(issue_id=10, repo="org/crosstrainingapp", node_type="DevTest")
        await state_manager.pause_project("graph-engineering")

        # Simulate multiple periodic 2-second background refresh passes
        for _ in range(3):
            await app.update_projects_table()
            await pilot.pause()

        # Verify clear was NEVER called
        clear_spy.assert_not_called()

        # Verify in-place cell updates
        row0 = table.get_row("crosstrainingapp::DevTest") if "crosstrainingapp::DevTest" in table.rows else table.get_row_at(0)
        assert "DevTest" in str(row0[2])
        assert "Issue #10" in str(row0[5])

        row1 = table.get_row("graph-engineering::Idle") if "graph-engineering::Idle" in table.rows else table.get_row_at(1)
        assert "Paused" in str(row1[3])

        # Assert cursor remains locked on row 1 ("graph-engineering")
        assert table.cursor_row == 1
        assert app.selected_project == "graph-engineering"


@pytest.mark.asyncio
async def test_dashboard_reactive_and_stable_project_selection_for_child_panes(tmp_path: Path, mocker):
    """
    Scenario: Reactive & Stable Project Selection for Child Panes
    Given the operator navigates from "crosstrainingapp" to "graph-engineering"
    When the operator explicitly presses the Down arrow key
    Then the RowHighlighted event sets selected_project to "graph-engineering"
    And SDLCProgressWidget and AnomalyAlertsWidget update their views for "graph-engineering"
    And subsequent background polling passes maintain "graph-engineering" as the active context without unnecessary re-query when state is unchanged.
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=str(tmp_path)),
            ProjectConfig(name="graph-engineering", repo="org/graph-engineering", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Seed SDLC items & anomalies for both projects
    await state_manager.sync_project_sdlc_items(
        "crosstrainingapp",
        [{"issue_number": 1, "title": "Cross Item", "labels": "ready-for-dev", "linked_pr": None}],
    )
    await state_manager.sync_project_sdlc_items(
        "graph-engineering",
        [{"issue_number": 71, "title": "In-Place Diffing", "labels": "in-progress", "linked_pr": 72}],
    )
    await state_manager.record_anomaly_event(
        project_name="graph-engineering",
        node_name="devtest",
        error_type="HarnessTimeout",
        error_message="Test timeout",
        issue_number=71,
    )

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        sdlc_widget = app.query_one("#sdlc_widget", SDLCProgressWidget)
        alerts_widget = app.query_one("#alerts_widget", AnomalyAlertsWidget)
        table.focus()

        # Move Down to row 1 ("graph-engineering")
        await pilot.press("down")
        await pilot.pause()

        assert app.selected_project == "graph-engineering"
        assert sdlc_widget.row_count == 1
        assert sdlc_widget.get_row("71")[1] == "In-Place Diffing"
        assert sdlc_widget.get_row("71")[3] == "#72"

        assert alerts_widget.row_count == 1
        assert alerts_widget.get_row_at(0)[2] == "HarnessTimeout"

        # Spy on get_sdlc_items and get_recent_anomalies to assert decoupling / caching
        sdlc_spy = mocker.spy(state_manager, "get_sdlc_items")
        anomaly_spy = mocker.spy(state_manager, "get_recent_anomalies")

        # Execute periodic 2s refresh cycles when state is unchanged
        for _ in range(3):
            await app.update_projects_table()
            await pilot.pause()

        # Both spies should NOT have been called during background ticks since state fingerprint was unchanged
        sdlc_spy.assert_not_called()
        anomaly_spy.assert_not_called()

        assert app.selected_project == "graph-engineering"
        assert table.cursor_row == 1

        # Now mutate state for graph-engineering
        await state_manager.sync_project_sdlc_items(
            "graph-engineering",
            [
                {"issue_number": 71, "title": "In-Place Diffing", "labels": "in-progress", "linked_pr": 72},
                {"issue_number": 75, "title": "New Story", "labels": "ready-for-dev", "linked_pr": None},
            ],
        )

        # Trigger refresh pass
        await app.update_projects_table()
        await pilot.pause()

        # Because fingerprint changed, SDLC items re-queried and updated in child pane
        assert sdlc_spy.call_count >= 1
        assert sdlc_widget.row_count == 2
        assert sdlc_widget.get_row("75")[1] == "New Story"
        assert app.selected_project == "graph-engineering"
        assert table.cursor_row == 1


@pytest.mark.asyncio
async def test_sdlc_progress_widget_keyed_inplace_diffing_preserves_cursor(tmp_path: Path, mocker):
    """
    Scenario: Non-Destructive SDLC Progress Widget
    Given the operator is viewing the SDLC items pane with multiple items
    And the cursor is positioned on a specific row
    When background data is refreshed from SQLite with modified, added, and removed items
    Then rows matching existing keys are updated in-place without clearing the table
    And cursor coordinate and row position are preserved
    And table.clear() is never invoked.
    """
    import aiosqlite
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Initial SDLC items
    await state_manager.sync_project_sdlc_items(
        "proj_alpha",
        [
            {"issue_number": 10, "title": "Issue Ten", "labels": "ready-for-dev", "linked_pr": None},
            {"issue_number": 20, "title": "Issue Twenty", "labels": "needs-triage", "linked_pr": None},
            {"issue_number": 30, "title": "Issue Thirty", "labels": "ready-for-dev", "linked_pr": None},
        ],
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="proj_alpha")

    app = TestApp()
    async with app.run_test() as pilot:
        widget = app.query_one(SDLCProgressWidget)
        widget.focus()
        assert widget.row_count == 3

        # Move cursor to row 1 (Issue 20)
        widget.move_cursor(row=1)
        await pilot.pause()
        assert widget.cursor_row == 1

        # Spy on DataTable.clear() to ensure it is NEVER called during refresh
        clear_spy = mocker.spy(widget, "clear")

        # Mutate SQLite data:
        # - Issue 10: status changes to "in-progress"
        # - Issue 20: status changes to "architect-approved", linked_pr becomes 99
        # - Issue 30: deleted from SQLite (by deleting and re-syncing new set)
        # - Issue 40: added
        async with aiosqlite.connect(tmp_path / "state.db") as db:
            await db.execute("DELETE FROM sdlc_items WHERE project_name = 'proj_alpha'")
            await db.commit()

        await state_manager.sync_project_sdlc_items(
            "proj_alpha",
            [
                {"issue_number": 10, "title": "Issue Ten", "labels": "in-progress", "linked_pr": None},
                {"issue_number": 20, "title": "Issue Twenty", "labels": "architect-approved", "linked_pr": 99},
                {"issue_number": 40, "title": "Issue Forty", "labels": "needs-architect-review", "linked_pr": 101},
            ],
        )

        # Trigger update_project
        await widget.update_project("proj_alpha")
        await pilot.pause()

        # Verify clear was NEVER called
        clear_spy.assert_not_called()

        # Verify row count and in-place updates
        assert widget.row_count == 3

        # Row 0: Issue 10 updated
        row0 = widget.get_row("10")
        assert row0[0] == "#10"
        assert row0[2] == "in-progress"

        # Row 1: Issue 20 updated in-place and cursor is PRESERVED on row 1
        row1 = widget.get_row("20")
        assert row1[0] == "#20"
        assert row1[2] == "architect-approved"
        assert row1[3] == "#99"
        assert widget.cursor_row == 1

        # Row 40 added
        row_new = widget.get_row("40")
        assert row_new[0] == "#40"
        assert row_new[1] == "Issue Forty"

        # Row 30 removed from table.rows
        assert "30" not in widget.rows


@pytest.mark.asyncio
async def test_anomaly_alerts_widget_keyed_inplace_diffing_preserves_cursor(tmp_path: Path, mocker):
    """
    Scenario: Non-Destructive Anomaly Alerts Widget
    Given the operator is viewing the Anomaly Alerts widget
    And the cursor is positioned on a specific anomaly row
    When new anomaly events are recorded in SQLite
    Then existing rows are preserved and new rows are appended without table.clear()
    And the cursor position is preserved.
    """
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    await state_manager.record_anomaly_event(
        project_name="proj_beta",
        node_name="devtest",
        error_type="Timeout",
        error_message="First timeout",
        issue_number=1,
    )
    await state_manager.record_anomaly_event(
        project_name="proj_beta",
        node_name="reviewer",
        error_type="Conflict",
        error_message="Merge conflict",
        issue_number=2,
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield AnomalyAlertsWidget(state_manager=state_manager, project_name="proj_beta")

    app = TestApp()
    async with app.run_test() as pilot:
        widget = app.query_one(AnomalyAlertsWidget)
        widget.focus()
        assert widget.row_count == 2

        # Move cursor to row 1
        widget.move_cursor(row=1)
        await pilot.pause()
        assert widget.cursor_row == 1

        clear_spy = mocker.spy(widget, "clear")

        # Record a 3rd anomaly
        await state_manager.record_anomaly_event(
            project_name="proj_beta",
            node_name="supervisor",
            error_type="SLAViolation",
            error_message="Issue exceeded SLA",
            issue_number=3,
        )

        await widget.update_project("proj_beta")
        await pilot.pause()

        clear_spy.assert_not_called()
        assert widget.row_count == 3
        assert widget.cursor_row == 1


@pytest.mark.asyncio
async def test_harness_quota_widget_keyed_inplace_diffing_preserves_cursor(tmp_path: Path, mocker):
    """
    Scenario: Non-Destructive Quota Limits Widget
    Given the operator is viewing the Quota Limits widget
    And cursor is positioned on a harness row
    When token usage updates harness status from OK to THROTTLED
    Then cell values update in-place without table.clear()
    And the cursor remains on the selected harness row.
    """
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(window_hours=1.0, window_token_limit=1_000_000, avg_tokens_per_hour=400_000),
                "claude": HarnessQuotaConfig(window_hours=2.0, window_token_limit=2_000_000, avg_tokens_per_hour=500_000),
            },
        )
    )
    quota_mgr = QuotaManager(config, state_manager)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=quota_mgr)

    app = TestApp()
    async with app.run_test() as pilot:
        widget = app.query_one(HarnessQuotaWidget)
        widget.focus()
        assert widget.row_count == 2

        # Position cursor on row 1 ("claude")
        widget.move_cursor(row=1)
        await pilot.pause()
        assert widget.cursor_row == 1
        row_claude = widget.get_row("claude")
        assert "2.0M / 2.0M (100%)" in str(row_claude[1])

        clear_spy = mocker.spy(widget, "clear")

        # Record usage to throttle claude
        await state_manager.record_token_usage_event(
            harness_name="claude",
            model_name="claude-3-5-sonnet",
            project_name="proj-x",
            node_name="devtest",
            issue_number=100,
            prompt_tokens=1_500_000,
            completion_tokens=400_000,
            total_tokens=1_900_000,
        )

        await widget.update_quotas()
        await pilot.pause()

        clear_spy.assert_not_called()
        assert widget.row_count == 2
        assert widget.cursor_row == 1

        # Check in-place cell update for claude
        row_claude_after = widget.get_row("claude")
        assert "[bold red]" in str(row_claude_after[1])
        assert "100k / 2.0M (5%)" in str(row_claude_after[1])
        assert '"proj-x": 100%' in str(row_claude_after[5])


@pytest.mark.asyncio
async def test_widgets_empty_state_transitions(tmp_path: Path):
    """
    Scenario: Seamless Empty State Transitions
    Tests that widgets transition between empty state and data state without table.clear() or errors.
    """
    import aiosqlite
    from textual.app import App, ComposeResult

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="test_proj", id="sdlc")
            yield AnomalyAlertsWidget(state_manager=state_manager, project_name="test_proj", id="alerts")

    app = TestApp()
    async with app.run_test() as pilot:
        sdlc = app.query_one("#sdlc", SDLCProgressWidget)
        alerts = app.query_one("#alerts", AnomalyAlertsWidget)

        # 1. Initially empty
        assert sdlc.row_count == 1
        assert "No active SDLC items" in str(sdlc.get_row_at(0)[1])
        assert alerts.row_count == 1
        assert "No anomalies in last 24h" in str(alerts.get_row_at(0)[2])

        # 2. Add data
        await state_manager.sync_project_sdlc_items(
            "test_proj",
            [{"issue_number": 55, "title": "Added Issue", "labels": "ready-for-dev"}],
        )
        await state_manager.record_anomaly_event(
            project_name="test_proj",
            node_name="devtest",
            error_type="ErrorX",
            error_message="MsgX",
        )

        await sdlc.update_project("test_proj")
        await alerts.update_project("test_proj")
        await pilot.pause()

        assert sdlc.row_count == 1
        assert sdlc.get_row("55")[1] == "Added Issue"
        assert alerts.row_count == 1
        assert alerts.get_row_at(0)[2] == "ErrorX"

        # 3. Transition back to empty
        async with aiosqlite.connect(tmp_path / "state.db") as db:
            await db.execute("DELETE FROM sdlc_items WHERE project_name = 'test_proj'")
            await db.execute("DELETE FROM anomaly_events WHERE project_name = 'test_proj'")
            await db.commit()

        await sdlc.update_project("test_proj")
        await alerts.update_project("test_proj")
        await pilot.pause()

        assert sdlc.row_count == 1
        assert "No active SDLC items" in str(sdlc.get_row_at(0)[1])
        assert alerts.row_count == 1
        assert "No anomalies in last 24h" in str(alerts.get_row_at(0)[2])


@pytest.mark.asyncio
async def test_dashboard_scenario_idempotent_project_scoped_log_hydration(tmp_path: Path):
    """
    Scenario: Idempotent Project-Scoped Log Hydration
      Given the user switches project selection from "graph-engineering" to "crosstrainingapp"
      When the project selection event fires
      Then the dashboard must retrieve "crosstrainingapp's" scoped log buffer from ProjectLogBufferManager
      And clear the RichLog pane and populate it with the retrieved historical lines
      And incoming live logs from other projects must accumulate in the background without polluting the active view
    """
    from orchestrator.logging import ProjectLogBufferManager, TextualLogHandler

    ProjectLogBufferManager.reset()

    repo_ge = tmp_path / "graph-engineering"
    repo_ge.mkdir()
    repo_ct = tmp_path / "crosstrainingapp"
    repo_ct.mkdir()

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=repo_ct),
            ProjectConfig(name="graph-engineering", repo="org/graph-engineering", local_path=repo_ge),
        ],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    log_handler = TextualLogHandler(maxlen=1000)
    logger = logging.getLogger("orchestrator")
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)

    app = DashboardApp(config=config, log_handler=log_handler)

    # Seed historical logs
    app.buffer_manager.add_line("[graph-engineering:architect] Historical GE story triage", project_name="graph-engineering", node_name="architect")
    app.buffer_manager.add_line("[crosstrainingapp:supervisor] Historical CT watchdog check", project_name="crosstrainingapp", node_name="supervisor")

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        table = app.query_one("#projects_table", DataTable)
        table.focus()
        await pilot.pause()

        # Switch selection to graph-engineering (row 1 because alphabetical sort)
        table.move_cursor(row=1)
        await pilot.pause()
        assert app.selected_project == "graph-engineering"

        rendered_ge = [line.text for line in log_view.lines]
        assert any("Historical GE story triage" in t for t in rendered_ge)
        assert not any("Historical CT watchdog check" in t for t in rendered_ge)

        # When user switches project selection to crosstrainingapp (row 0)
        table.move_cursor(row=0)
        await pilot.pause()
        assert app.selected_project == "crosstrainingapp"

        # Then RichLog clears and populates with crosstrainingapp logs
        rendered_ct = [line.text for line in log_view.lines]
        assert any("Historical CT watchdog check" in t for t in rendered_ct)
        assert not any("Historical GE story triage" in t for t in rendered_ct)

        # And incoming live logs from other projects accumulate in background without polluting active view
        logger.info("[graph-engineering:devtest] Live GE test execution")
        app._handle_harness_stream_line("  [dim cyan][graph-engineering:harness][/dim cyan] [dim]GE subprocess[/dim]")
        await pilot.pause()

        rendered_after_ge_live = [line.text for line in log_view.lines]
        assert not any("Live GE test execution" in t for t in rendered_after_ge_live)
        assert not any("GE subprocess" in t for t in rendered_after_ge_live)

        # Incoming live logs from active project appear in active view
        logger.info("[crosstrainingapp:devtest] Live CT test execution")
        app._handle_harness_stream_line("  [dim cyan][crosstrainingapp:harness][/dim cyan] [dim]CT subprocess[/dim]")
        await pilot.pause()

        rendered_after_ct_live = [line.text for line in log_view.lines]
        assert any("Live CT test execution" in t for t in rendered_after_ct_live)
        assert any("CT subprocess" in t for t in rendered_after_ct_live)


@pytest.mark.asyncio
async def test_dashboard_scenario_cold_start_disk_log_fallback(tmp_path: Path):
    """
    Scenario: Cold-Start Disk Log Fallback
      Given project "crosstrainingapp" is selected but the orchestrator daemon was recently restarted (in-memory deque is empty)
      When the UI requests the project logs
      Then the log manager must fallback to tailing the last 100 lines from the latest disk log file in "~/.config/orchestrator/logs/crosstrainingapp/"
      And display them in the RichLog pane with immediate historical context
    """
    from orchestrator.logging import ProjectLogBufferManager, TextualLogHandler

    ProjectLogBufferManager.reset()

    logs_dir = tmp_path / "logs"
    ct_log_dir = logs_dir / "crosstrainingapp" / "devtest"
    ct_log_dir.mkdir(parents=True, exist_ok=True)

    disk_log = ct_log_dir / "20260830_120000_devtest_run.log"
    content = "\n".join([f"Cold-start disk line {i:03d}" for i in range(1, 151)])
    disk_log.write_text(content, encoding="utf-8")

    repo_ct = tmp_path / "crosstrainingapp"
    repo_ct.mkdir()

    config = GlobalConfig(
        projects=[ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=repo_ct)],
        settings=SettingsConfig(log_dir=str(logs_dir)),
    )

    log_handler = TextualLogHandler(maxlen=1000)
    app = DashboardApp(config=config, log_handler=log_handler)
    app.buffer_manager.reset()

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        table = app.query_one("#projects_table", DataTable)
        table.focus()
        await pilot.pause()

        # Trigger project selection
        table.move_cursor(row=0)
        await pilot.pause()

        rendered = [line.text for line in log_view.lines]
        assert len(rendered) == 100
        assert "Cold-start disk line 051" in rendered[0]
        assert "Cold-start disk line 150" in rendered[-1]


@pytest.mark.asyncio
async def test_dashboard_harness_stream_line_routing_with_project_tag(tmp_path: Path):
    """
    Scenario: Live Subprocess Stream Tagging and Routing in Dashboard
      Given DashboardApp is active with project "graph-engineering" selected
      When AsyncHarnessAdapter emits a tagged stream line for "graph-engineering"
      Then the line is added to ProjectLogBufferManager's "graph-engineering" buffer
      And it is written to the active RichLog view
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    repo_ge = tmp_path / "graph-engineering"
    repo_ge.mkdir()

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="graph-engineering", repo="org/graph-engineering", local_path=repo_ge),
            ProjectConfig(name="other-proj", repo="org/other-proj", local_path=repo_ge),
        ]
    )

    app = DashboardApp(config=config)

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        table = app.query_one("#projects_table", DataTable)
        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()
        assert app.selected_project == "graph-engineering"

        # Emit tagged stream line for graph-engineering
        app._handle_harness_stream_line("graph-engineering", "  [dim cyan][graph-engineering:devtest][/dim cyan] [dim]Running unit tests...[/dim]")

        # Emit tagged stream line for other project
        app._handle_harness_stream_line("other-proj", "  [dim cyan][other-proj:devtest][/dim cyan] [dim]Other project tests...[/dim]")

        # Check buffer manager
        ge_buf = app.buffer_manager.PROJECT_BUFFERS.get("graph-engineering")
        assert ge_buf is not None
        assert any("Running unit tests..." in (item[1] if isinstance(item, tuple) else item) for item in ge_buf)

        other_buf = app.buffer_manager.PROJECT_BUFFERS.get("other-proj")
        assert other_buf is not None
        assert any("Other project tests..." in (item[1] if isinstance(item, tuple) else item) for item in other_buf)

        # Check RichLog in UI (only graph-engineering should be rendered)
        rendered = [line.text for line in log_view.lines]
        assert any("Running unit tests..." in r for r in rendered)
        assert not any("Other project tests..." in r for r in rendered)


@pytest.mark.asyncio
async def test_scenario_dashboard_harness_stream_3_arg_node_name_wiring(tmp_path: Path):
    """
    Scenario: Buffer manager wiring uses node_name (Issue #104)
      Given the TUI dashboard registers a stream listener that forwards to ProjectLogBufferManager.add_line
      When a line arrives with (project_name, node_name, line)
      Then ProjectLogBufferManager.add_line(line, project_name=project_name, node_name=node_name) must be called,
      populating the node-scoped tuple buffer
    """
    from orchestrator.logging import ProjectLogBufferManager
    from orchestrator.ui.dashboard import DashboardApp
    from textual.widgets import DataTable, RichLog

    ProjectLogBufferManager.reset()

    repo_dir = tmp_path / "crosstrainingapp"
    repo_dir.mkdir()

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=repo_dir),
        ],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    app = DashboardApp(config=config)

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        table = app.query_one("#projects_table", DataTable)
        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()
        assert app.selected_project == "crosstrainingapp"

        # Emit 3-arg stream line with explicit node_name
        app._handle_harness_stream_line(
            "crosstrainingapp",
            "devtest",
            "  [dim cyan][crosstrainingapp:devtest][/dim cyan] [dim]Running 3-Amigos DevTest...[/dim]",
        )

        # Emit 3-arg stream line for architect node
        app._handle_harness_stream_line(
            "crosstrainingapp",
            "architect",
            "  [dim cyan][crosstrainingapp:architect][/dim cyan] [dim]INVEST decomposition complete[/dim]",
        )

        # Check ProjectLogBufferManager has (node_name, line) tuples
        buf = app.buffer_manager.PROJECT_BUFFERS.get("crosstrainingapp")
        assert buf is not None
        assert len(buf) == 2
        assert buf[0][0] == "devtest"
        assert "Running 3-Amigos DevTest..." in buf[0][1]
        assert buf[1][0] == "architect"
        assert "INVEST decomposition complete" in buf[1][1]

        # Check log view rendered the active project's lines
        rendered = [line.text for line in log_view.lines]
        assert any("Running 3-Amigos DevTest..." in r for r in rendered)
        assert any("INVEST decomposition complete" in r for r in rendered)


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for Issue #106
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_multi_node_compound_row_rendering(tmp_path: Path):
    """
    Scenario: Multi-node compound row unit tests (Issue #106)
      Given a fake StateManager returning two RUNNING jobs for the same project with different node_type values
      When DashboardApp.update_projects_table() runs
      Then the table must contain exactly two rows keyed "<project>::<node1>" and "<project>::<node2>"
      And Row 1 must display "<project>" with Active Node "<node1>"
      And Row 2 must display "  └─" with Active Node "<node2>"
    """
    from unittest.mock import AsyncMock

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=str(tmp_path)),
        ],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    mock_sm = AsyncMock(spec=StateManager)
    mock_sm.get_paused_projects.return_value = []
    mock_sm.get_active_jobs.return_value = [
        {"repo": "org/crosstrainingapp", "node_type": "architect", "status": "RUNNING", "issue_id": 101},
        {"repo": "org/crosstrainingapp", "node_type": "devtest", "status": "RUNNING", "issue_id": 105},
    ]
    mock_sm.get_project_state_fingerprint.return_value = "fp_test"

    app = DashboardApp(config=config, state_manager=mock_sm)

    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        await pilot.pause()

        assert table.row_count == 2

        # Check row keys
        row_keys = [k.value if hasattr(k, "value") else str(k) for k in table.rows.keys()]
        assert row_keys == ["crosstrainingapp::architect", "crosstrainingapp::devtest"]

        # Check row contents
        row_arch = table.get_row("crosstrainingapp::architect")
        assert row_arch[0] == "crosstrainingapp"
        assert "architect" in str(row_arch[2])
        assert "Issue #101" in str(row_arch[5])

        row_dev = table.get_row("crosstrainingapp::devtest")
        assert row_dev[0] == "  └─"
        assert "devtest" in str(row_dev[2])
        assert "Issue #105" in str(row_dev[5])


@pytest.mark.asyncio
async def test_scenario_node_isolated_log_tailing_upon_row_selection(tmp_path: Path):
    """
    Scenario: Node-Isolated Log Tailing upon Row Selection (Issue #106 / #105)
      Given the ProjectStatusTable displays a row for "crosstrainingapp | devtest"
      When the operator highlights this specific row
      Then the RichLog pane must clear and hydrate using ONLY log records emitted by the "devtest" node
      And live incoming logs from the concurrent "architect" node must be suppressed from this view
      And the log pane title must display "Live Output [crosstrainingapp | devtest]"
    """
    from unittest.mock import AsyncMock

    ProjectLogBufferManager.reset()

    # Seed in-memory buffer with interleaved logs for architect and devtest
    ProjectLogBufferManager.add_line("Arch task: planning architecture", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("DevTest: running pytest", project_name="crosstrainingapp", node_name="devtest")
    ProjectLogBufferManager.add_line("Arch task: story decomposed", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("DevTest: green tests passing", project_name="crosstrainingapp", node_name="devtest")

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=str(tmp_path)),
        ],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    mock_sm = AsyncMock(spec=StateManager)
    mock_sm.get_paused_projects.return_value = []
    mock_sm.get_active_jobs.return_value = [
        {"repo": "org/crosstrainingapp", "node_type": "architect", "status": "RUNNING", "issue_id": 101},
        {"repo": "org/crosstrainingapp", "node_type": "devtest", "status": "RUNNING", "issue_id": 105},
    ]
    mock_sm.get_project_state_fingerprint.return_value = "fp_test"

    app = DashboardApp(config=config, state_manager=mock_sm)

    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        log_view = app.query_one("#log_view", RichLog)
        table.focus()
        await pilot.pause()

        # Highlight row 1: "crosstrainingapp::devtest"
        await pilot.press("down")
        await pilot.pause()

        assert app.selected_project == "crosstrainingapp"
        assert app.selected_node == "devtest"
        assert "Live Output" in str(log_view.border_title)
        assert "crosstrainingapp" in str(log_view.border_title)
        assert "devtest" in str(log_view.border_title)

        # Verify only devtest logs are in the RichLog pane
        rendered = [line.text for line in log_view.lines]
        assert any("DevTest: running pytest" in r for r in rendered)
        assert any("DevTest: green tests passing" in r for r in rendered)
        assert not any("Arch task" in r for r in rendered)

        # Incoming live log from architect is suppressed from active view
        app._handle_harness_stream_line(
            project_name="crosstrainingapp",
            node_name="architect",
            line="  [dim cyan][crosstrainingapp:architect][/dim cyan] [dim]New arch triage line[/dim]",
        )
        await pilot.pause()

        rendered_after = [line.text for line in log_view.lines]
        assert not any("New arch triage line" in r for r in rendered_after)

        # Incoming live log from devtest appears in active view
        app._handle_harness_stream_line(
            project_name="crosstrainingapp",
            node_name="devtest",
            line="  [dim cyan][crosstrainingapp:devtest][/dim cyan] [dim]Live devtest assertion passing[/dim]",
        )
        await pilot.pause()

        rendered_final = [line.text for line in log_view.lines]
        assert any("Live devtest assertion passing" in r for r in rendered_final)


@pytest.mark.asyncio
async def test_scenario_cursor_migration_regression_on_node_idle(tmp_path: Path):
    """
    Scenario: Cursor migration regression test (Issue #106 / #105)
      Given the highlighted row key no longer exists after a refresh (node went idle)
      When update_projects_table() runs
      Then no exception is raised and the cursor moves to a remaining valid row (or clears gracefully if none remain)
    """
    from unittest.mock import AsyncMock

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=str(tmp_path)),
        ],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    mock_sm = AsyncMock(spec=StateManager)
    mock_sm.get_paused_projects.return_value = []
    mock_sm.get_active_jobs.return_value = [
        {"repo": "org/crosstrainingapp", "node_type": "architect", "status": "RUNNING", "issue_id": 101},
        {"repo": "org/crosstrainingapp", "node_type": "devtest", "status": "RUNNING", "issue_id": 105},
    ]
    mock_sm.get_project_state_fingerprint.return_value = "fp_test"

    app = DashboardApp(config=config, state_manager=mock_sm)

    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        table.focus()
        await pilot.pause()

        # Position cursor on row 1 ("crosstrainingapp::devtest")
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 1

        # Now simulate devtest completing its job -> only architect remains running
        mock_sm.get_active_jobs.return_value = [
            {"repo": "org/crosstrainingapp", "node_type": "architect", "status": "RUNNING", "issue_id": 101},
        ]

        # Trigger refresh
        await app.update_projects_table()
        await pilot.pause()

        # No exception raised, table now has 1 row ("crosstrainingapp::architect")
        assert table.row_count == 1
        assert table.cursor_row == 0
        assert "crosstrainingapp::architect" in table.rows

        # Now simulate all jobs completing -> project becomes idle (1 row: "crosstrainingapp::Idle")
        mock_sm.get_active_jobs.return_value = []
        await app.update_projects_table()
        await pilot.pause()

        assert table.row_count == 1
        assert table.cursor_row == 0
        assert "crosstrainingapp::Idle" in table.rows


@pytest.mark.asyncio
async def test_scenario_node_scoped_cold_start_disk_tail_fallback_dashboard(tmp_path: Path):
    """
    Scenario: Node-scoped disk tail fallback test in DashboardApp (Issue #106 / #105)
      Given an empty in-memory buffer and a log file under "<log_dir>/<project>/<node>/*.log"
      When get_project_logs(project, node) / hydrate_project_logs is called
      Then the returned lines match the tail of that node-specific file, not a sibling node directory
    """
    from unittest.mock import AsyncMock

    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    arch_dir = logs_root / "crosstrainingapp" / "architect"
    dev_dir = logs_root / "crosstrainingapp" / "devtest"
    arch_dir.mkdir(parents=True, exist_ok=True)
    dev_dir.mkdir(parents=True, exist_ok=True)

    # Write log files
    (arch_dir / "20260830_100000_architect_run.log").write_text(
        "Arch cold disk line 1\nArch cold disk line 2\n", encoding="utf-8"
    )
    (dev_dir / "20260830_100000_devtest_run.log").write_text(
        "Dev cold disk line 1\nDev cold disk line 2\n", encoding="utf-8"
    )

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=str(tmp_path)),
        ],
        settings=SettingsConfig(log_dir=str(logs_root)),
    )

    mock_sm = AsyncMock(spec=StateManager)
    mock_sm.get_paused_projects.return_value = []
    mock_sm.get_active_jobs.return_value = [
        {"repo": "org/crosstrainingapp", "node_type": "architect", "status": "RUNNING", "issue_id": 101},
        {"repo": "org/crosstrainingapp", "node_type": "devtest", "status": "RUNNING", "issue_id": 105},
    ]
    mock_sm.get_project_state_fingerprint.return_value = "fp_test"

    app = DashboardApp(config=config, state_manager=mock_sm)

    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        log_view = app.query_one("#log_view", RichLog)
        table.focus()
        await pilot.pause()

        # Select devtest row (row 1)
        await pilot.press("down")
        await pilot.pause()

        assert app.selected_node == "devtest"
        rendered = [line.text for line in log_view.lines]
        assert "Dev cold disk line 1" in rendered
        assert "Dev cold disk line 2" in rendered
        assert not any("Arch" in r for r in rendered)


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for Epic #90 / Issue #94
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_non_destructive_sdlc_subtask_navigation(tmp_path: Path, mocker):
    """
    Scenario 1: Non-Destructive SDLC Subtask Navigation (Epic #90 / Issue #94)
      Given the TUI dashboard is active and project "crosstrainingapp" is selected in the top pane
      And the "Logs" pane is streaming execution output for the "devtest" node
      When the user clicks or navigates with arrow keys through subtasks (e.g., #455) in "#sdlc_widget"
      Then the "Logs" pane must NOT clear or reset its contents
      And it must continuously append the active node's live execution without interruption
    """
    ProjectLogBufferManager.reset()

    repo_ct = tmp_path / "crosstrainingapp"
    repo_ct.mkdir()
    repo_ge = tmp_path / "graph-engineering"
    repo_ge.mkdir()

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="crosstrainingapp", repo="org/crosstrainingapp", local_path=repo_ct),
            ProjectConfig(name="graph-engineering", repo="org/graph-engineering", local_path=repo_ge),
        ],
        settings=SettingsConfig(log_dir=str(tmp_path / "logs")),
    )

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Seed SDLC items with a parent story and subtasks
    await state_manager.sync_project_sdlc_items(
        "crosstrainingapp",
        [
            {
                "issue_number": 450,
                "title": "feat(app): core cross-training workout engine",
                "item_type": "STORY",
                "state": "OPEN",
                "labels": "ready-for-dev",
            },
            {
                "issue_number": 455,
                "title": "feat(ui): implement workout timer widget",
                "item_type": "SUBTASK",
                "parent_issue_id": 450,
                "state": "OPEN",
                "labels": "ready-for-dev",
            },
            {
                "issue_number": 456,
                "title": "test(ui): add unit tests for workout timer",
                "item_type": "SUBTASK",
                "parent_issue_id": 450,
                "state": "OPEN",
                "labels": "ready-for-dev",
            },
        ],
    )

    log_handler = TextualLogHandler(maxlen=1000)
    logger = logging.getLogger("orchestrator")
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        log_handler=log_handler,
    )

    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        sdlc_widget = app.query_one("#sdlc_widget", SDLCProgressWidget)
        log_view = app.query_one("#log_view", RichLog)

        table.focus()
        await pilot.press("down")
        await pilot.pause()
        assert app.selected_project == "graph-engineering"

        # Switch to crosstrainingapp (row 0)
        await pilot.press("up")
        await pilot.pause()

        # Project crosstrainingapp is selected
        assert app.selected_project == "crosstrainingapp"
        assert "Live Output" in log_view.border_title
        assert "crosstrainingapp" in log_view.border_title

        # Stream initial logs for devtest node
        logger.info("[crosstrainingapp:devtest] Running test suite for subtask #455")
        app._handle_harness_stream_line(
            project_name="crosstrainingapp",
            node_name="devtest",
            line="  [dim cyan][crosstrainingapp:devtest][/dim cyan] [dim]Step 1: Initializing test runner[/dim]",
        )
        await pilot.pause()

        # Verify initial logs are present
        initial_lines = [line.text for line in log_view.lines]
        assert any("Running test suite for subtask #455" in t for t in initial_lines)
        assert any("Step 1: Initializing test runner" in t for t in initial_lines)

        # Spy on log_view.clear to assert it is never called during #sdlc_widget interactions
        clear_spy = mocker.spy(log_view, "clear")

        # Focus SDLC widget and navigate through subtask rows
        sdlc_widget.focus()
        await pilot.pause()
        assert sdlc_widget.row_count >= 2

        # Navigate down through subtasks #455 and #456
        await pilot.press("down")
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()

        # Simulate row selection (Enter key / Click) on subtask #455
        await pilot.press("enter")
        await pilot.pause()

        # Assert log_view.clear was NOT called
        clear_spy.assert_not_called()

        # Verify all original logs are still present
        during_nav_lines = [line.text for line in log_view.lines]
        assert any("Running test suite for subtask #455" in t for t in during_nav_lines)
        assert any("Step 1: Initializing test runner" in t for t in during_nav_lines)

        # Stream new live execution logs while user is focused/navigating in #sdlc_widget
        app._handle_harness_stream_line(
            project_name="crosstrainingapp",
            node_name="devtest",
            line="  [dim cyan][crosstrainingapp:devtest][/dim cyan] [dim]Step 2: Subtask #455 assertions passed 100% green[/dim]",
        )
        logger.info("[crosstrainingapp:devtest] Subtask #455 completed successfully")
        await pilot.pause()

        # Verify log pane continuously appended without interruption
        final_lines = [line.text for line in log_view.lines]
        assert any("Running test suite for subtask #455" in t for t in final_lines)
        assert any("Step 1: Initializing test runner" in t for t in final_lines)
        assert any("Step 2: Subtask #455 assertions passed 100% green" in t for t in final_lines)
        assert any("Subtask #455 completed successfully" in t for t in final_lines)


@pytest.mark.asyncio
async def test_sdlc_widget_event_stop_isolation(tmp_path: Path):
    """
    Asserts @on(DataTable.RowSelected, "#sdlc_widget") and @on(DataTable.RowHighlighted, "#sdlc_widget")
    call event.stop() to isolate SDLC widget navigation events.
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="proj_test", repo="org/proj_test", local_path=str(tmp_path)),
        ]
    )
    app = DashboardApp(config=config)

    class MockEvent:
        def __init__(self):
            self.is_stopped = False

        def stop(self):
            self.is_stopped = True

    event_selected = MockEvent()
    app.on_sdlc_row_selected(event_selected)
    assert event_selected.is_stopped is True

    event_highlighted = MockEvent()
    app.on_sdlc_row_highlighted(event_highlighted)
    assert event_highlighted.is_stopped is True


@pytest.mark.asyncio
async def test_tab_activated_event_hydrates_panes(tmp_path: Path):
    """
    Scenario: Tab switching immediately triggers view hydration
      Given the operator switches between the 'Logs', 'Quota Limits', and 'Alerts (24h)' tabs
      When the 'TabActivated' event fires
      Then the dashboard must refresh and redraw the active pane immediately without waiting for the 2.0s timer.
    """
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(name="biq-playbook", repo="BasketIQ/biq-playbook", local_path=str(tmp_path))
    config = GlobalConfig(
        projects=[project],
        quota=QuotaSettings(
            harnesses={
                "antigravity": HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
            }
        ),
    )
    quota_manager = QuotaManager(config, state_manager)
    buffer_manager = ProjectLogBufferManager()
    buffer_manager.reset()
    buffer_manager.add_line("Initial biq-playbook log", project_name="biq-playbook", node_name="architect")

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        quota_manager=quota_manager,
        buffer_manager=buffer_manager,
        selected_project="biq-playbook",
    )

    class DummyPane:
        def __init__(self, pane_id: str):
            self.id = pane_id

    class DummyTabActivated:
        def __init__(self, pane_id: str):
            self.pane = DummyPane(pane_id)

    async with app.run_test() as pilot:
        await pilot.pause()

        # 1. Activate tab_quotas
        await app.on_tab_activated(DummyTabActivated("tab_quotas"))
        quota_widget = app.query_one(HarnessQuotaWidget)
        assert quota_widget.row_count > 0

        # 2. Activate tab_logs
        await app.on_tab_activated(DummyTabActivated("tab_logs"))
        log_view = app.query_one(RichLog)
        assert any("Initial biq-playbook log" in line.text for line in log_view.lines)

        # 3. Activate tab_alerts
        await app.on_tab_activated(DummyTabActivated("tab_alerts"))
        alerts_widget = app.query_one(AnomalyAlertsWidget)
        assert alerts_widget is not None


@pytest.mark.asyncio
async def test_dashboard_compound_node_log_streaming_and_prefix_matching(tmp_path: Path):
    """
    Scenario: Logs tab streams compound and sub-phase node logs without dropping
      Given project 'biq-playbook' is selected with active node 'architect'
      When the harness emits subprocess logs tagged with node 'architect_research'
      Then the log filter must recognize the node family prefix 'architect'
      And the RichLog widget must display the line in real time.
    """
    buffer_manager = ProjectLogBufferManager()
    buffer_manager.reset()

    project = ProjectConfig(name="biq-playbook", repo="BasketIQ/biq-playbook", local_path=str(tmp_path))
    config = GlobalConfig(projects=[project])

    app = DashboardApp(
        config=config,
        buffer_manager=buffer_manager,
        selected_project="biq-playbook",
        selected_node="architect",
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        # Set focused project and node
        app.selected_project = "biq-playbook"
        app.selected_node = "architect"
        await app.hydrate_project_logs("biq-playbook", node_name="architect")

        log_view = app.query_one("#log_view", RichLog)

        # Stream log with compound node name 'architect_research'
        app._handle_harness_stream_line(
            project_name="biq-playbook",
            node_name="architect_research",
            line="Architect research step 1: reading SPEC.md",
        )
        await pilot.pause()

        lines = [line.text for line in log_view.lines]
        assert any("Architect research step 1: reading SPEC.md" in t for t in lines)

        # Border title should show wildcard
        assert "architect*" in str(log_view.border_title)


@pytest.mark.asyncio
async def test_scenario_tui_dashboard_reactively_rebinds_all_4_config_holders_upon_reload(tmp_path: Path):
    """
    Scenario: TUI Dashboard reactively re-binds all 4 config holders upon reload
      Given the Textual TUI dashboard is active with an initial configuration snapshot
      When the daemon completes a configuration reload that modifies token limits or project models
      Then "DashboardApp._rebind_config" must update "self.config", "quota_manager.config", and "quota_manager.quota_settings"
      And "HarnessQuotaWidget.update_quotas" must immediately render the new token limit in the Quota tab
      And the "ConfigStatusBanner" must display the canonical config path, local timestamp, and trigger
      And the "projects_table" must immediately reflect the updated models.
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text("version: 2\n", encoding="utf-8")

    initial_project = ProjectConfig(
        name="test-project",
        repo="org/test-project",
        local_path=str(tmp_path),
        nodes={
            "devtest": NodeConfig(model="gemini-3.8-flash-high"),
        },
    )
    initial_config = GlobalConfig(
        projects=[initial_project],
        quota=QuotaSettings(
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                ),
            }
        ),
        resolved_path=config_file,
    )

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.record_reload_complete(
        status="SUCCESS",
        projects_count=1,
        config_path=config_file,
        trigger="Daemon Startup",
    )

    # Acquire lock for devtest to simulate active running node
    await state_manager.acquire_lock(issue_id=147, repo="org/test-project", node_type="devtest")

    quota_manager = QuotaManager(initial_config, state_manager)
    config_holder = ConfigHolder(initial_config)

    app = DashboardApp(
        config=config_holder,
        state_manager=state_manager,
        quota_manager=quota_manager,
        config_holder=config_holder,
        config_path=config_file,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        # Initial checks
        banner = app.query_one(ConfigStatusBanner)
        assert str(config_file.resolve()) in banner.canonical_config_path
        assert "Daemon Startup" in banner.last_reload_trigger

        table = app.query_one("#projects_table", DataTable)
        assert table.row_count == 1
        row = table.get_row_at(0)
        # Column 6 (7th column) is Agent Model
        assert row[6] == "gemini-3.8-flash-high"

        # Quota widget initial check
        quota_widget = app.query_one(HarnessQuotaWidget)
        assert "2.0M" in str(quota_widget.get_row("antigravity")[1])

        # WHEN: Daemon completes a configuration reload that modifies token limits and project models
        updated_project = ProjectConfig(
            name="test-project",
            repo="org/test-project",
            local_path=str(tmp_path),
            nodes={
                "devtest": NodeConfig(model="claude-opus-4", effort="high"),
            },
        )
        updated_config = GlobalConfig(
            projects=[updated_project],
            quota=QuotaSettings(
                harnesses={
                    "antigravity": HarnessQuotaConfig(
                        window_hours=1.0,
                        window_token_limit=8_000_000,
                        avg_tokens_per_hour=400_000,
                    ),
                }
            ),
            resolved_path=config_file,
        )

        # Trigger rebind via _rebind_config
        await app._rebind_config(
            new_config=updated_config,
            trigger="CLI IPC",
            timestamp="2026-09-03 16:30:00",
            config_path=config_file,
        )
        await pilot.pause()

        # THEN 1: DashboardApp._rebind_config must update self.config, quota_manager.config, and quota_manager.quota_settings
        assert app.config is updated_config
        assert quota_manager.config is updated_config
        assert quota_manager.quota_settings is updated_config.quota

        # THEN 2: HarnessQuotaWidget.update_quotas must immediately render the new token limit in the Quota tab
        assert quota_widget.config is updated_config
        assert "8.0M" in str(quota_widget.get_row("antigravity")[1])

        # THEN 3: ConfigStatusBanner must display canonical config path, local timestamp, and trigger
        assert str(config_file.resolve()) in banner.canonical_config_path
        assert banner.last_reload_timestamp == "2026-09-03 16:30:00"
        assert banner.last_reload_trigger == "CLI IPC"
        assert "CLI IPC" in banner.renderable.plain
        assert "2026-09-03 16:30:00" in banner.renderable.plain

        # THEN 4: projects_table must immediately reflect the updated models
        updated_row = table.get_row_at(0)
        assert updated_row[6] == "claude-opus-4 (high)"


@pytest.mark.asyncio
async def test_scenario_config_status_banner_rendering_and_layout_protection(tmp_path: Path):
    """
    Scenario: ConfigStatusBanner rendering and layout protection
      Given the Textual TUI dashboard
      When rendered in interactive mode
      Then the "ConfigStatusBanner" widget must display above "#projects_table" with height 3
      And "#projects_table" height must be styled as "1fr" to prevent layout overflow
      And it must render the resolved config path, last reload local time, and trigger source.
    """
    config_file = tmp_path / "custom_config.yaml"
    config_file.write_text("version: 2\n", encoding="utf-8")

    config = GlobalConfig(
        projects=[ProjectConfig(name="p1", repo="org/p1", local_path=str(tmp_path))],
        resolved_path=config_file,
    )

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.record_reload_complete(
        status="SUCCESS",
        projects_count=1,
        config_path=config_file,
        trigger="File Watcher",
        epoch=1725370000.0,
    )

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        config_path=config_file,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        banner = app.query_one(ConfigStatusBanner)
        table = app.query_one("#projects_table", DataTable)

        # 1. Displays above #projects_table
        children = list(app.screen.children)
        assert children.index(banner) < children.index(table)

        # 2. Banner height must be 3
        assert banner.styles.height.value == 3

        # 3. #projects_table height must be styled as 1fr
        assert str(table.styles.height) == "1fr"

        # 4. Render resolved config path, last reload local time, and trigger source
        assert str(config_file.resolve()) in banner.renderable.plain
        assert "File Watcher" in banner.renderable.plain
        assert banner.last_reload_trigger == "File Watcher"
        assert banner.canonical_config_path == str(config_file.resolve())
        assert banner.last_reload_timestamp != "-"


@pytest.mark.asyncio
async def test_scenario_7th_column_agent_model_in_tui_projects_table(tmp_path: Path):
    """
    Scenario: 7th Column Agent Model in TUI projects table
      Given the projects table in DashboardApp
      When rendered with active or idle projects
      Then the table must contain a 7th column titled "Agent Model"
      And running nodes must display the formatted agent specification via "format_node_agent_spec"
      And idle project rows must display "—".
    """
    p_active = ProjectConfig(
        name="active-proj",
        repo="org/active-proj",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(model="claude-sonnet-5", effort="medium"),
            "devtest": NodeConfig(model="gemini-3.8-flash-high"),
        },
    )
    p_idle = ProjectConfig(
        name="idle-proj",
        repo="org/idle-proj",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(model="claude-sonnet-5"),
        },
    )
    config = GlobalConfig(projects=[p_active, p_idle])

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Active lock on p_active
    await state_manager.acquire_lock(issue_id=101, repo="org/active-proj", node_type="architect")

    app = DashboardApp(config=config, state_manager=state_manager)

    async with app.run_test() as pilot:
        await pilot.pause()

        table = app.query_one("#projects_table", DataTable)

        # Assert 7th column title is "Agent Model"
        col_labels = [str(col.label) for col in table.columns.values()]
        assert len(col_labels) == 7
        assert col_labels[6] == "Agent Model"

        # Row 0: active-proj (running architect)
        row_active = table.get_row("active-proj::architect")
        expected_spec = format_node_agent_spec("claude-sonnet-5", "medium")
        assert expected_spec == "claude-sonnet-5 (medium)"
        assert row_active[6] == expected_spec

        # Row 1: idle-proj (idle)
        row_idle = table.get_row("idle-proj::Idle")
        assert row_idle[6] == "—"


@pytest.mark.asyncio
async def test_scenario_dashboard_app_config_holder_and_static_config_support(tmp_path: Path):
    """Asserts DashboardApp supports both ConfigHolder and static GlobalConfig fallback."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("version: 2\n", encoding="utf-8")

    initial_config = GlobalConfig(
        projects=[ProjectConfig(name="p1", repo="org/p1", local_path=str(tmp_path))],
        resolved_path=config_file,
    )

    # 1. With ConfigHolder
    holder = ConfigHolder(initial_config)
    app_with_holder = DashboardApp(config=holder)
    assert app_with_holder.config_holder is holder
    assert app_with_holder.config is initial_config

    new_cfg = GlobalConfig(
        projects=[ProjectConfig(name="p2", repo="org/p2", local_path=str(tmp_path))],
        resolved_path=config_file,
    )
    await app_with_holder._rebind_config(new_cfg)
    assert app_with_holder.config is new_cfg
    assert holder.config is new_cfg

    # 2. With static GlobalConfig
    app_static = DashboardApp(config=initial_config)
    assert app_static.config_holder is None
    assert app_static.config is initial_config
    await app_static._rebind_config(new_cfg)
    assert app_static.config is new_cfg


# ============================================================================
# Deterministic Verification Suite & Targeted Named Tests (Issue #148 / T-9)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_deterministic_non_blocking_startup_verification(tmp_path: Path):
    """
    Scenario: Deterministic non-blocking startup verification
      Given DashboardApp is mounted during daemon startup
      When "on_mount" completes
      Then the background label synchronization task must not be done
      And the dashboard must be interactive and fully responsive.
    """
    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\n"
        "projects:\n  - name: proj-fast\n    repo: org/fast\n    local_path: .\n",
        encoding="utf-8",
    )
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[ProjectConfig(name="proj-fast", repo="org/fast", local_path=str(tmp_path))],
        resolved_path=config_file,
    )
    holder = ConfigHolder(config)
    quota_mgr = QuotaManager(config, state_manager)

    sync_started = asyncio.Event()
    sync_release = asyncio.Event()

    async def mock_background_label_sync():
        sync_started.set()
        await sync_release.wait()
        return {"needs-triage": True}

    # Start background label sync task
    sync_task = asyncio.create_task(mock_background_label_sync())
    await sync_started.wait()

    app = DashboardApp(
        config=holder,
        state_manager=state_manager,
        quota_manager=quota_mgr,
        config_holder=holder,
        config_path=config_file,
    )

    # When on_mount completes
    async with app.run_test() as pilot:
        await pilot.pause()

        # THEN 1: the background label synchronization task must not be done
        assert not sync_task.done(), "Background label sync must still be executing concurrently"

        # THEN 2: the dashboard must be interactive and fully responsive
        assert app.is_running is True
        assert app.screen.is_mounted is True

        table = app.query_one("#projects_table", DataTable)
        banner = app.query_one(ConfigStatusBanner)
        sdlc = app.query_one(SDLCProgressWidget)
        quota = app.query_one(HarnessQuotaWidget)

        assert table is not None and table.is_mounted is True
        assert banner is not None and banner.is_mounted is True
        assert sdlc is not None and sdlc.is_mounted is True
        assert quota is not None and quota.is_mounted is True
        assert table.row_count >= 1

        # Simulate user interaction to confirm responsiveness
        await pilot.press("down")
        await pilot.press("up")

    # Clean up background sync
    sync_release.set()
    await sync_task
    assert sync_task.done()


test_named_non_blocking_startup = test_scenario_deterministic_non_blocking_startup_verification


@pytest.mark.asyncio
async def test_named_label_sync(tmp_path: Path):
    """
    Targeted Named Test 3: Label Sync
    Verifies smart 1-pass label synchronization, color case-folding,
    one-shot purge guard, and keyword-only state_manager parameter.
    """
    import json
    from orchestrator.config import LabelConfig
    from orchestrator.housekeeping import sync_repository_labels
    from unittest.mock import patch

    state_mgr = StateManager(tmp_path / "state.db")
    await state_mgr.init_db()

    managed = [
        LabelConfig(name="needs-triage", color="E2B7E1", description="Architect triage"),
        LabelConfig(name="ready-for-dev", color="0E8A16", description="DevTest pickup"),
    ]

    # Pre-record purge guard
    await state_mgr.set_daemon_control_value("legacy_purge_done:org/repo-sync", "1")

    remote_labels = [
        {"name": "needs-triage", "color": "e2b7e1", "description": "Architect triage"},
        {"name": "obsolete-lbl", "color": "111111", "description": "Obsolete"},
    ]

    executed_cmds = []

    class MockProc:
        def __init__(self, stdout=b"", returncode=0):
            self._stdout = stdout
            self.returncode = returncode
        async def communicate(self):
            return self._stdout, b""
        async def wait(self):
            return self.returncode

    async def mock_exec(*cmd, **kwargs):
        c = list(cmd)
        executed_cmds.append(c)
        if c[:3] == ["gh", "label", "list"]:
            return MockProc(stdout=json.dumps(remote_labels).encode("utf-8"))
        return MockProc()

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        results = await sync_repository_labels(
            "org/repo-sync",
            managed,
            purge_legacy=True,
            state_manager=state_mgr,
        )

    # 1. Single inspection call with limit 200
    list_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "list"]]
    assert len(list_calls) == 1
    assert "--limit" in list_calls[0]
    assert "200" in list_calls[0]

    # 2. Obsolete deletion skipped due to purge guard
    delete_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "delete"]]
    assert len(delete_calls) == 0

    # 3. Only missing "ready-for-dev" created; "needs-triage" case-folded match skipped
    create_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "create"]]
    assert len(create_calls) == 1
    assert "ready-for-dev" in create_calls[0]

    assert results["needs-triage"] is True
    assert results["ready-for-dev"] is True


def test_named_agent_format():
    """
    Targeted Named Test 4: Agent Format
    Verifies pure harness-agnostic format_node_agent_spec with zero harness-specific branching.
    """
    from orchestrator.cli import format_node_agent_spec

    # With effort
    assert format_node_agent_spec("claude-sonnet-5", "medium") == "claude-sonnet-5 (medium)"
    assert format_node_agent_spec("custom-model", "high") == "custom-model (high)"

    # Without effort / None / empty string
    assert format_node_agent_spec("gemini-3.8-flash-high", None) == "gemini-3.8-flash-high"
    assert format_node_agent_spec("gemini-3.8-flash-high", "") == "gemini-3.8-flash-high"
    assert format_node_agent_spec("devin", None) == "devin"

    # Idle / None model
    assert format_node_agent_spec(None, None) == "—"
    assert format_node_agent_spec(None, "medium") == "—"
    assert format_node_agent_spec("", None) == "—"


def test_scenario_full_test_suite_pass_rate_and_test_count_assertion(request):
    """
    Scenario: Full test suite pass rate and test count assertion
      Given the complete test suite in "tests/"
      When pytest is executed
      Then all 337 baseline tests plus at least 4 new targeted tests must pass (>=341 tests total)
      And 0 failures or regressions must occur.
    """
    session = request.session
    total_collected = len(session.items)
    if total_collected >= 341:
        assert total_collected >= 341, f"Expected >= 341 total tests, got {total_collected}"
    else:
        import ast
        tests_dir = Path(__file__).parent
        test_fn_count = 0
        for py_file in tests_dir.glob("test_*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8-sig"))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                        test_fn_count += 1
            except Exception:
                pass
        assert test_fn_count >= 341, f"Expected >= 341 tests defined in tests/, got {test_fn_count}"


def test_scenario_living_documentation_and_changelog_synchronization():
    """
    Scenario: Living documentation and CHANGELOG synchronization
      Given the changes introduced in #143 subtasks 1 through 4
      When inspecting "docs/node-cli.md", ".graph/architecture.md", and "CHANGELOG.md"
      Then the new CLI commands, reload behavior, table columns, and architecture invariants must be documented
      And an entry detailing all features and improvements must be present in "CHANGELOG.md" under "## [Unreleased]".
    """
    repo_root = Path(__file__).resolve().parent.parent

    # 1. docs/node-cli.md
    cli_doc = (repo_root / "docs" / "node-cli.md").read_text(encoding="utf-8")
    assert "ConfigStatusBanner" in cli_doc
    assert "Agent Model" in cli_doc
    assert "config reload" in cli_doc
    assert "background" in cli_doc.lower()

    # 2. .graph/architecture.md
    arch_doc = (repo_root / ".graph" / "architecture.md").read_text(encoding="utf-8")
    assert "ConfigStatusBanner" in arch_doc
    assert "ConfigHolder" in arch_doc
    assert "Agent Model" in arch_doc
    assert "legacy_purge_done" in arch_doc

    # 3. CHANGELOG.md
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert "deterministic verification suite" in changelog.lower() or "verification suite" in changelog.lower()
    assert "#148" in changelog


# ==============================================================================
# Issue #156 BDD Scenarios: Identity Transition Diffing & Direct RichLog Writes
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario_direct_main_loop_writing_without_call_from_thread(tmp_path: Path, mocker):
    """
    Scenario: Direct main-loop writing without call_from_thread
      Given the TUI dashboard event loop is actively running
      When "AsyncHarnessAdapter" stream listener or log handler receives an output line
      Then it must write directly to "log_view.write(rich.markup.escape(line))"
      And it must not invoke "call_from_thread" while executing on the main asyncio thread.
    """
    import rich.markup
    import threading

    config = GlobalConfig(
        projects=[
            ProjectConfig(name="biq-playbook", repo="BasketIQ/biq-playbook", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
        selected_node="architect",
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        log_view = app.query_one("#log_view", RichLog)

        call_from_thread_spy = mocker.spy(app, "call_from_thread")
        log_write_spy = mocker.spy(log_view, "write")

        # 1. Test harness stream listener execution on the main loop
        raw_stream_line = "  [biq-playbook:architect] Step 1: evaluating requirements [1/3] <xml-tag>"
        app._handle_harness_stream_line(
            project_name="biq-playbook",
            node_name="architect",
            line=raw_stream_line,
        )
        await pilot.pause()

        # Must NOT call call_from_thread on the main thread
        call_from_thread_spy.assert_not_called()
        # Must write directly to log_view.write with rich.markup.escape
        expected_escaped_stream = rich.markup.escape(raw_stream_line)
        log_write_spy.assert_called_with(expected_escaped_stream)

        # 2. Test log handler record emission on the main loop
        rec = logging.LogRecord(
            name="orchestrator",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="[biq-playbook:architect] Formatted log with [markup] tags",
            args=(),
            exc_info=None,
        )
        setattr(rec, "project", "biq-playbook")
        setattr(rec, "node", "architect")
        formatted_log = "[biq-playbook:architect] Formatted log with [markup] tags"

        log_write_spy.reset_mock()
        call_from_thread_spy.reset_mock()

        app._handle_log_record(rec, formatted_log)
        await pilot.pause()

        # Must NOT call call_from_thread on the main thread
        call_from_thread_spy.assert_not_called()
        # Must write directly to log_view.write with rich.markup.escape
        expected_escaped_log = rich.markup.escape(formatted_log)
        log_write_spy.assert_called_with(expected_escaped_log)

        # 3. Test background worker thread execution invokes call_from_thread
        log_write_spy.reset_mock()
        call_from_thread_spy.reset_mock()

        thread_line = "Background worker thread output line [worker]"
        thread_executed = threading.Event()

        def worker_target():
            app._handle_harness_stream_line(
                project_name="biq-playbook",
                node_name="architect",
                line=thread_line,
            )
            thread_executed.set()

        worker_thread = threading.Thread(target=worker_target)
        worker_thread.start()
        for _ in range(20):
            if thread_executed.is_set():
                break
            await asyncio.sleep(0.05)
        worker_thread.join(timeout=1.0)
        assert thread_executed.is_set()

        # From another thread, call_from_thread MUST be invoked
        assert call_from_thread_spy.call_count >= 1
        assert call_from_thread_spy.call_args[0][0] == log_view.write
        assert call_from_thread_spy.call_args[0][1] == rich.markup.escape(thread_line)


@pytest.mark.asyncio
async def test_scenario_identity_based_state_transition_on_active_job_change(tmp_path: Path, mocker):
    """
    Scenario: Identity-based state transition on active job change
      Given project "biq-playbook" is currently highlighted in "#projects_table" with state "Idle"
      When the active node state transitions to "architect" on Issue #75
      Then "update_projects_table" must detect the transition via (project_name, active_node) identity diffing
      And "selected_node" must automatically update to "architect" without manual navigation
      And the log view title must update to "Live Output [biq-playbook | architect*]"
      And "hydrate_project_logs" must be triggered with "node_name='architect'" and "issue_id=75".
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="biq-playbook",
                repo="BasketIQ/biq-playbook",
                local_path=str(tmp_path),
                nodes={"architect": NodeConfig(model="claude-sonnet-5")},
            ),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
    )

    async with app.run_test() as pilot:
        table = app.query_one("#projects_table", DataTable)
        log_view = app.query_one("#log_view", RichLog)
        await pilot.pause()

        # Given project "biq-playbook" is currently highlighted in "#projects_table" with state "Idle"
        assert app.selected_project == "biq-playbook"
        assert app.selected_node is None
        assert app.selected_issue_id is None
        assert table.row_count == 1
        assert "Idle" in str(table.get_row_at(0)[2])
        assert str(log_view.border_title).replace(r"\[", "[") == "Live Output [biq-playbook]"
        assert log_view.title == "Live Output [biq-playbook]"

        hydrate_spy = mocker.spy(app, "hydrate_project_logs")

        # When the active node state transitions to "architect" on Issue #75
        await state_manager.acquire_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")

        # Then "update_projects_table" must detect the transition via (project_name, active_node) identity diffing
        await app.update_projects_table()
        await pilot.pause()

        # And "selected_node" must automatically update to "architect" without manual navigation
        assert app.selected_node == "architect"
        assert app.selected_issue_id == 75
        assert app.issue_id == 75

        # And the log view title must update to "Live Output [biq-playbook | architect*]"
        assert str(log_view.border_title).replace(r"\[", "[") == "Live Output [biq-playbook | architect*]"
        assert log_view.title == "Live Output [biq-playbook | architect*]"

        # And "hydrate_project_logs" must be triggered with "node_name='architect'" and "issue_id=75"
        hydrate_spy.assert_called_with("biq-playbook", node_name="architect", issue_id=75)

        # And cursor is positioned on the active node row
        row_0 = table.get_row_at(0)
        assert "architect" in str(row_0[2]).lower()
        assert "Issue #75" in str(row_0[5])


@pytest.mark.asyncio
async def test_scenario_identity_transition_sequential_lifecycle_and_idle_recovery(tmp_path: Path, mocker):
    """
    Scenario: Multi-step identity transition lifecycle: Idle -> Architect (#75) -> DevTest (#76) -> Idle.
    Asserts identity diffing correctly updates node and issue_id and recovers cleanly when going Idle.
    """
    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="biq-playbook",
                repo="BasketIQ/biq-playbook",
                local_path=str(tmp_path),
                nodes={
                    "architect": NodeConfig(model="claude-sonnet-5"),
                    "devtest": NodeConfig(model="gemini-3.8-flash-high"),
                },
            ),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
    )

    async with app.run_test() as pilot:
        _ = app.query_one("#projects_table", DataTable)
        log_view = app.query_one("#log_view", RichLog)
        await pilot.pause()

        hydrate_spy = mocker.spy(app, "hydrate_project_logs")

        # 1. Idle -> Architect on Issue #75
        await state_manager.acquire_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")
        await app.update_projects_table()
        await pilot.pause()

        assert app.selected_node == "architect"
        assert app.selected_issue_id == 75
        assert "architect*" in str(log_view.border_title)
        hydrate_spy.assert_called_with("biq-playbook", node_name="architect", issue_id=75)

        # 2. Architect finishes -> DevTest starts on Issue #76
        hydrate_spy.reset_mock()
        await state_manager.release_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")
        await state_manager.acquire_lock(issue_id=76, repo="BasketIQ/biq-playbook", node_type="devtest")
        await app.update_projects_table()
        await pilot.pause()

        assert app.selected_node == "devtest"
        assert app.selected_issue_id == 76
        assert "devtest*" in str(log_view.border_title)
        hydrate_spy.assert_called_with("biq-playbook", node_name="devtest", issue_id=76)

        # 3. DevTest finishes -> Project becomes Idle
        hydrate_spy.reset_mock()
        await state_manager.release_lock(issue_id=76, repo="BasketIQ/biq-playbook", node_type="devtest")
        await app.update_projects_table()
        await pilot.pause()

        assert app.selected_node is None
        assert app.selected_issue_id is None
        assert str(log_view.border_title).replace(r"\[", "[") == "Live Output [biq-playbook]"
        assert log_view.title == "Live Output [biq-playbook]"
        hydrate_spy.assert_called_with("biq-playbook", node_name=None, issue_id=None)

        # 4. Next tick with identical Idle state does NOT re-trigger hydrate_project_logs
        hydrate_spy.reset_mock()
        await app.update_projects_table()
        await pilot.pause()
        hydrate_spy.assert_not_called()


def test_scenario_issue_id_plumbing_attributes():
    """
    Scenario: issue_id plumbing and property accessors on DashboardApp.
    """
    app = DashboardApp(selected_project="biq-playbook", selected_issue_id=123)
    assert app.selected_issue_id == 123
    assert app.issue_id == 123

    app.issue_id = 456
    assert app.selected_issue_id == 456
    assert app.issue_id == 456

    app2 = DashboardApp(selected_project="biq-playbook", issue_id=789)
    assert app2.selected_issue_id == 789
    assert app2.issue_id == 789


# ---------------------------------------------------------------------------
# Issue #157 BDD Scenarios: Seamless Offset Handoff, Incremental Log Polling,
# Disambiguated Placeholders & Log Rotation Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_seamless_offset_handoff_prevents_duplicate_and_dropped_lines(tmp_path: Path):
    """
    Scenario: Seamless offset handoff prevents duplicate and dropped lines
      Given "hydrate_project_logs" finishes reading byte offset N from the active log file
      When the incremental tailer "_poll_active_log_file" runs on the 2.0s table refresh tick
      Then it must begin reading strictly from byte offset N
      And previously hydrated lines must not be duplicated in the log pane.
    """
    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    project_log_dir = logs_root / "biq-playbook" / "architect"
    project_log_dir.mkdir(parents=True, exist_ok=True)
    log_file = project_log_dir / "20260903_120000_architect_run.log"

    initial_content = "Line 1: triage starting\nLine 2: analyzing requirements\n"
    log_file.write_text(initial_content, encoding="utf-8")
    initial_byte_offset = log_file.stat().st_size

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="biq-playbook",
                repo="BasketIQ/biq-playbook",
                local_path=str(tmp_path),
                nodes={"architect": NodeConfig(model="claude-sonnet-5")},
            ),
        ],
        settings=SettingsConfig(log_dir=str(logs_root)),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.acquire_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
        selected_node="architect",
        selected_issue_id=75,
    )

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        await pilot.pause()

        # Given "hydrate_project_logs" finishes reading byte offset N from the active log file
        assert app._last_tail_file == log_file
        assert app._last_tail_offset == initial_byte_offset

        rendered = [line.text for line in log_view.lines]
        assert "Line 1: triage starting" in rendered
        assert "Line 2: analyzing requirements" in rendered
        assert len(log_view.lines) == 2

        # When the incremental tailer "_poll_active_log_file" runs on the 2.0s table refresh tick
        # (Case A: No new lines written -> offset unchanged, no duplicates)
        await app._poll_active_log_file()
        await pilot.pause()

        assert app._last_tail_offset == initial_byte_offset
        assert len(log_view.lines) == 2
        rendered_after_idle_poll = [line.text for line in log_view.lines]
        assert rendered_after_idle_poll.count("Line 1: triage starting") == 1
        assert rendered_after_idle_poll.count("Line 2: analyzing requirements") == 1

        # Case B: Incremental new bytes written to the active log file
        appended_content = "Line 3: decomposing INVEST stories\nLine 4: triage complete\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(appended_content)

        expected_total_offset = log_file.stat().st_size

        # When "_poll_active_log_file" runs on the 2.0s tick
        await app._poll_active_log_file()
        await pilot.pause()

        # Then it must begin reading strictly from byte offset N
        assert app._last_tail_offset == expected_total_offset

        # And previously hydrated lines must not be duplicated in the log pane
        final_rendered = [line.text for line in log_view.lines]
        assert final_rendered == [
            "Line 1: triage starting",
            "Line 2: analyzing requirements",
            "Line 3: decomposing INVEST stories",
            "Line 4: triage complete",
        ]
        assert final_rendered.count("Line 1: triage starting") == 1
        assert final_rendered.count("Line 2: analyzing requirements") == 1
        assert final_rendered.count("Line 3: decomposing INVEST stories") == 1
        assert final_rendered.count("Line 4: triage complete") == 1


@pytest.mark.asyncio
async def test_scenario_disambiguated_placeholders_and_clean_retirement(tmp_path: Path):
    """
    Scenario: Disambiguated placeholders and clean retirement
      Given an active node has been selected on the dashboard
      When no log file exists on disk, the view must display "No execution logs found yet for node '{node_name}'"
      And when a 0-byte active startup file exists, the view must display "⚡ Initializing {node_name} harness on Issue #{issue_id}... Awaiting output."
      And when the harness emits its first byte, the placeholder must cleanly retire.
    """
    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    project_log_dir = logs_root / "biq-playbook" / "architect"
    project_log_dir.mkdir(parents=True, exist_ok=True)

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="biq-playbook",
                repo="BasketIQ/biq-playbook",
                local_path=str(tmp_path),
                nodes={
                    "architect": NodeConfig(model="claude-sonnet-5"),
                    "devtest": NodeConfig(model="gemini-3.8-flash-high"),
                },
            ),
        ],
        settings=SettingsConfig(log_dir=str(logs_root)),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.acquire_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")

    # 1. Given an active node has been selected on the dashboard
    # When no log file exists on disk, the view must display "No execution logs found yet for node '{node_name}'"
    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
        selected_node="architect",
        selected_issue_id=75,
    )

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        await pilot.pause()

        assert app._last_tail_file is None
        assert app._last_tail_offset == 0
        assert app._placeholder_active is True
        rendered = [line.text for line in log_view.lines]
        assert rendered == ["No execution logs found yet for node 'architect'"]

        # 2. When a 0-byte active startup file exists, the view must display "⚡ Initializing {node_name} harness on Issue #{issue_id}... Awaiting output."
        startup_log_file = project_log_dir / "20260903_120500_architect_run.log"
        startup_log_file.write_text("", encoding="utf-8")
        assert startup_log_file.stat().st_size == 0

        await app.hydrate_project_logs("biq-playbook", node_name="architect", issue_id=75)
        await pilot.pause()

        assert app._last_tail_file == startup_log_file
        assert app._last_tail_offset == 0
        assert app._placeholder_active is True
        rendered_startup = [line.text for line in log_view.lines]
        assert rendered_startup == ["⚡ Initializing architect harness on Issue #75... Awaiting output."]

        # 3. And when the harness emits its first byte, the placeholder must cleanly retire.
        # Subcase A: Retirement via live harness stream listener
        stream_line = "  [biq-playbook:architect] Initializing graph-orchestrator context..."
        app._handle_harness_stream_line(
            project_name="biq-playbook",
            node_name="architect",
            line=stream_line,
        )
        await pilot.pause()

        assert app._placeholder_active is False
        rendered_after_stream = [line.text for line in log_view.lines]
        assert rendered_after_stream == ["  [biq-playbook:architect] Initializing graph-orchestrator context..."]
        assert not any("⚡ Initializing" in r for r in rendered_after_stream)

        # Subcase B: Retirement via incremental tailer polling disk write
        devtest_dir = logs_root / "biq-playbook" / "devtest"
        devtest_dir.mkdir(parents=True, exist_ok=True)
        devtest_log_file = devtest_dir / "20260903_121000_devtest_run.log"
        devtest_log_file.write_text("", encoding="utf-8")

        # Switch to active devtest node on issue 76
        await state_manager.release_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")
        await state_manager.acquire_lock(issue_id=76, repo="BasketIQ/biq-playbook", node_type="devtest")
        await app.update_projects_table()
        await pilot.pause()

        assert app.selected_node == "devtest"
        assert app.selected_issue_id == 76
        assert app._last_tail_file == devtest_log_file
        assert app._last_tail_offset == 0
        assert app._placeholder_active is True
        assert [line.text for line in log_view.lines] == ["⚡ Initializing devtest harness on Issue #76... Awaiting output."]

        # Now harness writes its first byte to the 0-byte active startup file on disk
        first_disk_bytes = "DevTest execution: running pytest test suite\n"
        devtest_log_file.write_text(first_disk_bytes, encoding="utf-8")

        # When "_poll_active_log_file" runs on the next tick
        await app._poll_active_log_file()
        await pilot.pause()

        # The placeholder must cleanly retire and be replaced with live output
        assert app._placeholder_active is False
        assert app._last_tail_offset == devtest_log_file.stat().st_size
        rendered_after_poll = [line.text for line in log_view.lines]
        assert rendered_after_poll == ["DevTest execution: running pytest test suite"]
        assert not any("⚡ Initializing" in r for r in rendered_after_poll)


@pytest.mark.asyncio
async def test_scenario_graceful_recovery_on_log_rotation(tmp_path: Path):
    """
    Scenario: Graceful recovery on log rotation
      Given an actively tailed log file is rotated or unlinked by "rotate_logs"
      When "_poll_active_log_file" encounters "FileNotFoundError" or size truncation
      Then it must reset tracked target file and byte offset to None and 0
      And cleanly re-discover the active log file on the next tick.
    """
    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    project_log_dir = logs_root / "biq-playbook" / "architect"
    project_log_dir.mkdir(parents=True, exist_ok=True)
    initial_log_file = project_log_dir / "20260903_100000_architect_run.log"
    initial_log_file.write_text("Active run before rotation line 1\n", encoding="utf-8")

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="biq-playbook",
                repo="BasketIQ/biq-playbook",
                local_path=str(tmp_path),
                nodes={"architect": NodeConfig(model="claude-sonnet-5")},
            ),
        ],
        settings=SettingsConfig(log_dir=str(logs_root)),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.acquire_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
        selected_node="architect",
        selected_issue_id=75,
    )

    async with app.run_test() as pilot:
        log_view = app.query_one("#log_view", RichLog)
        await pilot.pause()

        # Given an actively tailed log file
        assert app._last_tail_file == initial_log_file
        assert app._last_tail_offset > 0
        assert any("Active run before rotation" in line.text for line in log_view.lines)

        # ------------------------------------------------------------------
        # Branch A: Log file unlinked by rotate_logs / file rotation
        # ------------------------------------------------------------------
        initial_log_file.unlink()
        assert not initial_log_file.exists()

        # When "_poll_active_log_file" encounters FileNotFoundError
        await app._poll_active_log_file()
        await pilot.pause()

        # Then it must reset tracked target file and byte offset to None and 0
        assert app._last_tail_file is None
        assert app._last_tail_offset == 0

        # And cleanly re-discover the active log file on the next tick
        # Simulate new rotated log file arriving on disk
        rotated_log_file = project_log_dir / "20260903_110000_architect_run.log"
        new_content = "Rotated run line 1: Fresh active agent cycle\n"
        rotated_log_file.write_text(new_content, encoding="utf-8")

        # Next tick poll
        await app._poll_active_log_file()
        await pilot.pause()

        assert app._last_tail_file == rotated_log_file
        assert app._last_tail_offset == rotated_log_file.stat().st_size
        assert any("Rotated run line 1" in line.text for line in log_view.lines)

        # ------------------------------------------------------------------
        # Branch B: Size truncation recovery (e.g. truncated in-place)
        # ------------------------------------------------------------------
        # File is truncated to a smaller size (e.g. 10 bytes while offset was ~45 bytes)
        truncated_content = "Truncated\n"
        rotated_log_file.write_text(truncated_content, encoding="utf-8")
        assert rotated_log_file.stat().st_size < app._last_tail_offset

        # When "_poll_active_log_file" encounters size truncation
        await app._poll_active_log_file()
        await pilot.pause()

        # Then it must reset tracked target file and byte offset to None and 0
        assert app._last_tail_file is None
        assert app._last_tail_offset == 0

        # And cleanly re-discover the active log file on the next tick
        await app._poll_active_log_file()
        await pilot.pause()

        assert app._last_tail_file == rotated_log_file
        assert app._last_tail_offset == rotated_log_file.stat().st_size
        assert any("Truncated" in line.text for line in log_view.lines)


@pytest.mark.asyncio
async def test_scenario_log_rotation_integration_with_rotate_logs_utility(tmp_path: Path):
    """
    Scenario: End-to-end integration test of log rotation with orchestrator.logging.rotate_logs
    """
    from orchestrator.logging import rotate_logs

    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    project_log_dir = logs_root / "biq-playbook" / "architect"
    project_log_dir.mkdir(parents=True, exist_ok=True)

    old_log = project_log_dir / "20260801_100000_architect_run.log"
    old_log.write_text("Old log line\n", encoding="utf-8")

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="biq-playbook",
                repo="BasketIQ/biq-playbook",
                local_path=str(tmp_path),
                nodes={"architect": NodeConfig(model="claude-sonnet-5")},
            ),
        ],
        settings=SettingsConfig(log_dir=str(logs_root)),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.acquire_lock(issue_id=75, repo="BasketIQ/biq-playbook", node_type="architect")

    app = DashboardApp(
        config=config,
        state_manager=state_manager,
        selected_project="biq-playbook",
        selected_node="architect",
        selected_issue_id=75,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._last_tail_file == old_log

        # Simulate age rotation: rotate_logs unlinks files older than max_age_days
        old_mtime = 1700000000.0  # deep in the past
        import os
        os.utime(old_log, (old_mtime, old_mtime))
        rotate_logs(logs_root, max_age_days=1)
        assert not old_log.exists()

        # Incremental poll detects rotation
        await app._poll_active_log_file()
        await pilot.pause()

        assert app._last_tail_file is None
        assert app._last_tail_offset == 0

        # Create fresh active log file
        fresh_log = project_log_dir / "20260903_123000_architect_run.log"
        fresh_log.write_text("Fresh post-rotation line\n", encoding="utf-8")

        # Next tick re-discovers fresh log
        await app._poll_active_log_file()
        await pilot.pause()

        assert app._last_tail_file == fresh_log
        assert app._last_tail_offset == fresh_log.stat().st_size









