from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.config import GlobalConfig, LabelConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.housekeeping import (
    sync_all_projects_labels,
    sync_repository_labels,
)
from orchestrator.cli import ConfigHolder, _project_worker_loop


def _make_sample_labels() -> List[LabelConfig]:
    return [
        LabelConfig(name="needs-triage", color="E2B7E1", description="Awaiting Architect Node triage and decomposition"),
        LabelConfig(name="ready-for-dev", color="0E8A16", description="Awaiting 3Amigos DevTest implementation"),
        LabelConfig(name="queued", color="CFD3D7", description="Subtask queued for sequential execution"),
    ]


class MockProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    async def wait(self) -> int:
        return self.returncode


# ============================================================================
# Scenario 1: Instant non-blocking TUI dashboard startup with first-cycle worker barrier
# ============================================================================

@pytest.mark.asyncio
async def test_worker_barrier_synchronization(tmp_path: Path):
    """
    Scenario: Instant non-blocking TUI dashboard startup with first-cycle worker barrier
    Each project worker loop must wait on its respective sync completion event (timeout 60s)
    before executing its first cycle. Once set, the worker proceeds immediately.
    """
    state_mgr = StateManager(tmp_path / "state.db")
    await state_mgr.init_db()

    project = ProjectConfig(name="proj1", repo="org/proj1", local_path=str(tmp_path))
    config = GlobalConfig(projects=[project])
    holder = ConfigHolder(config)

    sync_event = asyncio.Event()
    worker_entered_first_cycle = asyncio.Event()

    async def mock_run_project_cycle(proj, cfg, sm, silent_idle=False):
        worker_entered_first_cycle.set()
        # Request stop so worker loop terminates after first cycle
        await sm.request_stop()
        return False

    with patch("orchestrator.cli.run_project_cycle", side_effect=mock_run_project_cycle):
        # Spawn worker task
        worker_task = asyncio.create_task(
            _project_worker_loop(project, holder, state_mgr, interval=1, sync_event=sync_event)
        )

        # Worker should be waiting on sync_event barrier; first cycle must not have run yet
        await asyncio.sleep(0.05)
        assert not worker_entered_first_cycle.is_set()
        assert not worker_task.done()

        # Simulate background sync completing and setting the event
        sync_event.set()

        # Worker should now unblock and run first cycle
        await asyncio.wait_for(worker_entered_first_cycle.wait(), timeout=2.0)
        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_entered_first_cycle.is_set()


@pytest.mark.asyncio
async def test_worker_barrier_timeout_proceeds(tmp_path: Path):
    """
    When sync_event times out, the worker must proceed with its first cycle rather than deadlock.
    """
    state_mgr = StateManager(tmp_path / "state.db")
    await state_mgr.init_db()

    project = ProjectConfig(name="proj1", repo="org/proj1", local_path=str(tmp_path))
    config = GlobalConfig(projects=[project])
    holder = ConfigHolder(config)

    sync_event = asyncio.Event()
    sync_event.wait = AsyncMock(side_effect=asyncio.TimeoutError)
    worker_entered = asyncio.Event()

    async def mock_run_project_cycle(proj, cfg, sm, silent_idle=False):
        worker_entered.set()
        await sm.request_stop()
        return False

    with patch("orchestrator.cli.run_project_cycle", side_effect=mock_run_project_cycle):
        worker_task = asyncio.create_task(
            _project_worker_loop(project, holder, state_mgr, interval=1, sync_event=sync_event)
        )
        await asyncio.wait_for(worker_entered.wait(), timeout=2.0)
        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_entered.is_set()


# ============================================================================
# Scenario 2: Smart single-pass repository label synchronization with one-shot purge guard
# ============================================================================

