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


def test_derive_ci_status_pure_function():
    """
    Unit tests for derive_ci_status pure function across various rollup formats.
    """
    # 1. None or empty rollup
    assert poller.derive_ci_status(None) is None
    assert poller.derive_ci_status([]) is None
    assert poller.derive_ci_status({}) is None

    # 2. All PASS
    rollup_pass = [
        {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "NEUTRAL"},
        {"name": "build", "status": "COMPLETED", "conclusion": "SKIPPED"},
    ]
    assert poller.derive_ci_status(rollup_pass) == "PASS"

    # 3. Any FAIL (conclusion)
    rollup_fail = [
        {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
    ]
    assert poller.derive_ci_status(rollup_fail) == "FAIL"

    rollup_timeout = [
        {"name": "e2e", "status": "COMPLETED", "conclusion": "TIMED_OUT"},
    ]
    assert poller.derive_ci_status(rollup_timeout) == "FAIL"

    rollup_error_state = [
        {"name": "ci", "status": "COMPLETED", "state": "ERROR"},
    ]
    assert poller.derive_ci_status(rollup_error_state) == "FAIL"

    # 4. Any RUNNING / PENDING
    rollup_running = [
        {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "IN_PROGRESS", "conclusion": ""},
    ]
    assert poller.derive_ci_status(rollup_running) == "RUNNING"

    rollup_queued = [
        {"name": "test", "status": "QUEUED", "conclusion": None},
    ]
    assert poller.derive_ci_status(rollup_queued) == "RUNNING"

    # 5. Nested contexts dictionary
    rollup_dict_contexts = {
        "contexts": [
            {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "integration", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ]
    }
    assert poller.derive_ci_status(rollup_dict_contexts) == "PASS"

    # 6. Nested nodes dictionary
    rollup_dict_nodes = {
        "nodes": [
            {"name": "check", "status": "COMPLETED", "conclusion": "FAILURE"},
        ]
    }
    assert poller.derive_ci_status(rollup_dict_nodes) == "FAIL"


@pytest.mark.asyncio
async def test_fetch_open_prs_bulk_subprocess_output(monkeypatch):
    """
    Verifies fetch_open_prs executes gh CLI requesting state and statusCheckRollup,
    parsing output correctly in a single bulk pass.
    """
    import json
    from unittest.mock import MagicMock

    sample_output = [
        {
            "number": 459,
            "title": "feat(core): subtask implementation",
            "body": "Closes #455\nParent: #454",
            "headRefName": "feat/issue-455",
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
        {
            "number": 460,
            "title": "fix(core): bug fix",
            "body": "Closes #456",
            "headRefName": "fix/issue-456",
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "test", "status": "IN_PROGRESS", "conclusion": ""},
            ],
        },
    ]

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(
        return_value=(json.dumps(sample_output).encode("utf-8"), b"")
    )

    captured_cmd = []

    async def mock_subprocess_exec(*args, **kwargs):
        captured_cmd.extend(args)
        return mock_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_subprocess_exec)

    prs = await poller.fetch_open_prs("AntaresAndBharani/graph-engineering", limit=10)

    assert len(prs) == 2
    assert prs[0]["number"] == 459
    assert prs[0]["state"] == "OPEN"
    assert len(prs[0]["statusCheckRollup"]) == 2
    assert prs[1]["number"] == 460
    assert prs[1]["statusCheckRollup"][0]["status"] == "IN_PROGRESS"

    # Verify command requested state and statusCheckRollup
    assert "gh" in captured_cmd
    assert "pr" in captured_cmd
    assert "list" in captured_cmd
    json_idx = captured_cmd.index("--json")
    json_fields = captured_cmd[json_idx + 1]
    assert "state" in json_fields
    assert "statusCheckRollup" in json_fields


