from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.nodes.architect import run_architect_node
from orchestrator.nodes.reviewer import run_reviewer_node


def test_architect_config_defaults():
    cfg = NodeConfig(
        research_harness="antigravity",
        research_model="gemini-3.7-flash-high",
        research_interval_seconds=604800,
        review_trigger="needs-architect-review",
    )
    assert cfg.research_harness == "antigravity"
    assert cfg.research_model == "gemini-3.7-flash-high"
    assert cfg.research_interval_seconds == 604800
    assert cfg.review_trigger == "needs-architect-review"
    assert cfg.conflict_harness == "antigravity"
    assert cfg.conflict_model == "gemini-3.7-flash-low"


@pytest.mark.asyncio
async def test_architect_zero_token_gating_when_all_idle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Create dummy .graph/architecture.md so sync is not required
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")

    # Record recent run
    await state_manager.record_node_run("architect_research", "org/repo")

    config = GlobalConfig()
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                research_harness="antigravity",
                research_model="gemini-3.7-flash-high",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is False
    assert "Idle (0 tokens)" in msg


@pytest.mark.asyncio
async def test_reviewer_zero_token_gating_idle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    config = GlobalConfig()
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "reviewer": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="architect-approved",
            )
        },
    )

    ran, msg = await run_reviewer_node(project, config, state_manager)
    assert ran is False
    assert "Idle (0 tokens)" in msg


def test_build_triage_prompt_incorporates_pre_approved_gherkin_ac():
    from orchestrator.nodes.architect import build_triage_prompt

    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=".",
        context_files=[".graph/architecture.md"],
    )

    gherkin_text = (
        "Feature: Sample Feature\n"
        "  Scenario: Do something\n"
        "    Given a valid condition\n"
        "    When action happens\n"
        "    Then result is expected"
    )
    po_record = {
        "repo": "org/repo",
        "issue_number": 42,
        "body_hash": "abcdef123456",
        "status": "PO_APPROVED",
        "gherkin_ac": gherkin_text,
        "blockers": None,
        "updated_at": 1700000000.0,
    }

    prompt = build_triage_prompt(
        project=project,
        issue_id=42,
        issue_title="Implement Sample Feature",
        trigger="needs-triage",
        output_label="ready-for-dev",
        processed_label="architect-processed",
        po_record=po_record,
    )

    assert "PRE-APPROVED ACCEPTANCE CRITERIA (from PO Blackboard)" in prompt
    assert gherkin_text in prompt
    assert "Do NOT re-derive acceptance criteria from scratch" in prompt
    assert "using the pre-approved Gherkin acceptance criteria above" in prompt


def test_build_triage_prompt_without_blackboard_record():
    from orchestrator.nodes.architect import build_triage_prompt

    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=".",
    )

    prompt = build_triage_prompt(
        project=project,
        issue_id=42,
        issue_title="Implement Sample Feature",
        trigger="needs-triage",
        output_label="ready-for-dev",
        processed_label="architect-processed",
        po_record=None,
    )

    assert "PRE-APPROVED ACCEPTANCE CRITERIA" not in prompt
    assert "Do NOT re-derive acceptance criteria from scratch" not in prompt
    assert "Perform Triage, Classification, and Architectural Decomposition for GitHub Issue #42" in prompt


def test_build_triage_prompt_non_approved_status_ignored():
    from orchestrator.nodes.architect import build_triage_prompt

    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=".",
    )

    po_record = {
        "repo": "org/repo",
        "issue_number": 42,
        "body_hash": "abcdef123456",
        "status": "NEEDS_HUMAN_CLARIFICATION",
        "gherkin_ac": "Some AC that was not approved",
        "blockers": "Need clarification",
        "updated_at": 1700000000.0,
    }

    prompt = build_triage_prompt(
        project=project,
        issue_id=42,
        issue_title="Implement Sample Feature",
        trigger="needs-triage",
        output_label="ready-for-dev",
        processed_label="architect-processed",
        po_record=po_record,
    )

    assert "PRE-APPROVED ACCEPTANCE CRITERIA" not in prompt


