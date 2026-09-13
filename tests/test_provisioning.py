import asyncio
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from orchestrator.config import ProjectConfig
from orchestrator.db import StateManager
from orchestrator.provisioning import (
    FunctionalSlice,
    ProvisioningPlan,
    parse_decision_plan,
    check_project_active_lock,
    provision_story,
)

SAMPLE_PLAN_PATTERN_B = """
# 📋 Implementation Plan & Refinement Lifecycle: Sample Feature

## 🎯 Final Decision Plan & User Story Specification

### 📖 User Story
**As a** Graph Engineering Platform Operator,  
**I want** to execute automated regression suites on idle cycles,  
**So that** unexpected defects are surfaced with zero human intervention.

### 📋 INVEST Subtask Breakdown
1. **Subtask 1 (Suite Harness):** Create test runner wrapper and config parser.
2. **Subtask 2 (Reporting Adapter):** Implement markdown table summary generator.
3. **Subtask 3 (Verification & Docs):** Run integration tests and update changelog.
"""

SAMPLE_PLAN_PATTERN_A = """
# 📋 Implementation Plan & Refinement Lifecycle: Quick Bugfix

## 🎯 Final Decision Plan & User Story Specification

### 📖 User Story
**As a** Developer,  
**I want** to fix the regex parsing in helper module,  
**So that** malformed strings do not cause syntax errors.
"""

def test_parse_decision_plan_pattern_b():
    plan = parse_decision_plan(SAMPLE_PLAN_PATTERN_B)
    assert plan.pattern == "B"
    assert "automated regression suites" in plan.parent_title
    assert len(plan.slices) == 3
    assert "Suite Harness" in plan.slices[0].title
    assert "Reporting Adapter" in plan.slices[1].title
    assert "Verification & Docs" in plan.slices[2].title

def test_parse_decision_plan_pattern_a():
    plan = parse_decision_plan(SAMPLE_PLAN_PATTERN_A)
    assert plan.pattern == "A"
    assert "regex parsing" in plan.parent_title
    assert len(plan.slices) == 0

def test_parse_decision_plan_override():
    plan = parse_decision_plan(SAMPLE_PLAN_PATTERN_B, default_pattern="A")
    assert plan.pattern == "A"

@pytest.mark.asyncio
async def test_provision_story_active_lock_refusal(tmp_path):
    db_path = tmp_path / "state.db"
    state_mgr = StateManager(db_path)
    await state_mgr.init_db()

    # Insert an active story lock (must satisfy ActiveStory CTE: STORY with a child subtask)
    await state_mgr.sync_project_sdlc_items(
        "test-project",
        [
            {
                "issue_number": 100,
                "title": "Existing Feature",
                "state": "OPEN",
                "item_type": "STORY",
                "sequence_order": 0,
                "labels": ["architect-processed"],
            },
            {
                "issue_number": 101,
                "title": "Existing Child Subtask",
                "state": "OPEN",
                "item_type": "SUBTASK",
                "parent_issue_id": 100,
                "sequence_order": 1,
                "labels": ["ready-for-dev"],
            },
        ],
    )

    project = ProjectConfig(name="test-project", repo="org/test-project", local_path=tmp_path)
    plan = ProvisioningPlan(pattern="B", parent_title="feat: New Thing", parent_body="body")

    # Without force -> RuntimeError
    with pytest.raises(RuntimeError, match="currently has an active locked story #100"):
        await provision_story(project, plan, state_mgr, dry_run=False, force=False)

    # With force -> does not raise lock error (proceeds to dry-run or execution)
    result = await provision_story(project, plan, state_mgr, dry_run=True, force=True)
    assert result["dry_run"] is True

@pytest.mark.asyncio
async def test_provision_story_dry_run(tmp_path):
    state_mgr = MagicMock(spec=StateManager)
    f = asyncio.Future()
    f.set_result(None)
    state_mgr.get_active_locked_story_id = MagicMock(return_value=f)

    project = ProjectConfig(name="test-project", repo="org/test-project", local_path=tmp_path)
    plan = ProvisioningPlan(
        pattern="B",
        parent_title="feat: Test Feature",
        parent_body="body",
        slices=[
            FunctionalSlice(title="Slice 1", body="b1"),
            FunctionalSlice(title="Slice 2", body="b2"),
        ]
    )

    res = await provision_story(project, plan, state_mgr, dry_run=True)
    assert res["dry_run"] is True
    assert res["parent_issue"]["title"] == "feat: Test Feature"
    assert res["parent_issue"]["labels"] == ["architect-processed"]
    assert len(res["child_issues"]) == 2
    assert res["child_issues"][0]["labels"] == ["ready-for-dev"]
    assert res["child_issues"][1]["labels"] == ["queued"]

@pytest.mark.asyncio
async def test_provision_story_pattern_b_execution(tmp_path):
    db_path = tmp_path / "state.db"
    state_mgr = StateManager(db_path)
    await state_mgr.init_db()

    project = ProjectConfig(name="test-project", repo="org/test-project", local_path=tmp_path)
    plan = ProvisioningPlan(
        pattern="B",
        parent_title="feat: Real Feature",
        parent_body="Parent description",
        slices=[
            FunctionalSlice(title="feat: Step 1", body="b1"),
            FunctionalSlice(title="feat: Step 2", body="b2"),
        ]
    )

    created_issues = []
    def mock_create(repo, title, body, labels):
        num = 100 + len(created_issues)
        created_issues.append((num, repo, title, body, labels))
        return num

    updated_bodies = []
    def mock_update(repo, issue_number, body):
        updated_bodies.append((issue_number, body))

    comments = []
    def mock_comment(repo, issue_number, comment):
        comments.append((issue_number, comment))

    async def mock_poll(p, sm):
        return []

    with patch("orchestrator.provisioning.create_gh_issue", side_effect=mock_create), \
         patch("orchestrator.provisioning.update_gh_issue_body", side_effect=mock_update), \
         patch("orchestrator.provisioning.post_gh_issue_comment", side_effect=mock_comment), \
         patch("orchestrator.poller.poll_project_sdlc_items", side_effect=mock_poll):
        
        res = await provision_story(project, plan, state_mgr, dry_run=False)

    assert res["parent_issue"]["issue_number"] == 100
    assert len(res["child_issues"]) == 2
    assert res["child_issues"][0]["issue_number"] == 101
    assert res["child_issues"][1]["issue_number"] == 102

    # Verify parent update has checklist
    assert len(updated_bodies) == 1
    assert "- [ ] #101 - feat: Step 1" in updated_bodies[0][1]
    assert "- [ ] #102 - feat: Step 2" in updated_bodies[0][1]

    # Verify search-lag comment posted
    assert len(comments) == 1
    assert "Child issues: #101, #102" in comments[0][1]
