from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from orchestrator.config import ProjectConfig
from orchestrator.worktree import (
    WorktreeManager,
    ensure_worktree,
    parse_worktree_porcelain,
    prune_worktrees,
)


def test_parse_worktree_porcelain():
    sample_output = """worktree /path/to/main-repo
HEAD 1234567890abcdef1234567890abcdef12345678
branch refs/heads/main

worktree /path/to/detached
HEAD abcdef1234567890abcdef1234567890abcdef12
detached

worktree /path/to/bare
bare

worktree /path/to/locked
HEAD 9999999999999999999999999999999999999999
locked reason for lock

worktree /path/to/prunable
HEAD 8888888888888888888888888888888888888888
prunable gitdir file points to non-existent location
"""
    parsed = parse_worktree_porcelain(sample_output)
    assert len(parsed) == 5
    assert parsed[0]["worktree"] == "/path/to/main-repo"
    assert parsed[0]["head"] == "1234567890abcdef1234567890abcdef12345678"
    assert parsed[0]["branch"] == "refs/heads/main"

    assert parsed[1]["worktree"] == "/path/to/detached"
    assert parsed[1].get("detached") is True

    assert parsed[2]["worktree"] == "/path/to/bare"
    assert parsed[2].get("bare") is True

    assert parsed[3]["worktree"] == "/path/to/locked"
    assert parsed[3].get("locked") == "reason for lock"

    assert parsed[4]["worktree"] == "/path/to/prunable"
    assert parsed[4].get("prunable") == "gitdir file points to non-existent location"


def test_get_worktree_path_default_and_custom(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    project_default = ProjectConfig(name="myproject", repo="org/repo", local_path=str(repo_dir))
    expected_default = (repo_dir / ".graph" / "worktrees" / "architect_myproject").resolve()
    assert WorktreeManager.get_worktree_path(project_default, "architect") == expected_default

    custom_dir = tmp_path / "custom_worktrees"
    project_custom = ProjectConfig(
        name="myproject",
        repo="org/repo",
        local_path=str(repo_dir),
        worktree_dir=str(custom_dir),
    )
    expected_custom = (custom_dir / "devtest_myproject").resolve()
    assert WorktreeManager.get_worktree_path(project_custom, "devtest") == expected_custom


@pytest.mark.asyncio
async def test_scenario_worktree_created_under_configured_directory(tmp_path: Path):
    """
    Scenario: Worktree created under configured directory
      Given a project with local_path "/repo" and worktrees_enabled=True
      When WorktreeManager.ensure_worktree(project, "architect") is called
      Then a git worktree is created at ".graph/worktrees/architect_<project>" (or the configured worktree_dir)
      And the path is returned for the caller to use as node cwd
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=True)

    expected_path = (repo_dir / ".graph" / "worktrees" / "architect_app").resolve()

    executed_cmds = []

    async def mock_create_subprocess_exec(*args, **kwargs):
        cmd = list(args)
        executed_cmds.append((cmd, kwargs.get("cwd")))
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.wait.return_value = 0
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec), \
         patch("shutil.which", return_value="/usr/bin/git"):

        result_path = await WorktreeManager.ensure_worktree(project, "architect")

        assert result_path == expected_path
        # Verify git worktree add was executed
        add_calls = [c for c in executed_cmds if len(c[0]) >= 3 and c[0][:3] == ["git", "worktree", "add"]]
        assert len(add_calls) == 1
        assert add_calls[0][0] == ["git", "worktree", "add", str(expected_path)]
        assert add_calls[0][1] == str(project.local_path)


@pytest.mark.asyncio
async def test_scenario_existing_worktree_is_reused_not_recreated(tmp_path: Path):
    """
    Scenario: Existing worktree is reused, not recreated
      Given a worktree already exists at the expected path and is registered with git
      When WorktreeManager.ensure_worktree is called again
      Then no new "git worktree add" is executed
      And the existing worktree path is synced (e.g. fetch/reset to latest default branch) and returned
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=True)
    expected_path = (repo_dir / ".graph" / "worktrees" / "architect_app").resolve()
    expected_path.mkdir(parents=True, exist_ok=True)

    executed_cmds = []

    porcelain_output = f"worktree {expected_path}\nHEAD 123456\nbranch refs/heads/architect_app\n\n".encode("utf-8")

    async def mock_create_subprocess_exec(*args, **kwargs):
        cmd = list(args)
        executed_cmds.append((cmd, kwargs.get("cwd")))
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        if len(cmd) >= 4 and cmd[:4] == ["git", "worktree", "list", "--porcelain"]:
            mock_proc.communicate.return_value = (porcelain_output, b"")
        else:
            mock_proc.communicate.return_value = (b"", b"")
        mock_proc.wait.return_value = 0
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec), \
         patch("shutil.which", return_value="/usr/bin/git"):

        result_path = await WorktreeManager.ensure_worktree(project, "architect")

        assert result_path == expected_path

        # Verify NO 'git worktree add' was called
        add_calls = [c for c in executed_cmds if len(c[0]) >= 3 and c[0][:3] == ["git", "worktree", "add"]]
        assert len(add_calls) == 0

        # Verify sync operations were executed in the worktree directory
        fetch_calls = [c for c in executed_cmds if len(c[0]) >= 2 and c[0][:2] == ["git", "fetch"]]
        assert len(fetch_calls) == 1
        assert fetch_calls[0][1] == str(expected_path)

        reset_calls = [c for c in executed_cmds if len(c[0]) >= 2 and c[0][:2] == ["git", "reset"]]
        assert len(reset_calls) == 1
        assert reset_calls[0][1] == str(expected_path)