@pytest.mark.asyncio
async def test_smart_label_sync_single_pass_and_case_folding(tmp_path: Path):
    """
    Scenario: Smart single-pass repository label synchronization with one-shot purge guard
    - Fetches existing labels via a single 'gh label list --json name,color,description --limit 200' call
    - Normalizes colors via color.lstrip('#').casefold() to prevent false-positive drift
    - Issues 'gh label create --force' only if missing or normalized color/description differs
    - Returns True for verified-already-correct labels without invoking 'gh label create'
    """
    state_mgr = StateManager(tmp_path / "state.db")
    await state_mgr.init_db()

    managed_labels = _make_sample_labels()
    # Remote state:
    # 1. 'needs-triage': color is '#e2b7e1' (lowercase, with #) -> matches 'E2B7E1' after case-folding & lstrip
    # 2. 'ready-for-dev': color is '112233' -> differs from '0E8A16', needs create --force
    # 3. 'queued': missing entirely -> needs create --force
    # 4. 'status:ready': obsolete legacy label -> should be deleted on first pass
    remote_labels = [
        {"name": "needs-triage", "color": "#e2b7e1", "description": "Awaiting Architect Node triage and decomposition"},
        {"name": "ready-for-dev", "color": "112233", "description": "Awaiting 3Amigos DevTest implementation"},
        {"name": "status:ready", "color": "abcdef", "description": "Obsolete"},
    ]

    executed_cmds: List[List[str]] = []

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        cmd_list = list(cmd)
        executed_cmds.append(cmd_list)
        if cmd_list[:3] == ["gh", "label", "list"]:
            return MockProcess(stdout=json.dumps(remote_labels).encode("utf-8"), returncode=0)
        return MockProcess(stdout=b"", returncode=0)

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):

        results = await sync_repository_labels(
            repo="org/repo1",
            labels=managed_labels,
            purge_legacy=True,
            state_manager=state_mgr,
        )

    # 1. Assert gh label list was called with exact arguments
    list_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "list"]]
    assert len(list_calls) == 1
    assert list_calls[0] == ["gh", "label", "list", "--repo", "org/repo1", "--json", "name,color,description", "--limit", "200"]

    # 2. Assert obsolete label 'status:ready' was deleted
    delete_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "delete"]]
    assert len(delete_calls) == 1
    assert delete_calls[0] == ["gh", "label", "delete", "status:ready", "--repo", "org/repo1", "--yes"]

    # 3. Assert purge guard key is recorded in daemon_control
    purge_val = await state_mgr.get_daemon_control_value("legacy_purge_done:org/repo1")
    assert purge_val == "1"

    # 4. Assert 'needs-triage' was verified-already-correct and NOT recreated
    create_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "create"]]
    create_names = [c[3] for c in create_calls]
    assert "needs-triage" not in create_names
    assert "ready-for-dev" in create_names
    assert "queued" in create_names

    # 5. Assert returned dictionary reports True for verified-already-correct and newly created
    assert results["needs-triage"] is True
    assert results["ready-for-dev"] is True
    assert results["queued"] is True


@pytest.mark.asyncio
async def test_purge_guard_skips_deletion_when_recorded(tmp_path: Path):
    """
    Scenario: One-shot purge guard skips obsolete label deletion pass if recorded in daemon_control.
    """
    state_mgr = StateManager(tmp_path / "state.db")
    await state_mgr.init_db()

    # Pre-record the purge guard in daemon_control
    await state_mgr.set_daemon_control_value("legacy_purge_done:org/repo2", "1")

    managed_labels = _make_sample_labels()
    # Remote state includes an obsolete label, but purge guard is set so it should NOT be deleted
    remote_labels = [
        {"name": "needs-triage", "color": "E2B7E1", "description": "Awaiting Architect Node triage and decomposition"},
        {"name": "ready-for-dev", "color": "0E8A16", "description": "Awaiting 3Amigos DevTest implementation"},
        {"name": "queued", "color": "CFD3D7", "description": "Subtask queued for sequential execution"},
        {"name": "status:definition", "color": "111111", "description": "Legacy label"},
    ]

    executed_cmds: List[List[str]] = []

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        cmd_list = list(cmd)
        executed_cmds.append(cmd_list)
        if cmd_list[:3] == ["gh", "label", "list"]:
            return MockProcess(stdout=json.dumps(remote_labels).encode("utf-8"), returncode=0)
        return MockProcess(stdout=b"", returncode=0)

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):

        results = await sync_repository_labels(
            repo="org/repo2",
            labels=managed_labels,
            purge_legacy=True,
            state_manager=state_mgr,
        )

    # ZERO delete calls and ZERO create calls (all 3 managed labels already correct)
    delete_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "delete"]]
    create_calls = [c for c in executed_cmds if c[:3] == ["gh", "label", "create"]]
    assert len(delete_calls) == 0
    assert len(create_calls) == 0
    assert all(results.values())


