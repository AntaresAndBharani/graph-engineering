from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from typer.testing import CliRunner
from orchestrator.cli import app, run_project_cycle
from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
from orchestrator.db import StateManager
from orchestrator.logging import strip_ansi

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
    test_console = Console(file=buf, force_terminal=False, color_system=None, width=140)

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

    # Harnesses & Concurrency Modes & Agent Models
    assert "claude" in output
    assert "sonnet-5" in output
    assert "gemini-3.7-flash-low" in output
    assert "antigravity" in output
    assert "Worktree (Concurrent)" in output
    assert "Serial (Gatekeeper)" in output
    assert "Serial (Watchdog)" in output
    assert "Serial (Maintenance)" in output

    # 7th Column verification
    assert len(table.columns) == 7
    assert table.columns[6].header == "Agent Model"


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

    async def mock_issues(repo, *args, **kwargs):
        return []
    async def mock_prs(repo, *args, **kwargs):
        return []
    async def mock_all_issues(repo, *args, **kwargs):
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


def test_scenario_clean_harness_agnostic_agent_and_effort_representation():
    """
    Scenario: Clean harness-agnostic agent and effort representation
      Given a model string and optional effort
      When "format_node_agent_spec(model, effort)" is invoked
      Then it must return "<model> (<effort>)" when effort is non-empty
      And it must return "<model>" when effort is None or empty, with zero harness-specific branching or hardcoded harness names.
    """
    from orchestrator.cli import format_node_agent_spec

    # Effort is non-empty -> <model> (<effort>)
    assert format_node_agent_spec("claude-sonnet-5", "medium") == "claude-sonnet-5 (medium)"
    assert format_node_agent_spec("claude-opus-4", "high") == "claude-opus-4 (high)"
    assert format_node_agent_spec("custom-model-x", "low") == "custom-model-x (low)"

    # Effort is None or empty -> <model>
    assert format_node_agent_spec("gemini-3.8-flash-high", None) == "gemini-3.8-flash-high"
    assert format_node_agent_spec("gemini-3.8-flash-high", "") == "gemini-3.8-flash-high"
    assert format_node_agent_spec("gemini-3.8-flash-high", "   ") == "gemini-3.8-flash-high"
    assert format_node_agent_spec("claude-sonnet-5", None) == "claude-sonnet-5"
    assert format_node_agent_spec("claude-sonnet-5", "") == "claude-sonnet-5"

    # Whitespace cleanup
    assert format_node_agent_spec("  claude-sonnet-5  ", "  medium  ") == "claude-sonnet-5 (medium)"
    assert format_node_agent_spec("  gemini-3.8-flash-high  ", None) == "gemini-3.8-flash-high"


def test_scenario_idle_and_none_model_fallback():
    """
    Scenario: Idle and None model fallback
      Given a node with no model configured or in an idle state
      When "format_node_agent_spec(model, effort)" is invoked
      Then it must return "—".
    """
    from orchestrator.cli import format_node_agent_spec

    # Model is None
    assert format_node_agent_spec(None, None) == "—"
    assert format_node_agent_spec(None, "medium") == "—"

    # Model is empty string or pure whitespace
    assert format_node_agent_spec("", None) == "—"
    assert format_node_agent_spec("   ", None) == "—"
    assert format_node_agent_spec("", "high") == "—"
    assert format_node_agent_spec("   ", "medium") == "—"


