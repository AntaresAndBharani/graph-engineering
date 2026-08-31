from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from orchestrator.config import GlobalConfig, ProjectConfig, NodeConfig
from orchestrator.db import StateManager
from orchestrator.nodes.devtest import verify_git_safety
from orchestrator.nodes.architect import run_architect_node
from orchestrator.nodes.supervisor import run_supervisor_node


@pytest.mark.asyncio
async def test_verify_git_safety_no_git_dir(tmp_path: Path):
    safe, msg = await verify_git_safety(tmp_path, "my-org/my-repo")
    assert safe is False
    assert ".git directory" in msg


@pytest.mark.asyncio
async def test_zero_token_gating_idle(tmp_path: Path, monkeypatch):
    # Mock fetch_issues_with_label to return empty list
    from orchestrator import poller
    async def mock_fetch_empty(*args, **kwargs):
        return []

    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_empty)
    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_empty)

    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Precondition: architecture plane is already synced
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", project.repo)

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is False
    assert "Idle (0 tokens)" in msg

    ran_sup, msg_sup = await run_supervisor_node(project, config, state_manager)
    assert ran_sup is False
    assert "consistent (0 tokens)" in msg_sup

    from orchestrator.nodes.reviewer import run_reviewer_node, check_pr_ci_status
    ran_rev, msg_rev = await run_reviewer_node(project, config, state_manager)
    assert ran_rev is False
    assert "Idle (0 tokens)" in msg_rev

    from orchestrator.nodes.bau import run_bau_node
    ran_bau, msg_bau = await run_bau_node(project, config, state_manager, force=True)
    assert ran_bau is False
    assert "Idle (0 tokens)" in msg_bau


@pytest.mark.asyncio
async def test_reviewer_node_ci_checks_logic():
    from orchestrator.nodes.reviewer import check_pr_ci_status
    # When gh is not available or mock returns no checks
    status, msg = await check_pr_ci_status("org/repo", 999)
    assert status in ("PASS", "NO_CHECKS", "PENDING")


@pytest.mark.asyncio
async def test_bau_node_schedule_gating(tmp_path: Path):
    from orchestrator.nodes.bau import run_bau_node
    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Record run now
    await state_manager.record_node_run("bau", project.repo)

    # Without force, should report not due
    ran, msg = await run_bau_node(project, config, state_manager, force=False)
    assert ran is False
    assert "not due" in msg.lower()


@pytest.mark.asyncio
async def test_supervisor_node_schedule_gating(tmp_path: Path):
    from orchestrator.nodes.supervisor import run_supervisor_node
    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Record run now
    await state_manager.record_node_run("supervisor", project.repo)

    # Without force, should report not due
    ran, msg = await run_supervisor_node(project, config, state_manager, force=False)
    assert ran is False
    assert "not due" in msg.lower()


@pytest.mark.asyncio
async def test_supervisor_status_audit_and_sla(tmp_path: Path, monkeypatch):
    import time
    from orchestrator import poller
    from orchestrator.nodes.supervisor import check_repository_anomalies

    # 15 hours ago
    stale_iso = "2020-01-01T00:00:00Z"

    mock_issues = [
        # Issue 1: Unclassified (no managed label)
        {"number": 101, "title": "Unclassified Issue", "labels": [], "createdAt": "2026-01-01T00:00:00Z"},
        # Issue 2: Stale (> 12h) on active workflow
        {"number": 102, "title": "Stale Story", "labels": [{"name": "needs-triage"}], "createdAt": stale_iso},
        # Issue 3: Tech debt (excluded from 12h SLA)
        {"number": 103, "title": "Tech Debt Item", "labels": [{"name": "tech-debt"}], "createdAt": stale_iso},
    ]

    async def mock_fetch_open_issues(*args, **kwargs):
        return mock_issues

    async def mock_fetch_open_prs(*args, **kwargs):
        return []

    monkeypatch.setattr(poller, "fetch_all_open_issues", mock_fetch_open_issues)
    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_open_prs)

    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    anomalies = await check_repository_anomalies(project, state_manager)
    anomaly_types = [a["type"] for a in anomalies]
    anomaly_issues = [a.get("issue_id") for a in anomalies]

    assert "UNCLASSIFIED_ISSUE" in anomaly_types
    assert 101 in anomaly_issues
    assert "STALE_ISSUE_SLA" in anomaly_types
    assert 102 in anomaly_issues
    # 103 (tech-debt) must NOT trigger STALE_ISSUE_SLA
    assert 103 not in anomaly_issues


@pytest.mark.asyncio
async def test_sync_parent_subtask_links_no_gh(monkeypatch):
    import shutil
    from orchestrator.nodes.architect import sync_parent_subtask_links

    # If gh not available, returns 0 cleanly
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    linked = await sync_parent_subtask_links("org/repo", 100, "architect-processed", "needs-triage")
    assert linked == 0