@pytest.mark.asyncio
async def test_architect_triages_issue_with_blackboard_gherkin_ac(tmp_path: Path, monkeypatch):
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Pre-condition: Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "org/repo")

    # Store pre-approved Gherkin AC on Blackboard
    stored_ac = (
        "Feature: User Authentication\n"
        "  Scenario: Login success\n"
        "    Given valid credentials\n"
        "    When user logs in\n"
        "    Then session token is generated"
    )
    await state_manager.upsert_po_tracking(
        repo="org/repo",
        issue_number=101,
        body_hash="sha256_mock_hash",
        status="PO_APPROVED",
        gherkin_ac=stored_ac,
    )

    # Mock fetch_open_prs to return empty
    async def mock_fetch_prs(*args, **kwargs):
        return []

    monkeypatch.setattr(architect, "fetch_open_prs", mock_fetch_prs)

    # Mock fetch_issues_with_label to return issue 101
    async def mock_fetch_issues(repo, label, limit=1):
        if label == "needs-triage":
            return [{"number": 101, "title": "User Authentication Story"}]
        return []

    monkeypatch.setattr(architect, "fetch_issues_with_label", mock_fetch_issues)

    captured_prompt = {}

    async def mock_execute(self, prompt, **kwargs):
        captured_prompt["text"] = prompt
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    # Disable gh subprocess calls in post-execution sync
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    config = GlobalConfig()
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="needs-triage",
                label_output="ready-for-dev",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    assert "Architect node completed evaluation on issue #101" in msg
    assert "PRE-APPROVED ACCEPTANCE CRITERIA (from PO Blackboard)" in captured_prompt["text"]
    assert stored_ac in captured_prompt["text"]
    assert "Do NOT re-derive acceptance criteria from scratch" in captured_prompt["text"]


@pytest.mark.asyncio
async def test_architect_triages_issue_without_blackboard_record(tmp_path: Path, monkeypatch):
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Pre-condition: Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "org/repo")

    # Mock fetch_open_prs to return empty
    async def mock_fetch_prs(*args, **kwargs):
        return []

    monkeypatch.setattr(architect, "fetch_open_prs", mock_fetch_prs)

    # Mock fetch_issues_with_label to return issue 102 (no blackboard row)
    async def mock_fetch_issues(repo, label, limit=1):
        if label == "needs-triage":
            return [{"number": 102, "title": "Unregistered Story"}]
        return []

    monkeypatch.setattr(architect, "fetch_issues_with_label", mock_fetch_issues)

    captured_prompt = {}

    async def mock_execute(self, prompt, **kwargs):
        captured_prompt["text"] = prompt
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    config = GlobalConfig()
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="needs-triage",
                label_output="ready-for-dev",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    assert "Architect node completed evaluation on issue #102" in msg
    assert "PRE-APPROVED ACCEPTANCE CRITERIA" not in captured_prompt["text"]
    assert "Do NOT re-derive acceptance criteria from scratch" not in captured_prompt["text"]
    assert "Issue #102" in captured_prompt["text"]


@pytest.mark.asyncio
async def test_scenario_architect_lookahead_bounded_by_quota_gating(tmp_path: Path, monkeypatch):
    """
    Scenario: Architect Lookahead Bounded by Quota Gating
      Given a project with "max_planned_stories: 2"
      And SQLite records 2 stories in state "PLANNED"
      When the Architect node runs its evaluation cycle
      Then it must query SQLite for planned stories count via count_planned_stories()
      And it must skip harness invocation with zero token consumption
      And log a throttling notice: "[graph-engineering|architect] Lookahead limit reached (2/2). Pausing decomposition."
    """
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Pre-condition: Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "AntaresAndBharani/graph-engineering")

    # Record 2 stories in state "PLANNED"
    items = [
        {
            "issue_number": 201,
            "title": "Story A",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 1,
        },
        {
            "issue_number": 202,
            "title": "Story B",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 2,
        },
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", items)
    assert await state_manager.count_planned_stories("graph-engineering") == 2

    # Mock fetch_open_prs to return empty
    monkeypatch.setattr(architect, "fetch_open_prs", AsyncMock(return_value=[]))

    # Mock fetch_issues_with_label (should NOT even be processed by harness)
    monkeypatch.setattr(
        architect,
        "fetch_issues_with_label",
        AsyncMock(return_value=[{"number": 203, "title": "New Incoming Story"}]),
    )

    # Mock harness execute - assert it is NEVER called (zero token consumption)
    mock_execute = AsyncMock()
    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    config = GlobalConfig()
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        max_planned_stories=2,
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="needs-triage",
                label_output="ready-for-dev",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is False
    assert "[graph-engineering|architect] Lookahead limit reached (2/2). Pausing decomposition." in msg
    mock_execute.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_architect_operates_in_its_own_worktree(tmp_path: Path, monkeypatch):
    """
    Scenario: Architect operates in its own worktree
      Given a project configured with local_path "/repo" and worktrees_enabled=True
      When the Architect node executes
      Then it must operate in ".graph/worktrees/architect_<project>" obtained via WorktreeManager
      And it must not mutate the DevTest node's working tree
    """
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter
    from orchestrator.worktree import WorktreeManager

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Pre-condition: Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "AntaresAndBharani/graph-engineering")

    monkeypatch.setattr(architect, "fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        architect,
        "fetch_issues_with_label",
        AsyncMock(return_value=[{"number": 301, "title": "Test Worktree Execution"}]),
    )

    expected_wt_path = (tmp_path / ".graph" / "worktrees" / "architect_graph-engineering").resolve()
    devtest_wt_path = (tmp_path / ".graph" / "worktrees" / "devtest_graph-engineering").resolve()

    # Mock WorktreeManager.ensure_worktree to return expected_wt_path
    monkeypatch.setattr(
        WorktreeManager,
        "ensure_worktree",
        AsyncMock(return_value=expected_wt_path),
    )

    executed_cwds = []

    async def mock_execute(self, prompt, cwd=None, **kwargs):
        executed_cwds.append(cwd)
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    # Disable gh subprocess calls in post-execution sync
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    config = GlobalConfig()
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        worktrees_enabled=True,
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="needs-triage",
                label_output="ready-for-dev",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    assert len(executed_cwds) == 1
    assert executed_cwds[0] == expected_wt_path
    assert executed_cwds[0] != devtest_wt_path
    assert "architect_graph-engineering" in str(executed_cwds[0])


