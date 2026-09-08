from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.nodes.tech_debt import (
    get_origin_main_sha,
    is_project_fully_quiescent,
    parse_tech_debt_recommendation,
    run_tech_debt_node,
)
from orchestrator.worktree import run_git_command


@pytest.mark.asyncio
async def test_scenario_1_strict_quiescence_halts_on_active_story_lock(tmp_path: Path):
    """
    Scenario 1a: Active story lock is held in SQLite blackboard
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))

    # Hold story lock via active_jobs table
    await state_manager.acquire_lock(
        issue_id=101,
        repo=project.repo,
        node_type="devtest",
        ttl_minutes=15,
    )

    is_quiescent, reason = await is_project_fully_quiescent(project, state_manager)
    assert is_quiescent is False
    assert "Active story lock is held: Issue #101 by devtest" in reason

    config = GlobalConfig()
    ran, msg = await run_tech_debt_node(project, config, state_manager)
    assert ran is False
    assert "Development active" in msg
    assert "Active story lock is held: Issue #101" in msg
    assert "Idle (0 tokens)" in msg


@pytest.mark.asyncio
async def test_scenario_1_strict_quiescence_halts_on_open_sdlc_stories(tmp_path: Path):
    """
    Scenario 1b: Open SDLC story item exists with state OPEN, ACTIVE, or PLANNED
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))

    await state_manager.sync_project_sdlc_items(
        project.name,
        [
            {
                "issue_number": 102,
                "title": "Unfinished Feature",
                "state": "OPEN",
                "labels": [],
                "item_type": "SUBTASK",
            }
        ],
    )

    is_quiescent, reason = await is_project_fully_quiescent(project, state_manager)
    assert is_quiescent is False
    assert "Active SDLC item found: Issue #102 with state 'OPEN'" in reason


@pytest.mark.asyncio
async def test_scenario_1_strict_quiescence_halts_on_pipeline_labels(tmp_path: Path):
    """
    Scenario 1c: Closed SDLC item still holds active pipeline labels (e.g. architect-approved, ready-for-dev)
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))

    await state_manager.sync_project_sdlc_items(
        project.name,
        [
            {
                "issue_number": 103,
                "title": "Stale approved story",
                "state": "CLOSED",
                "labels": ["architect-approved"],
                "item_type": "STORY",
            }
        ],
    )

    is_quiescent, reason = await is_project_fully_quiescent(project, state_manager)
    assert is_quiescent is False
    assert "holds pipeline active label 'architect-approved'" in reason


@pytest.mark.asyncio
async def test_scenario_1_strict_quiescence_halts_on_open_prs(tmp_path: Path):
    """
    Scenario 1d: Open pull requests exist on GitHub
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b'[{"number": 42, "title": "Feature PR"}]', b""))

    with patch("shutil.which", return_value="gh"), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        is_quiescent, reason = await is_project_fully_quiescent(project, state_manager)
        assert is_quiescent is False
        assert "open Pull Requests pending" in reason


