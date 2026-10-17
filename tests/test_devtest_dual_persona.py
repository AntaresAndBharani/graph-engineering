from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from orchestrator.config import (
    DevTestReviewerConfig,
    GlobalConfig,
    HarnessConfig,
    NodeConfig,
    ProjectConfig,
    resolve_reviewer_config,
)
from orchestrator.db import StateManager
from orchestrator.nodes import devtest
from orchestrator.nodes.devtest import (
    DevTestAction,
    DevTestTurnMarker,
    determine_next_action,
    format_turn_marker,
    parse_reviewer_verdict,
    parse_turn_marker,
    run_devtest_node,
    _verify_and_auto_merge_pr,
)


# ============================================================================
# Unit Tests: Config Schema, Validation & Resolution
# ============================================================================

def test_reviewer_config_defaults():
    cfg = DevTestReviewerConfig()
    assert cfg.enabled is False
    assert cfg.harness is None
    assert cfg.model is None
    assert cfg.effort is None
    assert cfg.max_rounds == 3


def test_reviewer_config_validation():
    # max_rounds must be >= 1
    with pytest.raises(Exception):
        DevTestReviewerConfig(max_rounds=0)


def test_node_config_reviewer_coercion():
    # Coerces None to default DevTestReviewerConfig
    node = NodeConfig(harness="claude", model="sonnet", reviewer=None)
    assert isinstance(node.reviewer, DevTestReviewerConfig)
    assert node.reviewer.enabled is False


def test_resolve_reviewer_config_inheritance():
    # When reviewer fields are None, inherit from parent node_cfg
    node = NodeConfig(harness="antigravity", model="gemini-3.8-flash-high", effort="high")
    resolved = resolve_reviewer_config(node)
    assert resolved.enabled is False
    assert resolved.harness == "antigravity"
    assert resolved.model == "gemini-3.8-flash-high"
    assert resolved.effort == "high"
    assert resolved.max_rounds == 3

    # When reviewer fields are explicitly specified, do not overwrite
    node2 = NodeConfig(
        harness="claude",
        model="sonnet",
        effort="medium",
        reviewer=DevTestReviewerConfig(
            enabled=True,
            harness="antigravity",
            model="gemini-3.8-flash-high",
            effort="high",
            max_rounds=5,
        ),
    )
    resolved2 = resolve_reviewer_config(node2)
    assert resolved2.enabled is True
    assert resolved2.harness == "antigravity"
    assert resolved2.model == "gemini-3.8-flash-high"
    assert resolved2.effort == "high"
    assert resolved2.max_rounds == 5


# ============================================================================
# Unit Tests: Turn Marker Parsing & Formatting
# ============================================================================

def test_turn_marker_format_and_parse():
    marker_str = format_turn_marker("DEVELOPER", 1, "READY_FOR_REVIEW")
    assert marker_str == "<!-- DEVTEST_TURN: persona=DEVELOPER round=1 status=READY_FOR_REVIEW -->"

    parsed = parse_turn_marker(marker_str)
    assert parsed is not None
    assert parsed.persona == "DEVELOPER"
    assert parsed.round == 1
    assert parsed.status == "READY_FOR_REVIEW"


def test_turn_marker_parse_all_statuses():
    statuses = [
        "READY_FOR_REVIEW",
        "CHANGES_REQUESTED",
        "APPROVED",
        "CONTINGENCY_PLAN",
        "CONTINGENCY_COMPLETED",
    ]
    for st in statuses:
        comment = f"Here is the turn:\n<!-- DEVTEST_TURN: persona=REVIEWER round=2 status={st} -->\nNotes."
        parsed = parse_turn_marker(comment)
        assert parsed is not None
        assert parsed.persona == "REVIEWER"
        assert parsed.round == 2
        assert parsed.status == st


def test_turn_marker_parse_invalid():
    assert parse_turn_marker("") is None
    assert parse_turn_marker("Just a regular comment") is None
    assert parse_turn_marker("<!-- DEVTEST_TURN: persona=INVALID round=1 status=APPROVED -->") is None
    assert parse_turn_marker("<!-- DEVTEST_TURN: persona=DEVELOPER round=0 status=APPROVED -->") is None
    assert parse_turn_marker("<!-- DEVTEST_TURN: persona=DEVELOPER round=1 status=UNKNOWN_STATUS -->") is None