@pytest.mark.asyncio
async def test_reviewer_multi_pr_batch_evaluation(tmp_path: Path, monkeypatch):
    from orchestrator import poller
    from orchestrator.nodes.reviewer import run_reviewer_node

    mock_prs = [
        {"number": 418, "title": "Subtask 1", "labels": [{"name": "architect-approved"}], "mergeable": "MERGEABLE"},
        {"number": 419, "title": "Subtask 2", "labels": [{"name": "architect-approved"}], "mergeable": "MERGEABLE"},
    ]

    async def mock_fetch_open_prs(*args, **kwargs):
        return mock_prs

    async def mock_check_ci(*args, **kwargs):
        return "PASS", "100% green"

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_open_prs)
    import orchestrator.nodes.reviewer as rev_mod
    monkeypatch.setattr(rev_mod, "check_pr_ci_status", mock_check_ci)

    project = ProjectConfig(
        name="test",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "reviewer": NodeConfig(
                enabled=True,
                harness="claude",
                auto_merge_approved=False,
            )
        },
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    ran, msg = await run_reviewer_node(project, GlobalConfig(), state_manager)
    # With auto_merge=False, PRs are evaluated and reported without error
    assert isinstance(ran, bool)


@pytest.mark.asyncio
async def test_reviewer_autonomous_conflict_resolution_attempt(tmp_path: Path, monkeypatch):
    from orchestrator import poller
    from orchestrator.nodes.reviewer import run_reviewer_node

    mock_prs = [
        {"number": 437, "title": "Conflicting PR", "labels": [{"name": "architect-approved"}], "mergeable": "CONFLICTING", "headRefName": "feat/issue-430"},
    ]

    async def mock_fetch_open_prs(*args, **kwargs):
        return mock_prs

    import orchestrator.nodes.reviewer as rev_mod
    monkeypatch.setattr(rev_mod, "fetch_open_prs", mock_fetch_open_prs)
    async def mock_ci_pass(*args, **kwargs):
        return "PASS", "100% green"

    monkeypatch.setattr(rev_mod, "check_pr_ci_status", mock_ci_pass)
    async def mock_resolve(*args, **kwargs):
        return True, "Mock resolved conflicts and pushed branch"

    monkeypatch.setattr(rev_mod, "resolve_pr_merge_conflicts", mock_resolve)

    project = ProjectConfig(
        name="test",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "reviewer": NodeConfig(
                enabled=True,
                harness="claude",
            )
        },
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    ran, msg = await run_reviewer_node(project, GlobalConfig(), state_manager)
    assert ran is False
    assert "waiting for remote CI checks" in msg


@pytest.mark.asyncio
async def test_reviewer_ac2_closed_pr_handling(tmp_path: Path, monkeypatch):
    import orchestrator.nodes.reviewer as rev_mod
    from orchestrator.nodes.reviewer import run_reviewer_node

    # Mock closed & merged PR
    mock_prs = [
        {"number": 501, "title": "Merged PR", "state": "closed", "merged": True, "labels": [{"name": "architect-approved"}]},
        {"number": 502, "title": "Abandoned PR", "state": "closed", "merged": False, "labels": [{"name": "architect-approved"}]},
    ]
    async def mock_fetch(*a, **kw):
        return mock_prs

    monkeypatch.setattr(rev_mod, "fetch_open_prs", mock_fetch)

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.upsert_pr_artifact("org/repo", 501, "reviewer", "PENDING", "Initial")
    await state_manager.upsert_pr_artifact("org/repo", 502, "reviewer", "PENDING", "Initial")

    project = ProjectConfig(
        name="test",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={"reviewer": NodeConfig(enabled=True)},
    )

    ran, msg = await run_reviewer_node(project, GlobalConfig(), state_manager)
    assert ran is True
    assert "501" in msg
    # Blackboard artifacts for closed PRs are cleaned up
    assert await state_manager.get_pr_artifact("org/repo", 501) is None
    assert await state_manager.get_pr_artifact("org/repo", 502) is None


@pytest.mark.asyncio
async def test_reviewer_ac3_unknown_mergeability_deferred(tmp_path: Path, monkeypatch):
    import orchestrator.nodes.reviewer as rev_mod
    from orchestrator.nodes.reviewer import run_reviewer_node

    mock_prs = [
        {"number": 503, "title": "Calculating PR", "state": "open", "mergeable": "UNKNOWN", "labels": [{"name": "architect-approved"}]},
    ]
    async def mock_fetch(*a, **kw):
        return mock_prs

    monkeypatch.setattr(rev_mod, "fetch_open_prs", mock_fetch)

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="test",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={"reviewer": NodeConfig(enabled=True)},
    )

    ran, msg = await run_reviewer_node(project, GlobalConfig(), state_manager)
    assert ran is False
    assert "waiting for remote CI checks" in msg or "Idle" in msg


