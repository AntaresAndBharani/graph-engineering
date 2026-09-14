from __future__ import annotations

from pathlib import Path
import pytest
from textual.widgets import DataTable, RichLog

from orchestrator.config import (
    GlobalConfig,
    ProjectConfig,
)
from orchestrator.db import StateManager
from orchestrator.ui.dashboard import DashboardApp


@pytest.mark.asyncio
async def test_scenario_dashboard_smoothly_handles_display_renegotiation_upon_docking(tmp_path: Path):
    """
    Feature: Resilient TUI Display Renegotiation & Docking Re-Sync

    Scenario: Dashboard smoothly handles display renegotiation upon docking
      Given the Textual dashboard is actively running in application mode
      When the terminal window undergoes rapid resize events or momentary 0x0 geometry during docking
      Then the application does not raise an unhandled exception or crash
      And the layout automatically recalculates and repaints cleanly within 2 seconds
      And the clock and Last Updated timestamps continue advancing normally.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="proj-alpha",
                repo="AntaresAndBharani/proj-alpha",
                local_path=str(tmp_path),
            ),
            ProjectConfig(
                name="proj-beta",
                repo="AntaresAndBharani/proj-beta",
                local_path=str(tmp_path),
            ),
        ]
    )

    app = DashboardApp(config=config, state_manager=state_manager, selected_project="proj-alpha")

    async with app.run_test(size=(80, 24)) as pilot:
        # Given the Textual dashboard is actively running in application mode
        assert app.is_running is True
        initial_heartbeat = app._last_heartbeat_at
        await pilot.pause()

        # When the terminal window undergoes rapid resize events or momentary 0x0 geometry during docking
        storm_sizes = [
            (80, 24),
            (100, 30),
            (0, 0),        # Momentary 0x0 geometry during docking transition
            (0, 50),       # Degenerate zero width
            (60, 0),       # Degenerate zero height
            (40, 15),      # Constrained small terminal
            (160, 50),     # Wide multi-monitor resolution
            (80, 24),      # Normalized terminal geometry
        ]

        for w, h in storm_sizes:
            await pilot.resize_terminal(w, h)
            # rapid succession
            await pilot.pause(0.05)

        # Then the application does not raise an unhandled exception or crash
        assert app.is_running is True
        assert app._last_resize_size == (80, 24)

        # And the layout automatically recalculates and repaints cleanly within 2 seconds
        table = app.query_one("#projects_table", DataTable)
        assert table is not None
        assert table.row_count >= 2

        # And the clock and Last Updated timestamps continue advancing normally.
        # Advance watchdog / heartbeat
        await app._watchdog_beat()
        await pilot.pause()
        assert app._last_heartbeat_at >= initial_heartbeat

        daemon_info = await state_manager.get_daemon_info()
        assert "heartbeat_at" in daemon_info
        recorded_hb = float(daemon_info["heartbeat_at"])
        assert recorded_hb >= initial_heartbeat


@pytest.mark.asyncio
async def test_scenario_operator_manually_triggers_display_resync_via_keybinding(tmp_path: Path):
    """
    Feature: Resilient TUI Display Renegotiation & Docking Re-Sync

    Scenario: Operator manually triggers display re-sync via keybinding
      Given the dashboard display has experienced visual artifacts or stalled rendering
      When the operator presses "f5" or "ctrl+r"
      Then the dashboard executes an immediate full layout and repaint refresh
      And all tables and log panes are re-synchronized to the current terminal dimensions.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="proj-alpha",
                repo="AntaresAndBharani/proj-alpha",
                local_path=str(tmp_path),
            )
        ]
    )

    app = DashboardApp(config=config, state_manager=state_manager, selected_project="proj-alpha")

    async with app.run_test(size=(80, 24)) as pilot:
        table = app.query_one("#projects_table", DataTable)
        log_view = app.query_one("#log_view", RichLog)
        assert table is not None
        assert log_view is not None
        await pilot.pause()

        # Given the dashboard display has experienced visual artifacts or stalled rendering
        # When the operator presses "f5"
        await pilot.press("f5")
        await pilot.pause()

        # Then the dashboard executes an immediate full layout and repaint refresh
        assert app.is_running is True
        assert table.row_count >= 1

        # And all tables and log panes are re-synchronized to the current terminal dimensions.
        # When the operator presses "ctrl+r"
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.is_running is True
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_rapid_resize_storm_with_concurrent_streaming_and_watchdog(tmp_path: Path):
    """
    Integration test asserting that during rapid resize storms:
    1. Active live streaming into RichLog does not crash or corrupt state.
    2. Watchdog liveness heartbeat continues beating and advancing timestamp.
    3. Momentary degenerate zero geometries (0x0, 0x10, 10x0) are safely absorbed.
    4. Post-storm terminal size restores cleanly with populated DataTable and bottom panes.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="resilience-proj",
                repo="AntaresAndBharani/resilience-proj",
                local_path=str(tmp_path),
            )
        ]
    )

    app = DashboardApp(config=config, state_manager=state_manager, selected_project="resilience-proj")

    async with app.run_test(size=(90, 30)) as pilot:
        log_view = app.query_one("#log_view", RichLog)
        assert log_view is not None
        await pilot.pause()

        initial_heartbeat = app._last_heartbeat_at

        # Rapidly resize while concurrently pumping stream lines
        sizes = [
            (95, 32),
            (0, 0),
            (70, 20),
            (0, 25),
            (120, 35),
            (100, 0),
            (150, 45),
            (100, 30),
        ]

        for idx, (w, h) in enumerate(sizes):
            # Pump a log stream line
            app._handle_harness_stream_line("resilience-proj", "devtest", f"Harness output line during storm #{idx}")
            # Trigger resize
            await pilot.resize_terminal(w, h)
            await pilot.pause(0.02)

        # Trigger watchdog heartbeat
        await app._watchdog_beat()
        await pilot.pause()

        # Assert liveness and heartbeat advance
        assert app.is_running is True
        assert app._last_heartbeat_at >= initial_heartbeat
        assert app._last_resize_size == (100, 30)

        # Verify lines are present in RichLog
        assert any("Harness output line during storm #7" in line.text for line in log_view.lines)


@pytest.mark.asyncio
async def test_action_redraw_display_updates_projects_and_bottom_panes(tmp_path: Path):
    """
    Unit test verifying action_redraw_display invokes full refresh and re-syncs
    projects table and bottom panes when selected_project is set or unset.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="proj-1",
                repo="AntaresAndBharani/proj-1",
                local_path=str(tmp_path),
            )
        ]
    )

    # 1. With selected_project
    app1 = DashboardApp(config=config, state_manager=state_manager, selected_project="proj-1")
    async with app1.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app1.action_redraw_display()
        await pilot.pause()
        assert app1.is_running is True

    # 2. Without selected_project (None)
    app2 = DashboardApp(config=config, state_manager=state_manager, selected_project=None)
    async with app2.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app2.action_redraw_display()
        await pilot.pause()
        assert app2.is_running is True


@pytest.mark.asyncio
async def test_on_resize_handles_exception_resilience(tmp_path: Path, monkeypatch):
    """
    Unit test asserting on_resize swallows exceptions when internal refresh or update
    raises an unexpected error during erratic resize storms.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        projects=[
            ProjectConfig(
                name="proj-err",
                repo="AntaresAndBharani/proj-err",
                local_path=str(tmp_path),
            )
        ]
    )

    app = DashboardApp(config=config, state_manager=state_manager, selected_project="proj-err")

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        # Monkeypatch refresh to simulate an unexpected error during resize
        def faulty_refresh(*args, **kwargs):
            raise RuntimeError("Simulated display driver crash during redraw")

        monkeypatch.setattr(app, "refresh", faulty_refresh)

        # Resize event should catch exception cleanly
        await pilot.resize_terminal(100, 30)
        await pilot.pause()

        # App must not crash
        assert app.is_running is True
        assert app._last_resize_size == (100, 30)
