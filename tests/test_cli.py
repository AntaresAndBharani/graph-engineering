from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from typer.testing import CliRunner
from orchestrator.cli import app, run_project_cycle
from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
from orchestrator.db import StateManager

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


def test_cli_init(tmp_path, monkeypatch):
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
    async def mock_sync(repo, managed_labels):
        return {lbl.name: True for lbl in managed_labels}
    monkeypatch.setattr("orchestrator.cli.sync_repository_labels", mock_sync)

    result = runner.invoke(app, ["init", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "Initializing Graph Orchestrator" in result.stdout
    assert "SQLite WAL State Database initialized" in result.stdout


def test_cli_labels_help():
    result = runner.invoke(app, ["labels", "--help"])
    assert result.exit_code == 0
    assert "Provisions and synchronizes workflow taxonomy labels" in result.stdout


@pytest.mark.asyncio
async def test_run_project_cycle_idle(tmp_path: Path, monkeypatch):
    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Precondition: architecture plane is already synced
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", project.repo)

    async def mock_issues(repo, label):
        return []
    async def mock_prs(repo, label):
        return []
    monkeypatch.setattr("orchestrator.poller.fetch_issues_with_label", mock_issues)
    monkeypatch.setattr("orchestrator.poller.fetch_open_prs", mock_prs)

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

    async def mock_exec(*cmd, **kwargs):
        class MockProc:
            returncode = 0
            async def communicate(self):
                return (b"", b"")
            async def wait(self):
                return 0
        return MockProc()

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


def test_run_command_clears_stale_stop_signal(tmp_path: Path, monkeypatch):
    """Asserts that manual orchestrator run clears stale stop_requested flags in state.db."""
    from orchestrator.db import StateManager
    db_path = tmp_path / "state.db"
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "test-proj"
    repo: "org/test-proj"
    local_path: "{posix_path}"
    nodes:
      reviewer:
        enabled: true
        label_trigger: "architect-approved"
        """,
        encoding="utf-8",
    )

    async def mock_prs(repo, label):
        return []
    monkeypatch.setattr("orchestrator.poller.fetch_open_prs", mock_prs)

    async def prep():
        sm = StateManager(db_path)
        await sm.init_db()
        await sm.request_stop()
        assert await sm.is_stop_requested() is True

    asyncio.run(prep())

    result = runner.invoke(app, ["run", "-p", "test-proj", "-n", "reviewer", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "Reviewer: No PRs labeled 'architect-approved'" in result.stdout

    async def verify():
        sm = StateManager(db_path)
        assert await sm.is_stop_requested() is False

    asyncio.run(verify())


@pytest.mark.asyncio
async def test_scenario_disabled_nodes_bypass_execution_dispatch_in_cli_loop(tmp_path: Path, monkeypatch):
    """
    Scenario: Disabled Nodes Bypass Execution Dispatch in CLI Loop
      Given "reviewer" is set to "enabled = false"
      When cli.run_project_cycle processes the project workload
      Then it must not invoke run_reviewer_node
      And it must not allocate Git worktrees or memory buffers for the reviewer node.
    """
    from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
    from orchestrator.db import StateManager
    from orchestrator.cli import run_project_cycle
    import orchestrator.cli as cli_mod
    from orchestrator.worktree import WorktreeManager
    from orchestrator.logging import ProjectLogBufferManager

    invoked_nodes = []

    async def mock_run_supervisor(project, config, state_manager, force=False):
        invoked_nodes.append("supervisor")
        return False, "Supervisor idle"

    async def mock_run_architect(project, config, state_manager):
        invoked_nodes.append("architect")
        return False, "Architect idle"

    async def mock_run_devtest(project, config, state_manager):
        invoked_nodes.append("devtest")
        return False, "DevTest idle"

    async def mock_run_reviewer(project, config, state_manager):
        invoked_nodes.append("reviewer")
        return False, "Reviewer idle"

    async def mock_run_bau(project, config, state_manager, force=False):
        invoked_nodes.append("bau")
        return False, "BAU idle"

    monkeypatch.setattr(cli_mod, "run_supervisor_node", mock_run_supervisor)
    monkeypatch.setattr(cli_mod, "run_architect_node", mock_run_architect)
    monkeypatch.setattr(cli_mod, "run_devtest_node", mock_run_devtest)
    monkeypatch.setattr(cli_mod, "run_reviewer_node", mock_run_reviewer)
    monkeypatch.setattr(cli_mod, "run_bau_node", mock_run_bau)

    allocated_worktrees = []
    async def mock_ensure_worktree(project, node_name, **kwargs):
        allocated_worktrees.append(node_name)
        return project.local_path / f"worktree_{node_name}"

    monkeypatch.setattr(WorktreeManager, "ensure_worktree", mock_ensure_worktree)

    ProjectLogBufferManager.reset()

    # Configure project with reviewer disabled
    project = ProjectConfig(
        name="test-bypass",
        repo="org/test-bypass",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(enabled=True),
            "devtest": NodeConfig(enabled=True),
            "reviewer": NodeConfig(enabled=False),
            "supervisor": NodeConfig(enabled=True),
            "bau": NodeConfig(enabled=True),
        },
    )
    config = GlobalConfig(projects=[project])
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    work_done = await run_project_cycle(project, config, state_manager, silent_idle=True)
    assert work_done is False

    # Reviewer must NOT be invoked
    assert "reviewer" not in invoked_nodes
    assert "reviewer" not in allocated_worktrees

    # Other enabled nodes must be invoked
    assert "supervisor" in invoked_nodes
    assert "architect" in invoked_nodes
    assert "devtest" in invoked_nodes
    assert "bau" in invoked_nodes


@pytest.mark.asyncio
async def test_scenario_all_nodes_disabled_bypasses_all_dispatch(tmp_path: Path, monkeypatch):
    """Asserts that when all nodes are disabled, none are invoked."""
    from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
    from orchestrator.db import StateManager
    from orchestrator.cli import run_project_cycle
    import orchestrator.cli as cli_mod

    invoked_nodes = []

    monkeypatch.setattr(cli_mod, "run_supervisor_node", lambda *a, **kw: invoked_nodes.append("supervisor"))
    monkeypatch.setattr(cli_mod, "run_architect_node", lambda *a, **kw: invoked_nodes.append("architect"))
    monkeypatch.setattr(cli_mod, "run_devtest_node", lambda *a, **kw: invoked_nodes.append("devtest"))
    monkeypatch.setattr(cli_mod, "run_reviewer_node", lambda *a, **kw: invoked_nodes.append("reviewer"))
    monkeypatch.setattr(cli_mod, "run_bau_node", lambda *a, **kw: invoked_nodes.append("bau"))

    project = ProjectConfig(
        name="dormant-proj",
        repo="org/dormant",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(enabled=False),
            "devtest": NodeConfig(enabled=False),
            "reviewer": NodeConfig(enabled=False),
            "supervisor": NodeConfig(enabled=False),
            "bau": NodeConfig(enabled=False),
        },
    )
    config = GlobalConfig(projects=[project])
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    work_done = await run_project_cycle(project, config, state_manager, silent_idle=True)
    assert work_done is False
    assert len(invoked_nodes) == 0


def test_scenario_startup_node_status_registry_table_ux(tmp_path: Path):
    """
    Scenario: Startup Node Status Registry Table UX
      Given the orchestrator daemon initializes
      When the CLI boots up
      Then it must render a formatted Rich table displaying the enabled/disabled status, harness, and concurrency mode of all nodes.
    """
    from rich.console import Console
    import io
    from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
    from orchestrator.cli import render_node_status_table

    project = ProjectConfig(
        name="alpha-project",
        repo="org/alpha-project",
        local_path=str(tmp_path),
        worktrees_enabled=True,
        nodes={
            "architect": NodeConfig(enabled=True, harness="claude", model="sonnet-5"),
            "devtest": NodeConfig(enabled=True, harness="antigravity"),
            "reviewer": NodeConfig(enabled=False, harness="claude"),
            "supervisor": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.7-flash-low"),
            "bau": NodeConfig(enabled=False, harness="antigravity"),
        },
    )
    config = GlobalConfig(projects=[project])

    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, color_system=None, width=120)

    table = render_node_status_table(config, console_out=test_console)
    output = buf.getvalue()

    # Table metadata & headers
    assert table.title == "Autonomous Node Status Registry"
    assert "alpha-project" in output
    assert "architect" in output
    assert "devtest" in output
    assert "reviewer" in output
    assert "supervisor" in output
    assert "bau" in output

    # Statuses
    assert "ENABLED" in output
    assert "DISABLED" in output

    # Harnesses & Concurrency Modes
    assert "claude (sonnet-5)" in output
    assert "antigravity" in output
    assert "Worktree (Concurrent)" in output
    assert "Serial (Gatekeeper)" in output
    assert "Serial (Watchdog)" in output
    assert "Serial (Maintenance)" in output


def test_cli_run_renders_startup_node_status_table(tmp_path: Path, monkeypatch):
    """Verifies that orchestrator run displays the Autonomous Node Status Registry table at startup."""
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "table-proj"
    repo: "org/table-proj"
    local_path: "{posix_path}"
    worktrees_enabled: false
    nodes:
      architect:
        enabled: true
        harness: "claude"
      devtest:
        enabled: false
        harness: "antigravity"
      reviewer:
        enabled: false
      supervisor:
        enabled: true
      bau:
        enabled: false
        """,
        encoding="utf-8",
    )

    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")

    async def mock_issues(repo, label):
        return []
    async def mock_prs(repo, label):
        return []
    async def mock_all_issues(repo):
        return []
    monkeypatch.setattr("orchestrator.poller.fetch_issues_with_label", mock_issues)
    monkeypatch.setattr("orchestrator.poller.fetch_open_prs", mock_prs)
    monkeypatch.setattr("orchestrator.poller.fetch_all_open_issues", mock_all_issues)

    result = runner.invoke(app, ["run", "-p", "table-proj", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "Autonomous Node Status Registry" in result.stdout
    assert "table-proj" in result.stdout
    assert "ENABLED" in result.stdout
    assert "DISABLED" in result.stdout
    assert "Serial (Primary)" in result.stdout


@pytest.mark.asyncio
async def test_scenario_disabled_nodes_bypass_execution_dispatch_in_cli_loop(tmp_path: Path, monkeypatch):
    """
    Scenario: Disabled Nodes Bypass Execution Dispatch in CLI Loop
    Given "reviewer", "supervisor", and "bau" are set to "enabled = false"
    When cli.run_project_cycle processes the project workload
    Then it must not invoke run_reviewer_node, run_supervisor_node, or run_bau_node
    And it must not allocate Git worktrees or memory buffers for disabled nodes.
    """
    from unittest.mock import AsyncMock

    project = ProjectConfig(
        name="test-bypass-proj",
        repo="org/test-bypass",
        local_path=str(tmp_path),
        worktrees_enabled=True,
        nodes={
            "architect": NodeConfig(enabled=True),
            "devtest": NodeConfig(enabled=True),
            "reviewer": NodeConfig(enabled=False),
            "supervisor": NodeConfig(enabled=False),
            "bau": NodeConfig(enabled=False),
        },
    )
    config = GlobalConfig()
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    invoked_nodes: list[str] = []

    async def mock_arch(p, c, sm, **kw):
        invoked_nodes.append("architect")
        return False, "idle"

    async def mock_devtest(p, c, sm, **kw):
        invoked_nodes.append("devtest")
        return False, "idle"

    async def mock_reviewer(p, c, sm, **kw):
        invoked_nodes.append("reviewer")
        return False, "idle"

    async def mock_supervisor(p, c, sm, **kw):
        invoked_nodes.append("supervisor")
        return False, "idle"

    async def mock_bau(p, c, sm, **kw):
        invoked_nodes.append("bau")
        return False, "idle"

    monkeypatch.setattr("orchestrator.cli.run_architect_node", mock_arch)
    monkeypatch.setattr("orchestrator.cli.run_devtest_node", mock_devtest)
    monkeypatch.setattr("orchestrator.cli.run_reviewer_node", mock_reviewer)
    monkeypatch.setattr("orchestrator.cli.run_supervisor_node", mock_supervisor)
    monkeypatch.setattr("orchestrator.cli.run_bau_node", mock_bau)

    await run_project_cycle(project, config, state_manager, silent_idle=True)

    # Active enabled nodes were evaluated
    assert "architect" in invoked_nodes
    assert "devtest" in invoked_nodes

    # Disabled nodes were strictly bypassed
    assert "reviewer" not in invoked_nodes
    assert "supervisor" not in invoked_nodes
    assert "bau" not in invoked_nodes