@pytest.mark.asyncio
async def test_reviewer_ac4_blackboard_conflict_artifact(tmp_path: Path, monkeypatch):
    import orchestrator.nodes.reviewer as rev_mod
    from orchestrator.nodes.reviewer import run_reviewer_node

    mock_prs = [
        {"number": 504, "title": "Conflicting Feature", "state": "open", "mergeable": "CONFLICTING", "headRefName": "feat/504", "labels": [{"name": "architect-approved"}]},
    ]
    async def mock_fetch(*a, **kw):
        return mock_prs

    monkeypatch.setattr(rev_mod, "fetch_open_prs", mock_fetch)
    async def mock_ci_pass(*a, **kw):
        return "PASS", "100% green"

    monkeypatch.setattr(rev_mod, "check_pr_ci_status", mock_ci_pass)

    async def mock_resolve(*a, **kw):
        return True, "Resolved cleanly"

    monkeypatch.setattr(rev_mod, "resolve_pr_merge_conflicts", mock_resolve)

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="test",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={"reviewer": NodeConfig(enabled=True)},
    )

    ran, msg = await run_reviewer_node(project, GlobalConfig(), state_manager)
    # Verify Blackboard recorded the conflict resolution status
    art = await state_manager.get_pr_artifact("org/repo", 504)
    assert art is not None
    assert art["status"] in ("APPROVED_WITH_CONFLICT", "CONFLICT_RESOLVED")


@pytest.mark.asyncio
async def test_compute_issue_hash():
    from orchestrator.nodes.supervisor import compute_issue_hash
    hash1 = compute_issue_hash("Title A", "Body A")
    hash2 = compute_issue_hash("Title A", "Body A")
    hash3 = compute_issue_hash("Title A", "Body B")
    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 64


@pytest.mark.asyncio
async def test_parse_po_evaluation_response():
    from orchestrator.nodes.supervisor import parse_po_evaluation_response
    sample_approved = """
VERDICT: PO_APPROVED
GAPS:
None
GHERKIN_AC:
```gherkin
Feature: Auth
  Scenario: Login
    Given user on login page
    When user enters valid credentials
    Then user is logged in
```
"""
    verdict, gaps, gherkin = parse_po_evaluation_response(sample_approved, "Auth")
    assert verdict == "PO_APPROVED"
    assert gaps is None
    assert gherkin is not None
    assert "Feature: Auth" in gherkin

    sample_clarification = """
VERDICT: NEEDS_HUMAN_CLARIFICATION
GAPS:
1. Missing token expiration strategy.
2. Unclear error response codes.
GHERKIN_AC:
None
"""
    v2, g2, gh2 = parse_po_evaluation_response(sample_clarification, "Auth")
    assert v2 == "NEEDS_HUMAN_CLARIFICATION"
    assert "Missing token expiration strategy" in (g2 or "")


@pytest.mark.asyncio
async def test_supervisor_po_evaluation_hash_skip_gate(tmp_path: Path):
    from orchestrator.nodes.supervisor import evaluate_supervisor_issue, compute_issue_hash
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    config = GlobalConfig()

    title = "feat: checkout"
    body = "Ambiguous checkout requirements."
    body_hash = compute_issue_hash(title, body)

    # Pre-seed NEEDS_HUMAN_CLARIFICATION in po_tracking
    await state_manager.upsert_po_tracking(
        repo=project.repo,
        issue_number=42,
        body_hash=body_hash,
        status="NEEDS_HUMAN_CLARIFICATION",
        blockers="Missing payment details",
    )

    issue = {"number": 42, "title": title, "body": body}
    res = await evaluate_supervisor_issue(project, issue, config, state_manager, dry_run=False, force=False)
    assert res.skipped is True
    assert "hash unchanged" in res.details.lower()


@pytest.mark.asyncio
async def test_supervisor_blackboard_producer_wiring(tmp_path: Path, monkeypatch):
    """Verifies that Supervisor node syncs SDLC items and records detected anomalies into StateManager."""
    from orchestrator import poller
    from orchestrator.nodes.supervisor import check_repository_anomalies

    project = ProjectConfig(name="proj-alpha", repo="org/proj-alpha", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    mock_issues = [
        {"number": 101, "title": "feat: auth", "labels": [{"name": "needs-triage"}], "createdAt": "2026-08-29T10:00:00Z"},
        {"number": 102, "title": "bug: typo", "labels": [], "createdAt": "2026-08-20T10:00:00Z"},  # unclassified & stale
    ]
    mock_prs = [
        {"number": 201, "title": "feat: login", "labels": [{"name": "needs-architect-review"}], "mergeable": "CONFLICTING"},
    ]

    async def mock_fetch_issues(*a, **kw):
        return mock_issues

    async def mock_fetch_prs(*a, **kw):
        return mock_prs

    monkeypatch.setattr(poller, "fetch_all_open_issues", mock_fetch_issues)
    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)

    anomalies = await check_repository_anomalies(project, state_manager)
    assert len(anomalies) > 0

    # Verify SDLC items synced to Blackboard
    sdlc_items = await state_manager.get_sdlc_items("proj-alpha")
    item_ids = [item["issue_number"] for item in sdlc_items]
    assert 101 in item_ids
    assert 102 in item_ids
    assert 201 in item_ids

    # Verify Anomaly events recorded to Blackboard
    recorded_anomalies = await state_manager.get_recent_anomalies("proj-alpha")
    assert len(recorded_anomalies) > 0
    types = {a["error_type"] for a in recorded_anomalies}
    assert "MERGE_CONFLICT" in types or "UNCLASSIFIED_ISSUE" in types


