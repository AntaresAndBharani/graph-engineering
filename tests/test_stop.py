from __future__ import annotations

from pathlib import Path
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app, run_project_cycle
from orchestrator.config import GlobalConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter

runner = CliRunner()


@pytest.mark.asyncio
async def test_daemon_registration_and_stop_lifecycle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Initial state: stop not requested
    assert await state_manager.is_stop_requested() is False

    # Register daemon
    await state_manager.register_daemon(12345)
    info = await state_manager.get_daemon_info()
    assert info.get("status") == "RUNNING"
    assert info.get("pid") == "12345"
    assert info.get("stop_requested") == "0"
    assert await state_manager.is_stop_requested() is False

    # Request stop
    pid = await state_manager.request_stop()
    assert pid == 12345
    assert await state_manager.is_stop_requested() is True
    info_stopped = await state_manager.get_daemon_info()
    assert info_stopped.get("status") == "STOP_REQUESTED"
    assert info_stopped.get("stop_requested") == "1"

    # Clear stop
    await state_manager.clear_stop_request()
    assert await state_manager.is_stop_requested() is False

    # Unregister daemon
    await state_manager.unregister_daemon()
    info_unregistered = await state_manager.get_daemon_info()
    assert info_unregistered.get("status") == "STOPPED"
    assert "pid" not in info_unregistered


@pytest.mark.asyncio
async def test_run_project_cycle_aborts_when_stop_requested(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))

    # Request stop
    await state_manager.request_stop()

    # Cycle should abort immediately and return False
    ran = await run_project_cycle(project, config, state_manager, silent_idle=True)
    assert ran is False


def test_cli_stop_command(tmp_path: Path):
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
    local_path: "."
        """,
        encoding="utf-8",
    )

    result = runner.invoke(app, ["stop", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "Safe stop signal registered" in result.stdout


def test_cli_stop_help():
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "Gracefully halts the running background daemon" in result.stdout
    assert "--force" in result.stdout


def test_harness_terminate_all_active():
    # Calling terminate_all_active when no processes are running returns 0 safely
    count = AsyncHarnessAdapter.terminate_all_active()
    assert count == 0