def test_scenario_cli_table_agent_model_surfacing_render_node_status_table(tmp_path: Path):
    """
    Scenario: CLI table agent model surfacing in render_node_status_table
      Given configured projects in "config.yaml"
      When the operator checks "render_node_status_table"
      Then the table must render an explicit 7th column titled "Agent Model"
      And active nodes must display the formatted model and effort string
      And idle rows must display "—".
    """
    from rich.console import Console
    import io
    from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
    from orchestrator.cli import render_node_status_table

    project = ProjectConfig(
        name="status-proj",
        repo="org/status-proj",
        local_path=str(tmp_path),
        worktrees_enabled=True,
        nodes={
            "architect": NodeConfig(enabled=True, harness="claude", model="claude-sonnet-5", effort="medium"),
            "devtest": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.8-flash-high", effort=None),
            "reviewer": NodeConfig(enabled=True, harness="claude", model=None),  # Active node, no model configured
            "supervisor": NodeConfig(enabled=False, harness="antigravity", model="gemini-3.7-flash-low"),  # Disabled / idle node
            "bau": NodeConfig(enabled=False, harness="antigravity"),  # Disabled / idle node
        },
    )
    config = GlobalConfig(projects=[project])

    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, color_system=None, width=140)

    table = render_node_status_table(config, console_out=test_console)
    output = buf.getvalue()

    # Explicit 7th column titled "Agent Model"
    assert len(table.columns) == 7
    assert table.columns[6].header == "Agent Model"
    assert "Agent Model" in output

    # Active nodes display formatted model and effort string
    assert "claude-sonnet-5 (medium)" in output
    assert "gemini-3.8-flash-high" in output

    # Idle / unassigned nodes display "—"
    assert "—" in output


def test_scenario_cli_table_agent_model_surfacing_orchestrator_list(tmp_path: Path):
    """
    Scenario: CLI table agent model surfacing in orchestrator list
      Given configured projects in "config.yaml"
      When the operator executes "orchestrator list"
      Then the table must render an explicit 7th column titled "Agent Model"
      And active nodes must display the formatted model and effort string
      And idle rows must display "—".
    """
    import asyncio
    from orchestrator.cli import _list_projects, app
    from orchestrator.db import StateManager

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    asyncio.run(state_manager.init_db())

    # Register an active running job for 'active-proj' devtest node
    asyncio.run(
        state_manager.acquire_lock(
            issue_id=42,
            repo="org/active-proj",
            node_type="devtest",
            ttl_minutes=30,
        )
    )

    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "active-proj"
    repo: "org/active-proj"
    local_path: "{posix_path}"
    enabled: true
    nodes:
      architect:
        enabled: true
        harness: "claude"
        model: "claude-sonnet-5"
        effort: "medium"
      devtest:
        enabled: true
        harness: "antigravity"
        model: "gemini-3.8-flash-high"
  - name: "idle-proj"
    repo: "org/idle-proj"
    local_path: "{posix_path}"
    enabled: true
    nodes:
      architect:
        enabled: true
        harness: "claude"
        model: "claude-sonnet-5"
  - name: "disabled-proj"
    repo: "org/disabled-proj"
    local_path: "{posix_path}"
    enabled: false
    nodes:
      architect:
        enabled: true
        harness: "claude"
        """,
        encoding="utf-8",
    )

    # 1. Verify programmatically via _list_projects
    table = asyncio.run(_list_projects(config_file))
    assert len(table.columns) == 7
    assert table.columns[6].header == "Agent Model"

    # 2. Verify via CLI runner invoke with adequate terminal width
    result = runner.invoke(app, ["list", "--config", str(config_file)], env={"COLUMNS": "140"})
    assert result.exit_code == 0
    assert "Agent Model" in result.stdout
    # Active node with running devtest job displays formatted model
    assert "gemini-3.8-flash-high" in result.stdout
    # Idle and disabled projects display "—"
    assert "—" in result.stdout


def test_cli_labels_passes_state_manager(tmp_path: Path, monkeypatch):
    """
    Verify orchestrator labels command provides state_manager to sync_repository_labels.
    """
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
    enabled: true
        """,
        encoding="utf-8",
    )

    passed_kwargs = {}

    async def mock_sync(repo, labels, purge_legacy=True, *, state_manager=None):
        passed_kwargs["repo"] = repo
        passed_kwargs["state_manager"] = state_manager
        return {lbl.name: True for lbl in labels}

    monkeypatch.setattr("orchestrator.cli.sync_repository_labels", mock_sync)

    result = runner.invoke(app, ["labels", "--config", str(config_file)])
    assert result.exit_code == 0
    assert passed_kwargs.get("repo") == "org/alpha"
    assert isinstance(passed_kwargs.get("state_manager"), StateManager)


