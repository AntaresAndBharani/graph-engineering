from __future__ import annotations

from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig, HarnessConfig
from orchestrator.db import StateManager
from orchestrator.nodes.devtest import run_devtest_node, _remediate_refactor_pr


@pytest.mark.asyncio
async def test_devtest_zero_token_gating(tmp_path: Path, monkeypatch):
    """Verifies that DevTest exits with 0 tokens when no needs-refactor PRs and no ready-for-dev issues exist."""
    from orchestrator import poller

    async def mock_fetch_empty(*args, **kwargs):
        return []

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_empty)
    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_empty)

    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is False
    assert "Idle (0 tokens)" in msg


@pytest.mark.asyncio
async def test_devtest_remediates_needs_refactor_pr(tmp_path: Path, monkeypatch):
    """Verifies that DevTest prioritizes and executes remediation for PRs labeled 'needs-refactor'."""
    from orchestrator import poller
    from orchestrator.nodes import devtest

    # Mock open PR with needs-refactor
    mock_pr = {
        "number": 34,
        "title": "feat(harness): transient retry engine",
        "headRefName": "feat/issue-33",
        "labels": [{"name": "needs-refactor"}],
    }

    async def mock_fetch_prs(repo, label=None, limit=20):
        if label == "needs-refactor":
            return [mock_pr]
        return []

    async def mock_fetch_issues(repo, label, limit=5):
        return []

    async def mock_verify_safety(path, repo):
        return True, "Safety verified."

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr(devtest, "verify_git_safety", mock_verify_safety)

    config = GlobalConfig(
        harnesses={
            "antigravity": HarnessConfig(binary="agy", timeout_minutes=30)
        }
    )
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={
            "devtest": NodeConfig(harness="antigravity", enabled=True)
        }
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Mock adapter execution to succeed
    executed_prompts = []
    async def mock_execute(self, prompt, **kwargs):
        executed_prompts.append(prompt)
        return 0

    from orchestrator.harness import AsyncHarnessAdapter
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    # Mock subprocesses for git & gh
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b'{"reviews": [{"body": "Architectural Review: Needs refactor"}], "comments": []}', b""))
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    async def mock_subprocess_exec(*args, **kwargs):
        return mock_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    ran, msg = await run_devtest_node(project, config, state_manager)

    assert ran is True
    assert "remediated PR #34" in msg
    assert len(executed_prompts) == 1
    assert "ARCHITECTURAL CODE REVIEW FEEDBACK" in executed_prompts[0]
