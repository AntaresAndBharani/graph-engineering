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


@pytest.mark.asyncio
async def test_reviewer_node_ci_checks_logic():
    from orchestrator.nodes.reviewer import check_pr_ci_status
    # When gh is not available or mock returns no checks
    status, msg = await check_pr_ci_status("org/repo", 999)
    assert status in ("PASS", "NO_CHECKS", "PENDING")
