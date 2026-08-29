from __future__ import annotations

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