@pytest.mark.asyncio
async def test_scenario_1_strict_quiescence_fails_closed_when_gh_missing(tmp_path: Path):
    """
    Scenario 1e: GitHub CLI missing causes fail-closed quiescence evaluation
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))

    with patch("shutil.which", return_value=None):
        is_quiescent, reason = await is_project_fully_quiescent(project, state_manager)
        assert is_quiescent is False
        assert "fail-closed" in reason.lower()


@pytest.mark.asyncio
async def test_scenario_2_zero_token_skip_on_previously_audited_commit_sha(tmp_path: Path):
    """
    Scenario 2: Zero Token Skip on Previously Audited Commit SHA
      Given a registered project that is fully quiescent
      And the current upstream "origin/main" commit SHA matches "last_audited_sha" in SQLite blackboard
      And the invocation is not forced via "--force"
      When the tech_debt node executes
      Then it MUST log "Commit <sha> already audited for tech-debt. Idle (0 tokens)."
      And immediately exit without creating worktrees or invoking Claude Sonnet.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))
    config = GlobalConfig()

    sha = "abcdef1234567890abcdef1234567890abcdef12"
    await state_manager.record_tech_debt_audit(
        project_name=project.name,
        commit_sha=sha,
        status="CLEAN",
        issue_number=None,
        details="Clean audit",
    )

    with patch("orchestrator.nodes.tech_debt.is_project_fully_quiescent", AsyncMock(return_value=(True, ""))), \
         patch("orchestrator.nodes.tech_debt.get_origin_main_sha", AsyncMock(return_value=sha)), \
         patch("orchestrator.nodes.tech_debt.WorktreeManager.ensure_worktree") as mock_wt, \
         patch.object(AsyncHarnessAdapter, "execute") as mock_exec:

        ran, msg = await run_tech_debt_node(project, config, state_manager, force=False)
        assert ran is False
        assert f"Commit {sha[:7]} already audited for tech-debt. Idle (0 tokens)." in msg
        mock_wt.assert_not_called()
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_3_cooldown_interval_throttling(tmp_path: Path):
    """
    Scenario 3: Cooldown Interval Throttling
      Given a registered project that is fully quiescent
      And a new commit SHA is present on "origin/main"
      And elapsed time since the last audit is less than "tech_debt_interval_seconds" (default: 14,400s)
      And the invocation is not forced via "--force"
      When the tech_debt node executes
      Then it MUST log "Tech-debt node in cooldown (<N>m remaining). Idle (0 tokens)."
      And immediately exit consuming 0 LLM tokens.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))
    config = GlobalConfig()

    old_sha = "old_commit_sha_11111111111111111111111111"
    new_sha = "new_commit_sha_22222222222222222222222222"

    # Audited 1 hour ago (3600s), interval is 14400s (4 hours)
    audited_at = time.time() - 3600
    await state_manager.record_tech_debt_audit(
        project_name=project.name,
        commit_sha=old_sha,
        status="CLEAN",
        issue_number=None,
        details="Previous audit",
        audited_at=audited_at,
    )

    with patch("orchestrator.nodes.tech_debt.is_project_fully_quiescent", AsyncMock(return_value=(True, ""))), \
         patch("orchestrator.nodes.tech_debt.get_origin_main_sha", AsyncMock(return_value=new_sha)), \
         patch.object(AsyncHarnessAdapter, "execute") as mock_exec:

        ran, msg = await run_tech_debt_node(project, config, state_manager, force=False)
        assert ran is False
        assert "Tech-debt node in cooldown" in msg
        assert "remaining). Idle (0 tokens)." in msg
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_4_successful_audit_surfacing_high_impact_debt(tmp_path: Path):
    """
    Scenario 4: Successful Audit Surfacing High-Impact Debt
      Given a registered project that is fully quiescent
      And a new commit SHA exists on "origin/main"
      And the cooldown interval has elapsed
      When the tech_debt node executes
      Then it checks out "origin/main" in detached HEAD mode in an ephemeral worktree
      And executes Claude Sonnet with low effort and strict negative constraints
      And when Claude returns a qualifying "=== TECH DEBT RECOMMENDATION ==="
      Then the Python orchestrator validates the schema
      And creates exactly one GitHub issue with labels "story" and "needs-triage"
      And updates the SQLite blackboard with the new commit SHA, timestamp, and created issue number
      And reports completion to the operator.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))
    config = GlobalConfig()
    config.settings.log_dir = str(tmp_path / "logs")

    new_sha = "feat_commit_sha_99999999999999999999999999"

    claude_recommendation = """=== TECH DEBT RECOMMENDATION ===
Title: [Story]: Decouple Monolithic Harness Execution into Pluggable Drivers
Severity: High
Component: orchestrator/harness.py
Description:
The harness execution module contains hardcoded conditionals across multiple provider models, increasing regression risk.
Acceptance Criteria:
```gherkin
Feature: Pluggable Harness Drivers
  Scenario: Load custom adapter driver
    Given a custom harness adapter registered in config
    When execution is triggered
    Then the custom adapter executes cleanly
```
Remediation Plan:
- Extract base driver interface
- Refactor claude and antigravity adapters
=== END RECOMMENDATION ===
"""

    wt_dir = tmp_path / "worktree_tech_debt"
    wt_dir.mkdir(parents=True, exist_ok=True)

    gh_cmds = []
    mock_gh_proc = AsyncMock()
    mock_gh_proc.returncode = 0
    mock_gh_proc.communicate = AsyncMock(return_value=(b"https://github.com/AntaresAndBharani/crosstrainingapp/issues/555\n", b""))

    async def mock_subprocess_exec(*args, **kwargs):
        gh_cmds.append(list(args))
        return mock_gh_proc

    async def fake_adapter_execute(*args, **kwargs):
        log_file = kwargs.get("log_file")
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(claude_recommendation, encoding="utf-8")
        assert kwargs.get("model") == "sonnet"
        assert kwargs.get("effort") == "low"
        return 0

    with patch("orchestrator.nodes.tech_debt.is_project_fully_quiescent", AsyncMock(return_value=(True, ""))), \
         patch("orchestrator.nodes.tech_debt.get_origin_main_sha", AsyncMock(return_value=new_sha)), \
         patch("orchestrator.nodes.tech_debt.WorktreeManager.ensure_worktree", AsyncMock(return_value=wt_dir)), \
         patch("orchestrator.nodes.tech_debt.checkout_detached_upstream", AsyncMock(return_value=True)) as mock_detach, \
         patch("orchestrator.nodes.tech_debt.WorktreeManager.remove_worktree", AsyncMock(return_value=True)) as mock_rm_wt, \
         patch.object(AsyncHarnessAdapter, "execute", side_effect=fake_adapter_execute), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec), \
         patch("shutil.which", return_value="gh"):

        ran, msg = await run_tech_debt_node(project, config, state_manager, force=False)
        assert ran is True
        assert "created issue #555" in msg
        mock_detach.assert_awaited_once_with(wt_dir, branch="main", timeout=30.0)
        mock_rm_wt.assert_awaited_once_with(project, "tech_debt")

        # Verify GitHub issue creation args
        create_calls = [c for c in gh_cmds if "issue" in c and "create" in c]
        assert len(create_calls) == 1
        cmd_call = create_calls[0]
        assert "--repo" in cmd_call
        assert "AntaresAndBharani/crosstrainingapp" in cmd_call
        assert "--label" in cmd_call
        idx = cmd_call.index("--label")
        assert cmd_call[idx + 1] == "story,needs-triage"

        # Verify SQLite blackboard record
        audit = await state_manager.get_last_tech_debt_audit(project.name)
        assert audit is not None
        assert audit["commit_sha"] == new_sha
        assert audit["status"] == "STORY_CREATED"
        assert audit["issue_number"] == 555


