from __future__ import annotations

from pathlib import Path
import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from orchestrator.config import (
    GlobalConfig,
    HarnessQuotaConfig,
    QuotaSettings,
    WindowLimitConfig,
)
from orchestrator.db import StateManager
from orchestrator.quota import QuotaManager
from orchestrator.ui.widgets import (
    HarnessQuotaWidget,
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
        assert row0[0] == "#454 [LOCKED]"
        assert row0[1] == "-"
        assert row0[2] == "Email Password Setup"
        assert row0[3] == "architect-processed"

        # Row 1: Subtask #455 (First child -> ├─ prefix)
        row1 = widget.get_row_at(1)
        assert row1[0] == "#455"
        assert "[blue]MERGED[/blue]" in row1[1]
        assert row1[2] == "  ├─ Extract modular dialog"
        assert row1[3] == "dev-implemented"

        # Row 2: Subtask #456 (Last child -> └─ prefix)
        row2 = widget.get_row_at(2)
        assert row2[0] == "#456"
        assert row2[1] == "-"
        assert row2[2] == "  └─ Sanitize email input"
        assert row2[3] == "ready-for-dev"


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
        assert "#100" in widget.get_row_at(0)[0]
        assert widget.get_row_at(1)[0] == "#101"
        assert widget.get_row_at(1)[2] == "  └─ Sole Subtask"


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
        assert widget.get_row_at(1)[2] == "  ├─ Step 1"
        assert widget.get_row_at(2)[2] == "  ├─ Step 2"
        assert widget.get_row_at(3)[2] == "  └─ Step 3"


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
        assert root_row[2] == "Email Password Setup"

        # Check subtasks under root
        sub_row = widget.get_row_at(2)
        assert sub_row[0] == "#456"
        assert "Sanitize email input" in sub_row[2]
        assert sub_row[3] == "ready-for-dev"


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
        assert row1[1] == "#459 [green]PASS[/green]"


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
        assert widget.get_row("20")[2] == "Story 20 (Active)"
        assert widget.get_row("20")[1] == "#100 [green]PASS[/green]"


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
        assert row0[0] == "#90 [LOCKED]"
        assert "[LOCKED]" in row0[0]
        assert row0[1] == "-"
        assert row0[2] == "Active Locked Story A"
        assert row0[3] == "architect-processed"
        assert "[LOCKED]" not in row0[3]

        # Row 1: Subtask #93 (No locked badge)
        row1 = widget.get_row_at(1)
        assert row1[0] == "#93"
        assert "[LOCKED]" not in row1[0]
        assert "[LOCKED]" not in row1[3]

        # Row 2: Subtask #94 (No locked badge)
        row2 = widget.get_row_at(2)
        assert row2[0] == "#94"
        assert "[LOCKED]" not in row2[0]
        assert "[LOCKED]" not in row2[3]

        # Row 3: Story #95 (Concurrently open story -> NO locked badge)
        row3 = widget.get_row_at(3)
        assert row3[0] == "#95"
        assert "[LOCKED]" not in row3[0]
        assert row3[2] == "Concurrently Open Story B"
        assert row3[3] == "architect-processed"
        assert "[LOCKED]" not in row3[3]

        # Row 4: Subtask #98 (No locked badge)
        row4 = widget.get_row_at(4)
        assert row4[0] == "#98"
        assert "[LOCKED]" not in row4[0]
        assert "[LOCKED]" not in row4[3]


# ---------------------------------------------------------------------------
# Acceptance Criteria Tests for Issue #138: Dual-Gauge HarnessQuotaWidget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_dual_gauge_rendering_with_exact_figures(tmp_path: Path):
    """
    Scenario: Dual gauge rendering with exact figures
      Given an AI harness configured with a 5-hour limit (5.0M tokens) and a weekly limit (20.0M tokens)
      When the operator views the "Quota" tab
      Then the widget renders independent progress gauges for both the 5-Hour Limit and Weekly Limit
      And each gauge displays exact integer tokens remaining, percentage, and window size (e.g. "3.8M / 5.0M (76%)")
    """
    from datetime import datetime, timezone, timedelta

    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                    weekly=WindowLimitConfig(hours=168.0, token_limit=20_000_000),
                )
            },
        )
    )
    quota_mgr = QuotaManager(config, state_manager)

    now_utc = datetime.now(timezone.utc)
    # Event 2h ago (within 5h and weekly): 1.2M used -> 3.8M remaining in 5h window
    recent_time = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj-a",
        node_name="devtest",
        issue_number=10,
        prompt_tokens=1_000_000,
        completion_tokens=200_000,
        total_tokens=1_200_000,
        created_at=recent_time,
    )

    # Event 20h ago (outside 5h, within weekly): 3.8M used -> total weekly used = 5.0M -> 15.0M remaining
    older_time = (now_utc - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S")
    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj-b",
        node_name="architect",
        issue_number=11,
        prompt_tokens=3_000_000,
        completion_tokens=800_000,
        total_tokens=3_800_000,
        created_at=older_time,
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=quota_mgr)

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(HarnessQuotaWidget)
        assert widget.row_count == 1

        row = widget.get_row("claude")
        assert row[0] == "claude"

        # 5-Hour Limit gauge: 3.8M / 5.0M (76%)
        short_gauge = str(row[1])
        assert "3.8M / 5.0M (76%)" in short_gauge
        assert "[green]" in short_gauge

        # Weekly Limit gauge: 15.0M / 20.0M (75%)
        weekly_gauge = str(row[2])
        assert "15.0M / 20.0M (75%)" in weekly_gauge
        assert "[green]" in weekly_gauge


