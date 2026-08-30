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
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
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


@pytest.mark.asyncio
async def test_devtest_e2e_auto_merges_pr_when_ci_green(tmp_path: Path, monkeypatch):
    """Verifies that DevTest performs E2E verification and auto-merges the PR when CI is green."""
    from orchestrator import poller
    from orchestrator.nodes import devtest

    mock_issue = {
        "number": 42,
        "title": "feat: user authentication",
        "labels": [{"name": "ready-for-dev"}],
    }

    mock_pr = {
        "number": 99,
        "title": "feat: resolve #42 - user authentication",
        "headRefName": "feat/issue-42",
        "labels": [],
    }

    async def mock_fetch_prs(repo, label=None, limit=20):
        if label == "needs-refactor":
            return []
        return [mock_pr]

    async def mock_fetch_issues(repo, label, limit=5):
        if label == "ready-for-dev":
            return [mock_issue]
        return []

    async def mock_verify_safety(path, repo):
        return True, "Safety verified."

    async def mock_ci_status(repo, pr_number):
        return "PASS", "All CI checks passed (100% green)"

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "verify_git_safety", mock_verify_safety)
    monkeypatch.setattr(devtest, "check_pr_ci_status", mock_ci_status)

    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="agy", timeout_minutes=30)}
    )
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(harness="antigravity", enabled=True, auto_merge_approved=True)},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    from orchestrator.harness import AsyncHarnessAdapter
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b'[{"number": 99, "title": "feat: resolve #42", "labels": [], "headRefName": "feat/issue-42"}]', b""))
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc))

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is True
    assert "auto-merged PR #99 into main" in msg

    sdlc_items = await state_manager.get_sdlc_items(project.name)
    assert len(sdlc_items) > 0
    assert sdlc_items[0]["state"] == "MERGED"


@pytest.mark.asyncio
async def test_devtest_e2e_flags_needs_refactor_when_ci_fails(tmp_path: Path, monkeypatch):
    """Verifies that DevTest flags the PR with needs-refactor when remote CI fails."""
    from orchestrator import poller
    from orchestrator.nodes import devtest

    mock_issue = {
        "number": 43,
        "title": "feat: payment gateway",
        "labels": [{"name": "ready-for-dev"}],
    }

    mock_pr = {
        "number": 100,
        "title": "feat: resolve #43 - payment gateway",
        "headRefName": "feat/issue-43",
        "labels": [],
    }

    async def mock_fetch_prs(repo, label=None, limit=20):
        if label == "needs-refactor":
            return []
        return [mock_pr]

    async def mock_fetch_issues(repo, label, limit=5):
        if label == "ready-for-dev":
            return [mock_issue]
        return []

    async def mock_verify_safety(path, repo):
        return True, "Safety verified."

    async def mock_ci_status(repo, pr_number):
        return "FAIL", "Failing checks: lint, test"

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "verify_git_safety", mock_verify_safety)
    monkeypatch.setattr(devtest, "check_pr_ci_status", mock_ci_status)

    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="agy", timeout_minutes=30)}
    )
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(harness="antigravity", enabled=True, auto_merge_approved=True)},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    from orchestrator.harness import AsyncHarnessAdapter
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b'[{"number": 100, "title": "feat: resolve #43", "labels": [], "headRefName": "feat/issue-43"}]', b""))
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc))

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is False
    assert "failed CI checks" in msg


@pytest.mark.asyncio
async def test_devtest_phase2_auto_merges_open_implemented_pr_when_ci_green(tmp_path: Path, monkeypatch):
    """Verifies that DevTest Phase 2 sweeps open dev-implemented PRs, validates CI green, and auto-merges."""
    from orchestrator import poller
    from orchestrator.nodes import devtest

    mock_pr = {
        "number": 85,
        "title": "feat(worktree): WorktreeManager module",
        "headRefName": "feat/issue-79",
        "body": "Closes #79\n\nParent: #77",
        "labels": [{"name": "dev-implemented"}],
    }

    async def mock_fetch_prs(repo, label=None, limit=20):
        if label == "dev-implemented":
            return [mock_pr]
        return []

    async def mock_fetch_issues(repo, label, limit=5):
        return []

    async def mock_ci_status(repo, pr_number):
        return "PASS", "All CI checks passed (100% green)"

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "check_pr_ci_status", mock_ci_status)

    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="agy", timeout_minutes=30)}
    )
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(harness="antigravity", enabled=True, auto_merge_approved=True)},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    executed_cmds = []

    async def mock_subprocess_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        return mock_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "C:\\Program Files\\GitHub CLI\\gh.exe")

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is True
    assert "auto-merged PR #85" in msg

    # Verify that gh pr merge and gh issue close were executed
    merged = any("merge" in c and "85" in c for c in executed_cmds)
    closed = any("close" in c and "79" in c for c in executed_cmds)
    assert merged is True
    assert closed is True


