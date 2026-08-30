from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

from orchestrator.config import (
    GlobalConfig,
    HarnessConfig,
    HarnessQuotaConfig,
    NodeConfig,
    ProjectConfig,
    QuotaSettings,
)
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.nodes.devtest import run_devtest_node
from orchestrator import poller


@pytest.mark.asyncio
async def test_scenario_cross_project_global_pooling(tmp_path: Path):
    """
    Scenario: Cross-project global pooling of harness quota
      Given two projects targeting harness "antigravity"
      When Project A executes a task and consumes 120k tokens
      Then available quota for Project B's next dispatch check is immediately decremented
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        harnesses={
            "antigravity": HarnessConfig(binary="antigravity", args=["-p", "{prompt}"]),
        },
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                )
            },
        ),
        projects=[
            ProjectConfig(
                name="graph-engineering",
                repo="AntaresAndBharani/graph-engineering",
                local_path=tmp_path / "graph-engineering",
                nodes={"devtest": NodeConfig(harness="antigravity")},
            ),
            ProjectConfig(
                name="second-project",
                repo="AntaresAndBharani/second-project",
                local_path=tmp_path / "second-project",
                nodes={"devtest": NodeConfig(harness="antigravity")},
            ),
        ],
    )

    proj_a = config.projects[0]
    proj_b = config.projects[1]

    # Pre-execution check: 0 tokens used, full capacity allowed
    allowed_a, res_a = await poller.check_dispatch_quota(proj_a, "devtest", config, state_manager)
    allowed_b, res_b = await poller.check_dispatch_quota(proj_b, "devtest", config, state_manager)
    assert allowed_a is True
    assert allowed_b is True
    assert res_a.used == 0
    assert res_b.used == 0
    assert res_a.remaining == 2_000_000

    # Project A executes task and records token event (120k total)
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name=proj_a.name,
        node_name="devtest",
        issue_number=49,
        prompt_tokens=100000,
        completion_tokens=20000,
        total_tokens=120000,
    )

    events = await state_manager.get_token_usage_events("antigravity", window_hours=1.0)
    assert len(events) == 1
    assert events[0]["project_name"] == "graph-engineering"
    assert events[0]["node_name"] == "devtest"
    assert events[0]["total_tokens"] == 120000

    # Verify available quota computed for Project B's next dispatch check is immediately decremented
    allowed_b_after, res_b_after = await poller.check_dispatch_quota(proj_b, "devtest", config, state_manager)
    assert allowed_b_after is True
    assert res_b_after.used == 120000
    assert res_b_after.remaining == 1880000


@pytest.mark.asyncio
async def test_scenario_poller_defers_dispatch_without_github_mutation_when_throttled(tmp_path: Path, monkeypatch):
    """
    Scenario: Poller defers dispatch without GitHub mutation when throttled
      Given a queued task whose resolved harness is currently THROTTLED
      When the poller evaluates the task for dispatch
      Then the task is deferred
      And no GitHub label mutation occurs
      And a renewal ETA is logged
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        harnesses={
            "antigravity": HarnessConfig(binary="antigravity", args=["-p", "{prompt}"]),
        },
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                )
            },
        ),
        projects=[
            ProjectConfig(
                name="graph-engineering",
                repo="AntaresAndBharani/graph-engineering",
                local_path=tmp_path / "graph-engineering",
                nodes={"devtest": NodeConfig(harness="antigravity")},
            ),
        ],
    )

    project = config.projects[0]
    (tmp_path / "graph-engineering").mkdir()

    # Pre-populate state.db with usage exceeding runway (limit 2M, required runway 200k, used 1.95M -> remaining 50k < 200k)
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name=project.name,
        node_name="devtest",
        issue_number=1,
        prompt_tokens=1500000,
        completion_tokens=450000,
        total_tokens=1950000,
    )

    # Mock poller fetching a ready-for-dev issue
    async def mock_fetch_issues(repo, label, limit=1):
        if label == "ready-for-dev":
            return [{"number": 49, "title": "feat(poller,harness): pre-flight quota gating", "body": "Issue body"}]
        return []

    async def mock_fetch_prs(repo, label=None, limit=1):
        return []

    gh_subprocess_called = False

    async def mock_create_subprocess_exec(*args, **kwargs):
        nonlocal gh_subprocess_called
        if args and args[0] == "gh":
            # Assert no GitHub mutations occur
            if len(args) > 1 and args[1] in ("issue", "pr") and len(args) > 2 and args[2] in ("edit", "create", "comment", "close"):
                gh_subprocess_called = True
        # Dummy process for git or other calls if any
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": 49,
            "title": "feat(poller,harness): pre-flight quota gating",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
        }],
    )
    mock_issue = {"number": 49, "title": "feat(poller,harness): pre-flight quota gating", "body": "Issue body", "labels": [{"name": "ready-for-dev"}]}
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", AsyncMock(return_value=mock_issue))
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)

    # Run devtest node evaluation
    ran, msg = await run_devtest_node(project, config, state_manager)

    # Task must be deferred
    assert ran is False
    assert "Quota throttled for harness 'antigravity'" in msg
    assert "Dispatch deferred" in msg
    assert "Renewal in" in msg

    # Assert no GitHub label mutation occurred
    assert gh_subprocess_called is False


