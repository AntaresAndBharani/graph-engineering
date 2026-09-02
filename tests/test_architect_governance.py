from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.nodes.architect import run_architect_node, build_triage_prompt
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


def test_build_triage_prompt_clean_3_cases():
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=".",
        context_files=[".graph/architecture.md"],
    )

    prompt = build_triage_prompt(
        project=project,
        issue_id=42,
        issue_title="Implement Sample Feature",
        trigger="needs-triage",
        output_label="ready-for-dev",
        processed_label="architect-processed",
        queued_label="queued",
    )

    assert "Perform Triage, Classification, and Architectural Decomposition for GitHub Issue #42" in prompt
    assert "Case 1: ALREADY IMPLEMENTED ON MAIN" in prompt
    assert "Case 2: STANDALONE TASK / SMALL BUG" in prompt
    assert "Case 3: FULL USER STORY / COMPLEX FEATURE" in prompt
    assert "Create all Subtasks 1..N (Queued)" in prompt
    assert "--label 'queued'" in prompt
    assert "--add-label 'architect-processed'" in prompt
    assert "needs-po-review" not in prompt
    assert "tech-debt" not in prompt


@pytest.mark.asyncio
async def test_architect_triages_issue(tmp_path: Path, monkeypatch):
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
                processed_label="architect-processed",
                queued_label="queued",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    assert "Architect node completed evaluation on issue #101" in msg
    assert "Issue #101" in captured_prompt["text"]
    assert "Create all Subtasks 1..N (Queued)" in captured_prompt["text"]
    assert "--label 'queued'" in captured_prompt["text"]
    assert "--add-label 'architect-processed'" in captured_prompt["text"]


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
async def test_scenario_config_driven_1_pass_story_decomposition_stream_out(tmp_path: Path, monkeypatch):
    """
    Scenario: Config-driven 1-pass story decomposition with 'stream out'
      Given a project configured with label_trigger="stream out", processed_label="architect-processed", queued_label="queued"
      And a GitHub parent issue #60 is labeled "stream out"
      When the Architect node executes its triage cycle
      Then it must create all child subtasks labeled strictly as "queued"
      And it must remove "stream out" and add "architect-processed" to issue #60.
    """
    from orchestrator.nodes import architect
    from orchestrator.harness import AsyncHarnessAdapter

    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Living architecture plane initialized
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")
    await state_manager.record_node_run("architect_research", "AntaresAndBharani/graph-engineering")

    async def mock_fetch_issues(repo, label, limit=1):
        if label == "stream out":
            return [{"number": 60, "title": "Epic: Custom Stream Out Story"}]
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
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="stream out",
                processed_label="architect-processed",
                queued_label="queued",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is True
    assert "evaluation on issue #60" in msg
    prompt_text = captured_prompt["text"]
    assert "Create all Subtasks 1..N (Queued)" in prompt_text
    assert "--label 'queued'" in prompt_text
    assert "--remove-label 'stream out'" in prompt_text
    assert "--add-label 'architect-processed'" in prompt_text
