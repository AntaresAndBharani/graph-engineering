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


@pytest.mark.asyncio
async def test_devtest_oversized_issue_body_offloaded_to_file(tmp_path: Path, monkeypatch):
    """Verifies that an issue body > 8000 chars is offloaded to .graph/ISSUE_SPECIFICATION.md."""
    from orchestrator import poller
    from orchestrator.nodes import devtest
    from orchestrator.worktree import WorktreeManager

    huge_body = "A" * 15000
    mock_issue = {
        "number": 99,
        "title": "feat: massive spec",
        "body": huge_body,
        "labels": [{"name": "ready-for-dev"}],
    }

    async def mock_fetch_prs(repo, label=None, limit=20):
        return []

    async def mock_fetch_issues(repo, label, limit=5):
        if label == "ready-for-dev":
            return [mock_issue]
        return []

    async def mock_verify_safety(path, repo):
        return True, "Safety verified."

    async def mock_ensure_worktree(project, node_name):
        return tmp_path

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "verify_git_safety", mock_verify_safety)
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", mock_ensure_worktree)

    captured_prompt = None

    class MockAdapter:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, prompt, cwd, log_file, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return 0

    monkeypatch.setattr(devtest, "AsyncHarnessAdapter", MockAdapter)

    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="agy", timeout_minutes=30)}
    )
    project = ProjectConfig(
        name="test-repo",
        repo="BasketIQ/test-repo",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(harness="antigravity", enabled=True)},
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()
    await state_manager.sync_project_sdlc_items("test-repo", [{
        "issue_number": 99,
        "title": "feat: massive spec",
        "state": "OPEN",
        "labels": "ready-for-dev",
        "item_type": "TASK",
        "sequence_order": 1,
        "parent_issue_id": None,
    }])

    async def mock_fetch_issue_by_num(repo, num):
        if num == 99:
            return mock_issue
        return None

    monkeypatch.setattr(devtest, "fetch_issue_by_number", mock_fetch_issue_by_num)

    async def mock_subprocess_exec(*args, **kwargs):
        mock_proc = AsyncMock()
        if "pr" in args and "list" in args:
            pr_data = json.dumps([{"number": 101, "title": "feat: test", "headRefName": "feat/issue-99", "state": "OPEN", "labels": [], "statusCheckRollup": []}])
            mock_proc.communicate = AsyncMock(return_value=(pr_data.encode("utf-8"), b""))
        else:
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        return mock_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "C:\\Program Files\\GitHub CLI\\gh.exe")

    await run_devtest_node(project, config, state_manager)

    # Verify that .graph/ISSUE_SPECIFICATION.md was created
    spec_file = tmp_path / ".graph" / "ISSUE_SPECIFICATION.md"
    assert spec_file.exists()
    assert spec_file.read_text(encoding="utf-8") == huge_body

    # Verify that captured prompt references the file rather than blowing up CLI args
    assert captured_prompt is not None
    assert "saved to '.graph/ISSUE_SPECIFICATION.md'" in captured_prompt
    assert len(captured_prompt) < 4000


@pytest.mark.asyncio
async def test_devtest_development_concurrency_limit(tmp_path: Path, monkeypatch):
    """Verifies that DevTest defers a project when max_concurrent_developing_projects is reached."""
    from orchestrator import poller
    from orchestrator.nodes import devtest
    from orchestrator.config import SettingsConfig

    async def mock_fetch_prs(repo, label=None, limit=20):
        return []

    async def mock_fetch_issue(repo, num):
        return {"number": 105, "title": "Subtask 105", "state": "OPEN", "labels": [{"name": "ready-for-dev"}]}

    monkeypatch.setattr(poller, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "fetch_issue_by_number", mock_fetch_issue)

    config = GlobalConfig(settings=SettingsConfig(max_concurrent_developing_projects=2))
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    # Pre-occupy 2 development slots with other projects
    await state_manager.acquire_development_slot("proj-1", "org/repo-1", max_slots=2)
    await state_manager.acquire_development_slot("proj-2", "org/repo-2", max_slots=2)

    # Setup project 3 with a ready-for-dev subtask
    project3 = ProjectConfig(name="proj-3", repo="org/repo-3", local_path=str(tmp_path))
    await state_manager.sync_project_sdlc_items(
        "proj-3",
        [{
            "issue_number": 105,
            "title": "Subtask 105",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "item_type": "SUBTASK",
            "sequence_order": 1,
        }],
    )

    # 1. Project 3 runs devtest -> Concurrency limit reached, ignored/deferred
    ran, msg = await run_devtest_node(project3, config, state_manager)
    assert ran is False
    assert "Development concurrency limit reached" in msg
    assert "2/2 active" in msg
    assert "Project 'proj-3' deferred" in msg

    # 2. Release slot for proj-1 (simulating finished development)
    await state_manager.release_development_slot("proj-1")

    # 3. Project 3 should now acquire the slot
    # Mock adapter & git status to exit cleanly
    class DummyAdapter:
        def __init__(self, *args, **kwargs):
            pass
        async def execute(self, *args, **kwargs):
            return 0

    monkeypatch.setattr(devtest, "AsyncHarnessAdapter", DummyAdapter)
    monkeypatch.setattr(devtest, "verify_git_safety", AsyncMock(return_value=(True, "ok")))

    async def mock_subprocess_exec(*args, **kwargs):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        return mock_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)

    await run_devtest_node(project3, config, state_manager)
    # Slot is acquired by proj-3
    assert "proj-3" in await state_manager.get_active_development_slots()




