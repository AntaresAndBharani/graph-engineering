from __future__ import annotations

import time
from pathlib import Path
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