@pytest.mark.asyncio
async def test_architect_blackboard_producer_wiring(tmp_path: Path, monkeypatch):
    """Verifies that Architect node syncs SDLC items on triage and records anomaly events on harness failure."""
    from orchestrator.harness import AsyncHarnessAdapter

    project = ProjectConfig(
        name="arch-proj",
        repo="org/arch-proj",
        local_path=str(tmp_path),
        nodes={"architect": NodeConfig(enabled=True, harness="claude")},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    config = GlobalConfig()

    # Precondition: arch plane synced
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Arch\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", project.repo)

    mock_issues = [{"number": 55, "title": "Epic: Payments"}]

    async def mock_fetch_issues(*a, **kw):
        return mock_issues

    async def mock_fetch_prs(*a, **kw):
        return []

    async def mock_exec_fail(*a, **kw):
        return 1

    monkeypatch.setattr("orchestrator.nodes.architect.fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr("orchestrator.nodes.architect.fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_exec_fail)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is False

    # Verify SDLC item was registered before execution
    sdlc_items = await state_manager.get_sdlc_items("arch-proj")
    assert len(sdlc_items) == 1
    assert sdlc_items[0]["issue_number"] == 55

    # Verify anomaly event recorded
    anomalies = await state_manager.get_recent_anomalies("arch-proj")
    assert len(anomalies) == 1
    assert anomalies[0]["node_name"] == "architect"
    assert anomalies[0]["error_type"] == "HARNESS_ERROR"
    assert anomalies[0]["issue_number"] == 55


@pytest.mark.asyncio
async def test_devtest_blackboard_producer_wiring(tmp_path: Path, monkeypatch):
    """Verifies that DevTest node records anomaly events when safety or execution fails."""
    from orchestrator.nodes.devtest import run_devtest_node

    project = ProjectConfig(
        name="dev-proj",
        repo="org/dev-proj",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    config = GlobalConfig()

    await state_manager.sync_project_sdlc_items(
        "dev-proj",
        [{
            "issue_number": 88,
            "title": "feat: worker",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
        }],
    )
    mock_issue = {"number": 88, "title": "feat: worker", "labels": [{"name": "ready-for-dev"}]}
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", AsyncMock(return_value=mock_issue))

    # Safety check fails (not a valid git dir)
    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is False

    # Verify SDLC item synced and safety anomaly recorded
    sdlc_items = await state_manager.get_sdlc_items("dev-proj")
    assert len(sdlc_items) == 1
    assert sdlc_items[0]["issue_number"] == 88

    anomalies = await state_manager.get_recent_anomalies("dev-proj")
    assert len(anomalies) == 1
    assert anomalies[0]["node_name"] == "devtest"
    assert anomalies[0]["error_type"] == "SAFETY_ERROR"
    assert anomalies[0]["issue_number"] == 88


@pytest.mark.asyncio
async def test_reviewer_blackboard_producer_wiring(tmp_path: Path, monkeypatch):
    """Verifies that Reviewer node syncs PR SDLC items and records CI failure anomaly."""
    from orchestrator.nodes.reviewer import run_reviewer_node

    project = ProjectConfig(
        name="rev-proj",
        repo="org/rev-proj",
        local_path=str(tmp_path),
        nodes={"reviewer": NodeConfig(enabled=True, harness="claude")},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    config = GlobalConfig()

    mock_prs = [{"number": 77, "title": "feat: api", "state": "OPEN", "mergeable": "CLEAN", "headRefName": "feat/77"}]

    async def mock_fetch_prs(*a, **kw):
        return mock_prs

    async def mock_ci_fail(*a, **kw):
        return "FAIL", "Lint error"

    monkeypatch.setattr("orchestrator.nodes.reviewer.fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr("orchestrator.nodes.reviewer.check_pr_ci_status", mock_ci_fail)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    ran, msg = await run_reviewer_node(project, config, state_manager)
    assert ran is False

    # Verify PR synced to SDLC items
    sdlc_items = await state_manager.get_sdlc_items("rev-proj")
    assert len(sdlc_items) == 1
    assert sdlc_items[0]["issue_number"] == 77

    # Verify CI_FAILURE anomaly recorded
    anomalies = await state_manager.get_recent_anomalies("rev-proj")
    assert len(anomalies) == 1
    assert anomalies[0]["node_name"] == "reviewer"
    assert anomalies[0]["error_type"] == "CI_FAILURE"
    assert anomalies[0]["issue_number"] == 77


@pytest.mark.asyncio
async def test_reviewer_resolves_conflict_and_merges_immediately(tmp_path: Path, monkeypatch):
    """Verifies that Reviewer node resolves conflicts and merges immediately if auto_merge is enabled."""
    from orchestrator.nodes.reviewer import run_reviewer_node
    import orchestrator.nodes.reviewer as rev_mod

    project = ProjectConfig(
        name="merge-proj",
        repo="org/merge-proj",
        local_path=str(tmp_path),
        nodes={"reviewer": NodeConfig(enabled=True, harness="claude", auto_merge_approved=True)},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    config = GlobalConfig()

    mock_prs = [{"number": 99, "title": "feat: conflict resolved", "state": "OPEN", "mergeable": "CONFLICTING", "headRefName": "feat/99"}]

    async def mock_fetch_prs(*a, **kw):
        return mock_prs

    async def mock_ci_pass(*a, **kw):
        return "PASS", "100% green"

    async def mock_resolve(*a, **kw):
        return True, "Conflicts resolved cleanly"

    class MockProc:
        returncode = 0
        async def wait(self):
            return 0
        async def communicate(self):
            return b"", b""

    async def mock_subprocess_exec(*cmd, **kw):
        return MockProc()

    monkeypatch.setattr(rev_mod, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(rev_mod, "check_pr_ci_status", mock_ci_pass)
    monkeypatch.setattr(rev_mod, "resolve_pr_merge_conflicts", mock_resolve)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)

    ran, msg = await run_reviewer_node(project, config, state_manager)
    assert ran is True
    assert "auto-merged 1 approved PR(s) into main: #99" in msg

    # Verify SDLC item was marked MERGED
    sdlc_items = await state_manager.get_sdlc_items("merge-proj")
    assert len(sdlc_items) == 1
    assert sdlc_items[0]["state"] == "MERGED"


@pytest.mark.asyncio
async def test_scenario_devtest_operates_in_its_own_worktree(tmp_path: Path, monkeypatch):
    """
    Scenario: DevTest operates in its own worktree
      Given a project configured with local_path "/repo" and worktrees_enabled=True
      When the DevTest node executes
      Then it must operate in ".graph/worktrees/devtest_<project>" obtained via WorktreeManager
      And it must not mutate the Architect node's working tree
      And both nodes execute git operations without ".git/index.lock" collisions
    """
    from unittest.mock import AsyncMock
    from orchestrator.nodes.devtest import run_devtest_node, verify_git_safety
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.worktree import WorktreeManager
    import orchestrator.poller as poller_mod

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        worktrees_enabled=True,
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    await state_manager.sync_project_sdlc_items(
        "graph-engineering",
        [{
            "issue_number": 75,
            "title": "feat: worktree execution",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
        }],
    )
    mock_issue = {"number": 75, "title": "feat: worktree execution", "labels": [{"name": "ready-for-dev"}]}
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", AsyncMock(return_value=mock_issue))
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))

    expected_devtest_wt = (tmp_path / ".graph" / "worktrees" / "devtest_graph-engineering").resolve()
    architect_wt = (tmp_path / ".graph" / "worktrees" / "architect_graph-engineering").resolve()

    monkeypatch.setattr(
        WorktreeManager,
        "ensure_worktree",
        AsyncMock(return_value=expected_devtest_wt),
    )

    executed_cwds = []

    async def mock_execute(self, prompt, cwd=None, **kwargs):
        executed_cwds.append(cwd)
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    class MockProc:
        returncode = 0
        async def wait(self):
            return 0
        async def communicate(self):
            return b"", b""

    subprocess_cwds = []

    async def mock_subprocess_exec(*cmd, **kw):
        if "cwd" in kw:
            subprocess_cwds.append(kw["cwd"])
        mock_p = MockProc()
        if "status" in cmd and "--porcelain" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"M file.py\n", b""))
        elif "pr" in cmd and "create" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"https://github.com/AntaresAndBharani/graph-engineering/pull/75\n", b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")
    monkeypatch.setattr("orchestrator.nodes.devtest.check_pr_ci_status", AsyncMock(return_value=("PASS", "100% green")))

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is True
    assert len(executed_cwds) == 1
    assert executed_cwds[0] == expected_devtest_wt
    assert executed_cwds[0] != architect_wt
    assert "devtest_graph-engineering" in str(executed_cwds[0])
    # Verify git operations executed in the worktree directory
    assert str(expected_devtest_wt) in subprocess_cwds


@pytest.mark.asyncio
async def test_scenario_autonomous_story_promotion_on_completion(tmp_path: Path, monkeypatch, caplog):
    """
    Scenario: Autonomous Story Promotion on Completion
      Given DevTest completes and merges the final subtask of active Story #50
      When DevTest closes Story #50 with label "dev-implemented"
      And SQLite contains planned Story #60 with Subtasks #61 and #62
      Then DevTest must call promote_planned_story to activate Story #60
      And Subtask #61 must have label "queued" removed and "ready-for-dev" applied
      And the orchestrator logs: "[graph-engineering|devtest] Story #50 complete. Activating planned Story #60."
    """
    import json
    import logging
    from unittest.mock import AsyncMock
    from orchestrator.nodes.devtest import _advance_parent_and_unlock_next_subtask

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
    )

    # Seed active Story #50 and child Subtasks #51, #52
    items = [
        {
            "issue_number": 50,
            "title": "Story #50: Core Pipeline",
            "state": "OPEN",
            "item_type": "STORY",
            "sequence_order": 1,
            "labels": ["architect-processed"],
        },
        {
            "issue_number": 51,
            "parent_issue_id": 50,
            "title": "Subtask #51",
            "state": "CLOSED",
            "item_type": "SUBTASK",
            "sequence_order": 1,
        },
        {
            "issue_number": 52,
            "parent_issue_id": 50,
            "title": "Subtask #52",
            "state": "OPEN",
            "item_type": "SUBTASK",
            "sequence_order": 2,
        },
        # Seed planned Story #60 with Subtasks #61 and #62
        {
            "issue_number": 60,
            "title": "Story #60: Planned Extensions",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 2,
            "labels": ["planned"],
            "created_at": 1700000000.0,
        },
        {
            "issue_number": 61,
            "parent_issue_id": 60,
            "title": "Subtask #61",
            "state": "OPEN",
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "labels": ["queued"],
        },
        {
            "issue_number": 62,
            "parent_issue_id": 60,
            "title": "Subtask #62",
            "state": "OPEN",
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "labels": ["queued"],
        },
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)
    assert await state_manager.count_planned_stories("graph-engineering") == 1

    subtask_52_data = {
        "number": 52,
        "title": "Subtask #52",
        "body": "Acceptance criteria.\n\nParent: #50",
        "labels": [{"name": "dev-implemented"}],
    }
    parent_50_data = {
        "number": 50,
        "title": "Story #50: Core Pipeline",
        "body": "## Subtasks\n- [x] #51\n- [ ] #52\n",
        "state": "OPEN",
        "labels": [{"name": "architect-processed"}],
    }
    story_50_children = [
        {"number": 51, "title": "Subtask #51", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
        {"number": 52, "title": "Subtask #52", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
    ]
    story_60_children = [
        {"number": 61, "title": "Subtask #61", "state": "OPEN", "labels": [{"name": "queued"}]},
        {"number": 62, "title": "Subtask #62", "state": "OPEN", "labels": [{"name": "queued"}]},
    ]

    executed_cmds = []

    async def mock_subprocess_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        cmd_str = " ".join(str(a) for a in args)
        mock_p = AsyncMock()
        mock_p.returncode = 0
        mock_p.wait = AsyncMock(return_value=0)

        if "issue view 52" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(subtask_52_data).encode("utf-8"), b""))
        elif "issue view 50" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(parent_50_data).encode("utf-8"), b""))
        elif "issue list" in cmd_str and "#50" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(story_50_children).encode("utf-8"), b""))
        elif "issue list" in cmd_str and "#60" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(story_60_children).encode("utf-8"), b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")

    with caplog.at_level(logging.INFO):
        await _advance_parent_and_unlock_next_subtask(project, state_manager, 52)

    # 1. Verify Story #50 was closed with dev-implemented
    story_50_closed = any("issue" in c and "close" in c and "50" in c for c in executed_cmds)
    assert story_50_closed is True

    # 2. Verify planned Story #60 was promoted in SQLite
    assert await state_manager.count_planned_stories("graph-engineering") == 0
    sdlc_items = await state_manager.get_sdlc_items("graph-engineering")
    story_60 = next(s for s in sdlc_items if s["issue_number"] == 60)
    assert story_60["state"] == "ACTIVE"

    # 3. Verify Subtask #61 had 'queued' removed and 'ready-for-dev' applied
    subtask_61_unlocked = any(
        "issue" in c and "edit" in c and "61" in c and "--add-label" in c and "ready-for-dev" in c
        for c in executed_cmds
    )
    assert subtask_61_unlocked is True

    # 4. Verify the exact log statement
    expected_log = "[graph-engineering|devtest] Story #50 complete. Activating planned Story #60."
    assert expected_log in caplog.text


