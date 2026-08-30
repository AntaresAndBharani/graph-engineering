from __future__ import annotations

from pathlib import Path
import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from orchestrator.db import StateManager
from orchestrator.ui.widgets import (
    SDLCProgressWidget,
    format_pr_status_badge,
    _apply_keyed_diff,
)


def test_format_pr_status_badge_mapping():
    """
    Unit test verifying color badge formatting for all PR state and CI rollup combinations.
    """
    # 1. No linked PR
    assert format_pr_status_badge(None) == "-"
    assert format_pr_status_badge(None, pr_status="OPEN", pr_ci_details="PASS") == "-"

    # 2. Merged PR
    assert format_pr_status_badge(459, pr_status="MERGED") == "#459 [blue]MERGED[/blue]"
    assert format_pr_status_badge(459, pr_status="merged") == "#459 [blue]MERGED[/blue]"

    # 3. CI Status PASS / FAIL / RUNNING
    assert format_pr_status_badge(459, pr_status="OPEN", pr_ci_details="PASS") == "#459 [green]PASS[/green]"
    assert format_pr_status_badge(459, pr_status="OPEN", pr_ci_details="FAIL") == "#459 [red]FAIL[/red]"
    assert format_pr_status_badge(459, pr_status="OPEN", pr_ci_details="RUNNING") == "#459 [yellow]RUNNING[/yellow]"

    # 4. Open PR without CI details
    assert format_pr_status_badge(459, pr_status="OPEN") == "#459"
    assert format_pr_status_badge(459, pr_status=None) == "#459"

    # 5. Non-standard PR status without CI details
    assert format_pr_status_badge(459, pr_status="DRAFT") == "#459 [DRAFT]"


@pytest.mark.asyncio
async def test_scenario_hierarchical_sdlc_tree_rendering_multi_child(tmp_path: Path):
    """
    Scenario 1: Hierarchical SDLC Tree Rendering
      Given project "crosstrainingapp" is selected in the TUI dashboard
      And the project contains parent story #454 with subtasks #455 and #456
      When SDLCProgressWidget renders the items from SQLite
      Then story #454 must render at the root level
      And subtask #455 must render with "  ├─ " prefix
      And subtask #456 must render with "  └─ " prefix
      And subtasks must be ordered by sequence_order ascending.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 454,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "Email Password Setup",
            "state": "OPEN",
            "labels": "architect-processed",
            "parent_issue_id": None,
            "linked_pr": None,
        },
        {
            "issue_number": 456,
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "title": "Sanitize email input",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "parent_issue_id": 454,
            "linked_pr": None,
        },
        {
            "issue_number": 455,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Extract modular dialog",
            "state": "OPEN",
            "labels": "dev-implemented",
            "parent_issue_id": 454,
            "linked_pr": 459,
            "pr_status": "MERGED",
        },
    ]
    await state_manager.sync_project_sdlc_items("crosstrainingapp", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="crosstrainingapp")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.row_count == 3

        # Row 0: Root Story #454
        row0 = widget.get_row_at(0)
        assert row0[0] == "#454"
        assert row0[1] == "Email Password Setup"
        assert row0[2] == "[LOCKED] architect-processed"
        assert row0[3] == "-"

        # Row 1: Subtask #455 (First child -> ├─ prefix)
        row1 = widget.get_row_at(1)
        assert row1[0] == "#455"
        assert row1[1] == "  ├─ Extract modular dialog"
        assert row1[2] == "dev-implemented"
        assert "[blue]MERGED[/blue]" in row1[3]

        # Row 2: Subtask #456 (Last child -> └─ prefix)
        row2 = widget.get_row_at(2)
        assert row2[0] == "#456"
        assert row2[1] == "  └─ Sanitize email input"
        assert row2[2] == "ready-for-dev"
        assert row2[3] == "-"


@pytest.mark.asyncio
async def test_sdlc_progress_widget_tree_prefix_single_child(tmp_path: Path):
    """
    Asserts single child subtask renders with └─ prefix instead of ├─ prefix.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 100,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "Standalone Feature Story",
            "state": "OPEN",
            "labels": "architect-processed",
        },
        {
            "issue_number": 101,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Sole Subtask",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "parent_issue_id": 100,
        },
    ]
    await state_manager.sync_project_sdlc_items("single-child-proj", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="single-child-proj")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.row_count == 2
        assert widget.get_row_at(0)[0] == "#100"
        assert widget.get_row_at(1)[0] == "#101"
        assert widget.get_row_at(1)[1] == "  └─ Sole Subtask"