@pytest.mark.asyncio
async def test_scenario_single_pass_bulk_pr_and_ci_rollup_display(tmp_path: Path, monkeypatch):
    """
    Scenario 3: Single-Pass Bulk PR & CI Rollup Display
      Given subtask #455 has an associated open Pull Request #459
      And GitHub Actions CI checks for PR #459 are all passing
      When the background poller executes a sync cycle
      Then it must extract PR #459 and CI status "PASS" in a single bulk PR query
      And it must persist pr_status="OPEN" and pr_ci_details="PASS" for subtask #455
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(
        name="crosstrainingapp",
        repo="AntaresAndBharani/crosstrainingapp",
        local_path=tmp_path / "crosstrainingapp",
    )

    mock_issues = [
        {
            "number": 454,
            "title": "Story: Email Password Setup",
            "state": "OPEN",
            "labels": [{"name": "architect-processed"}],
            "body": "User Story\n\n## Subtasks\n- [ ] #455\n- [ ] #456",
            "updatedAt": "2026-08-30T10:00:00Z",
        },
        {
            "number": 455,
            "title": "Subtask: Extract modular dialog",
            "state": "OPEN",
            "labels": [{"name": "dev-implemented"}],
            "body": "Subtask implementation.\n\nParent: #454",
            "updatedAt": "2026-08-30T10:05:00Z",
        },
        {
            "number": 456,
            "title": "Subtask: Sanitize email input",
            "state": "OPEN",
            "labels": [{"name": "ready-for-dev"}],
            "body": "Subtask implementation.\n\nParent: #454",
            "updatedAt": "2026-08-30T10:10:00Z",
        },
    ]

    mock_prs = [
        {
            "number": 459,
            "title": "feat(auth): extract modular dialog",
            "headRefName": "feat/issue-455",
            "body": "Closes #455\nParent: #454",
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }
    ]

    monkeypatch.setattr(poller, "fetch_all_open_issues", AsyncMock(return_value=mock_issues))
    monkeypatch.setattr(poller, "fetch_open_prs", AsyncMock(return_value=mock_prs))

    # Run poller sweep
    items = await poller.poll_project_sdlc_items(project, state_manager)

    assert len(items) == 3

    # Check subtask #455
    subtask_455 = next(item for item in items if item["issue_number"] == 455)
    assert subtask_455["parent_issue_id"] == 454
    assert subtask_455["item_type"] == "SUBTASK"
    assert subtask_455["linked_pr"] == 459
    assert subtask_455["pr_status"] == "OPEN"
    assert subtask_455["pr_ci_details"] == "PASS"

    # Check subtask #456 (no PR linked)
    subtask_456 = next(item for item in items if item["issue_number"] == 456)
    assert subtask_456["parent_issue_id"] == 454
    assert subtask_456["linked_pr"] is None
    assert subtask_456["pr_status"] is None
    assert subtask_456["pr_ci_details"] is None

    # Check story #454
    story_454 = next(item for item in items if item["issue_number"] == 454)
    assert story_454["parent_issue_id"] is None
    assert story_454["item_type"] == "STORY"

    # Verify persistence in SQLite
    stored_items = await state_manager.get_sdlc_items("crosstrainingapp")
    stored_455 = next(s for s in stored_items if s["issue_number"] == 455)
    assert stored_455["parent_issue_id"] == 454
    assert stored_455["linked_pr"] == 459
    assert stored_455["pr_status"] == "OPEN"
    assert stored_455["pr_ci_details"] == "PASS"


@pytest.mark.asyncio
async def test_scenario_bulk_pr_and_ci_rollup_variations(tmp_path: Path, monkeypatch):
    """
    Tests poll_project_sdlc_items with various PR statuses (MERGED, CLOSED, OPEN)
    and CI check conclusions (FAIL, RUNNING, None).
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    project = ProjectConfig(
        name="multi-state-project",
        repo="AntaresAndBharani/multi-state-project",
        local_path=tmp_path / "multi-state-project",
    )

    mock_issues = [
        {
            "number": 10,
            "title": "Subtask: Failing CI",
            "state": "OPEN",
            "labels": [{"name": "dev-implemented"}],
            "body": "Subtask\nParent: #1",
        },
        {
            "number": 20,
            "title": "Subtask: Running CI",
            "state": "OPEN",
            "labels": [{"name": "dev-implemented"}],
            "body": "Subtask\nParent: #1",
        },
        {
            "number": 30,
            "title": "Subtask: Merged PR",
            "state": "CLOSED",
            "labels": [{"name": "dev-implemented"}],
            "body": "Subtask\nParent: #1",
        },
    ]

    mock_prs = [
        {
            "number": 101,
            "title": "feat: failing check",
            "headRefName": "feat/issue-10",
            "body": "Closes #10",
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}
            ],
        },
        {
            "number": 102,
            "title": "feat: running check",
            "headRefName": "feat/issue-20",
            "body": "Closes #20",
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "test", "status": "IN_PROGRESS", "conclusion": ""}
            ],
        },
        {
            "number": 103,
            "title": "feat: merged PR",
            "headRefName": "feat/issue-30",
            "body": "Closes #30",
            "state": "MERGED",
            "statusCheckRollup": [],
        },
    ]

    monkeypatch.setattr(poller, "fetch_all_open_issues", AsyncMock(return_value=mock_issues))
    monkeypatch.setattr(poller, "fetch_open_prs", AsyncMock(return_value=mock_prs))

    items = await poller.poll_project_sdlc_items(project, state_manager)
    assert len(items) == 3

    item_10 = next(i for i in items if i["issue_number"] == 10)
    assert item_10["linked_pr"] == 101
    assert item_10["pr_status"] == "OPEN"
    assert item_10["pr_ci_details"] == "FAIL"

    item_20 = next(i for i in items if i["issue_number"] == 20)
    assert item_20["linked_pr"] == 102
    assert item_20["pr_status"] == "OPEN"
    assert item_20["pr_ci_details"] == "RUNNING"

    item_30 = next(i for i in items if i["issue_number"] == 30)
    assert item_30["linked_pr"] == 103
    assert item_30["pr_status"] == "MERGED"
    assert item_30["pr_ci_details"] is None