def test_parse_reviewer_verdict():
    # Explicit VERDICT: <STATUS>
    v, _ = parse_reviewer_verdict("Looks good.\nVERDICT: APPROVED", 1, 3)
    assert v == "APPROVED"

    v, _ = parse_reviewer_verdict("Needs work.\nVERDICT: CHANGES_REQUESTED", 1, 3)
    assert v == "CHANGES_REQUESTED"

    v, _ = parse_reviewer_verdict("Max rounds reached.\nVERDICT: CONTINGENCY_PLAN", 3, 3)
    assert v == "CONTINGENCY_PLAN"

    # At max_rounds, CHANGES_REQUESTED is coerced to CONTINGENCY_PLAN
    v, _ = parse_reviewer_verdict("Still broken.\nVERDICT: CHANGES_REQUESTED", 3, 3)
    assert v == "CONTINGENCY_PLAN"


# ============================================================================
# Unit Tests: Exhaustive Transition Table (determine_next_action)
# ============================================================================

def test_transition_no_pr_exists():
    # No PR exists yet -> DEVELOPER, Round 1 initial implementation
    action = determine_next_action(pr_comments=None, max_rounds=3)
    assert action.next_persona == "DEVELOPER"
    assert action.round == 1
    assert "INITIAL_IMPLEMENTATION" in action.action or "initial implementation" in action.action


