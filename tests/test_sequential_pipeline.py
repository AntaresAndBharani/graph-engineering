from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.nodes.architect import build_triage_prompt
from orchestrator.nodes.devtest import _advance_parent_and_unlock_next_subtask


@pytest.mark.asyncio
async def test_db_sdlc_items_hierarchical_columns_and_helpers(tmp_path: Path):
    """Tests SQLite schema extensions and StateManager story/subtask query helpers."""
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project_name = "test-project"

    items = [
        {
            "issue_number": 50,
            "title": "Epic: User Authentication",
            "state": "OPEN",
            "labels": ["architect-processed"],
            "item_type": "STORY",
            "sequence_order": 0,
        },
        {
            "issue_number": 51,
            "parent_issue_id": 50,
            "title": "Subtask 1: Database Migration",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "item_type": "SUBTASK",
            "sequence_order": 1,
        },
        {
            "issue_number": 52,
            "parent_issue_id": 50,
            "title": "Subtask 2: Auth Endpoints",
            "state": "OPEN",
            "labels": ["queued"],
            "item_type": "SUBTASK",
            "sequence_order": 2,
        },
    ]

    await state_manager.sync_project_sdlc_items(project_name, items)

    # 1. Test get_active_story
    story = await state_manager.get_active_story(project_name)
    assert story is not None
    assert story["issue_number"] == 50
    assert story["item_type"] == "STORY"

    # 2. Test get_pending_subtasks
    subtasks = await state_manager.get_pending_subtasks(project_name, 50)
    assert len(subtasks) == 2
    assert [s["issue_number"] for s in subtasks] == [51, 52]

    # 3. Test get_next_queued_subtask
    queued = await state_manager.get_next_queued_subtask(project_name, 50)
    assert queued is not None
    assert queued["issue_number"] == 52


def test_architect_build_triage_prompt_queued_instructions():
    """Verifies that Architect triage prompt instructs 1st subtask active and remaining queued."""
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=".",
    )

    prompt = build_triage_prompt(
        project=project,
        issue_id=50,
        issue_title="Epic: User Auth",
        trigger="needs-triage",
        output_label="ready-for-dev",
        processed_label="architect-processed",
    )

    assert "Create Subtask 1 (Active)" in prompt
    assert "ready-for-dev" in prompt
    assert "Create Subtasks 2..N (Queued)" in prompt
    assert "queued" in prompt
    assert "architect-processed" in prompt


@pytest.mark.asyncio
async def test_devtest_advances_parent_and_unlocks_next_queued_subtask(tmp_path: Path, monkeypatch):
    """Verifies that completing a subtask checks off parent checklist and unlocks the next queued subtask."""
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    subtask_data = {
        "number": 51,
        "title": "Subtask 1: Migration",
        "body": "Acceptance criteria.\n\nParent: #50",
        "labels": [{"name": "dev-implemented"}],
    }

    parent_data = {
        "number": 50,
        "title": "Epic: User Authentication",
        "body": "# Description\n\n## Subtasks\n- [ ] #51 - Migration\n- [ ] #52 - Auth API\n",
        "state": "OPEN",
        "labels": [{"name": "architect-processed"}],
    }

    children_data = [
        {"number": 51, "title": "Subtask 1: Migration", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
        {"number": 52, "title": "Subtask 2: Auth API", "state": "OPEN", "labels": [{"name": "queued"}]},
    ]

    executed_cmds = []

    async def mock_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        cmd_str = " ".join(str(a) for a in args)
        mock_p = AsyncMock()
        mock_p.returncode = 0
        mock_p.wait = AsyncMock(return_value=0)

        if "issue view 51" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(subtask_data).encode("utf-8"), b""))
        elif "issue view 50" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(parent_data).encode("utf-8"), b""))
        elif "issue list" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(children_data).encode("utf-8"), b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "C:\\Program Files\\GitHub CLI\\gh.exe")

    await _advance_parent_and_unlock_next_subtask(project, state_manager, 51)

    unlocked = any("edit" in c and "52" in c and "ready-for-dev" in c for c in executed_cmds)
    assert unlocked is True


