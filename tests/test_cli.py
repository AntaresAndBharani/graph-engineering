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

    from unittest.mock import AsyncMock, MagicMock
    calls = []

    async def mock_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

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


def test_cli_quota_help():
    result = runner.invoke(app, ["quota", "--help"])
    assert result.exit_code == 0
    assert "Harness token quota, velocity, and runway visibility" in result.stdout
    assert "status" in result.stdout


def test_cli_quota_status_table(tmp_path: Path):
    """
    Scenario 1: quota status displays formatted table
    Given multiple harnesses have recorded usage
    When "orchestrator quota status" is run
    Then a Rich table is printed with window limits, current usage, velocity, and countdowns for each configured harness
    """
    from orchestrator.db import StateManager

    config_file = tmp_path / "config.yaml"
    db_path = tmp_path / "state.db"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
quota:
  buffer_minutes: 30
  harnesses:
    antigravity:
      window_hours: 1.0
      window_token_limit: 2000000
      avg_tokens_per_hour: 400000
    claude:
      window_hours: 5.0
      window_token_limit: 5000000
      avg_tokens_per_hour: 300000
""",
        encoding="utf-8",
    )

    # Seed recorded usage across multiple harnesses
    async def seed_usage():
        sm = StateManager(db_path)
        await sm.init_db()
        # Seed antigravity usage (within capacity)
        await sm.record_token_usage_event(
            harness_name="antigravity",
            model_name="gemini-3.7-flash",
            project_name="graph-engineering",
            node_name="devtest",
            issue_number=10,
            prompt_tokens=500000,
            completion_tokens=100000,
            total_tokens=600000,
        )
        # Seed claude usage (exhausting capacity / throttled)
        await sm.record_token_usage_event(
            harness_name="claude",
            model_name="claude-sonnet-5",
            project_name="graph-engineering",
            node_name="architect",
            issue_number=11,
            prompt_tokens=4000000,
            completion_tokens=900000,
            total_tokens=4900000,
        )

    asyncio.run(seed_usage())

    result = runner.invoke(app, ["quota", "status", "--config", str(config_file)], env={"COLUMNS": "160"})
    assert result.exit_code == 0
    assert "Harness Token Quota & Capacity Status" in result.stdout
    assert "antigravity" in result.stdout
    assert "claude" in result.stdout
    assert "1.0h" in result.stdout
    assert "5.0h" in result.stdout
    assert "600,000" in result.stdout
    assert "4,900" in result.stdout
    assert "tok/h" in result.stdout
    assert "100.0%" in result.stdout
    assert "OK" in result.stdout
    assert "THROT" in result.stdout
    assert "Ready" in result.stdout


def test_cli_quota_status_filter(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
quota:
  harnesses:
    antigravity:
      window_hours: 1.0
      window_token_limit: 2000000
      avg_tokens_per_hour: 400000
    claude:
      window_hours: 5.0
      window_token_limit: 5000000
      avg_tokens_per_hour: 300000
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["quota", "status", "--config", str(config_file), "--harness", "claude"])
    assert result.exit_code == 0
    assert "claude" in result.stdout
    assert "antigravity" not in result.stdout


def test_one_shot_cli_exit_code_2(tmp_path: Path, monkeypatch):
    """
    Scenario 2: One-shot CLI run fails fast on insufficient quota (Issue #56)
    Given the operator runs "orchestrator run -p graph-engineering --issue 42"
    And the target node's harness lacks the required 30-minute token runway
    When pre-flight validation executes
    Then the CLI prints a descriptive error detailing the quota deficit and replenishment countdown
    And exits immediately with exit code 2
    And no GitHub issue state is mutated
    """
    from orchestrator.db import StateManager

    config_file = tmp_path / "config.yaml"
    db_path = tmp_path / "state.db"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
quota:
  buffer_minutes: 30
  harnesses:
    antigravity:
      window_hours: 1.0
      window_token_limit: 2000000
      avg_tokens_per_hour: 400000
projects:
  - name: "graph-engineering"
    repo: "AntaresAndBharani/graph-engineering"
    local_path: "{posix_path}"
    nodes:
      devtest:
        enabled: true
        harness: "antigravity"
""",
        encoding="utf-8",
    )

    # Pre-populate DB with usage that exhausts the quota runway (remaining < 200k)
    async def seed_usage():
        sm = StateManager(db_path)
        await sm.init_db()
        await sm.record_token_usage_event(
            harness_name="antigravity",
            model_name="gemini-3.7-flash",
            project_name="graph-engineering",
            node_name="devtest",
            issue_number=1,
            prompt_tokens=1500000,
            completion_tokens=450000,
            total_tokens=1950000,
        )

    asyncio.run(seed_usage())

    mutations = []

    async def mock_run_project_cycle(*args, **kwargs):
        mutations.append("ran_project_cycle")
        return False

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_project_cycle)

    result = runner.invoke(
        app,
        ["run", "-p", "graph-engineering", "--issue", "42", "--config", str(config_file)],
    )
    assert result.exit_code == 2
    assert "Pre-flight Quota Gate Failed" in result.stdout
    assert "antigravity" in result.stdout
    assert "Quota Deficit:" in result.stdout
    assert "Replenishment:" in result.stdout
    assert "Ready in" in result.stdout
    # Verify no downstream project cycle or mutation executed
    assert len(mutations) == 0


def test_one_shot_cli_quota_sufficient_passthrough(tmp_path: Path, monkeypatch):
    """
    Scenario 3: One-shot CLI run proceeds when quota is sufficient (Issue #56)
    Given the target node's harness has sufficient runway
    When "orchestrator run" pre-flight validation executes
    Then the harness dispatch proceeds normally
    """
    from orchestrator.db import StateManager

    config_file = tmp_path / "config.yaml"
    db_path = tmp_path / "state.db"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
quota:
  buffer_minutes: 30
  harnesses:
    antigravity:
      window_hours: 1.0
      window_token_limit: 2000000
      avg_tokens_per_hour: 400000
projects:
  - name: "graph-engineering"
    repo: "AntaresAndBharani/graph-engineering"
    local_path: "{posix_path}"
    nodes:
      devtest:
        enabled: true
        harness: "antigravity"
""",
        encoding="utf-8",
    )

    dispatched = []

    async def mock_run_project_cycle(*args, **kwargs):
        dispatched.append(True)
        return False

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_project_cycle)

    result = runner.invoke(
        app,
        ["run", "-p", "graph-engineering", "--issue", "42", "--config", str(config_file)],
    )
    assert result.exit_code == 0
    assert "Pre-flight Quota Gate Failed" not in result.stdout
    assert len(dispatched) == 1