def test_scenario_visual_status_thresholds():
    """
    Scenario: Visual status thresholds
      When a windows remaining capacity is >= 40%, its gauge renders in [green]
      When remaining capacity is between 15% and 39%, its gauge renders in [yellow]
      When remaining capacity is < 15%, its gauge renders in [bold red]
    """
    # 1. >= 40% -> [green]
    g_100 = HarnessQuotaWidget._render_gauge(5_000_000, 5_000_000)
    assert "[green]" in g_100
    assert "[/green]" in g_100
    assert "5.0M / 5.0M (100%)" in g_100

    g_76 = HarnessQuotaWidget._render_gauge(3_800_000, 5_000_000)
    assert "[green]" in g_76
    assert "3.8M / 5.0M (76%)" in g_76

    g_40 = HarnessQuotaWidget._render_gauge(2_000_000, 5_000_000)
    assert "[green]" in g_40
    assert "2.0M / 5.0M (40%)" in g_40

    # 2. 15% to 39% -> [yellow]
    g_39 = HarnessQuotaWidget._render_gauge(1_950_000, 5_000_000)
    assert "[yellow]" in g_39
    assert "[/yellow]" in g_39
    assert "2.0M / 5.0M (39%)" in g_39 or "1.9M / 5.0M (39%)" in g_39

    g_25 = HarnessQuotaWidget._render_gauge(1_250_000, 5_000_000)
    assert "[yellow]" in g_25
    assert "1.2M / 5.0M (25%)" in g_25 or "1.3M / 5.0M (25%)" in g_25

    g_15 = HarnessQuotaWidget._render_gauge(750_000, 5_000_000)
    assert "[yellow]" in g_15
    assert "750k / 5.0M (15%)" in g_15

    # 3. < 15% -> [bold red]
    g_14 = HarnessQuotaWidget._render_gauge(700_000, 5_000_000)
    assert "[bold red]" in g_14
    assert "[/bold red]" in g_14
    assert "700k / 5.0M (14%)" in g_14

    g_5 = HarnessQuotaWidget._render_gauge(250_000, 5_000_000)
    assert "[bold red]" in g_5
    assert "250k / 5.0M (5%)" in g_5

    g_0 = HarnessQuotaWidget._render_gauge(0, 5_000_000)
    assert "[bold red]" in g_0
    assert "0 / 5.0M (0%)" in g_0