@pytest.mark.asyncio
async def test_devtest_closes_parent_when_all_subtasks_closed(tmp_path: Path, monkeypatch):
    """Verifies that completing the final child subtask automatically closes the parent story."""
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    subtask_data = {
        "number": 52,
        "title": "Subtask 2: Auth API",
        "body": "Acceptance criteria.\n\nParent: #50",
        "labels": [{"name": "dev-implemented"}],
    }

    parent_data = {
        "number": 50,
        "title": "Epic: User Authentication",
        "body": "# Description\n\n## Subtasks\n- [x] #51 - Migration\n- [ ] #52 - Auth API\n",
        "state": "OPEN",
        "labels": [{"name": "architect-processed"}],
    }

    children_data = [
        {"number": 51, "title": "Subtask 1: Migration", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
        {"number": 52, "title": "Subtask 2: Auth API", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
    ]

    executed_cmds = []

    async def mock_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        cmd_str = " ".join(str(a) for a in args)
        mock_p = AsyncMock()
        mock_p.returncode = 0
        mock_p.wait = AsyncMock(return_value=0)

        if "issue view 52" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(subtask_data).encode("utf-8"), b""))
        elif "issue view 50" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(parent_data).encode("utf-8"), b""))
        elif "issue list" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(children_data).encode("utf-8"), b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "C:\\Program Files\\GitHub CLI\\gh.exe")

    await _advance_parent_and_unlock_next_subtask(project, state_manager, 52)

    closed_parent = any("issue" in c and "close" in c and "50" in c for c in executed_cmds)
    assert closed_parent is True

    sdlc_items = await state_manager.get_sdlc_items(project.name)
    assert len(sdlc_items) > 0
    parent_sdlc = next(item for item in sdlc_items if item["issue_number"] == 50)
    assert parent_sdlc["state"] == "CLOSED"


@pytest.mark.asyncio
async def test_sequential_pipeline_strict_story_lock_dispatch(tmp_path: Path, monkeypatch):
    """
    Scenario: Strict Story Lock Prevents Cross-Story Pickup
      Given SQLite contains active Story A (#90) with subtasks #93 and #94 in "ready-for-dev"
      And Story B (#95) with subtask #98 in "ready-for-dev"
      When DevTest queries get_next_devtest_task / runs run_devtest_node
      Then the node must fetch and dispatch ONLY subtask #93
      And subtask #98 must be completely ignored until Story A is closed.
    """
    import json
    import logging
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.worktree import WorktreeManager

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    items = [
        {"issue_number": 90, "title": "Story A", "state": "OPEN", "item_type": "STORY", "sequence_order": 1, "labels": ["architect-processed"]},
        {"issue_number": 93, "parent_issue_id": 90, "title": "Subtask A1", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["ready-for-dev"]},
        {"issue_number": 94, "parent_issue_id": 90, "title": "Subtask A2", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 2, "labels": ["ready-for-dev"]},
        {"issue_number": 95, "title": "Story B", "state": "OPEN", "item_type": "STORY", "sequence_order": 2, "labels": ["architect-processed"]},
        {"issue_number": 98, "parent_issue_id": 95, "title": "Subtask B1", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["ready-for-dev"]},
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)

    # StateManager CTE verification
    next_task = await state_manager.get_next_devtest_task("graph-engineering")
    assert next_task == 93

    fetched_issues = []

    async def mock_fetch_issue_by_number(repo, issue_number):
        fetched_issues.append(issue_number)
        return {
            "number": issue_number,
            "title": f"Subtask #{issue_number}",
            "body": f"Details for issue #{issue_number}",
            "labels": [{"name": "ready-for-dev"}],
        }

    dispatched_prompts = []

    async def mock_harness_execute(self, prompt, cwd=None, **kwargs):
        dispatched_prompts.append(prompt)
        return 0

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

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", mock_fetch_issue_by_number)
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_harness_execute)
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")
    monkeypatch.setattr("orchestrator.nodes.devtest.check_pr_ci_status", AsyncMock(return_value=("PASS", "100% green")))

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is True
    assert fetched_issues == [93]
    assert len(dispatched_prompts) == 1
    assert "#93" in dispatched_prompts[0]
    assert "#98" not in dispatched_prompts[0]


