from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from orchestrator.config import GlobalConfig, HarnessConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.nodes.supervisor import (
    compute_issue_hash,
    evaluate_supervisor_issue,
    run_supervisor_node,
)


async def _mock_harness_execute_success(*args, **kwargs):
    """Mock execution helper that writes dummy output file to satisfy log_file.exists()."""
    log_file = kwargs.get("log_file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text("DUMMY HARNESS LOG", encoding="utf-8")
    return 0


@pytest.mark.asyncio
async def test_compute_issue_hash():
    """Verify compute_issue_hash is deterministic and SHA-256 compliant."""
    title = "feat: add user authentication"
    body = "As a user, I want to login with email and password."
    h1 = compute_issue_hash(title, body)
    h2 = compute_issue_hash(title, body)
    assert h1 == h2
    assert len(h1) == 64

    # Body difference changes hash
    h3 = compute_issue_hash(title, "Different body content")
    assert h1 != h3

    # Empty body handling
    h_empty1 = compute_issue_hash(title, None)
    h_empty2 = compute_issue_hash(title, "")
    assert h_empty1 == h_empty2


@pytest.mark.asyncio
async def test_scenario_unchanged_issue_is_skipped_with_zero_tokens(tmp_path: Path, caplog):
    """
    Scenario: Unchanged issue is skipped with zero tokens
    Given an open issue labeled `needs-po-review`
    And its SHA-256 hash of (title + "\n" + body) matches the `body_hash` stored in `po_tracking`
    And the stored `status` is "NEEDS_HUMAN_CLARIFICATION"
    When the Supervisor node evaluates the issue
    Then no AI harness subprocess is invoked
    And the log emits "[DEBUG] [supervisor] Issue #X hash unchanged. Skipping PO evaluation."
    And the function returns without mutating GitHub state
    """
    project = ProjectConfig(
        name="test-project",
        repo="AntaresAndBharani/test-project",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.7-flash-low"),
        },
    )
    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="echo", command_template="{prompt}")}
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_number = 101
    title = "feat(auth): token expiration logic"
    body = "Need clarification on JWT token lifespan and refresh rotation."
    body_hash = compute_issue_hash(title, body)

    # Given stored record with NEEDS_HUMAN_CLARIFICATION and matching hash
    await state_manager.upsert_po_tracking(
        repo=project.repo,
        issue_number=issue_number,
        body_hash=body_hash,
        status="NEEDS_HUMAN_CLARIFICATION",
        gherkin_ac=None,
        blockers="Missing refresh token strategy details",
    )

    issue = {
        "number": issue_number,
        "title": title,
        "body": body,
        "labels": [{"name": "needs-po-review"}],
    }

    # Mock harness execute to assert 0 invocations
    mock_harness_execute = AsyncMock(side_effect=_mock_harness_execute_success)
    mock_subprocess_exec = AsyncMock()

    with (
        patch("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_harness_execute),
        patch("asyncio.create_subprocess_exec", mock_subprocess_exec),
        caplog.at_level("DEBUG"),
    ):
        result = await evaluate_supervisor_issue(
            project=project,
            issue=issue,
            config=config,
            state_manager=state_manager,
            dry_run=False,
            force=False,
        )

        # Then no AI harness subprocess is invoked
        assert mock_harness_execute.call_count == 0

        # And the function returns without mutating GitHub state
        assert mock_subprocess_exec.call_count == 0

        # And the result reflects skip state
        assert result.skipped is True
        assert result.issue_number == issue_number
        assert result.verdict == "NEEDS_HUMAN_CLARIFICATION"
        assert result.status == "NEEDS_HUMAN_CLARIFICATION"
        assert result.gaps == "Missing refresh token strategy details"
        expected_msg = f"[DEBUG] [supervisor] Issue #{issue_number} hash unchanged. Skipping PO evaluation."
        assert result.details == expected_msg
        assert expected_msg in caplog.text


@pytest.mark.asyncio
async def test_scenario_changed_issue_proceeds_to_evaluation(tmp_path: Path):
    """
    Scenario: Changed issue proceeds to evaluation
    Given an open issue labeled `needs-po-review`
    And its computed SHA-256 hash differs from the stored `body_hash` (or no row exists)
    When the Supervisor node evaluates the issue
    Then it proceeds to full PO evaluation (harness dispatch)
    """
    project = ProjectConfig(
        name="test-project",
        repo="AntaresAndBharani/test-project",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.7-flash-low"),
        },
    )
    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="echo", command_template="{prompt}")}
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_number = 102
    title = "feat(auth): token expiration logic"
    initial_body = "Old description that lacked clarity."
    initial_hash = compute_issue_hash(title, initial_body)

    # Pre-seed with initial hash
    await state_manager.upsert_po_tracking(
        repo=project.repo,
        issue_number=issue_number,
        body_hash=initial_hash,
        status="NEEDS_HUMAN_CLARIFICATION",
        blockers="Old blockers",
    )

    # Now the issue has been updated with new requirements
    updated_body = "Updated with full JWT specification: access 15m, refresh 7d."
    issue = {
        "number": issue_number,
        "title": title,
        "body": updated_body,
        "labels": [{"name": "needs-po-review"}],
    }

    mock_harness_execute = AsyncMock(side_effect=_mock_harness_execute_success)
    mock_subprocess_exec = AsyncMock()

    with (
        patch("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_harness_execute),
        patch("asyncio.create_subprocess_exec", mock_subprocess_exec),
        patch("orchestrator.nodes.supervisor.parse_po_evaluation_response") as mock_parse,
    ):
        mock_parse.return_value = (
            "PO_APPROVED",
            None,
            "Feature: JWT Auth\n  Scenario: Valid token\n    Given valid token\n    When verified\n    Then access granted",
        )

        result = await evaluate_supervisor_issue(
            project=project,
            issue=issue,
            config=config,
            state_manager=state_manager,
            dry_run=False,
            force=False,
        )

        # Then harness is dispatched
        assert mock_harness_execute.call_count == 1
        assert result.skipped is False
        assert result.verdict == "PO_APPROVED"
        assert result.gherkin_ac is not None

        # And state in po_tracking was updated with new hash
        record = await state_manager.get_po_tracking(project.repo, issue_number)
        assert record is not None
        assert record["body_hash"] == compute_issue_hash(title, updated_body)
        assert record["status"] == "PO_APPROVED"


@pytest.mark.asyncio
async def test_scenario_new_issue_without_existing_row_proceeds_to_evaluation(tmp_path: Path):
    """
    Scenario: Issue with no existing row in po_tracking proceeds to evaluation
    """
    project = ProjectConfig(
        name="test-project",
        repo="AntaresAndBharani/test-project",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.7-flash-low"),
        },
    )
    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="echo", command_template="{prompt}")}
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_number = 103
    title = "feat: fresh issue"
    body = "Brand new issue content."
    issue = {
        "number": issue_number,
        "title": title,
        "body": body,
        "labels": [{"name": "needs-po-review"}],
    }

    mock_harness_execute = AsyncMock(side_effect=_mock_harness_execute_success)
    mock_subprocess_exec = AsyncMock()

    with (
        patch("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_harness_execute),
        patch("asyncio.create_subprocess_exec", mock_subprocess_exec),
        patch("orchestrator.nodes.supervisor.parse_po_evaluation_response") as mock_parse,
    ):
        mock_parse.return_value = ("NEEDS_HUMAN_CLARIFICATION", "Ambiguous scope", None)

        result = await evaluate_supervisor_issue(
            project=project,
            issue=issue,
            config=config,
            state_manager=state_manager,
            dry_run=False,
            force=False,
        )

        assert mock_harness_execute.call_count == 1
        assert result.skipped is False
        assert result.verdict == "NEEDS_HUMAN_CLARIFICATION"


@pytest.mark.asyncio
async def test_scenario_approved_issues_are_not_reskipped_indefinitely(tmp_path: Path):
    """
    Scenario: Approved issues are not re-skipped indefinitely
    Given a `po_tracking` row with `status` != "NEEDS_HUMAN_CLARIFICATION" (e.g., "PO_APPROVED")
    When the Supervisor node evaluates the issue
    Then the hash-skip short-circuit does not apply (only NEEDS_HUMAN_CLARIFICATION rows are hash-gated)
    """
    project = ProjectConfig(
        name="test-project",
        repo="AntaresAndBharani/test-project",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.7-flash-low"),
        },
    )
    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="echo", command_template="{prompt}")}
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_number = 104
    title = "feat: already approved issue"
    body = "Fully specified feature with acceptance criteria."
    body_hash = compute_issue_hash(title, body)

    # Pre-seed with status = "PO_APPROVED" (not NEEDS_HUMAN_CLARIFICATION)
    await state_manager.upsert_po_tracking(
        repo=project.repo,
        issue_number=issue_number,
        body_hash=body_hash,
        status="PO_APPROVED",
        gherkin_ac="Feature: AC\n  Scenario: S1\n    Given G\n    When W\n    Then T",
    )

    issue = {
        "number": issue_number,
        "title": title,
        "body": body,
        "labels": [{"name": "needs-po-review"}],
    }

    mock_harness_execute = AsyncMock(side_effect=_mock_harness_execute_success)
    mock_subprocess_exec = AsyncMock()

    with (
        patch("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_harness_execute),
        patch("asyncio.create_subprocess_exec", mock_subprocess_exec),
        patch("orchestrator.nodes.supervisor.parse_po_evaluation_response") as mock_parse,
    ):
        mock_parse.return_value = (
            "PO_APPROVED",
            None,
            "Feature: AC\n  Scenario: S1\n    Given G\n    When W\n    Then T",
        )

        result = await evaluate_supervisor_issue(
            project=project,
            issue=issue,
            config=config,
            state_manager=state_manager,
            dry_run=False,
            force=False,
        )

        # Then the hash-skip short-circuit does not apply: harness is dispatched
        assert mock_harness_execute.call_count == 1
        assert result.skipped is False


@pytest.mark.asyncio
async def test_run_supervisor_node_skips_unchanged_po_issues(tmp_path: Path):
    """
    Integration test: run_supervisor_node skips evaluation for unchanged NEEDS_HUMAN_CLARIFICATION issues
    and reports idle when no other anomalies are present.
    """
    project = ProjectConfig(
        name="test-project",
        repo="AntaresAndBharani/test-project",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(enabled=True, harness="antigravity", model="gemini-3.7-flash-low"),
        },
    )
    config = GlobalConfig(
        harnesses={"antigravity": HarnessConfig(binary="echo", command_template="{prompt}")}
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    title = "feat: unchanged po review ticket"
    body = "Awaiting clarification from human stakeholder."
    body_hash = compute_issue_hash(title, body)

    await state_manager.upsert_po_tracking(
        repo=project.repo,
        issue_number=55,
        body_hash=body_hash,
        status="NEEDS_HUMAN_CLARIFICATION",
        blockers="Unspecified API protocol",
    )

    mock_issues = [
        {"number": 55, "title": title, "body": body, "labels": [{"name": "needs-po-review"}], "createdAt": "2026-08-29T10:00:00Z"}
    ]

    mock_harness_execute = AsyncMock(side_effect=_mock_harness_execute_success)

    async def mock_fetch_issues(repo, label, limit=5):
        if label == "needs-po-review":
            return mock_issues
        return []

    async def mock_fetch_prs(repo):
        return []

    async def mock_fetch_all(repo, limit=100):
        return mock_issues

    with (
        patch("orchestrator.poller.fetch_issues_with_label", side_effect=mock_fetch_issues),
        patch("orchestrator.poller.fetch_open_prs", side_effect=mock_fetch_prs),
        patch("orchestrator.poller.fetch_all_open_issues", side_effect=mock_fetch_all),
        patch.object(AsyncHarnessAdapter, "execute", mock_harness_execute),
    ):
        ran, msg = await run_supervisor_node(project, config, state_manager, force=True)

        # 0 tokens consumed for harness
        assert mock_harness_execute.call_count == 0
        # No action taken because the only issue was skipped
        assert ran is False
        assert "State is consistent (0 tokens)" in msg