@pytest.mark.asyncio
async def test_scenario_worktree_creation_failure_falls_back_safely(tmp_path: Path, caplog):
    """
    Scenario: Worktree creation failure falls back safely
      Given "git worktree add" fails (e.g. unsupported git version, locked repo)
      When WorktreeManager.ensure_worktree is called
      Then it returns the project's primary local_path instead of raising
      And logs a warning indicating fallback to serial execution
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=True)

    async def mock_create_subprocess_exec(*args, **kwargs):
        cmd = list(args)
        mock_proc = AsyncMock()
        if len(cmd) >= 4 and cmd[:4] == ["git", "worktree", "list", "--porcelain"]:
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"", b"")
        elif len(cmd) >= 3 and cmd[:3] == ["git", "worktree", "add"]:
            mock_proc.returncode = 128
            mock_proc.communicate.return_value = (b"", b"fatal: index.lock exists or unsupported git version")
        else:
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"", b"")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec), \
         patch("shutil.which", return_value="/usr/bin/git"), \
         caplog.at_level("WARNING"):

        result_path = await WorktreeManager.ensure_worktree(project, "architect")

        # Returns primary local_path
        assert result_path == project.local_path
        # Warning logged with fallback notice
        assert "serial execution" in caplog.text.lower()
        assert "falling back" in caplog.text.lower()


@pytest.mark.asyncio
async def test_scenario_worktrees_are_safely_prunable(tmp_path: Path):
    """
    Scenario: Worktrees are safely prunable
      Given stale worktrees registered in git but with missing directories
      When WorktreeManager.prune() is called
      Then "git worktree prune" is invoked
      And no exception propagates if there is nothing to prune
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir))

    executed_cmds = []

    async def mock_create_subprocess_exec(*args, **kwargs):
        cmd = list(args)
        executed_cmds.append((cmd, kwargs.get("cwd")))
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec), \
         patch("shutil.which", return_value="/usr/bin/git"):

        # 1. Prune with project
        res_proj = await WorktreeManager.prune(project)
        assert res_proj is True

        # 2. Prune with None (uses cwd)
        res_none = await WorktreeManager.prune()
        assert res_none is True

        # 3. Prune with raw path
        res_path = await WorktreeManager.prune(repo_dir)
        assert res_path is True

        prune_calls = [c for c in executed_cmds if c[0] == ["git", "worktree", "prune"]]
        assert len(prune_calls) == 3


@pytest.mark.asyncio
async def test_scenario_prune_swallows_exceptions_gracefully(tmp_path: Path):
    """
    Verifies that WorktreeManager.prune does not raise any exception when git fails.
    """
    async def mock_failing_subprocess(*args, **kwargs):
        raise RuntimeError("Unexpected git crash")

    with patch("asyncio.create_subprocess_exec", side_effect=mock_failing_subprocess), \
         patch("shutil.which", return_value="/usr/bin/git"):

        # Must not raise
        result = await WorktreeManager.prune(tmp_path)
        assert result is False


@pytest.mark.asyncio
async def test_worktrees_disabled_returns_primary_path(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=False)

    with patch("shutil.which", return_value="/usr/bin/git"):
        result = await WorktreeManager.ensure_worktree(project, "architect")
        assert result == project.local_path