def test_transition_pr_with_no_markers_or_only_bot_comments():
    # PR exists with non-marker comments -> DEVELOPER, Round 1 recovery
    comments = [
        {"body": "🤖 GitHub Actions: CI checks started."},
        {"body": "Random review comment by a human operator."},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "DEVELOPER"
    assert action.round == 1
    assert "RECOVERY" in action.action or "recovery" in action.action


def test_transition_pr_empty_comments():
    action = determine_next_action(pr_comments=[], max_rounds=3)
    assert action.next_persona == "DEVELOPER"
    assert action.round == 1
    assert "RECOVERY" in action.action or "recovery" in action.action


def test_transition_malformed_marker_fails_safe():
    comments = [
        {"body": "<!-- DEVTEST_TURN: malformed syntax without attributes -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "NONE"
    assert "FAIL_SAFE" in action.action or "fail safe" in action.action


def test_transition_developer_ready_for_review_routes_to_reviewer():
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=DEVELOPER round=1 status=READY_FOR_REVIEW -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "REVIEWER"
    assert action.round == 1
    assert "REVIEW" in action.action or "Evaluate PR" in action.action


def test_transition_reviewer_changes_requested_routes_to_developer_next_round():
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=DEVELOPER round=1 status=READY_FOR_REVIEW -->"},
        {"body": "<!-- DEVTEST_TURN: persona=REVIEWER round=1 status=CHANGES_REQUESTED -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "DEVELOPER"
    assert action.round == 2
    assert "REVISE" in action.action or "Address reviewer feedback" in action.action


def test_transition_reviewer_changes_requested_at_max_rounds():
    # If reviewer requests changes at max_rounds instead of contingency, fail-safe without merging
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=REVIEWER round=3 status=CHANGES_REQUESTED -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "NONE"
    assert "FAIL_SAFE" in action.action or "fail safe" in action.action


def test_transition_reviewer_contingency_plan_routes_to_developer():
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=REVIEWER round=3 status=CONTINGENCY_PLAN -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "DEVELOPER"
    assert action.round == 3
    assert "APPLY_CONTINGENCY" in action.action or "Apply reviewer contingency" in action.action


def test_transition_reviewer_approved_routes_to_orchestrator():
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=REVIEWER round=2 status=APPROVED -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "ORCHESTRATOR"
    assert action.round == 2
    assert "AUTO_MERGE" in action.action or "_verify_and_auto_merge_pr" in action.action


def test_transition_developer_contingency_completed_routes_to_orchestrator():
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=DEVELOPER round=3 status=CONTINGENCY_COMPLETED -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "ORCHESTRATOR"
    assert action.round == 3
    assert "AUTO_MERGE" in action.action or "_verify_and_auto_merge_pr" in action.action


def test_transition_pr_already_merged_or_closed():
    action = determine_next_action(pr_comments=[], max_rounds=3, pr_state="MERGED")
    assert action.next_persona == "NONE"
    assert "TERMINAL_NOOP" in action.action or "No-op terminal state" in action.action

    action2 = determine_next_action(pr_comments=[], max_rounds=3, pr_state="CLOSED")
    assert action2.next_persona == "NONE"
    assert "TERMINAL_NOOP" in action2.action or "No-op terminal state" in action2.action


def test_transition_scans_newest_to_oldest():
    # The last comment is the authoritative turn
    comments = [
        {"body": "<!-- DEVTEST_TURN: persona=DEVELOPER round=1 status=READY_FOR_REVIEW -->"},
        {"body": "<!-- DEVTEST_TURN: persona=REVIEWER round=1 status=CHANGES_REQUESTED -->"},
        {"body": "Some intermediate note"},
        {"body": "<!-- DEVTEST_TURN: persona=DEVELOPER round=2 status=READY_FOR_REVIEW -->"},
    ]
    action = determine_next_action(pr_comments=comments, max_rounds=3)
    assert action.next_persona == "REVIEWER"
    assert action.round == 2


# ============================================================================
# Integration BDD Tests: Scenarios 1 to 7
# ============================================================================

@pytest.fixture
def test_setup(tmp_path: Path):
    config = GlobalConfig()
    project = ProjectConfig(name="test", repo="org/repo", local_path=str(tmp_path))
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    return project, config, state_manager, tmp_path


@pytest.mark.asyncio
async def test_scenario_1_legacy_compatibility_reviewer_disabled(test_setup, monkeypatch):
    """
    Scenario 1: Legacy Compatibility (Reviewer Disabled)
    Given nodes.devtest.reviewer.enabled is false or omitted
    When devtest runs on a ready-for-dev issue
    Then Developer CLI is invoked in single pass, opens PR, and auto-merges only after CI is green.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    project.nodes["devtest"] = NodeConfig(
        harness="claude",
        reviewer=DevTestReviewerConfig(enabled=False),
    )

    # Mock open issues
    mock_issue = {"number": 101, "title": "Implement feature X", "labels": ["ready-for-dev"], "body": "Gherkin AC"}
    async def mock_fetch_issues(*args, **kwargs):
        return [mock_issue]

    async def mock_fetch_prs(repo, label=None, limit=20):
        return []

    async def mock_fetch_issue(repo, issue_number):
        return mock_issue

    async def mock_ci_status(*args, **kwargs):
        return "PASS", "All checks green"

    # Mock git safety, quota, and harness
    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "fetch_issue_by_number", mock_fetch_issue)
    monkeypatch.setattr(devtest, "check_dispatch_quota", AsyncMock(return_value=(True, MagicMock(harness_name="claude"))))
    monkeypatch.setattr(devtest, "verify_git_safety", AsyncMock(return_value=(True, "OK")))
    monkeypatch.setattr(devtest, "check_pr_ci_status", mock_ci_status)
    monkeypatch.setattr(devtest.WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(devtest.AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))
    monkeypatch.setattr(state_manager, "get_next_devtest_task", AsyncMock(return_value=101))

    # Mock _verify_and_auto_merge_pr to assert it is called directly in legacy mode
    verify_merge_mock = AsyncMock(return_value=(True, "Auto-merged PR #123"))
    monkeypatch.setattr(devtest, "_verify_and_auto_merge_pr", verify_merge_mock)

    # Mock PR creation check
    mock_created_pr = {"number": 123, "state": "OPEN", "headRefName": "feat/issue-101"}
    monkeypatch.setattr(devtest, "fetch_pr_comments", AsyncMock(return_value=[]))

    with patch("shutil.which", return_value="/usr/bin/gh"):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock git commands and gh pr list
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b'[{"number": 123, "state": "OPEN", "headRefName": "feat/issue-101"}]', b""))
            mock_proc.wait = AsyncMock(return_value=0)
            mock_exec.return_value = mock_proc

            ran, msg = await run_devtest_node(project, config, state_manager)
            assert ran is True
            assert verify_merge_mock.called


@pytest.mark.asyncio
async def test_scenario_2_agreement_reached_within_max_rounds(test_setup, monkeypatch):
    """
    Scenario 2: Agreement Reached Within Max Rounds
    Given nodes.devtest.reviewer.enabled is true and max_rounds is 3
    When Developer opens PR with DEVELOPER/READY_FOR_REVIEW round=1
    And Reviewer posts REVIEWER/CHANGES_REQUESTED round=1
    And Developer pushes fixes with DEVELOPER/READY_FOR_REVIEW round=2
    And Reviewer posts REVIEWER/APPROVED round=2
    Then negotiation terminates early at Round 2, Round 3 is not executed,
    And orchestrator auto-merges PR via _verify_and_auto_merge_pr.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    project.nodes["devtest"] = NodeConfig(
        harness="claude",
        reviewer=DevTestReviewerConfig(enabled=True, max_rounds=3),
    )

    # Trace through state transitions
    # Step 1: Developer Round 1
    comments_r1_dev = [
        {"body": format_turn_marker("DEVELOPER", 1, "READY_FOR_REVIEW")},
    ]
    a1 = determine_next_action(comments_r1_dev, max_rounds=3)
    assert a1.next_persona == "REVIEWER"
    assert a1.round == 1

    # Step 2: Reviewer Round 1 requests changes
    comments_r1_rev = comments_r1_dev + [
        {"body": format_turn_marker("REVIEWER", 1, "CHANGES_REQUESTED")},
    ]
    a2 = determine_next_action(comments_r1_rev, max_rounds=3)
    assert a2.next_persona == "DEVELOPER"
    assert a2.round == 2

    # Step 3: Developer Round 2 pushes fixes
    comments_r2_dev = comments_r1_rev + [
        {"body": format_turn_marker("DEVELOPER", 2, "READY_FOR_REVIEW")},
    ]
    a3 = determine_next_action(comments_r2_dev, max_rounds=3)
    assert a3.next_persona == "REVIEWER"
    assert a3.round == 2

    # Step 4: Reviewer Round 2 approves
    comments_r2_rev = comments_r2_dev + [
        {"body": format_turn_marker("REVIEWER", 2, "APPROVED")},
    ]
    a4 = determine_next_action(comments_r2_rev, max_rounds=3)
    assert a4.next_persona == "ORCHESTRATOR"
    assert a4.round == 2
    assert "AUTO_MERGE" in a4.action or "_verify_and_auto_merge_pr" in a4.action


@pytest.mark.asyncio
async def test_scenario_3_bounded_rounds_exhaustion_and_contingency(test_setup, monkeypatch):
    """
    Scenario 3: Bounded Rounds Exhaustion & Contingency Resolution
    Given reviewer.enabled is true and max_rounds is 3
    When Developer posts READY_FOR_REVIEW for Rounds 1, 2, 3
    And Reviewer requests changes in Rounds 1, 2, and posts CONTINGENCY_PLAN in Round 3
    Then Developer executes contingency and posts CONTINGENCY_COMPLETED round=3
    And orchestrator auto-merges upon passing CI.
    """
    comments = [
        {"body": format_turn_marker("DEVELOPER", 1, "READY_FOR_REVIEW")},
        {"body": format_turn_marker("REVIEWER", 1, "CHANGES_REQUESTED")},
        {"body": format_turn_marker("DEVELOPER", 2, "READY_FOR_REVIEW")},
        {"body": format_turn_marker("REVIEWER", 2, "CHANGES_REQUESTED")},
        {"body": format_turn_marker("DEVELOPER", 3, "READY_FOR_REVIEW")},
    ]
    # On Round 3, Reviewer evaluates
    a_rev3 = determine_next_action(comments, max_rounds=3)
    assert a_rev3.next_persona == "REVIEWER"
    assert a_rev3.round == 3

    # Reviewer posts CONTINGENCY_PLAN
    comments.append({"body": format_turn_marker("REVIEWER", 3, "CONTINGENCY_PLAN")})
    a_dev_contingency = determine_next_action(comments, max_rounds=3)
    assert a_dev_contingency.next_persona == "DEVELOPER"
    assert a_dev_contingency.round == 3
    assert "APPLY_CONTINGENCY" in a_dev_contingency.action or "Apply reviewer contingency" in a_dev_contingency.action

    # Developer executes contingency and posts CONTINGENCY_COMPLETED
    comments.append({"body": format_turn_marker("DEVELOPER", 3, "CONTINGENCY_COMPLETED")})
    a_merge = determine_next_action(comments, max_rounds=3)
    assert a_merge.next_persona == "ORCHESTRATOR"
    assert a_merge.round == 3
    assert "AUTO_MERGE" in a_merge.action or "_verify_and_auto_merge_pr" in a_merge.action


@pytest.mark.asyncio
async def test_scenario_4_phase_2_auto_merge_skip_on_active_negotiation(test_setup, monkeypatch):
    """
    Scenario 4: Phase 2 Auto-Merge Skip on Active Non-Terminal Negotiation
    Given dual mode is enabled and an open PR has green CI (PASS)
    When latest PR marker is DEVELOPER/READY_FOR_REVIEW round=1
    Then Phase 2 does NOT auto-merge the PR, allowing Reviewer persona to evaluate it.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    project.nodes["devtest"] = NodeConfig(
        harness="claude",
        reviewer=DevTestReviewerConfig(enabled=True, max_rounds=3),
    )

    open_pr = {
        "number": 55,
        "title": "feat: slice 1",
        "headRefName": "feat/issue-55",
        "labels": ["dev-implemented"],
    }

    comments = [
        {"body": format_turn_marker("DEVELOPER", 1, "READY_FOR_REVIEW")},
    ]

    async def mock_fetch_prs(repo, label=None, limit=20):
        if label == "needs-refactor":
            return []
        return [open_pr]

    monkeypatch.setattr(devtest, "fetch_open_prs", mock_fetch_prs)
    monkeypatch.setattr(devtest, "check_pr_ci_status", AsyncMock(return_value=("PASS", "Checks green")))
    monkeypatch.setattr(devtest, "_extract_issue_from_pr", AsyncMock(return_value=(55, "feat: slice 1")))
    monkeypatch.setattr(devtest, "fetch_pr_comments", AsyncMock(return_value=comments))
    monkeypatch.setattr(devtest, "verify_git_safety", AsyncMock(return_value=(True, "OK")))

    auto_merge_mock = AsyncMock()
    monkeypatch.setattr(devtest, "_verify_and_auto_merge_pr", auto_merge_mock)

    # When run_devtest_node runs with no new ready-for-dev issues
    monkeypatch.setattr(state_manager, "get_next_devtest_task", AsyncMock(return_value=None))
    monkeypatch.setattr(devtest, "fetch_issue_by_number", AsyncMock(return_value=None))

    ran, msg = await run_devtest_node(project, config, state_manager)
    # Phase 2 skips auto-merge for active negotiation
    assert not auto_merge_mock.called
    assert "Idle (0 tokens)" in msg or "0 tokens" in msg or ran is False


@pytest.mark.asyncio
async def test_scenario_5_mid_loop_restart_resumption(test_setup, monkeypatch):
    """
    Scenario 5: Mid-Loop Restart Resumption
    Given open PR exists with latest marker DEVELOPER/READY_FOR_REVIEW round=2 after restart
    When devtest runs
    Then Phase 3 routes to dispatcher without error
    And Reviewer persona is dispatched for Round 2 without resetting branch to origin/main or opening duplicate PR.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    project.nodes["devtest"] = NodeConfig(
        harness="claude",
        reviewer=DevTestReviewerConfig(enabled=True, max_rounds=3),
    )

    mock_issue = {"number": 88, "title": "Subtask 88", "labels": ["ready-for-dev"], "body": "AC"}
    monkeypatch.setattr(state_manager, "get_next_devtest_task", AsyncMock(return_value=88))
    monkeypatch.setattr(devtest, "fetch_issue_by_number", AsyncMock(return_value=mock_issue))
    monkeypatch.setattr(devtest, "fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(devtest, "verify_git_safety", AsyncMock(return_value=(True, "OK")))

    # Existing item in sdlc_items with linked_pr = 880
    await state_manager.sync_project_sdlc_items(
        project.name,
        [{"issue_number": 88, "title": "Subtask 88", "state": "IN_PROGRESS", "linked_pr": 880}],
    )

    comments = [
        {"body": format_turn_marker("DEVELOPER", 1, "READY_FOR_REVIEW")},
        {"body": format_turn_marker("REVIEWER", 1, "CHANGES_REQUESTED")},
        {"body": format_turn_marker("DEVELOPER", 2, "READY_FOR_REVIEW")},
    ]
    monkeypatch.setattr(devtest, "fetch_pr_comments", AsyncMock(return_value=comments))

    reviewer_turn_mock = AsyncMock(return_value=(True, "Reviewer round 2 completed with verdict: APPROVED"))
    monkeypatch.setattr(devtest, "_execute_reviewer_turn", reviewer_turn_mock)

    ran, msg = await run_devtest_node(project, config, state_manager)
    assert reviewer_turn_mock.called
    args, kwargs = reviewer_turn_mock.call_args
    assert kwargs["pr_number"] == 880
    assert kwargs["round_num"] == 2


@pytest.mark.asyncio
async def test_scenario_6_post_contingency_ci_failure_routing(test_setup, monkeypatch):
    """
    Scenario 6: Post-Contingency CI Failure Routing
    Given latest marker is DEVELOPER/CONTINGENCY_COMPLETED
    When _verify_and_auto_merge_pr checks CI and detects a failure (FAIL)
    Then PR is not merged and issue is routed to needs-refactor.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    # CI check returns FAIL
    monkeypatch.setattr(devtest, "check_pr_ci_status", AsyncMock(return_value=("FAIL", "Test suite failed")))

    edited_labels = []
    with patch("shutil.which", return_value="/usr/bin/gh"):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            async def fake_exec(*cmd, **kwargs):
                proc = AsyncMock()
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
                if "--add-label" in cmd:
                    idx = cmd.index("--add-label")
                    edited_labels.append(cmd[idx + 1])
                return proc
            mock_exec.side_effect = fake_exec

            ran, msg = await _verify_and_auto_merge_pr(
                project=project,
                state_manager=state_manager,
                pr_number=99,
                issue_id=99,
                issue_title="Contingency Task",
                trigger_label="ready-for-dev",
                auto_merge_approved=True,
            )
            assert ran is False
            assert "needs-refactor" in edited_labels or "needs-refactor" in msg


@pytest.mark.asyncio
async def test_scenario_7_full_sdlc_side_effects_on_merge(test_setup, monkeypatch):
    """
    Scenario 7: Full SDLC Side-Effect Execution on Merge
    Given dual-mode PR reaches APPROVED or CONTINGENCY_COMPLETED with passing CI
    When _verify_and_auto_merge_pr executes
    Then sdlc_items state is set to MERGED, worktree is cleaned up,
    parent feature checklist is advanced, and next queued child slice is unlocked.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    # Seed SDLC items: Parent issue #10, child #11 (IN_PROGRESS), child #12 (QUEUED)
    await state_manager.sync_project_sdlc_items(
        project.name,
        [
            {"issue_number": 10, "title": "Parent Feature", "state": "IN_PROGRESS", "labels": ["architect-processed"]},
            {"issue_number": 11, "title": "Child Slice 1", "state": "IN_PROGRESS", "labels": ["ready-for-dev"], "linked_pr": 111},
            {"issue_number": 12, "title": "Child Slice 2", "state": "QUEUED", "labels": ["queued"]},
        ],
    )

    monkeypatch.setattr(devtest, "check_pr_ci_status", AsyncMock(return_value=("PASS", "All checks green")))
    advance_mock = AsyncMock()
    monkeypatch.setattr(devtest, "_advance_parent_and_unlock_next_subtask", advance_mock)
    clean_wt_mock = AsyncMock()
    monkeypatch.setattr(devtest, "clean_worktree", clean_wt_mock)
    wt_path = tmp_path / "wt_devtest"
    wt_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(devtest.WorktreeManager, "get_worktree_path", MagicMock(return_value=wt_path))

    with patch("shutil.which", return_value="/usr/bin/gh"):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = proc

            ran, msg = await _verify_and_auto_merge_pr(
                project=project,
                state_manager=state_manager,
                pr_number=111,
                issue_id=11,
                issue_title="Child Slice 1",
                trigger_label="ready-for-dev",
                auto_merge_approved=True,
            )

            assert ran is True
            assert advance_mock.called
            assert clean_wt_mock.called

            # Check that child #11 SDLC item is set to MERGED
            items = await state_manager.get_sdlc_items(project.name)
            item_11 = next(it for it in items if it["issue_number"] == 11)
            assert item_11["state"] == "MERGED"


@pytest.mark.asyncio
async def test_execute_reviewer_turn_lock_acquisition(test_setup, monkeypatch):
    """
    Verifies that _execute_reviewer_turn acquires a lock using valid ttl_minutes and config.harnesses.
    """
    project, config, state_manager, tmp_path = test_setup
    await state_manager.init_db()

    node_cfg = NodeConfig(
        harness="claude",
        reviewer=DevTestReviewerConfig(enabled=True, harness="claude", max_rounds=3),
    )
    project.nodes["devtest"] = node_cfg

    mock_issue = {"number": 41, "title": "Test Issue", "body": "Gherkin"}

    acquire_lock_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(state_manager, "acquire_lock", acquire_lock_mock)
    monkeypatch.setattr(state_manager, "acquire_development_slot", AsyncMock(return_value=(True, [])))
    monkeypatch.setattr(state_manager, "release_lock", AsyncMock())
    monkeypatch.setattr(devtest, "check_dispatch_quota", AsyncMock(return_value=(True, MagicMock(harness_name="claude"))))
    monkeypatch.setattr(devtest.WorktreeManager, "ensure_worktree", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(devtest.AsyncHarnessAdapter, "execute", AsyncMock(return_value=0))
    monkeypatch.setattr(devtest, "post_pr_turn_comment", AsyncMock())
    monkeypatch.setattr(devtest, "check_pr_ci_status", AsyncMock(return_value=("PASS", "All green")))
    monkeypatch.setattr(devtest, "_verify_and_auto_merge_pr", AsyncMock(return_value=(True, "Merged")))

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        ran, msg = await devtest._execute_reviewer_turn(
            project=project,
            config=config,
            state_manager=state_manager,
            node_cfg=node_cfg,
            target_issue=mock_issue,
            pr_number=42,
            round_num=1,
            max_rounds=3,
            branch_prefix="feat/issue-",
        )

        assert acquire_lock_mock.called
        kwargs = acquire_lock_mock.call_args.kwargs
        assert "ttl_minutes" in kwargs
        assert "ttl_seconds" not in kwargs
        assert kwargs["issue_id"] == 41
        assert kwargs["node_type"] == "devtest"


def test_resolve_reviewer_config_peer_node_inheritance():
    """
    Verifies that resolve_reviewer_config inherits from project.nodes['reviewer']
    when devtest.reviewer is not explicitly enabled.
    """
    devtest_node = NodeConfig(harness="claude", model="sonnet", reviewer=DevTestReviewerConfig(enabled=False))
    peer_reviewer_node = NodeConfig(
        enabled=True,
        harness="antigravity",
        model="claude-sonnet-5-5-low",
        effort="low",
    )
    project = ProjectConfig(
        name="test-project",
        repo="org/repo",
        local_path=Path("/tmp/test"),
        nodes={
            "devtest": devtest_node,
            "reviewer": peer_reviewer_node,
        },
    )

    resolved = resolve_reviewer_config(devtest_node, project=project)
    assert resolved.enabled is True
    assert resolved.harness == "antigravity"
    assert resolved.model == "claude-sonnet-5-5-low"
    assert resolved.effort == "low"
    assert resolved.max_rounds == 3


def test_reviewer_config_antigravity_claude_sonnet_explicit():
    """
    Verifies that explicit antigravity / claude-sonnet-5-5-low configuration is preserved.
    """
    node = NodeConfig(
        harness="claude",
        reviewer=DevTestReviewerConfig(
            enabled=True,
            harness="antigravity",
            model="claude-sonnet-5-5-low",
            effort="low",
            max_rounds=3,
        ),
    )
    resolved = resolve_reviewer_config(node)
    assert resolved.enabled is True
    assert resolved.harness == "antigravity"
    assert resolved.model == "claude-sonnet-5-5-low"
    assert resolved.effort == "low"
    assert resolved.max_rounds == 3


