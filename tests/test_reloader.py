from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.config import GlobalConfig, load_config
from orchestrator.db import StateManager
from orchestrator.reloader import ConfigHolder, hot_reload_runtime

runner = CliRunner()


def test_cli_reload_help():
    result = runner.invoke(app, ["reload", "--help"])
    assert result.exit_code == 0
    assert "Hot-reloads configuration and Python modules" in result.stdout


def test_cli_config_reload_help():
    result = runner.invoke(app, ["config", "reload", "--help"])
    assert result.exit_code == 0
    assert "Hot-reloads configuration" in result.stdout


@pytest.mark.asyncio
async def test_daemon_reload_flag_lifecycle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Initial state: not requested
    assert await state_manager.is_reload_requested() is False

    # Register daemon
    await state_manager.register_daemon(12345)

    # Request reload
    pid = await state_manager.request_reload()
    assert pid == 12345
    assert await state_manager.is_reload_requested() is True

    # Clear reload
    await state_manager.clear_reload_request()
    assert await state_manager.is_reload_requested() is False


def test_hot_reload_runtime_returns_config(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "projects:\n"
        "  - name: test-proj\n"
        "    repo: org/repo\n"
        "    local_path: .\n",
        encoding="utf-8",
    )

    new_cfg = hot_reload_runtime(config_file)
    assert new_cfg.__class__.__name__ == "GlobalConfig"
    assert len(new_cfg.projects) == 1
    assert new_cfg.projects[0].name == "test-proj"