@pytest.mark.asyncio
async def test_sequential_pipeline_blocked_pipeline_halts_and_warns(tmp_path: Path, monkeypatch, caplog):
    """
    Scenario: Blocked Pipeline Halts Without Skipping Stories
      Given active Story A is locked
      And its next subtask #93 has transitioned to "status:blocked" or "orchestration-failed"
      When DevTest queries get_next_devtest_task / runs run_devtest_node
      Then DevTest must return None / idle (0 tokens)
      And DevTest must log a warning indicating the project is locked on Story A
      And it must NOT skip or dispatch subtasks from Story B (#98).
    """
    import logging
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.nodes.devtest import run_devtest_node

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    items = [
        {"issue_number": 90, "title": "Story A", "state": "OPEN", "item_type": "STORY", "sequence_order": 1, "labels": ["architect-processed"]},
        {"issue_number": 93, "parent_issue_id": 90, "title": "Subtask A1", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["status:blocked"]},
        {"issue_number": 94, "parent_issue_id": 90, "title": "Subtask A2", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 2, "labels": ["ready-for-dev"]},
        {"issue_number": 95, "title": "Story B", "state": "OPEN", "item_type": "STORY", "sequence_order": 2, "labels": ["architect-processed"]},
        {"issue_number": 98, "parent_issue_id": 95, "title": "Subtask B1", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["ready-for-dev"]},
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)

    # 1. StateManager query returns None
    assert await state_manager.get_next_devtest_task("graph-engineering") is None

    harness_dispatched = False

    async def mock_harness_execute(*args, **kwargs):
        nonlocal harness_dispatched
        harness_dispatched = True
        return 0

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_harness_execute)

    # 2. run_devtest_node execution idles and emits warning log
    with caplog.at_level(logging.WARNING):
        ran, msg = await run_devtest_node(project, config, state_manager)

    assert ran is False
    assert harness_dispatched is False
    assert "Idle (0 tokens)" in msg
    assert any("Project is locked on active story" in r.message for r in caplog.records)

    # 3. Test with orchestration-failed label
    items[1]["labels"] = ["orchestration-failed"]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)
    assert await state_manager.get_next_devtest_task("graph-engineering") is None


@pytest.mark.asyncio
async def test_sequential_pipeline_targeted_fetch_stateless_execution(tmp_path: Path, monkeypatch):
    """
    Scenario: Targeted Fetch and Stateless Execution in DevTest
      Given get_next_devtest_task returns issue ID #93
      When run_devtest_node executes Phase 3
      Then it must fetch issue #93 directly via fetch_issue_by_number
      And it must not perform generic blind label polling across the repository.
    """
    import orchestrator.poller as poller_mod
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.worktree import WorktreeManager

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    monkeypatch.setattr(state_manager, "get_next_devtest_task", AsyncMock(return_value=93))

    fetched_issues = []

    async def mock_fetch_issue_by_number(repo, issue_number):
        fetched_issues.append(issue_number)
        return {
            "number": issue_number,
            "title": "Deterministic target task",
            "body": "Issue payload",
            "labels": [{"name": "ready-for-dev"}],
        }

    generic_label_polled = False

    async def mock_fetch_issues_with_label(*args, **kwargs):
        nonlocal generic_label_polled
        generic_label_polled = True
        return []

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", mock_fetch_issue_by_number)
    monkeypatch.setattr(poller_mod, "fetch_issues_with_label", mock_fetch_issues_with_label)
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))

    class MockProc:
        returncode = 0
        async def wait(self):
            return 0
        async def communicate(self):
            return b"", b""

    async def mock_subprocess_exec(*cmd, **kw):
        mock_p = MockProc()
        if "status" in cmd and "--porcelain" in cmd:
            mock_p.communicate = AsyncMock(return_value=(b"M file.py\n", b""))
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
    assert fetched_issues == [93]
    assert generic_label_polled is False


