from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

from orchestrator.config import ProjectConfig
from orchestrator.db import StateManager
from orchestrator import poller


def test_extract_linked_pr():
    """
    Asserts linked PR extraction accurately inspects headRefName, title, and body.
    """
    prs = [
        {"number": 101, "headRefName": "feat/issue-37", "title": "feat(core): setup", "body": "Initial work"},
        {"number": 102, "headRefName": "fix/bug-fix", "title": "fix: resolve bug (closes #42)", "body": "Fixes logic"},
        {"number": 103, "headRefName": "chore/cleanup", "title": "chore: cleanup", "body": "Closes #55\nParent: #50"},
    ]

    # Matching by branch
    assert poller.extract_linked_pr(37, prs) == 101
    # Matching by title
    assert poller.extract_linked_pr(42, prs) == 102
    # Matching by body
    assert poller.extract_linked_pr(55, prs) == 103
    # No match
    assert poller.extract_linked_pr(99, prs) is None
    # Empty PR list
    assert poller.extract_linked_pr(37, []) is None


def test_parse_iso_timestamp():
    """
    Asserts ISO timestamp strings and epoch floats are correctly converted to seconds.
    """
    assert poller.parse_iso_timestamp(1234567.0) == 1234567.0
    parsed = poller.parse_iso_timestamp("2026-08-29T18:00:00Z")
    assert parsed > 0
    # Fallback on invalid string
    now_ts = poller.parse_iso_timestamp("not-a-timestamp")
    assert now_ts > 0


@pytest.mark.asyncio
async def test_scenario_poller_syncs_sdlc_items_after_sweep(tmp_path: Path, monkeypatch):
    """
    Scenario: Poller syncs SDLC items after a sweep
    Given `orchestrator/poller.py` completes a zero-token polling sweep for a project
    When issues and linked PRs are fetched
    Then `StateManager.sync_project_sdlc_items(project_name, items)` is called with
     (project_name, issue_number, title, state, labels, linked_pr, updated_at) for each active issue
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(
        name="graph-core",
        repo="AntaresAndBharani/graph-core",
        local_path=tmp_path / "graph-core",
    )

    mock_issues = [
        {
            "number": 37,
            "title": "feat(poller): populate sdlc_items",
            "state": "OPEN",
            "labels": [{"name": "ready-for-dev"}],
            "updatedAt": "2026-08-29T18:00:00Z",
        },
        {
            "number": 38,
            "title": "feat(ui): widgets",
            "state": "OPEN",
            "labels": ["needs-triage"],
            "updatedAt": "2026-08-29T18:10:00Z",
        },
    ]

    mock_prs = [
        {
            "number": 45,
            "headRefName": "feat/issue-37",
            "title": "feat(poller): initial wiring",
            "body": "Closes #37",
        }
    ]

    monkeypatch.setattr(poller, "fetch_all_open_issues", AsyncMock(return_value=mock_issues))
    monkeypatch.setattr(poller, "fetch_open_prs", AsyncMock(return_value=mock_prs))

    # Execute polling sweep
    items = await poller.poll_project_sdlc_items(project, state_manager)

    assert len(items) == 2
    assert items[0]["issue_number"] == 37
    assert items[0]["project_name"] == "graph-core"
    assert items[0]["linked_pr"] == 45
    assert items[1]["issue_number"] == 38
    assert items[1]["linked_pr"] is None

    # Verify persisted in StateManager
    stored_items = await state_manager.get_sdlc_items("graph-core")
    assert len(stored_items) == 2
    assert stored_items[0]["issue_number"] == 37
    assert stored_items[0]["linked_pr"] == 45
    assert "ready-for-dev" in stored_items[0]["labels"]


@pytest.mark.asyncio
async def test_fetch_project_workload_syncs_sdlc(tmp_path: Path, monkeypatch):
    """
    Asserts fetch_project_workload invokes poll_project_sdlc_items when state_manager is provided.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(
        name="test-project",
        repo="org/test-project",
        local_path=tmp_path / "test-project",
    )

    mock_issues = [{"number": 1, "title": "Bug 1", "state": "OPEN", "labels": [], "updatedAt": "2026-08-29T12:00:00Z"}]
    monkeypatch.setattr(poller, "fetch_all_open_issues", AsyncMock(return_value=mock_issues))
    monkeypatch.setattr(poller, "fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(poller, "fetch_issues_with_label", AsyncMock(return_value=[]))

    workload = await poller.fetch_project_workload(project, state_manager=state_manager)
    assert isinstance(workload, dict)

    stored = await state_manager.get_sdlc_items("test-project")
    assert len(stored) == 1
    assert stored[0]["issue_number"] == 1


@pytest.mark.asyncio
async def test_scenario_non_blocking_sdlc_sync_failure(tmp_path: Path, monkeypatch):
    """
    Scenario: Non-blocking, best-effort recording
    Given the SQLite write for sdlc_items fails unexpectedly
    When the poller continues its normal flow
    Then the failure must not crash the polling sweep (log and continue)
    """
    project = ProjectConfig(
        name="failing-db-project",
        repo="org/failing-db-project",
        local_path=tmp_path / "failing",
    )

    mock_issues = [{"number": 5, "title": "Resilient Item", "state": "OPEN", "labels": []}]
    monkeypatch.setattr(poller, "fetch_all_open_issues", AsyncMock(return_value=mock_issues))
    monkeypatch.setattr(poller, "fetch_open_prs", AsyncMock(return_value=[]))

    # Mock a StateManager whose sync_project_sdlc_items raises an exception
    failing_state_manager = AsyncMock()
    failing_state_manager.sync_project_sdlc_items.side_effect = RuntimeError("Disk I/O Error / Locked DB")

    # Must complete cleanly without raising
    items = await poller.poll_project_sdlc_items(project, failing_state_manager)
    assert len(items) == 1
    assert items[0]["issue_number"] == 5
