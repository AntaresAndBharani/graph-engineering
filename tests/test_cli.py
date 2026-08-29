from __future__ import annotations

import asyncio
from typer.testing import CliRunner
from orchestrator.cli import app

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Graph Orchestrator" in result.stdout


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Decoupled, Agnostic Multi-Agent CLI Orchestrator" in result.stdout
    assert "run" in result.stdout
    assert "watch" in result.stdout
    assert "list" in result.stdout
    assert "doctor" in result.stdout


def test_cli_list(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
version: 2
projects:
  - name: "alpha"
    repo: "org/alpha"
    local_path: "."
        """,
        encoding="utf-8",
    )
    result = runner.invoke(app, ["list", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "org/alpha" in result.stdout


def test_cli_doctor(tmp_path):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Orchestrator System Diagnostics" in result.stdout


def test_cli_init(tmp_path):
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
    result = runner.invoke(app, ["init", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "Initializing Graph Orchestrator" in result.stdout
    assert "SQLite WAL State Database initialized" in result.stdout


def test_cli_labels_help():
    result = runner.invoke(app, ["labels", "--help"])
    assert result.exit_code == 0
    assert "Provisions and synchronizes workflow taxonomy labels" in result.stdout


import pytest
from pathlib import Path
from orchestrator.config import GlobalConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.cli import run_project_cycle


@pytest.mark.asyncio
async def test_run_project_cycle_idle(tmp_path: Path):
    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Precondition: architecture plane is already synced
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", project.repo)

    # When no issues or PRs exist, all nodes report idle (0 tokens) -> False
    work_done = await run_project_cycle(project, config, state_manager, silent_idle=True)
    assert work_done is False


def test_cli_supervisor_help():
    result = runner.invoke(app, ["supervisor", "--help"])
    assert result.exit_code == 0
    assert "PO-proxy Supervisor" in result.stdout
    assert "evaluate" in result.stdout
    assert "status" in result.stdout


def test_cli_supervisor_evaluate_dry_run(tmp_path: Path, monkeypatch):
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

    async def mock_fetch_issue(repo, issue_id):
        return {
            "number": issue_id,
            "title": "feat: login flow",
            "body": "User can log in using email and password.\n\n## Acceptance Criteria\nGiven valid credentials",
            "labels": [{"name": "needs-po-review"}],
        }

    monkeypatch.setattr("orchestrator.poller.fetch_issue_by_number", mock_fetch_issue)

    calls = []

    async def mock_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        proc = asyncio.subprocess.Process()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    result = runner.invoke(app, ["supervisor", "evaluate", "15", "-p", "alpha", "--dry-run", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "PO-Proxy Supervisor Evaluation" in result.stdout
    assert "DRY-RUN" in result.stdout
    assert "PO_APPROVED" in result.stdout
    assert "Generated Gherkin Acceptance Criteria" in result.stdout

    # Assert no mutation calls (gh issue edit / gh issue comment)
    mutation_calls = [c for c in calls if len(c) >= 3 and c[0] == "gh" and c[1] == "issue" and c[2] in ("edit", "comment")]
    assert len(mutation_calls) == 0


def test_cli_supervisor_evaluate_live(tmp_path: Path, monkeypatch):
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

    async def mock_fetch_issue(repo, issue_id):
        return {
            "number": issue_id,
            "title": "feat: registration flow",
            "body": "User can register.\n\n## Acceptance Criteria\nGiven valid email",
            "labels": [{"name": "needs-po-review"}],
        }

    monkeypatch.setattr("orchestrator.poller.fetch_issue_by_number", mock_fetch_issue)

    result = runner.invoke(app, ["supervisor", "evaluate", "20", "-p", "alpha", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "LIVE" in result.stdout
    assert "PO_APPROVED" in result.stdout

    # Verify po_tracking row is created in SQLite state DB
    manager = StateManager(tmp_path / "state.db")
    async def check_db():
        await manager.init_db()
        record = await manager.get_po_tracking("org/alpha", 20)
        assert record is not None
        assert record["status"] == "PO_APPROVED"
        assert record["issue_number"] == 20
    asyncio.run(check_db())


def test_cli_supervisor_status(tmp_path: Path):
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

    # Empty status
    res_empty = runner.invoke(app, ["supervisor", "status", "-p", "alpha", "--config", str(config_file)])
    assert res_empty.exit_code == 0
    assert "No issues currently tracked" in res_empty.stdout

    # Seed records into po_tracking
    manager = StateManager(tmp_path / "state.db")
    async def seed():
        await manager.init_db()
        await manager.upsert_po_tracking(
            repo="org/alpha",
            issue_number=15,
            body_hash="sha256-abc",
            status="PO_APPROVED",
            gherkin_ac="Feature: Test",
            blockers=None,
        )
        await manager.upsert_po_tracking(
            repo="org/alpha",
            issue_number=16,
            body_hash="sha256-def",
            status="NEEDS_HUMAN_CLARIFICATION",
            gherkin_ac=None,
            blockers="Ambiguous payment provider",
        )
    asyncio.run(seed())

    res_table = runner.invoke(app, ["supervisor", "status", "-p", "alpha", "--config", str(config_file)])
    assert res_table.exit_code == 0
    assert "PO-Proxy Blackboard Tracking" in res_table.stdout
    assert "15" in res_table.stdout
    assert "16" in res_table.stdout
    assert "PO_APPROVED" in res_table.stdout


def test_cli_watch_headless_fallback(tmp_path: Path, monkeypatch):
    """Asserts watch_command with dashboard disabled never imports orchestrator.ui.dashboard."""
    import sys
    # Ensure orchestrator.ui.dashboard is not imported beforehand
    sys.modules.pop("orchestrator.ui.dashboard", None)
    sys.modules.pop("orchestrator.ui", None)

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

    called_headless = []

    async def mock_headless(interval, config_path):
        called_headless.append(True)

    monkeypatch.setattr("orchestrator.cli._watch_daemon_headless", mock_headless)

    # Invoke watch with --no-dashboard
    result = runner.invoke(app, ["watch", "--no-dashboard", "--config", str(config_file)])
    assert result.exit_code == 0
    assert len(called_headless) == 1
    assert "orchestrator.ui.dashboard" not in sys.modules


def test_cli_watch_headless_flag(tmp_path: Path, monkeypatch):
    """Asserts --headless flag invokes headless mode."""
    called_headless = []

    async def mock_headless(interval, config_path):
        called_headless.append(True)

    monkeypatch.setattr("orchestrator.cli._watch_daemon_headless", mock_headless)

    result = runner.invoke(app, ["watch", "--headless"])
    assert result.exit_code == 0
    assert len(called_headless) == 1