@pytest.mark.asyncio
async def test_scenario_no_planned_story_available(tmp_path: Path, monkeypatch):
    """
    Scenario: No planned story available
      Given DevTest closes the final subtask of the active story
      And SQLite contains no story in "PLANNED" state
      Then DevTest completes normally without attempting promotion
      And no error is raised
    """
    import json
    from unittest.mock import AsyncMock
    from orchestrator.nodes.devtest import _advance_parent_and_unlock_next_subtask

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
    )

    # Only active Story #50, NO planned stories in SQLite
    items = [
        {
            "issue_number": 50,
            "title": "Story #50: Standalone Story",
            "state": "OPEN",
            "item_type": "STORY",
            "sequence_order": 1,
            "labels": ["architect-processed"],
        },
        {
            "issue_number": 51,
            "parent_issue_id": 50,
            "title": "Subtask #51",
            "state": "OPEN",
            "item_type": "SUBTASK",
            "sequence_order": 1,
        },
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)
    assert await state_manager.count_planned_stories("graph-engineering") == 0

    subtask_51_data = {
        "number": 51,
        "title": "Subtask #51",
        "body": "Acceptance criteria.\n\nParent: #50",
        "labels": [{"name": "dev-implemented"}],
    }
    parent_50_data = {
        "number": 50,
        "title": "Story #50: Standalone Story",
        "body": "## Subtasks\n- [ ] #51\n",
        "state": "OPEN",
        "labels": [{"name": "architect-processed"}],
    }
    story_50_children = [
        {"number": 51, "title": "Subtask #51", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
    ]

    executed_cmds = []

    async def mock_subprocess_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        cmd_str = " ".join(str(a) for a in args)
        mock_p = AsyncMock()
        mock_p.returncode = 0
        mock_p.wait = AsyncMock(return_value=0)

        if "issue view 51" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(subtask_51_data).encode("utf-8"), b""))
        elif "issue view 50" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(parent_50_data).encode("utf-8"), b""))
        elif "issue list" in cmd_str:
            mock_p.communicate = AsyncMock(return_value=(json.dumps(story_50_children).encode("utf-8"), b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")

    # Call _advance_parent_and_unlock_next_subtask - should complete normally without error
    await _advance_parent_and_unlock_next_subtask(project, state_manager, 51)

    story_50_closed = any("issue" in c and "close" in c and "50" in c for c in executed_cmds)
    assert story_50_closed is True

    # No promotion calls occurred
    sdlc_items = await state_manager.get_sdlc_items("graph-engineering")
    story_50 = next(s for s in sdlc_items if s["issue_number"] == 50)
    assert story_50["state"] == "CLOSED"
    assert await state_manager.count_planned_stories("graph-engineering") == 0


@pytest.mark.asyncio
async def test_devtest_phase3_targeted_fetch_and_stateless_execution(tmp_path: Path, monkeypatch):
    """
    Scenario: Targeted Fetch and Stateless Execution in DevTest
      Given get_next_devtest_task returns issue ID #93
      When run_devtest_node executes Phase 3
      Then it must fetch issue #93 directly via fetch_issue_by_number
      And it must not perform generic blind label polling across the repository.
    """
    from unittest.mock import AsyncMock
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.worktree import WorktreeManager
    import orchestrator.poller as poller_mod

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    # Mock get_next_devtest_task returning issue ID #93
    monkeypatch.setattr(state_manager, "get_next_devtest_task", AsyncMock(return_value=93))

    fetched_issue_numbers = []

    async def mock_fetch_issue_by_number(repo, issue_number):
        fetched_issue_numbers.append(issue_number)
        return {
            "number": issue_number,
            "title": "feat: deterministic locked issue dispatch",
            "body": "Issue body",
            "labels": [{"name": "ready-for-dev"}],
        }

    # Tracking for generic blind label polling to assert it is never called
    blind_polling_called = False

    async def mock_fetch_issues_with_label(*args, **kwargs):
        nonlocal blind_polling_called
        blind_polling_called = True
        return [{"number": 999, "title": "Wrong issue from blind polling"}]

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", mock_fetch_issue_by_number)
    monkeypatch.setattr(poller_mod, "fetch_issues_with_label", mock_fetch_issues_with_label)
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))

    dispatched_prompt = ""

    async def mock_harness_execute(self, prompt, cwd=None, **kwargs):
        nonlocal dispatched_prompt
        dispatched_prompt = prompt
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_harness_execute)

    class MockProc:
        returncode = 0
        async def wait(self):
            return 0
        async def communicate(self):
            return b"", b""

    async def mock_subprocess_exec(*cmd, **kw):
        mock_p = MockProc()
        if "status" in cmd and "--porcelain" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"M orchestrator/nodes/devtest.py\n", b""))
        elif "pr" in cmd and "create" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"https://github.com/AntaresAndBharani/graph-engineering/pull/93\n", b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")
    monkeypatch.setattr("orchestrator.nodes.devtest.check_pr_ci_status", AsyncMock(return_value=("PASS", "100% green")))

    ran, msg = await run_devtest_node(project, config, state_manager)

    assert ran is True
    # Assert issue #93 was fetched directly via fetch_issue_by_number
    assert fetched_issue_numbers == [93]
    # Assert generic blind label polling was NOT performed
    assert blind_polling_called is False
    # Assert harness prompt targeted issue #93
    assert "#93" in dispatched_prompt