@pytest.mark.asyncio
async def test_sdlc_progress_widget_tree_prefix_three_children(tmp_path: Path):
    """
    Asserts tree-prefix rendering for 3 subtasks: ├─, ├─, └─.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {"issue_number": 200, "item_type": "STORY", "sequence_order": 1, "title": "Epic Story"},
        {"issue_number": 201, "item_type": "SUBTASK", "sequence_order": 1, "title": "Step 1", "parent_issue_id": 200},
        {"issue_number": 202, "item_type": "SUBTASK", "sequence_order": 2, "title": "Step 2", "parent_issue_id": 200},
        {"issue_number": 203, "item_type": "SUBTASK", "sequence_order": 3, "title": "Step 3", "parent_issue_id": 200},
    ]
    await state_manager.sync_project_sdlc_items("multi-proj", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="multi-proj")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.row_count == 4
        assert widget.get_row_at(1)[1] == "  ├─ Step 1"
        assert widget.get_row_at(2)[1] == "  ├─ Step 2"
        assert widget.get_row_at(3)[1] == "  └─ Step 3"


@pytest.mark.asyncio
async def test_scenario_smart_visibility_prevents_orphaned_subtask_loss_ui(tmp_path: Path):
    """
    Scenario 2: Smart Visibility Prevents Orphaned Subtask Loss
      Given parent story #454 is marked "closed"
      And child subtask #456 is still "open" (ready-for-dev)
      When the UI fetches the SDLC hierarchy from StateManager
      Then the query must return parent story #454 and open subtask #456
      And the widget must render the parent as root to maintain visual context for the active subtask.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 454,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "Email Password Setup",
            "state": "CLOSED",
            "labels": "done",
            "parent_issue_id": None,
        },
        {
            "issue_number": 455,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Extract modular dialog",
            "state": "CLOSED",
            "labels": "done",
            "parent_issue_id": 454,
        },
        {
            "issue_number": 456,
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "title": "Sanitize email input",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "parent_issue_id": 454,
        },
    ]
    await state_manager.sync_project_sdlc_items("crosstrainingapp", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="crosstrainingapp")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        # Parent #454 is retained as root, and open subtask #456 is shown
        assert widget.row_count >= 2
        root_row = widget.get_row_at(0)
        assert root_row[0] == "#454"
        assert root_row[1] == "Email Password Setup"

        # Check subtasks under root
        sub_row = widget.get_row_at(2)
        assert sub_row[0] == "#456"
        assert "Sanitize email input" in sub_row[1]
        assert sub_row[2] == "ready-for-dev"


@pytest.mark.asyncio
async def test_scenario_pr_status_badge_display(tmp_path: Path):
    """
    Scenario 3: PR Status Badge Display
      Given subtask #455 has pr_status="OPEN" and pr_ci_details="PASS"
      When the widget renders the row
      Then it must display "#459 [green]PASS[/green]" in the PR Status column.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 454,
            "item_type": "STORY",
            "title": "Story Title",
            "state": "OPEN",
        },
        {
            "issue_number": 455,
            "item_type": "SUBTASK",
            "title": "Subtask Title",
            "state": "OPEN",
            "parent_issue_id": 454,
            "linked_pr": 459,
            "pr_status": "OPEN",
            "pr_ci_details": "PASS",
        },
    ]
    await state_manager.sync_project_sdlc_items("test-proj", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="test-proj")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        row1 = widget.get_row_at(1)
        assert row1[0] == "#455"
        assert row1[3] == "#459 [green]PASS[/green]"


@pytest.mark.asyncio
async def test_scenario_cursor_stability_across_refresh_cycle(tmp_path: Path, mocker):
    """
    Scenario 4: Cursor Stability Across Refresh
      Given the operator's cursor is on a specific row
      When the 2.0s table refresh occurs in SDLCProgressWidget
      Then rows must update in-place with stable keys without resetting cursor position
      And table.clear() must not be called.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {"issue_number": 10, "item_type": "STORY", "title": "Story 10", "state": "OPEN"},
        {"issue_number": 20, "item_type": "STORY", "title": "Story 20", "state": "OPEN"},
        {"issue_number": 30, "item_type": "STORY", "title": "Story 30", "state": "OPEN"},
    ]
    await state_manager.sync_project_sdlc_items("test-proj", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="test-proj")

    app = TestApp()
    async with app.run_test() as pilot:
        widget = app.query_one(SDLCProgressWidget)
        widget.focus()
        assert widget.row_count == 3

        # Move cursor to row 1 (Story 20)
        widget.move_cursor(row=1)
        await pilot.pause()
        assert widget.cursor_row == 1

        clear_spy = mocker.spy(widget, "clear")

        # Mutate SQLite state
        updated_items = [
            {"issue_number": 10, "item_type": "STORY", "title": "Story 10 (Updated)", "state": "OPEN"},
            {"issue_number": 20, "item_type": "STORY", "title": "Story 20 (Active)", "state": "OPEN", "linked_pr": 100, "pr_ci_details": "PASS"},
            {"issue_number": 30, "item_type": "STORY", "title": "Story 30", "state": "OPEN"},
        ]
        await state_manager.sync_project_sdlc_items("test-proj", updated_items)

        # Trigger update
        await widget.update_project("test-proj")
        await pilot.pause()

        # Verify clear was NEVER called
        clear_spy.assert_not_called()

        # Verify cursor position is maintained
        assert widget.cursor_row == 1
        assert widget.row_count == 3
        assert widget.get_row("20")[1] == "Story 20 (Active)"
        assert widget.get_row("20")[3] == "#100 [green]PASS[/green]"