@pytest.mark.asyncio
async def test_scenario_poller_automatically_dispatches_once_throttle_clears(tmp_path: Path, monkeypatch):
    """
    Scenario: Poller automatically dispatches once throttle clears
      Given a previously throttled harness now has remaining >= required runway
      When the next polling pass runs
      Then the queued task is dispatched to the node
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        harnesses={
            "antigravity": HarnessConfig(binary="antigravity", args=["-p", "{prompt}"]),
        },
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                )
            },
        ),
        projects=[
            ProjectConfig(
                name="graph-engineering",
                repo="AntaresAndBharani/graph-engineering",
                local_path=tmp_path / "graph-engineering",
                nodes={"devtest": NodeConfig(harness="antigravity")},
            ),
        ],
    )

    project = config.projects[0]
    (tmp_path / "graph-engineering").mkdir()

    # Old event aged out (2 hours ago)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name=project.name,
        node_name="devtest",
        issue_number=1,
        prompt_tokens=1500000,
        completion_tokens=450000,
        total_tokens=1950000,
        created_at=old_time,
    )

    # Check capacity: throttle has cleared!
    allowed, q_res = await poller.check_dispatch_quota(project, "devtest", config, state_manager)
    assert allowed is True
    assert q_res.used == 0
    assert q_res.remaining == 2_000_000

    # Mock poller fetching a ready-for-dev issue
    async def mock_fetch_issues(repo, label, limit=1):
        if label == "ready-for-dev":
            return [{"number": 49, "title": "feat(poller,harness): pre-flight quota gating", "body": "Issue body"}]
        return []

    async def mock_fetch_prs(repo, label=None, limit=1):
        return []

    # Mock git safety check
    async def mock_verify_git_safety(path, repo):
        return True, "Safe"

    # Mock harness execute
    harness_dispatched = False
    async def mock_harness_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None, project_name=None, node_name=None, issue_number=None, state_manager=None):
        nonlocal harness_dispatched
        harness_dispatched = True
        return 0

    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": 49,
            "title": "feat(poller,harness): pre-flight quota gating",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
        }],
    )
    mock_issue = {"number": 49, "title": "feat(poller,harness): pre-flight quota gating", "body": "Issue body", "labels": [{"name": "ready-for-dev"}]}
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", AsyncMock(return_value=mock_issue))
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", mock_verify_git_safety)
    monkeypatch.setattr("orchestrator.nodes.devtest.AsyncHarnessAdapter.execute", mock_harness_execute)
    monkeypatch.setattr("orchestrator.harness.AsyncHarnessAdapter.execute", mock_harness_execute)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd)
    monkeypatch.setattr("orchestrator.nodes.devtest.shutil.which", lambda cmd: cmd)
    monkeypatch.setattr("orchestrator.harness.shutil.which", lambda cmd: cmd)

    class MockProc:
        def __init__(self, data=b"", returncode=0):
            self._data = data
            self.returncode = returncode
        async def communicate(self):
            return self._data, b""
        async def wait(self):
            return self.returncode

    # Also mock git and gh commands
    async def mock_create_subprocess_exec(*args, **kwargs):
        if args and args[0] == "gh" and len(args) > 1 and args[1] == "pr" and args[2] == "list":
            import json
            pr_data = json.dumps([{"number": 101, "title": "feat: resolve #49", "labels": [], "headRefName": "feat/issue-49"}]).encode("utf-8")
            return MockProc(data=pr_data)
        return MockProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)

    # When next polling pass runs, the task is dispatched
    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is True, f"Failed: {msg}"
    assert harness_dispatched is True



@pytest.mark.asyncio
async def test_architect_sync_gates_on_research_harness_quota(tmp_path: Path, monkeypatch):
    """
    Asserts _sync_architecture_plane gates on research_harness (antigravity)
    rather than node primary harness (claude).
    """
    from orchestrator.nodes.architect import _sync_architecture_plane

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        harnesses={
            "antigravity": HarnessConfig(binary="antigravity", args=["-p", "{prompt}"]),
            "claude": HarnessConfig(binary="claude", args=["-p", "{prompt}"]),
        },
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                ),
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                ),
            },
        ),
        projects=[
            ProjectConfig(
                name="graph-engineering",
                repo="AntaresAndBharani/graph-engineering",
                local_path=tmp_path / "graph-engineering",
                nodes={
                    "architect": NodeConfig(
                        harness="claude",
                        research_harness="antigravity",
                    )
                },
            ),
        ],
    )

    project = config.projects[0]
    (tmp_path / "graph-engineering").mkdir()
    node_cfg = project.nodes["architect"]

    # Exhaust antigravity quota (research harness)
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash-high",
        project_name=project.name,
        node_name="architect",
        issue_number=None,
        prompt_tokens=1500000,
        completion_tokens=450000,
        total_tokens=1950000,
    )

    # Claude quota is completely empty (healthy), but antigravity is throttled
    ran, msg = await _sync_architecture_plane(project, config, state_manager, node_cfg, force=True)
    assert ran is False
    assert "Quota throttled for harness 'antigravity'" in msg


@pytest.mark.asyncio
async def test_reviewer_conflict_gates_on_conflict_harness_quota(tmp_path: Path, monkeypatch):
    """
    Asserts resolve_pr_merge_conflicts gates on conflict_harness (antigravity)
    rather than node primary harness (claude).
    """
    from orchestrator.nodes.reviewer import resolve_pr_merge_conflicts

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        harnesses={
            "antigravity": HarnessConfig(binary="antigravity", args=["-p", "{prompt}"]),
            "claude": HarnessConfig(binary="claude", args=["-p", "{prompt}"]),
        },
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                ),
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                ),
            },
        ),
        projects=[
            ProjectConfig(
                name="graph-engineering",
                repo="AntaresAndBharani/graph-engineering",
                local_path=tmp_path / "graph-engineering",
                nodes={
                    "reviewer": NodeConfig(
                        harness="claude",
                        conflict_harness="antigravity",
                    )
                },
            ),
        ],
    )

    project = config.projects[0]
    (tmp_path / "graph-engineering").mkdir()
    node_cfg = project.nodes["reviewer"]

    # Exhaust antigravity quota
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash-low",
        project_name=project.name,
        node_name="reviewer",
        issue_number=10,
        prompt_tokens=1500000,
        completion_tokens=450000,
        total_tokens=1950000,
    )

    # Mock git subprocesses
    async def mock_create_subprocess_exec(*args, **kwargs):
        proc = AsyncMock()
        # Mock git merge origin/main failing (conflict exists)
        if args and args[0] == "git" and len(args) > 1 and args[1] == "merge" and args[2] == "origin/main":
            proc.communicate.return_value = (b"CONFLICT (content): Merge conflict in file.txt", b"")
            proc.returncode = 1
        else:
            proc.communicate.return_value = (b"", b"")
            proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)

    resolved, msg = await resolve_pr_merge_conflicts(
        project, config, pr_number=10, branch_name="feat/issue-10", node_cfg=node_cfg, state_manager=state_manager
    )
    assert resolved is False
    assert "Quota throttled for harness 'antigravity'" in msg