# ============================================================================
# Scenario 3: Compilation safety and keyword-only state_manager parameter
# ============================================================================

@pytest.mark.asyncio
async def test_keyword_only_state_manager_compilation_safety():
    """
    Scenario: Compilation safety and keyword-only state_manager parameter
    sync_repository_labels must accept '*, state_manager: Optional[StateManager] = None'.
    Existing callers without state_manager must function seamlessly.
    Positional passing of state_manager must raise TypeError.
    """
    managed_labels = _make_sample_labels()

    executed_cmds: List[List[str]] = []

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        cmd_list = list(cmd)
        executed_cmds.append(cmd_list)
        if cmd_list[:3] == ["gh", "label", "list"]:
            return MockProcess(stdout=b"[]", returncode=0)
        return MockProcess(stdout=b"", returncode=0)

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):

        # Call with 2 positional args
        res1 = await sync_repository_labels("org/repo", managed_labels)
        assert len(res1) == 3

        # Call with 3 positional args (purge_legacy=False)
        res2 = await sync_repository_labels("org/repo", managed_labels, False)
        assert len(res2) == 3

        # Call with keyword-only state_manager=None
        res3 = await sync_repository_labels("org/repo", managed_labels, purge_legacy=True, state_manager=None)
        assert len(res3) == 3

        # Positional passing of 4th argument must raise TypeError
        with pytest.raises(TypeError):
            await sync_repository_labels("org/repo", managed_labels, True, None)  # type: ignore


# ============================================================================
# Additional Concurrency and Edge Case Tests
# ============================================================================

@pytest.mark.asyncio
async def test_sync_all_projects_labels_concurrency_and_events():
    """
    sync_all_projects_labels syncs enabled projects concurrently and triggers sync_events.
    """
    projects = [
        ProjectConfig(name="p1", repo="org/p1", enabled=True, local_path="."),
        ProjectConfig(name="p2", repo="org/p2", enabled=True, local_path="."),
        ProjectConfig(name="p3", repo="org/p3", enabled=False, local_path="."),
    ]
    labels = _make_sample_labels()
    sync_events = {"p1": asyncio.Event(), "p2": asyncio.Event()}

    async def mock_sync_repo(repo, managed_labels, purge_legacy=True, *, state_manager=None):
        await asyncio.sleep(0.01)
        return {lbl.name: True for lbl in managed_labels}

    with patch("orchestrator.housekeeping.sync_repository_labels", side_effect=mock_sync_repo):
        all_results = await sync_all_projects_labels(
            projects=projects,
            labels=labels,
            concurrency=2,
            sync_events=sync_events,
        )

    assert "org/p1" in all_results
    assert "org/p2" in all_results
    assert "org/p3" not in all_results
    assert sync_events["p1"].is_set()
    assert sync_events["p2"].is_set()


@pytest.mark.asyncio
async def test_gh_not_installed():
    """When gh is not installed, sync_repository_labels returns False for all labels."""
    labels = _make_sample_labels()
    with patch("shutil.which", return_value=None):
        res = await sync_repository_labels("org/repo", labels)
        assert all(v is False for v in res.values())


@pytest.mark.asyncio
async def test_gh_list_error_returns_false():
    """When gh label list fails with non-zero returncode, all labels return False."""
    labels = _make_sample_labels()
    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("asyncio.create_subprocess_exec", return_value=MockProcess(stderr=b"auth error", returncode=1)):
        res = await sync_repository_labels("org/repo", labels)
        assert all(v is False for v in res.values())
