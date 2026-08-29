from __future__ import annotations

import asyncio
from pathlib import Path
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

    mock_issues = [{"number": 88, "title": "feat: worker"}]

    async def mock_fetch_issues(*a, **kw):
        return mock_issues

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issues_with_label", mock_fetch_issues)

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