@pytest.mark.asyncio
async def test_scenario_runway_and_countdown_surfaced_in_widget(tmp_path: Path):
    """
    Scenario: Runway and countdown surfaced in the widget
      Given `QuotaManager.calculate_dashboard_metrics` (from #137) returns a runway forecast and replenishment countdown
      When the widget renders a harness row
      Then it displays the formatted runway string (e.g. "~12.6h runway remaining @ 300k tok/hr" or "Runway: Idle (∞)")
      And it displays the formatted reset countdown (e.g. "Resets in 26 min" or "Full Capacity (0s)")
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                    weekly=WindowLimitConfig(hours=168.0, token_limit=20_000_000),
                ),
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=1_000_000,
                    avg_tokens_per_hour=400_000,
                ),
            },
        )
    )
    quota_mgr = QuotaManager(config, state_manager)

    # 1. Claude: 1.2M used -> 3.8M remaining @ 300k/hr -> runway = ~12.7h
    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=1_000_000,
        completion_tokens=200_000,
        total_tokens=1_200_000,
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=quota_mgr)

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(HarnessQuotaWidget)
        assert widget.row_count == 2

        # Check Claude row: active runway and full capacity countdown
        row_claude = widget.get_row("claude")
        assert "runway remaining @ 300k tok/hr" in str(row_claude[3])
        assert "Full Capacity (0s)" in str(row_claude[4])

        # Check Antigravity row: active runway with default burn rate & full capacity countdown
        row_antigravity = widget.get_row("antigravity")
        assert "runway remaining @ 400k tok/hr" in str(row_antigravity[3])
        assert "Full Capacity (0s)" in str(row_antigravity[4])

    # Direct unit test of _format_runway for idle / zero burn-rate and active
    from orchestrator.quota import calculate_operational_runway, DashboardQuotaMetrics, WindowMetric

    idle_forecast = calculate_operational_runway(1_000_000, burn_rate=0)
    idle_metrics = DashboardQuotaMetrics(
        harness_name="idle-harness",
        short_window=WindowMetric(window_hours=1.0, limit=1_000_000, used=0, remaining=1_000_000, percentage=100.0),
        runway_forecast=idle_forecast,
    )
    assert HarnessQuotaWidget._format_runway(idle_metrics) == "Runway: Idle (∞)"
    assert HarnessQuotaWidget._format_runway(None) == "Runway: Idle (∞)"

    active_forecast = calculate_operational_runway(3_800_000, burn_rate=300_000)
    active_metrics = DashboardQuotaMetrics(
        harness_name="active-harness",
        short_window=WindowMetric(window_hours=5.0, limit=5_000_000, used=1_200_000, remaining=3_800_000, percentage=76.0),
        runway_forecast=active_forecast,
    )
    assert HarnessQuotaWidget._format_runway(active_metrics) == "~12.7h runway remaining @ 300k tok/hr"


@pytest.mark.asyncio
async def test_scenario_non_blocking_refresh_preserves_cursor_stability(tmp_path: Path, mocker):
    """
    Scenario: Non-blocking refresh preserves cursor stability
      Given the dashboard refresh tick fires every 2.0s
      When `update_quotas()` re-renders rows with new dual-window data
      Then row updates use keyed in-place diffing (no full table rebuild) and the operators cursor position is preserved
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(window_hours=1.0, window_token_limit=1_000_000, avg_tokens_per_hour=200_000),
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                    weekly=WindowLimitConfig(hours=168.0, token_limit=20_000_000),
                ),
            },
        )
    )
    quota_mgr = QuotaManager(config, state_manager)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield HarnessQuotaWidget(config=config, state_manager=state_manager, quota_manager=quota_mgr)

    app = TestApp()
    async with app.run_test() as pilot:
        widget = app.query_one(HarnessQuotaWidget)
        widget.focus()
        assert widget.row_count == 2

        # Position cursor on row 1 ("claude")
        widget.move_cursor(row=1)
        await pilot.pause()
        assert widget.cursor_row == 1

        row_claude = widget.get_row("claude")
        assert "5.0M / 5.0M (100%)" in str(row_claude[1])

        clear_spy = mocker.spy(widget, "clear")

        # Mutate token usage to update Claude to throttled state
        await state_manager.record_token_usage_event(
            harness_name="claude",
            model_name="claude-sonnet-5",
            project_name="proj-x",
            node_name="devtest",
            issue_number=50,
            prompt_tokens=4_000_000,
            completion_tokens=800_000,
            total_tokens=4_800_000,
        )

        # Trigger update_quotas (as fired on periodic refresh)
        await widget.update_quotas()
        await pilot.pause()

        # In-place keyed diffing: clear() is never called
        clear_spy.assert_not_called()

        # Cursor position is preserved on row 1
        assert widget.cursor_row == 1
        assert widget.row_count == 2

        # Row data updated in place with new gauge values
        row_claude_updated = widget.get_row("claude")
        assert "[bold red]" in str(row_claude_updated[1])
        assert "200k / 5.0M (4%)" in str(row_claude_updated[1])
        assert '"proj-x": 100%' in str(row_claude_updated[5])