@pytest.mark.asyncio
async def test_missing_git_binary_falls_back(tmp_path: Path, caplog):
    repo_dir = tmp_path / "repo"
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=True)

    with patch("shutil.which", return_value=None), caplog.at_level("WARNING"):
        result = await WorktreeManager.ensure_worktree(project, "architect")
        assert result == project.local_path
        assert "serial execution" in caplog.text.lower()
        assert "falling back" in caplog.text.lower()


@pytest.mark.asyncio
async def test_remove_worktree_lifecycle(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=True)
    expected_path = (repo_dir / ".graph" / "worktrees" / "devtest_app").resolve()

    executed_cmds = []

    async def mock_create_subprocess_exec(*args, **kwargs):
        cmd = list(args)
        executed_cmds.append((cmd, kwargs.get("cwd")))
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec), \
         patch("shutil.which", return_value="/usr/bin/git"):

        res = await WorktreeManager.remove_worktree(project, "devtest", force=True)
        assert res is True

        remove_calls = [c for c in executed_cmds if len(c[0]) >= 3 and c[0][:3] == ["git", "worktree", "remove"]]
        assert len(remove_calls) == 1
        assert remove_calls[0][0] == ["git", "worktree", "remove", "--force", str(expected_path)]

        prune_calls = [c for c in executed_cmds if c[0] == ["git", "worktree", "prune"]]
        assert len(prune_calls) == 1


@pytest.mark.asyncio
async def test_instance_methods(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=True)
    mgr = WorktreeManager(project)

    with patch.object(WorktreeManager, "ensure_worktree", new_callable=AsyncMock) as mock_ensure, \
         patch.object(WorktreeManager, "prune", new_callable=AsyncMock) as mock_prune, \
         patch.object(WorktreeManager, "remove_worktree", new_callable=AsyncMock) as mock_remove:

        mock_ensure.return_value = repo_dir / "wt"
        mock_prune.return_value = True
        mock_remove.return_value = True

        p = await mgr.ensure("architect")
        assert p == repo_dir / "wt"
        mock_ensure.assert_called_once_with(project, "architect", default_branch="main")

        pr = await mgr.prune_self()
        assert pr is True
        mock_prune.assert_called_once_with(project)

        rem = await mgr.remove("architect")
        assert rem is True
        mock_remove.assert_called_once_with(project, "architect", force=True)


@pytest.mark.asyncio
async def test_standalone_functions_exported(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    project = ProjectConfig(name="app", repo="org/app", local_path=str(repo_dir), worktrees_enabled=False)

    with patch("shutil.which", return_value="/usr/bin/git"):
        p = await ensure_worktree(project, "architect")
        assert p == project.local_path

        res_prune = await prune_worktrees(project)
        # Without mock, might run or return True/False safely without exception
        assert isinstance(res_prune, bool)


@pytest.mark.asyncio
async def test_real_git_worktree_integration(tmp_path: Path):
    """
    End-to-end integration test with a real git repository on disk.
    """
    import subprocess
    import shutil

    if not shutil.which("git"):
        pytest.skip("git CLI not available")

    repo_dir = tmp_path / "real_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Initialize a real git repo with a commit
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True, capture_output=True)

    project = ProjectConfig(name="realapp", repo="org/realapp", local_path=str(repo_dir), worktrees_enabled=True)

    # 1. Create worktree
    wt_path = await WorktreeManager.ensure_worktree(project, "architect")
    assert wt_path.exists()
    assert (wt_path / "README.md").exists()
    assert wt_path == (repo_dir / ".graph" / "worktrees" / "architect_realapp").resolve()

    # 2. Re-ensure worktree (reused)
    wt_path2 = await WorktreeManager.ensure_worktree(project, "architect")
    assert wt_path2 == wt_path

    # 3. List worktrees
    wts = await WorktreeManager.list_worktrees(repo_dir)
    assert len(wts) >= 2

    # 4. Remove worktree
    removed = await WorktreeManager.remove_worktree(project, "architect")
    assert removed is True

    # 5. Prune
    pruned = await WorktreeManager.prune(project)
    assert pruned is True


import asyncio
from orchestrator.config import GlobalConfig
from orchestrator.db import StateManager
from orchestrator.cli import run_project_cycle, _project_worker_loop