@pytest.mark.asyncio
async def test_scenario_5_clean_codebase_zero_issue_idempotency(tmp_path: Path):
    """
    Scenario 5: Clean Codebase Zero-Issue Idempotency
      Given a quiescent repository undergoing tech debt audit
      When Claude Sonnet evaluates the codebase and returns "=== NO TECH DEBT FOUND ==="
      Then the Python orchestrator creates ZERO GitHub issues
      And updates the SQLite blackboard recording the commit SHA as audited and clean
      And logs "Codebase audit completed: No actionable technical debt identified."
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(name="crosstrainingapp", repo="AntaresAndBharani/crosstrainingapp", local_path=str(tmp_path))
    config = GlobalConfig()
    config.settings.log_dir = str(tmp_path / "logs")

    clean_sha = "clean_commit_sha_33333333333333333333333333"

    wt_dir = tmp_path / "wt_clean"
    wt_dir.mkdir(parents=True, exist_ok=True)

    gh_cmds = []

    async def fake_adapter_clean(*args, **kwargs):
        log_file = kwargs.get("log_file")
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text("=== NO TECH DEBT FOUND ===\n", encoding="utf-8")
        return 0

    with patch("orchestrator.nodes.tech_debt.is_project_fully_quiescent", AsyncMock(return_value=(True, ""))), \
         patch("orchestrator.nodes.tech_debt.get_origin_main_sha", AsyncMock(return_value=clean_sha)), \
         patch("orchestrator.nodes.tech_debt.WorktreeManager.ensure_worktree", AsyncMock(return_value=wt_dir)), \
         patch("orchestrator.nodes.tech_debt.checkout_detached_upstream", AsyncMock(return_value=True)), \
         patch("orchestrator.nodes.tech_debt.WorktreeManager.remove_worktree", AsyncMock(return_value=True)), \
         patch.object(AsyncHarnessAdapter, "execute", side_effect=fake_adapter_clean), \
         patch("asyncio.create_subprocess_exec") as mock_subproc:

        ran, msg = await run_tech_debt_node(project, config, state_manager, force=False)
        assert ran is True
        assert "Codebase audit completed: No actionable technical debt identified." in msg
        mock_subproc.assert_not_called()

        audit = await state_manager.get_last_tech_debt_audit(project.name)
        assert audit is not None
        assert audit["commit_sha"] == clean_sha
        assert audit["status"] == "CLEAN"
        assert audit["issue_number"] is None


@pytest.mark.asyncio
async def test_scenario_6_subprocess_timeout_and_crash_resilience(tmp_path: Path):
    """
    Scenario 6: Subprocess Timeout and Crash Resilience
      Given any git command executed by worktree or tech debt operations
      When the command exceeds the 30.0s timeout threshold
      Then the orchestrator MUST terminate the process tree using "proc.kill()" and "await proc.wait()"
      And return an error tuple without leaking zombie processes or retaining ".git/index.lock"
      And the orchestrator continues normal operation.
    """
    mock_proc = AsyncMock()
    mock_proc.pid = 77777
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=-9)

    async def timeout_hang():
        await asyncio.sleep(10.0)
        return (b"", b"")

    mock_proc.communicate = AsyncMock(side_effect=timeout_hang)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("shutil.which", return_value="/usr/bin/git"), \
         patch("orchestrator.worktree._kill_process_tree") as mock_kill_tree:

        code, stdout, stderr = await run_git_command(["fetch", "origin", "main"], cwd=tmp_path, timeout=0.05)
        assert code == -1
        assert stdout == ""
        assert "Git command timed out after 0.05s" in stderr
        mock_kill_tree.assert_called_once_with(mock_proc)
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_awaited_once()


def test_parse_tech_debt_recommendation_parser():
    """Unit tests for parse_tech_debt_recommendation helper."""
    # 1. No tech debt
    assert parse_tech_debt_recommendation("=== NO TECH DEBT FOUND ===") is None
    assert parse_tech_debt_recommendation("Random text with no recommendation block") is None

    # 2. Well-formed block
    sample = """
=== TECH DEBT RECOMMENDATION ===
Title: [Story]: Refactor async blackboard queries
Severity: Critical
Component: orchestrator/db.py
Description:
Excessive open connections during heavy polling loops.
Acceptance Criteria:
```gherkin
Feature: Connection pooling
  Scenario: Run 100 concurrent blackboard queries
    Given 100 concurrent tasks
    When executing queries
    Then no connection timeout occurs
```
Remediation Plan:
- Introduce connection pool
- Add connection timeout retry
=== END RECOMMENDATION ===
"""
    rec = parse_tech_debt_recommendation(sample)
    assert rec is not None
    assert rec["title"] == "[Story]: Refactor async blackboard queries"
    assert rec["severity"] == "Critical"
    assert rec["component"] == "orchestrator/db.py"
    assert "Excessive open connections" in rec["description"]
    assert "Feature: Connection pooling" in rec["acceptance_criteria"]
    assert "Introduce connection pool" in rec["remediation_plan"]
