from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import pytest
from textual.widgets import DataTable, Footer, Header, RichLog

from orchestrator.config import GlobalConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import TextualLogHandler
from orchestrator.ui.dashboard import DashboardApp


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
    """Asserts action_quit performs clean resource cleanup and daemon unregistration."""
    config = GlobalConfig(
        projects=[
            ProjectConfig(name="alpha", repo="org/alpha", local_path=str(tmp_path)),
        ]
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.register_daemon(12345)

    terminated = []

    def mock_terminate_all():
        terminated.append(True)
        return 1

    monkeypatch.setattr(AsyncHarnessAdapter, "terminate_all_active", mock_terminate_all)

    app = DashboardApp(config=config, state_manager=state_manager)
    async with app.run_test() as pilot:
        await pilot.press("q")

    assert len(terminated) == 1
    # Verify daemon PID was unregistered
    info = await state_manager.get_daemon_info()
    assert info.get("status") == "STOPPED"
    assert "pid" not in info


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