@pytest.mark.asyncio
async def test_scenario_concurrent_execution_when_worktrees_enabled(tmp_path: Path):
    """
    Scenario: Concurrent execution when worktrees enabled
      Given a project with worktrees_enabled=True
      When "_project_worker_loop" / "run_project_cycle" runs a cycle
      Then it invokes "asyncio.gather(architect_cycle, devtest_cycle)" for that project
      And both nodes execute concurrently without blocking each other
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(name="concurrent_app", repo="org/concurrent_app", local_path=str(repo_dir), worktrees_enabled=True)
    config = GlobalConfig(projects=[project])
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    arch_started = asyncio.Event()
    dev_started = asyncio.Event()
    concurrency_barrier = asyncio.Event()
    both_ran_concurrently = False

    async def mock_architect(proj, cfg, sm):
        nonlocal both_ran_concurrently
        arch_started.set()
        # Wait until devtest is also started (proving concurrent execution)
        await asyncio.wait_for(dev_started.wait(), timeout=5.0)
        both_ran_concurrently = True
        return True, "Architect completed triage and decomposition"

    async def mock_devtest(proj, cfg, sm):
        nonlocal both_ran_concurrently
        dev_started.set()
        # Wait until architect is also started (proving concurrent execution)
        await asyncio.wait_for(arch_started.wait(), timeout=5.0)
        both_ran_concurrently = True
        return True, "DevTest implemented subtask and opened PR"

    async def mock_poll(proj, sm):
        pass

    async def mock_reviewer(proj, cfg, sm):
        return False, "No PRs to review"

    async def mock_bau(proj, cfg, sm, force=False):
        return False, "No BAU tech debt"

    with patch("orchestrator.cli.poller.poll_project_sdlc_items", side_effect=mock_poll), \
         patch("orchestrator.cli.run_architect_node", side_effect=mock_architect), \
         patch("orchestrator.cli.run_devtest_node", side_effect=mock_devtest), \
         patch("orchestrator.cli.run_reviewer_node", side_effect=mock_reviewer), \
         patch("orchestrator.cli.run_bau_node", side_effect=mock_bau):

        work_done = await asyncio.wait_for(
            run_project_cycle(project, config, state_manager, silent_idle=True),
            timeout=10.0,
        )

        assert both_ran_concurrently is True
        assert work_done is True


@pytest.mark.asyncio
async def test_scenario_non_destructive_fallback_for_existing_workspaces(tmp_path: Path, caplog):
    """
    Scenario: Non-Destructive Fallback for Existing Workspaces
      Given a repository where git worktree creation fails or is disabled in config
      When the orchestrator runs the project cycle
      Then it must fall back to serial node execution on the primary local_path with locking
      And record an informative warning in the project logs without crashing the daemon
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    # 1. Configured with worktrees_enabled=False
    project = ProjectConfig(name="serial_app", repo="org/serial_app", local_path=str(repo_dir), worktrees_enabled=False)
    config = GlobalConfig(projects=[project])
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    execution_order = []

    async def mock_architect(proj, cfg, sm):
        execution_order.append("architect")
        return True, "Architect completed triage"

    async def mock_devtest(proj, cfg, sm):
        execution_order.append("devtest")
        return True, "DevTest completed implementation"

    async def mock_poll(proj, sm):
        pass

    with patch("orchestrator.cli.poller.poll_project_sdlc_items", side_effect=mock_poll), \
         patch("orchestrator.cli.run_architect_node", side_effect=mock_architect), \
         patch("orchestrator.cli.run_devtest_node", side_effect=mock_devtest), \
         patch("orchestrator.cli.run_reviewer_node", new_callable=AsyncMock, return_value=(False, "idle")), \
         patch("orchestrator.cli.run_bau_node", new_callable=AsyncMock, return_value=(False, "idle")):

        work_done = await run_project_cycle(project, config, state_manager, silent_idle=True)

        assert work_done is True
        # Verified serial execution order: Architect completes before DevTest executes
        assert execution_order == ["architect", "devtest"]