# ---------------------------------------------------------------------------
# Acceptance Criteria Tests for Issue #164: SDLC Table Column Prioritization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_sdlc_table_column_prioritization_and_badges(tmp_path: Path):
    """
    Scenario: SDLC table displays PR Status as second column and preserves badges
      Given the dashboard displays the SDLC items table
      When the table is rendered
      Then column 0 must be "ID" (with "[LOCKED]" badge if active)
      And column 1 must be "PR Status"
      And column 2 must be "Title" (capped at width 45 with ellipsis)
      And column 3 must be "Status/Label"
      And PR badges and locked status must be visible without horizontal scrolling
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    items = [
        {
            "issue_number": 500,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "A Very Long Epic Parent Story Title That Surely Exceeds Forty Five Characters In Total Length",
            "state": "OPEN",
            "labels": "architect-processed",
        },
        {
            "issue_number": 501,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "A Very Long Subtask Title That Exceeds Forty Five Characters For Truncation",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "parent_issue_id": 500,
            "linked_pr": 555,
            "pr_status": "OPEN",
            "pr_ci_details": "PASS",
        },
        {
            "issue_number": 502,
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "title": "Short Task",
            "state": "OPEN",
            "labels": "queued",
            "parent_issue_id": 500,
            "linked_pr": 556,
            "pr_status": "MERGED",
        },
    ]
    await state_manager.sync_project_sdlc_items("col-test-proj", items)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SDLCProgressWidget(state_manager=state_manager, project_name="col-test-proj")

    app = TestApp()
    async with app.run_test() as _:
        widget = app.query_one(SDLCProgressWidget)

        # 1. Assert Column Order
        assert widget.TABLE_COLUMNS == ["ID", "PR Status", "Title", "Status/Label"]
        column_labels = [str(col.label) for col in widget.columns.values()]
        assert column_labels == ["ID", "PR Status", "Title", "Status/Label"]

        assert widget.row_count == 3

        # 2. Row 0: Active Locked Story #500
        row0 = widget.get_row_at(0)
        assert row0[0] == "#500 [LOCKED]"
        assert "[LOCKED]" in row0[0]
        assert row0[1] == "-"
        # Title capped at 45 with ellipsis
        assert len(row0[2]) == 45
        assert row0[2].endswith("...")
        assert row0[2] == "A Very Long Epic Parent Story Title That S..."
        assert row0[3] == "architect-processed"
        assert "[LOCKED]" not in row0[3]

        # 3. Row 1: Subtask #501 with PR badge
        row1 = widget.get_row_at(1)
        assert row1[0] == "#501"
        assert "[LOCKED]" not in row1[0]
        assert row1[1] == "#555 [green]PASS[/green]"
        # Title capped at 45 with ellipsis
        assert len(row1[2]) == 45
        assert row1[2].endswith("...")
        assert row1[2].startswith("  ├─ ")
        assert row1[3] == "ready-for-dev"

        # 4. Row 2: Subtask #502 with merged PR badge and short title
        row2 = widget.get_row_at(2)
        assert row2[0] == "#502"
        assert row2[1] == "#556 [blue]MERGED[/blue]"
        assert row2[2] == "  └─ Short Task"
        assert len(row2[2]) <= 45
        assert not row2[2].endswith("...")
        assert row2[3] == "queued"