@pytest.mark.asyncio
async def test_scenario_decomposition_respects_active_story_state(tmp_path: Path, monkeypatch):
    """
    Scenario: Decomposition respects active-story state
      Given capacity exists under max_planned_stories
      And no active story is currently running
      When the Architect decomposes a story into subtasks
      Then Subtask 1 is labeled "ready-for-dev" and Subtasks 2..N are labeled "queued"
    """
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Pre-condition: Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "AntaresAndBharani/graph-engineering")

    # Capacity exists: 0 planned stories, and NO active story
    assert await state_manager.count_planned_stories("graph-engineering") == 0
    assert await state_manager.get_active_story("graph-engineering") is None

    monkeypatch.setattr(architect, "fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        architect,
        "fetch_issues_with_label",
        AsyncMock(return_value=[{"number": 401, "title": "Epic: User Onboarding"}]),
    )

    captured_prompt = {}

    async def mock_execute(self, prompt, **kwargs):
        captured_prompt["text"] = prompt
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    config = GlobalConfig()
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        max_planned_stories=2,
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="needs-triage",
                label_output="ready-for-dev",
                processed_label="architect-processed",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    prompt_text = captured_prompt["text"]
    assert "Create Subtask 1 (Active)" in prompt_text
    assert "--label 'ready-for-dev'" in prompt_text
    assert "Create Subtasks 2..N (Queued)" in prompt_text
    assert "--label 'queued'" in prompt_text
    assert "--add-label 'architect-processed'" in prompt_text


@pytest.mark.asyncio
async def test_scenario_decomposition_queues_behind_an_active_story(tmp_path: Path, monkeypatch):
    """
    Scenario: Decomposition queues behind an active story
      Given capacity exists under max_planned_stories
      And an active story is currently running
      When the Architect decomposes a new story into subtasks
      Then all subtasks are labeled "queued" and the parent story is labeled "planned"
    """
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Pre-condition: Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "AntaresAndBharani/graph-engineering")

    # Set up an active story currently running (state="OPEN", item_type="STORY")
    active_story_item = [
        {
            "issue_number": 100,
            "title": "Epic: Active Running Story",
            "state": "OPEN",
            "item_type": "STORY",
            "labels": ["architect-processed"],
        }
    ]
    await state_manager.sync_project_sdlc_items("graph-engineering", active_story_item)
    active_story = await state_manager.get_active_story("graph-engineering")
    assert active_story is not None
    assert active_story["issue_number"] == 100

    # Planned stories count is 0 (capacity under max_planned_stories=2)
    assert await state_manager.count_planned_stories("graph-engineering") == 0

    monkeypatch.setattr(architect, "fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        architect,
        "fetch_issues_with_label",
        AsyncMock(return_value=[{"number": 501, "title": "Epic: Second Feature Behind Active"}]),
    )

    captured_prompt = {}

    async def mock_execute(self, prompt, **kwargs):
        captured_prompt["text"] = prompt
        return 0

    monkeypatch.setattr(AsyncHarnessAdapter, "execute", mock_execute)

    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    config = GlobalConfig()
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        max_planned_stories=2,
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="needs-triage",
                label_output="ready-for-dev",
                processed_label="architect-processed",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    prompt_text = captured_prompt["text"]
    assert "An active story is currently running in this project" in prompt_text
    assert "Create all Subtasks 1..N (Queued)" in prompt_text
    assert "--label 'queued'" in prompt_text
    assert "--add-label 'planned'" in prompt_text
    assert "Create Subtask 1 (Active)" not in prompt_text