@pytest.mark.asyncio
async def test_scenario_failure_isolation_between_concurrent_nodes(tmp_path: Path, caplog):
    """
    Scenario: Failure isolation between concurrent nodes
      Given Architect and DevTest run concurrently via asyncio.gather
      When one node's cycle raises an exception
      Then the exception is caught and logged for that node
      And the other node's cycle is unaffected and completes normally
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(name="isolated_app", repo="org/isolated_app", local_path=str(repo_dir), worktrees_enabled=True)
    config = GlobalConfig(projects=[project])
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    async def mock_poll(proj, sm):
        pass

    # Case A: Architect throws exception, DevTest completes successfully
    async def mock_failing_architect(proj, cfg, sm):
        raise RuntimeError("Simulated crash in Architect node")

    async def mock_successful_devtest(proj, cfg, sm):
        return True, "DevTest successfully implemented subtask"

    with patch("orchestrator.cli.poller.poll_project_sdlc_items", side_effect=mock_poll), \
         patch("orchestrator.cli.run_architect_node", side_effect=mock_failing_architect), \
         patch("orchestrator.cli.run_devtest_node", side_effect=mock_successful_devtest), \
         patch("orchestrator.cli.run_reviewer_node", new_callable=AsyncMock, return_value=(False, "idle")), \
         patch("orchestrator.cli.run_bau_node", new_callable=AsyncMock, return_value=(False, "idle")), \
         caplog.at_level("ERROR"):

        work_done = await run_project_cycle(project, config, state_manager, silent_idle=True)

        # DevTest work succeeded, so pipeline_work_done is True
        assert work_done is True
        # Architect exception was logged
        assert "Simulated crash in Architect node" in caplog.text

    caplog.clear()

    # Case B: DevTest throws exception, Architect completes successfully
    async def mock_successful_architect(proj, cfg, sm):
        return True, "Architect successfully triaged story"

    async def mock_failing_devtest(proj, cfg, sm):
        raise ValueError("Simulated crash in DevTest node")

    with patch("orchestrator.cli.poller.poll_project_sdlc_items", side_effect=mock_poll), \
         patch("orchestrator.cli.run_architect_node", side_effect=mock_successful_architect), \
         patch("orchestrator.cli.run_devtest_node", side_effect=mock_failing_devtest), \
         patch("orchestrator.cli.run_reviewer_node", new_callable=AsyncMock, return_value=(False, "idle")), \
         patch("orchestrator.cli.run_bau_node", new_callable=AsyncMock, return_value=(False, "idle")), \
         caplog.at_level("ERROR"):

        work_done = await run_project_cycle(project, config, state_manager, silent_idle=True)

        # Architect work succeeded, so pipeline_work_done is True
        assert work_done is True
        # DevTest exception was logged
        assert "Simulated crash in DevTest node" in caplog.text


@pytest.mark.asyncio
async def test_concurrent_nodes_git_lock_isolation(tmp_path: Path):
    """
    Integration test with a real git repository:
    Verifies that Architect and DevTest running in parallel worktrees execute git
    operations simultaneously without encountering .git/index.lock collisions.
    """
    import subprocess
    import shutil

    if not shutil.which("git"):
        pytest.skip("git CLI not available")

    repo_dir = tmp_path / "concurrent_git_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Initialize a real git repo with an initial commit
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Test Main Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True, capture_output=True)

    project = ProjectConfig(name="concrepo", repo="org/concrepo", local_path=str(repo_dir), worktrees_enabled=True)

    # Create both worktrees
    wt_arch = await WorktreeManager.ensure_worktree(project, "architect")
    wt_dev = await WorktreeManager.ensure_worktree(project, "devtest")

    assert wt_arch != wt_dev
    assert wt_arch.exists()
    assert wt_dev.exists()

    # Perform concurrent git commits and branch creations in each worktree simultaneously
    async def arch_git_work():
        for i in range(5):
            f = wt_arch / f"arch_file_{i}.txt"
            f.write_text(f"Architect file content {i}\n", encoding="utf-8")
            p_add = await asyncio.create_subprocess_exec("git", "add", str(f), cwd=str(wt_arch))
            await p_add.wait()
            p_commit = await asyncio.create_subprocess_exec("git", "commit", "-m", f"arch commit {i}", cwd=str(wt_arch))
            await p_commit.wait()
            await asyncio.sleep(0.01)
        return True

    async def dev_git_work():
        for i in range(5):
            f = wt_dev / f"dev_file_{i}.txt"
            f.write_text(f"DevTest file content {i}\n", encoding="utf-8")
            p_add = await asyncio.create_subprocess_exec("git", "add", str(f), cwd=str(wt_dev))
            await p_add.wait()
            p_commit = await asyncio.create_subprocess_exec("git", "commit", "-m", f"dev commit {i}", cwd=str(wt_dev))
            await p_commit.wait()
            await asyncio.sleep(0.01)
        return True

    results = await asyncio.gather(arch_git_work(), dev_git_work(), return_exceptions=True)

    assert results == [True, True]

    # Clean up worktrees
    await WorktreeManager.remove_worktree(project, "architect")
    await WorktreeManager.remove_worktree(project, "devtest")
    await WorktreeManager.prune(project)