@pytest.mark.asyncio
async def test_devtest_phase3_blocked_pipeline_halts_without_skipping_stories(tmp_path: Path, monkeypatch, caplog):
    """
    Scenario: Blocked Pipeline Halts Without Skipping Stories
      Given get_next_devtest_task returns None because the locked story's next subtask is blocked
      When run_devtest_node executes Phase 3
      Then DevTest must idle without dispatching any other issue
      And it must emit a warning log indicating the project is locked on the active story.
    """
    import logging
    from unittest.mock import AsyncMock
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    # get_next_devtest_task returns None (blocked story / no actionable task)
    monkeypatch.setattr(state_manager, "get_next_devtest_task", AsyncMock(return_value=None))
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))

    harness_called = False

    async def mock_harness_execute(*args, **kwargs):
        nonlocal harness_called
        harness_called = True
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_harness_execute)

    with caplog.at_level(logging.WARNING):
        ran, msg = await run_devtest_node(project, config, state_manager)

    # 1. DevTest must idle without dispatching any issue
    assert ran is False
    assert harness_called is False
    assert "Idle (0 tokens)" in msg
    assert "story lock active or idle" in msg

    # 2. Must emit a warning log indicating the project is locked on the active story
    warning_logged = any(
        "Project is locked on active story" in record.message or "locked on active story" in record.message.lower()
        for record in caplog.records
    )
    assert warning_logged is True