@pytest.mark.asyncio
async def test_cli_watch_headless_nonblocking_startup(tmp_path: Path, monkeypatch):
    """
    Verify orchestrator watch in headless mode launches background sync task
    and wires sync_events to project worker loops.
    """
    from orchestrator.cli import _watch_daemon_headless

    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
  poll_interval_seconds: 5
projects:
  - name: "alpha"
    repo: "org/alpha"
    local_path: "{posix_path}"
    enabled: true
        """,
        encoding="utf-8",
    )

    sync_started = asyncio.Event()
    worker_started = asyncio.Event()
    received_sync_event = []

    async def mock_sync_all(projects, labels, *, state_manager=None, concurrency=4, sync_events=None):
        sync_started.set()
        # Simulate non-blocking work
        await asyncio.sleep(0.05)
        if sync_events and "alpha" in sync_events:
            sync_events["alpha"].set()
        return {"org/alpha": {lbl.name: True for lbl in labels}}

    async def mock_worker_loop(project, config, state_mgr, interval, config_path=None, sync_event=None):
        received_sync_event.append(sync_event)
        worker_started.set()
        # Request daemon stop so watch loop finishes
        await state_mgr.request_stop()

    monkeypatch.setattr("orchestrator.cli.sync_all_projects_labels", mock_sync_all)
    monkeypatch.setattr("orchestrator.cli._project_worker_loop", mock_worker_loop)

    await _watch_daemon_headless(interval_override=5, config_path=config_file)

    assert sync_started.is_set()
    assert worker_started.is_set()
    assert len(received_sync_event) == 1
    assert isinstance(received_sync_event[0], asyncio.Event)


def test_cli_start_help():
    result = runner.invoke(app, ["start", "--help"])
    assert result.exit_code == 0
    clean_stdout = strip_ansi(result.stdout)
    assert "Executes a dedicated project node lifecycle" in clean_stdout
    assert "project_name" in clean_stdout.lower()
    assert "--node" in clean_stdout
    assert "--max-passes" in clean_stdout


def test_cli_start_invalid_node(tmp_path):
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
    result = runner.invoke(app, ["start", "alpha", "-n", "architect", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "only supports node 'devtest'" in result.stdout


def test_cli_start_project_not_found(tmp_path):
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
    result = runner.invoke(app, ["start", "nonexistent", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "Project 'nonexistent' not found" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_refuses_when_global_stop_active(tmp_path):
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
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.request_stop()

    result = runner.invoke(app, ["start", "alpha", "-n", "devtest", "--config", str(config_file)])
    assert result.exit_code == 1
    assert "Stop requested or global stop active" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_lifecycle_pid_tracking(tmp_path, monkeypatch):
    import os
    import aiosqlite
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
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.register_daemon(88888)

    observed_pid = []
    observed_daemon_pid = []

    async def mock_run_cycle(project, config, state_mgr, node_name=None, silent_idle=False):
        l_pid = await state_mgr.get_lifecycle_pid()
        observed_pid.append(l_pid)
        async with aiosqlite.connect(state_mgr.db_path) as db:
            cursor = await db.execute("SELECT value FROM daemon_control WHERE key = 'pid';")
            row = await cursor.fetchone()
            observed_daemon_pid.append(row[0] if row else None)
        return False

    async def mock_drained(project, state_mgr, node_name=None):
        return True

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle)
    monkeypatch.setattr("orchestrator.cli.is_project_queue_drained", mock_drained)

    result = runner.invoke(app, ["start", "alpha", "-n", "devtest", "--config", str(config_file)])
    assert result.exit_code == 0
    assert observed_pid == [os.getpid()]
    assert observed_daemon_pid == ["88888"]  # Main daemon PID is never overwritten

    # Upon exit, lifecycle_pid is cleaned up, but daemon PID remains
    final_l_pid = await state_manager.get_lifecycle_pid()
    assert final_l_pid is None
    async with aiosqlite.connect(state_manager.db_path) as db:
        cursor = await db.execute("SELECT value FROM daemon_control WHERE key = 'pid';")
        row = await cursor.fetchone()
        assert row[0] == "88888"


@pytest.mark.asyncio
async def test_cli_start_aborts_on_max_passes(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    passes = []
    async def mock_run_cycle(project, config, state_mgr, node_name=None, silent_idle=False):
        passes.append(1)
        return False

    async def mock_drained(project, state_mgr, node_name=None):
        return False

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle)
    monkeypatch.setattr("orchestrator.cli.is_project_queue_drained", mock_drained)
    orig_sleep = asyncio.sleep
    monkeypatch.setattr("orchestrator.cli.asyncio.sleep", lambda s: orig_sleep(0.001))

    result = runner.invoke(app, [
        "start", "biq-playbook", "-n", "devtest",
        "--config", str(config_file),
        "--max-passes", "3",
        "--interval", "1"
    ])
    assert result.exit_code == 2
    assert len(passes) == 3
    assert "Reached maximum passes limit" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_exits_code_1_on_stop_requested(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    async def mock_run_cycle(project, config, state_mgr, node_name=None, silent_idle=False):
        await state_mgr.request_stop()
        return True

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle)

    result = runner.invoke(app, ["start", "biq-playbook", "-n", "devtest", "--config", str(config_file)])
    assert result.exit_code == 1
    assert "Safe stop active" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_drain_queue_through_pending_ci(tmp_path, monkeypatch):
    """
    Given project "biq-playbook" has 2 queued subtasks for "devtest"
    When the operator executes "orchestrator start biq-playbook -n devtest"
    Then it must execute DevTest on subtask #1, create the PR, and await CI
    And it must NOT exit as "drained" while CI is running
    And upon CI pass and auto-merge, it must proceed to subtask #2
    And upon draining all actionable tasks and PRs, it must exit cleanly with code 0
    """
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    cycle_steps = [
        # Pass 1: DevTest implements subtask #1, creates PR #10, returns work_done=True
        (True, "DevTest node implemented issue #1 and opened PR #10"),
        # Pass 2: PR #10 CI is running, returns work_done=False
        (False, "Subtask #1 already has open PR #10 (linked_pr). Waiting for Phase 2 CI verification."),
        # Pass 3: PR #10 CI passed, auto-merged, returns work_done=True
        (True, "DevTest node auto-merged PR #10 into main."),
        # Pass 4: DevTest implements subtask #2, creates PR #11, returns work_done=True
        (True, "DevTest node implemented issue #2 and opened PR #11"),
        # Pass 5: PR #11 CI passed, auto-merged, returns work_done=True
        (True, "DevTest node auto-merged PR #11 into main."),
        # Pass 6: All done, returns work_done=False
        (False, "No PRs awaiting CI and no actionable task."),
    ]
    step_idx = 0

    # Queue drain predicate results corresponding to when work_done=False:
    # Pass 2: PR CI running -> False (must NOT exit as drained!)
    # Pass 6: All tasks and PRs drained -> True (exits cleanly with code 0!)
    drained_steps = [False, True]
    drained_idx = 0

    async def mock_run_cycle(project, config, state_mgr, node_name=None, silent_idle=False):
        nonlocal step_idx
        res = cycle_steps[step_idx]
        step_idx += 1
        return res[0]

    async def mock_is_drained(project, state_mgr, node_name=None):
        nonlocal drained_idx
        res = drained_steps[drained_idx]
        drained_idx += 1
        return res

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle)
    monkeypatch.setattr("orchestrator.cli.is_project_queue_drained", mock_is_drained)
    orig_sleep = asyncio.sleep
    monkeypatch.setattr("orchestrator.cli.asyncio.sleep", lambda s: orig_sleep(0.001))

    result = runner.invoke(app, ["start", "biq-playbook", "-n", "devtest", "--config", str(config_file)])
    assert result.exit_code == 0
    assert step_idx == 6
    assert drained_idx == 2
    assert "Queue not drained" in result.stdout
    assert "Queue fully drained. Lifecycle complete" in result.stdout


@pytest.mark.asyncio
async def test_is_project_queue_drained_predicate(tmp_path, monkeypatch):
    from orchestrator.cli import is_project_queue_drained

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(name="biq-playbook", repo="org/biq-playbook", local_path=str(tmp_path))

    # Case 1: Active story with queued subtask
    await state_manager.sync_project_sdlc_items(
        "biq-playbook",
        [
            {"issue_number": 100, "item_type": "STORY", "title": "Story 1", "state": "OPEN", "labels": ["planned"]},
            {"issue_number": 101, "parent_issue_id": 100, "item_type": "SUBTASK", "title": "Subtask 1", "state": "OPEN", "labels": ["queued"]},
        ],
    )
    async def mock_empty_prs(repo, limit=20):
        return []

    monkeypatch.setattr("orchestrator.poller.fetch_open_prs", mock_empty_prs)

    drained = await is_project_queue_drained(project, state_manager, node_name="devtest")
    assert drained is False

    # Case 2: Subtask has open PR awaiting CI
    await state_manager.sync_project_sdlc_items(
        "biq-playbook",
        [
            {
                "issue_number": 101,
                "parent_issue_id": 100,
                "item_type": "SUBTASK",
                "title": "Subtask 1",
                "state": "IN_PROGRESS",
                "labels": ["dev-implemented"],
                "linked_pr": 10,
                "pr_status": "OPEN",
                "pr_ci_details": "RUNNING",
            }
        ],
    )
    drained = await is_project_queue_drained(project, state_manager, node_name="devtest")
    assert drained is False

    # Case 3: Open PR fetched via poller with CI running
    async def mock_running_prs(repo, limit=20):
        return [{
            "number": 10,
            "headRefName": "feat/issue-101",
            "state": "OPEN",
            "labels": ["dev-implemented"],
            "statusCheckRollup": [{"status": "IN_PROGRESS"}],
        }]

    monkeypatch.setattr("orchestrator.poller.fetch_open_prs", mock_running_prs)
    drained = await is_project_queue_drained(project, state_manager, node_name="devtest")
    assert drained is False

    # Case 4: PR merged and subtasks closed
    monkeypatch.setattr("orchestrator.poller.fetch_open_prs", mock_empty_prs)
    await state_manager.sync_project_sdlc_items(
        "biq-playbook",
        [
            {"issue_number": 100, "item_type": "STORY", "title": "Story 1", "state": "CLOSED", "labels": []},
            {
                "issue_number": 101,
                "parent_issue_id": 100,
                "item_type": "SUBTASK",
                "title": "Subtask 1",
                "state": "CLOSED",
                "labels": [],
                "linked_pr": 10,
                "pr_status": "MERGED",
                "pr_ci_details": "PASS",
            },
        ],
    )
    drained = await is_project_queue_drained(project, state_manager, node_name="devtest")
    assert drained is True


@pytest.mark.asyncio
async def test_stop_command_force_kills_lifecycle_pid(tmp_path, monkeypatch):
    from orchestrator.cli import _stop_daemon

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
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.register_lifecycle(12345)

    killed_pids = []
    class DummyProc:
        def __init__(self, pid):
            self.pid = pid
        def children(self, recursive=True):
            return []
        def kill(self):
            killed_pids.append(self.pid)

    import psutil
    monkeypatch.setattr(psutil, "Process", DummyProc)

    await _stop_daemon(force=True, config_path=config_file)
    assert 12345 in killed_pids


@pytest.mark.asyncio
async def test_cli_start_refuses_when_global_stop_already_active(tmp_path, monkeypatch):
    """
    Scenario: Lifecycle command exits with code 1 when stop is requested
    Given a global stop is already requested
    When the operator executes "orchestrator start biq-playbook"
    Then it must refuse to start and exit with code 1
    """
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.request_stop()

    result = runner.invoke(app, ["start", "biq-playbook", "--config", str(config_file)])
    assert result.exit_code == 1
    assert "Stop requested or global stop active" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_aborts_on_max_passes_exceeded(tmp_path, monkeypatch):
    """
    Scenario: Lifecycle command aborts on max passes or fatal error
    Given "orchestrator start" reaches max_passes without draining
    When the pass limit is exceeded
    Then it must log an error and exit with code 2
    """
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    pass_counter = 0

    async def mock_run_cycle(project, config, state_mgr, node_name=None, silent_idle=False):
        nonlocal pass_counter
        pass_counter += 1
        return False

    async def mock_is_drained(project, state_mgr, node_name=None):
        return False

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle)
    monkeypatch.setattr("orchestrator.cli.is_project_queue_drained", mock_is_drained)
    orig_sleep = asyncio.sleep
    monkeypatch.setattr("orchestrator.cli.asyncio.sleep", lambda s: orig_sleep(0.001))

    result = runner.invoke(app, ["start", "biq-playbook", "-n", "devtest", "--max-passes", "3", "--config", str(config_file)])
    assert result.exit_code == 2
    assert pass_counter == 3
    assert "Reached maximum passes limit (3) without draining queue" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_aborts_on_fatal_error(tmp_path, monkeypatch):
    """
    Scenario: Lifecycle command aborts on fatal error
    Given an unexpected exception occurs during cycle execution
    When the error occurs with exit_when_idle=True
    Then it must log an error and exit with code 2
    """
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    async def mock_run_cycle_fatal(*args, **kwargs):
        raise RuntimeError("Fatal database corruption!")

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle_fatal)

    result = runner.invoke(app, ["start", "biq-playbook", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "Worker Error" in result.stdout or "Fatal" in result.stdout


@pytest.mark.asyncio
async def test_cli_start_lifecycle_pid_tracked_and_cleaned_up(tmp_path, monkeypatch):
    """
    Verifies that lifecycle_pid is registered in state.db during execution and unregistered upon exit.
    """
    import os
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    observed_pid = None

    async def mock_run_cycle(project, config, state_mgr, node_name=None, silent_idle=False):
        nonlocal observed_pid
        observed_pid = await state_mgr.get_lifecycle_pid()
        return False

    async def mock_is_drained(project, state_mgr, node_name=None):
        return True

    monkeypatch.setattr("orchestrator.cli.run_project_cycle", mock_run_cycle)
    monkeypatch.setattr("orchestrator.cli.is_project_queue_drained", mock_is_drained)

    result = runner.invoke(app, ["start", "biq-playbook", "--config", str(config_file)])
    assert result.exit_code == 0
    assert observed_pid == os.getpid()

    # After exit, lifecycle_pid must be unregistered
    state_mgr = StateManager(tmp_path / "state.db")
    assert await state_mgr.get_lifecycle_pid() is None


def test_cli_start_invalid_node_and_project(tmp_path):
    config_file = tmp_path / "config.yaml"
    posix_path = tmp_path.as_posix()
    config_file.write_text(
        f"""
version: 2
settings:
  db_path: "{posix_path}/state.db"
  log_dir: "{posix_path}/logs"
projects:
  - name: "biq-playbook"
    repo: "org/biq-playbook"
    local_path: "."
        """,
        encoding="utf-8",
    )

    # Invalid node
    result = runner.invoke(app, ["start", "biq-playbook", "-n", "invalid_node", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "only supports node 'devtest'" in result.stdout

    # Unknown project
    result_proj = runner.invoke(app, ["start", "unknown-proj", "--config", str(config_file)])
    assert result_proj.exit_code == 2
    assert "not found in configuration" in result_proj.stdout










