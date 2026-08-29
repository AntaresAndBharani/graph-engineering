from __future__ import annotations

from pathlib import Path
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app, run_project_cycle
from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager

runner = CliRunner()


@pytest.mark.asyncio
async def test_project_pause_db_lifecycle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    assert await state_manager.is_project_paused("test-proj") is False
    assert await state_manager.get_paused_projects() == set()

    await state_manager.pause_project("test-proj")
    assert await state_manager.is_project_paused("test-proj") is True
    assert await state_manager.get_paused_projects() == {"test-proj"}

    await state_manager.resume_project("test-proj")
    assert await state_manager.is_project_paused("test-proj") is False
    assert await state_manager.get_paused_projects() == set()


@pytest.mark.asyncio
async def test_run_project_cycle_skips_paused_project(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="paused-proj",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(enabled=True, harness="claude"),
        },
    )

    # Precondition: architecture plane is already synced
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", project.repo)

    ran = await run_project_cycle(project, GlobalConfig(), state_manager, silent_idle=True)
    assert isinstance(ran, bool)

    await state_manager.pause_project("paused-proj")
    ran_paused = await run_project_cycle(project, GlobalConfig(), state_manager)
    assert ran_paused is False


def test_cli_pause_and_resume_commands(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_content = f"""
version: 2
settings:
  db_path: "{db_file.as_posix()}"
  log_dir: "{(tmp_path / 'logs').as_posix()}"
projects:
  - name: "my-app"
    repo: "org/my-app"
    local_path: "{tmp_path.as_posix()}"
    enabled: true
"""
    config_file.write_text(config_content, encoding="utf-8")

    # 1. Pause project
    res_pause = runner.invoke(app, ["pause", "my-app", "--config", str(config_file)])
    assert res_pause.exit_code == 0
    assert "PAUSED" in res_pause.stdout

    # 2. Check list output
    res_list = runner.invoke(app, ["list", "--config", str(config_file)])
    assert res_list.exit_code == 0
    assert "Paused" in res_list.stdout

    # 3. Resume project
    res_resume = runner.invoke(app, ["resume", "my-app", "--config", str(config_file)])
    assert res_resume.exit_code == 0
    assert "RESUMED" in res_resume.stdout

    # 4. Check list output again
    res_list2 = runner.invoke(app, ["list", "--config", str(config_file)])
    assert res_list2.exit_code == 0
    assert "Active" in res_list2.stdout

    # 5. Stop with --project alias
    res_stop_p = runner.invoke(app, ["stop", "--project", "my-app", "--config", str(config_file)])
    assert res_stop_p.exit_code == 0
    assert "PAUSED" in res_stop_p.stdout


def test_cli_pause_unknown_project(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "state.db"
    config_content = f"""
version: 2
settings:
  db_path: "{db_file.as_posix()}"
  log_dir: "{(tmp_path / 'logs').as_posix()}"
projects:
  - name: "my-app"
    repo: "org/my-app"
    local_path: "{tmp_path.as_posix()}"
    enabled: true
"""
    config_file.write_text(config_content, encoding="utf-8")

    res = runner.invoke(app, ["pause", "unknown-app", "--config", str(config_file)])
    assert res.exit_code == 1
    assert "Error:" in res.stdout
