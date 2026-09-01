from __future__ import annotations

from pathlib import Path
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.reloader import hot_reload_runtime

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