def test_cli_reload_command_executes(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["reload", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "In-memory hot-reload signal registered" in result.stdout


def test_cli_config_reload_command_executes(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "reload", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "In-memory hot-reload signal registered" in result.stdout


def test_global_config_resolved_path_field(tmp_path: Path):
    """
    Scenario: Watcher fail-safe and path pinning (T-3)
      Given resolved config path declared as 'resolved_path: Optional[Path] = None' on GlobalConfig
    """
    import orchestrator.config as cfg_mod

    # Default unpinned instance
    cfg = cfg_mod.GlobalConfig()
    assert cfg.resolved_path is None

    # Loaded from file pins resolved_path
    config_file = tmp_path / "config.yaml"
    config_file.write_text("projects:\n  - name: p1\n    repo: o/p1\n    local_path: .\n", encoding="utf-8")
    loaded_cfg = cfg_mod.load_config(config_file)
    assert loaded_cfg.resolved_path == config_file.resolve()


def test_config_holder_thread_safety_and_accessors():
    """
    Unit test verifying thread/async-safe operations on ConfigHolder.
    """
    import orchestrator.config as cfg_mod

    p1 = cfg_mod.ProjectConfig(name="proj1", repo="org/proj1", local_path=".")
    initial_cfg = cfg_mod.GlobalConfig(projects=[p1])
    holder = ConfigHolder(initial_cfg)

    # Accessors
    assert holder.config == initial_cfg
    assert holder.get() == initial_cfg
    assert holder.get_project("proj1") == p1
    assert holder.get_project("nonexistent") is None
    # __getattr__ delegation
    assert len(holder.projects) == 1
    assert holder.settings.poll_interval_seconds == 300

    # Threaded concurrent update & read
    p2 = cfg_mod.ProjectConfig(name="proj2", repo="org/proj2", local_path=".")
    new_cfg = cfg_mod.GlobalConfig(projects=[p1, p2])

    errors = []

    def updater():
        for _ in range(50):
            holder.update(new_cfg)
            holder.set(initial_cfg)

    def reader():
        for _ in range(100):
            c = holder.config
            if len(c.projects) not in (1, 2):
                errors.append(f"Invalid project count: {len(c.projects)}")

    threads = [threading.Thread(target=updater), threading.Thread(target=reader), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


@pytest.mark.asyncio
async def test_scenario_synchronous_confirmation_when_daemon_running(tmp_path: Path, monkeypatch):
    """
    Scenario: Synchronous confirmation upon executing orchestrator config reload when daemon is running
      Given an active orchestrator daemon with PID 18532 verified via psutil.pid_exists
      And pre-reload epoch timestamp recorded
      When the operator executes 'orchestrator config reload'
      Then the command must register 'reload_requested=1' in 'daemon_control'
      And the command must poll until 'last_reload_at_epoch > pre_epoch' within 2.0s
      And upon acknowledgement it must display confirmed reload timestamp, PID, and active project count.
    """
    from orchestrator.cli import _reload_daemon

    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\nprojects:\n  - name: p1\n    repo: o/p1\n    local_path: .\n",
        encoding="utf-8",
    )
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Active daemon with PID 18532 verified via psutil.pid_exists
    pid = 18532
    await state_manager.register_daemon(pid)
    pre_epoch = time.time() - 10.0
    await state_manager.record_reload_complete(status="SUCCESS", projects_count=1, epoch=pre_epoch)

    monkeypatch.setattr("psutil.pid_exists", lambda p: p == pid)

    # Background task simulating daemon watcher acknowledging reload
    async def simulate_daemon_watcher():
        # Wait until reload_requested=1 is registered by CLI
        for _ in range(50):
            if await state_manager.is_reload_requested():
                break
            await asyncio.sleep(0.02)
        assert await state_manager.is_reload_requested() is True

        # Complete reload and set new epoch > pre_epoch
        await state_manager.record_reload_complete(
            status="SUCCESS",
            projects_count=3,
            config_path=config_file,
            trigger="CLI IPC",
        )
        await state_manager.clear_reload_request()

    watcher_task = asyncio.create_task(simulate_daemon_watcher())

    captured_lines = []
    monkeypatch.setattr(
        "orchestrator.cli.console.print",
        lambda *args, **kwargs: captured_lines.append(" ".join(str(a) for a in args)),
    )

    await _reload_daemon(config_file)
    await watcher_task

    output = "\n".join(captured_lines)
    assert "acknowledged and reloaded configuration" in output
    assert "Confirmed Reload Timestamp" in output
    assert "18532" in output
    assert "Active Project Count" in output
    assert "3" in output


@pytest.mark.asyncio
async def test_scenario_short_circuit_reload_when_no_active_daemon(tmp_path: Path, monkeypatch):
    """
    Scenario: Short-circuit reload when no active daemon is running
      Given no active orchestrator daemon registered or daemon PID does not exist according to psutil.pid_exists
      When the operator executes 'orchestrator config reload'
      Then the command must register the reload signal without waiting 2.0s
      And it must output that the signal is queued for the next daemon startup.
    """
    from orchestrator.cli import _reload_daemon

    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\n",
        encoding="utf-8",
    )
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Case A: No daemon registered in DB at all
    captured_lines = []
    monkeypatch.setattr(
        "orchestrator.cli.console.print",
        lambda *args, **kwargs: captured_lines.append(" ".join(str(a) for a in args)),
    )

    start_time = time.time()
    await _reload_daemon(config_file)
    elapsed = time.time() - start_time

    assert elapsed < 0.5  # Short-circuited without waiting 2.0s
    assert await state_manager.is_reload_requested() is True  # Signal registered
    output = "\n".join(captured_lines)
    assert "queued for the next daemon startup" in output

    # Clear request
    await state_manager.clear_reload_request()

    # Case B: Dead PID in DB (PID exists in DB but not in OS according to psutil.pid_exists)
    dead_pid = 9999999
    await state_manager.register_daemon(dead_pid)
    monkeypatch.setattr("psutil.pid_exists", lambda p: False)

    captured_lines.clear()
    start_time = time.time()
    await _reload_daemon(config_file)
    elapsed = time.time() - start_time

    assert elapsed < 0.5  # Short-circuited without waiting 2.0s
    assert await state_manager.is_reload_requested() is True  # Signal registered
    output = "\n".join(captured_lines)
    assert "queued for the next daemon startup" in output


@pytest.mark.asyncio
async def test_scenario_single_owner_daemon_reload_watcher_and_worker_race_elimination(tmp_path: Path):
    """
    Scenario: Single-owner daemon reload watcher and worker race elimination
      Given an active daemon running multiple project worker loops
      When a reload request is signaled
      Then only the dedicated 1.0s '_daemon_reload_watcher' task must consume 'reload_requested' and call 'hot_reload_runtime'
      And worker loops in '_project_worker_loop' must never call 'hot_reload_runtime' or clear reload flags
      And workers must read the updated configuration from the shared 'ConfigHolder'.
    """
    from orchestrator.cli import _daemon_reload_watcher, _project_worker_loop
    from unittest.mock import AsyncMock, patch

    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\n  poll_interval_seconds: 100\n"
        "projects:\n"
        "  - name: worker-proj-1\n    repo: org/p1\n    local_path: .\n"
        "  - name: worker-proj-2\n    repo: org/p2\n    local_path: .\n",
        encoding="utf-8",
    )
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    initial_config = load_config(config_file)
    holder = ConfigHolder(initial_config)

    # Track calls to hot_reload_runtime and run_project_cycle
    hot_reload_calls = []
    observed_worker_configs = []

    def tracking_hot_reload(path):
        hot_reload_calls.append(path)
        # Return updated config with new poll interval
        cfg = load_config(path)
        cfg.settings.poll_interval_seconds = 42
        return cfg

    async def mock_run_project_cycle(proj, cfg, sm, silent_idle=False):
        # Record the config seen by the worker loop
        observed_worker_configs.append(cfg.settings.poll_interval_seconds)
        # Signal stop after first cycle to terminate worker loop cleanly
        await sm.request_stop()
        return False

    with patch("orchestrator.cli.hot_reload_runtime", side_effect=tracking_hot_reload), \
         patch("orchestrator.cli.run_project_cycle", side_effect=mock_run_project_cycle):

        # Signal a reload request
        await state_manager.request_reload()
        assert await state_manager.is_reload_requested() is True

        # Run 1 cycle of watcher
        watcher_task = asyncio.create_task(
            _daemon_reload_watcher(holder, state_manager, config_file, interval_seconds=0.01)
        )
        # Allow watcher to process reload
        for _ in range(50):
            if not await state_manager.is_reload_requested():
                break
            await asyncio.sleep(0.05)
        watcher_task.cancel()

        # Verify only watcher consumed reload_requested and called hot_reload_runtime
        assert len(hot_reload_calls) == 1
        assert await state_manager.is_reload_requested() is False
        assert holder.config.settings.poll_interval_seconds == 42

        # Reset stop flag and run two worker loops
        await state_manager.clear_stop_request()
        p1 = holder.get_project("worker-proj-1")
        p2 = holder.get_project("worker-proj-2")

        worker1 = asyncio.create_task(_project_worker_loop(p1, holder, state_manager, interval=1))
        worker2 = asyncio.create_task(_project_worker_loop(p2, holder, state_manager, interval=1))
        await asyncio.gather(worker1, worker2)

        # hot_reload_runtime must NOT have been called by workers
        assert len(hot_reload_calls) == 1

        # Both workers must have read the updated config (poll_interval_seconds == 42) from ConfigHolder
        assert 42 in observed_worker_configs


@pytest.mark.asyncio
async def test_scenario_watcher_failsafe_and_path_pinning(tmp_path: Path):
    """
    Scenario: Watcher fail-safe and path pinning
      Given resolved config path pinned once at daemon boot and declared as 'resolved_path: Optional[Path] = None' on GlobalConfig
      When a reload encounters a malformed config or syntax error
      Then the watcher must record 'last_reload_status='FAILED'' in 'daemon_control'
      And the shared 'ConfigHolder' must retain the previous valid configuration
      And the watcher must clear 'reload_requested' to prevent infinite reload loops.
    """
    from orchestrator.cli import _daemon_reload_watcher

    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_file.write_text(
        f"settings:\n  db_path: '{db_file.as_posix()}'\nprojects:\n  - name: valid-proj\n    repo: o/p\n    local_path: .\n",
        encoding="utf-8",
    )
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Initial valid config pinned at boot
    initial_config = load_config(config_file)
    assert initial_config.resolved_path == config_file.resolve()
    holder = ConfigHolder(initial_config)

    # Corrupt config file with malformed syntax
    config_file.write_text("projects: [invalid: yaml: syntax: error\n", encoding="utf-8")

    # Signal reload request
    await state_manager.request_reload()
    assert await state_manager.is_reload_requested() is True

    # Run watcher for one tick
    watcher_task = asyncio.create_task(
        _daemon_reload_watcher(holder, state_manager, config_file, interval_seconds=0.01)
    )
    for _ in range(50):
        if not await state_manager.is_reload_requested():
            break
        await asyncio.sleep(0.05)
    watcher_task.cancel()

    # Then watcher must record last_reload_status='FAILED' in daemon_control
    daemon_info = await state_manager.get_daemon_info()
    assert daemon_info.get("last_reload_status") == "FAILED"

    # And shared ConfigHolder must retain the previous valid configuration
    assert holder.config == initial_config
    assert len(holder.config.projects) == 1
    assert holder.config.projects[0].name == "valid-proj"

    # And watcher must clear reload_requested to prevent infinite loops
    assert await state_manager.is_reload_requested() is False