@pytest.mark.asyncio
async def test_sequential_pipeline_autonomous_story_promotion_e2e(tmp_path: Path, monkeypatch):
    """
    Scenario: Autonomous Story Promotion After Full Completion
      Given 100% of subtasks for Story A are closed and merged
      When DevTest marks Story A as closed
      Then it must promote the oldest planned Story B to active status
      And unlock Story B's first subtask #98 to "ready-for-dev".
    """
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.worktree import WorktreeManager

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    items = [
        {"issue_number": 90, "title": "Story A", "state": "CLOSED", "item_type": "STORY", "sequence_order": 1, "labels": ["dev-implemented"], "created_at": 1000.0},
        {"issue_number": 93, "parent_issue_id": 90, "title": "Subtask A1", "state": "CLOSED", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["dev-implemented"]},
        {"issue_number": 94, "parent_issue_id": 90, "title": "Subtask A2", "state": "CLOSED", "item_type": "SUBTASK", "sequence_order": 2, "labels": ["dev-implemented"]},
        {"issue_number": 95, "title": "Story B", "state": "PLANNED", "item_type": "STORY", "sequence_order": 2, "labels": ["architect-processed"], "created_at": 2000.0},
        {"issue_number": 98, "parent_issue_id": 95, "title": "Subtask B1", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["queued"]},
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)

    # 1. get_next_devtest_task automatically promotes Story B and returns Subtask #98
    target_task = await state_manager.get_next_devtest_task("graph-engineering")
    assert target_task == 98

    # Verify Story B is now ACTIVE in SQLite
    sdlc_items = await state_manager.get_sdlc_items("graph-engineering")
    story_b = next(s for s in sdlc_items if s["issue_number"] == 95)
    assert story_b["state"] == "ACTIVE"

    # Verify Subtask #98 is now ready-for-dev
    sub_98 = next(s for s in sdlc_items if s["issue_number"] == 98)
    assert "ready-for-dev" in sub_98["labels"]

    # 2. Execute run_devtest_node and verify #98 is dispatched
    dispatched_target = None

    async def mock_fetch_issue_by_number(repo, issue_number):
        nonlocal dispatched_target
        dispatched_target = issue_number
        return {
            "number": issue_number,
            "title": f"Subtask #{issue_number}",
            "body": "Subtask B1 body",
            "labels": [{"name": "ready-for-dev"}],
        }

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", mock_fetch_issue_by_number)
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))

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
            mock_p.communicate = AsyncMock(return_value=(b"https://github.com/AntaresAndBharani/graph-engineering/pull/98\n", b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "gh")
    monkeypatch.setattr("orchestrator.nodes.devtest.check_pr_ci_status", AsyncMock(return_value=("PASS", "100% green")))

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert ran is True
    assert dispatched_target == 98


@pytest.mark.asyncio
async def test_scenario_story_lock_acquisition_observable_in_terminal_logs(tmp_path: Path, monkeypatch, caplog):
    """
    Scenario: Story Lock acquisition is observable in terminal logs
      Given get_next_devtest_task returns subtask #93 under active Story #90
      When DevTest dispatches the harness for #93
      Then the terminal log must contain "Story Lock Active: Parent #90. Dispatched Subtask #93".
    """
    import logging
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.logging import ProjectLogBufferManager
    from orchestrator.nodes.devtest import run_devtest_node
    from orchestrator.worktree import WorktreeManager

    ProjectLogBufferManager.reset()

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={"devtest": NodeConfig(enabled=True, harness="antigravity")},
    )
    config = GlobalConfig()

    items = [
        {"issue_number": 90, "title": "Active Story #90", "state": "OPEN", "item_type": "STORY", "sequence_order": 1, "labels": ["architect-processed"]},
        {"issue_number": 93, "parent_issue_id": 90, "title": "Subtask #93", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 1, "labels": ["ready-for-dev"]},
        {"issue_number": 94, "parent_issue_id": 90, "title": "Subtask #94", "state": "OPEN", "item_type": "SUBTASK", "sequence_order": 2, "labels": ["queued"]},
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)

    # Verify get_next_devtest_task returns subtask #93 under active Story #90
    resolved_id = await state_manager.get_next_devtest_task("graph-engineering")
    assert resolved_id == 93

    async def mock_fetch_issue_by_number(repo, issue_number):
        return {
            "number": issue_number,
            "title": f"Subtask #{issue_number}",
            "body": f"Description for subtask #{issue_number}.\n\nParent: #90",
            "labels": [{"name": "ready-for-dev"}],
        }

    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_issue_by_number", mock_fetch_issue_by_number)
    monkeypatch.setattr("orchestrator.nodes.devtest.fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr("orchestrator.nodes.devtest.verify_git_safety", AsyncMock(return_value=(True, "Safety verified.")))
    monkeypatch.setattr(WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))

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

    with caplog.at_level(logging.INFO):
        ran, msg = await run_devtest_node(project, config, state_manager)

    assert ran is True

    # Terminal log assertions
    expected_log_line = "Story Lock Active: Parent #90. Dispatched Subtask #93"

    # 1. Standard logger records contain the expected message
    matching_records = [r for r in caplog.records if expected_log_line in r.message]
    assert len(matching_records) > 0, f"Expected '{expected_log_line}' in log records, got: {[r.message for r in caplog.records]}"

    # 2. ProjectLogBufferManager in-memory buffer contains the expected message
    proj_logs = ProjectLogBufferManager.get_project_logs("graph-engineering", node_name="devtest")
    assert any(expected_log_line in line for line in proj_logs)