@pytest.mark.asyncio
async def test_devtest_phase3_sqlite_cte_story_lock_end_to_end(tmp_path: Path, monkeypatch, caplog):
    """
    Integration test asserting end-to-end SQLite CTE Story Lock gating in run_devtest_node:
    - Story A (#90) with Subtask #93 (status:blocked) and Subtask #94 (ready-for-dev)
    - Story B (#100) with Subtask #101 (ready-for-dev)
    - run_devtest_node must idle and NOT skip to #94 or Story B #101.
    - When Subtask #93 is unblocked (ready-for-dev), run_devtest_node dispatches #93.
    """
    import logging
    from unittest.mock import AsyncMock
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.worktree import WorktreeManager

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    # Seed Story A (#90) with Subtask #93 (blocked) and Subtask #94 (ready-for-dev)
    # Seed Story B (#100) with Subtask #101 (ready-for-dev)
    items = [
        {"issue_number": 90, "title": "Story A", "state": "OPEN", "item_type": "STORY", "sequence_order": 1, "labels": ["architect-processed"]},
        {"issue_number": 93, "parent_issue_id": 90, "title": "Subtask #93", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["status:blocked"]},
        {"issue_number": 94, "parent_issue_id": 90, "title": "Subtask #94", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 2, "labels": ["ready-for-dev"]},
        {"issue_number": 100, "title": "Story B", "state": "OPEN", "item_type": "STORY", "sequence_order": 2, "labels": ["architect-processed"]},
        {"issue_number": 101, "parent_issue_id": 100, "title": "Subtask #101", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["ready-for-dev"]},
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))

    dispatched_issues = []

    async def mock_fetch_issue_by_number(repo, issue_number):
        return {
            "number": issue_number,
            "title": f"Subtask #{issue_number}",
            "body": "Subtask body",
            "labels": [{"name": "ready-for-dev"}],
        }

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", mock_fetch_issue_by_number)

    async def mock_harness_execute(self, prompt, cwd=None, **kwargs):
        dispatched_issues.append(getattr(self, "issue_number", None))
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_harness_execute)

    class MockProc:
        returncode = 0
        async def wait(self):
            return 0
        async def communicate(self):
            return b"", b""

    async def mock_subprocess_exec(*cmd, **kw):
        mock_p = MockProc()
        if "status" in cmd and "--porcelain" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"M test.py\n", b""))
        elif "pr" in cmd and "create" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"https://github.com/AntaresAndBharani/graph-engineering/pull/93\n", b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")
    monkeypatch.setattr("orchestrator.nodes.devtest.check_pr_ci_status", AsyncMock(return_value=("PASS", "100% green")))

    # 1. First execution: Subtask #93 is blocked -> DevTest idles with warning log
    with caplog.at_level(logging.WARNING):
        ran1, msg1 = await run_devtest_node(project, config, state_manager)

    assert ran1 is False
    assert len(dispatched_issues) == 0
    assert any("Project is locked on active story" in r.message for r in caplog.records)

    # 2. Unblock Subtask #93 (transitions to 'ready-for-dev')
    items[1]["labels"] = ["ready-for-dev"]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)

    # 3. Second execution: DevTest dispatches Subtask #93 directly
    ran2, msg2 = await run_devtest_node(project, config, state_manager)
    assert ran2 is True
    assert 93 in dispatched_issues