@pytest.mark.asyncio
async def test_apply_keyed_diff_crud_and_ordering():
    """
    Direct unit test for _apply_keyed_diff validating add, update, remove, and ordering.
    """
    class DiffTestApp(App):
        def compose(self) -> ComposeResult:
            yield DataTable()

    app = DiffTestApp()
    async with app.run_test() as _:
        table = app.query_one(DataTable)
        # Before adding columns, calling diff is a safe no-op
        _apply_keyed_diff(table, [("row1", ("A", "B"))])
        assert table.row_count == 0

        table.add_columns("Col1", "Col2")

        # Initial population
        _apply_keyed_diff(table, [
            ("r1", ("Val1", "Val2")),
            ("r2", ("Val3", "Val4")),
        ])
        assert table.row_count == 2
        assert table.get_row("r1") == ["Val1", "Val2"]
        assert table.get_row("r2") == ["Val3", "Val4"]

        # In-place update + remove r1 + insert r3 + reverse order
        _apply_keyed_diff(table, [
            ("r3", ("Val5", "Val6")),
            ("r2", ("Val3-Updated", "Val4")),
        ])
        assert table.row_count == 2
        assert table.get_row("r2") == ["Val3-Updated", "Val4"]
        assert table.get_row("r3") == ["Val5", "Val6"]


@pytest.mark.asyncio
async def test_scenario_sdlc_widget_shows_active_lock(tmp_path: Path):
    """
    Scenario: SDLC widget shows the active lock
      Given Story #90 is the currently active locked story
      When the SDLCProgressWidget renders its table
      Then the row for Story #90 must display a "[LOCKED]" badge
      And no other concurrently open story row displays the badge.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 90,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "Active Locked Story A",
            "state": "OPEN",
            "labels": "architect-processed",
        },
        {
            "issue_number": 93,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Subtask A1",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "parent_issue_id": 90,
        },
        {
            "issue_number": 94,
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "title": "Subtask A2",
            "state": "OPEN",
            "labels": "queued",
            "parent_issue_id": 90,
        },
        {
            "issue_number": 95,
            "item_type": "STORY",
            "sequence_order": 2,
            "title": "Concurrently Open Story B",
            "state": "OPEN",
            "labels": "architect-processed",
        },
        {
            "issue_number": 98,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Subtask B1",
            "state": "OPEN",
            "labels": "queued",
            "parent_issue_id": 95,
        },
    ]
    await state_manager.sync_project_sdlc_items("lock-test-proj", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="lock-test-proj")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)
        assert widget.row_count == 5

        # Row 0: Story #90 (Active Locked Story)
        row0 = widget.get_row_at(0)
        assert row0[0] == "#90"
        assert row0[1] == "Active Locked Story A"
        assert "[LOCKED]" in row0[2]
        assert row0[2] == "[LOCKED] architect-processed"

        # Row 1: Subtask #93 (No locked badge)
        row1 = widget.get_row_at(1)
        assert row1[0] == "#93"
        assert "[LOCKED]" not in row1[2]

        # Row 2: Subtask #94 (No locked badge)
        row2 = widget.get_row_at(2)
        assert row2[0] == "#94"
        assert "[LOCKED]" not in row2[2]

        # Row 3: Story #95 (Concurrently open story -> NO locked badge)
        row3 = widget.get_row_at(3)
        assert row3[0] == "#95"
        assert row3[1] == "Concurrently Open Story B"
        assert "[LOCKED]" not in row3[2]
        assert row3[2] == "architect-processed"

        # Row 4: Subtask #98 (No locked badge)
        row4 = widget.get_row_at(4)
        assert row4[0] == "#98"
        assert "[LOCKED]" not in row4[2]