@pytest.mark.asyncio
async def test_devtest_phase3_aborts_and_cleans_labels_on_closed_target_issue(tmp_path: Path, monkeypatch, caplog):
    """
    Given a subtask #126 is resolved from SQLite
    When fetch_issue_by_number returns state='CLOSED' from GitHub
    Then DevTest immediately aborts, cleans stale labels, updates SQLite to 'CLOSED', and avoids invoking the harness.
    """
    import logging
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.harness import AsyncHarnessAdapter

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    config = GlobalConfig()

    items = [
        {"issue_number": 125, "item_type": "STORY", "state": "OPEN", "labels": ["architect-processed"]},
        {"issue_number": 126, "parent_issue_id": 125, "item_type": "SUBTASK", "state": "OPEN", "labels": ["ready-for-dev"], "sequence_order": 1},
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)

    # Mock fetch_open_prs to empty
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))

    # Mock fetch_issue_by_number returning state="CLOSED"
    mock_issue_payload = {
        "number": 126,
        "title": "feat: closed subtask",
        "state": "CLOSED",
        "labels": [{"name": "ready-for-dev"}],
    }
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", AsyncMock(return_value=mock_issue_payload))

    harness_called = []
    async def mock_execute(*a, **kw):
        harness_called.append(True)
        return 0
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    edited_cmds = []
    class MockProc:
        returncode = 0
        async def wait(self):
            return 0
        async def communicate(self):
            return b"", b""

    async def mock_subprocess_exec(*cmd, **kw):
        edited_cmds.append(list(cmd))
        return MockProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda c: "gh")

    with caplog.at_level(logging.WARNING):
        ran, msg = await run_devtest_node(project, config, state_manager)

    assert ran is False
    assert "already closed on GitHub" in msg
    assert len(harness_called) == 0

    # Verify SQLite was updated to CLOSED
    sdlc_items = await state_manager.get_sdlc_items("graph-engineering")
    item_map = {item["issue_number"]: item for item in sdlc_items}
    assert item_map[126]["state"] == "CLOSED"

    # Verify label removal was attempted
    assert any("--remove-label" in " ".join(cmd) for cmd in edited_cmds)









