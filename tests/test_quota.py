from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
import math
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

from orchestrator.config import (
    GlobalConfig,
    HarnessQuotaConfig,
    NodeConfig,
    ProjectConfig,
    QuotaSettings,
    WindowLimitConfig,
)
from orchestrator.db import StateManager
from orchestrator.quota import (
    DashboardQuotaMetrics,
    QuotaCheckResult,
    QuotaManager,
    RunwayForecast,
    WindowMetric,
    calculate_operational_runway,
    calculate_remaining,
    calculate_replenishment_eta,
    calculate_required_runway,
    calculate_runway,
    calculate_velocity,
    extract_token_counts,
    extract_token_usage,
    fallback_token_heuristic,
    format_replenishment_countdown,
    format_reset_countdown,
    format_runway,
)


def test_pure_calculation_functions():
    # Runway: 300,000 TPH with 30m buffer = 150,000 tokens
    assert calculate_required_runway(300_000, 30) == 150_000
    assert calculate_required_runway(400_000, 30) == 200_000
    assert calculate_required_runway(400_000, 0) == 0

    # Remaining: max(0, limit - used)
    assert calculate_remaining(5_000_000, 4_880_000) == 120_000
    assert calculate_remaining(5_000_000, 5_500_000) == 0
    assert calculate_remaining(5_000_000, 0) == 5_000_000

    # Velocity: used / window_hours
    assert calculate_velocity(4_880_000, 5.0) == 976_000.0
    assert calculate_velocity(120_000, 1.0) == 120_000.0
    assert calculate_velocity(100, 0) == 0.0


def test_calculate_replenishment_eta_pure():
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    limit = 5_000_000
    required_runway = 150_000  # Max allowed used = 4,850,000
    used_tokens = 4_880_000    # Excess = 30,000
    window_hours = 5.0

    # Event occurred at 08:00:00 (4 hours ago). Expiry = 08:00:00 + 5h = 13:00:00 (1 hour from now = 3600s)
    events = [
        {"created_at": "2026-08-29 08:00:00", "total_tokens": 50_000},
        {"created_at": "2026-08-29 10:00:00", "total_tokens": 4_830_000},
    ]

    eta = calculate_replenishment_eta(
        events=events,
        used_tokens=used_tokens,
        limit=limit,
        required_runway=required_runway,
        window_hours=window_hours,
        now=now_utc,
    )
    assert eta == 3600

    # When usage is within capacity, ETA is 0
    eta_allowed = calculate_replenishment_eta(
        events=events,
        used_tokens=4_800_000,
        limit=limit,
        required_runway=required_runway,
        window_hours=window_hours,
        now=now_utc,
    )
    assert eta_allowed == 0


def test_quota_check_result_formatted_eta():
    res1 = QuotaCheckResult(
        harness_name="claude",
        allowed=False,
        remaining=120_000,
        required=150_000,
        used=4_880_000,
        limit=5_000_000,
        velocity=976_000.0,
        eta_seconds=860,  # 14m 20s
        deficit=30_000,
        window_hours=5.0,
    )
    assert res1.formatted_eta == "14m 20s"

    res2 = QuotaCheckResult(
        harness_name="claude",
        allowed=False,
        remaining=0,
        required=150_000,
        used=5_000_000,
        limit=5_000_000,
        velocity=1_000_000.0,
        eta_seconds=3665,  # 1h 1m 5s
        deficit=150_000,
        window_hours=5.0,
    )
    assert res2.formatted_eta == "1h 1m 5s"

    res3 = QuotaCheckResult(
        harness_name="antigravity",
        allowed=True,
        remaining=1_880_000,
        required=200_000,
        used=120_000,
        limit=2_000_000,
        velocity=120_000.0,
        eta_seconds=0,
        deficit=0,
        window_hours=1.0,
    )
    assert res3.formatted_eta == "0s"


def test_token_extraction_and_fallback():
    # 1. JSON block extraction
    stdout_json = 'Execution finished. {"usage": {"prompt_tokens": 1500, "completion_tokens": 500, "total_tokens": 2000}}'
    p, c, t = extract_token_usage(stdout_json)
    assert p == 1500
    assert c == 500
    assert t == 2000

    # 2. Regex matching
    stdout_regex = 'Total tokens used: 12,500\nPrompt tokens: 10,000\nCompletion tokens: 2,500'
    p, c, t = extract_token_usage(stdout_regex)
    assert p == 10000
    assert c == 2500
    assert t == 12500

    # 3. Fallback heuristic on character count
    prompt = "A" * 2000
    stdout = "B" * 2000
    p_h, c_h, t_h = extract_token_usage(stdout, prompt)
    assert t_h == 1000
    assert p_h + c_h == t_h

    # 4. Fallback minimum 1000 tokens for short non-empty text
    p_min, c_min, t_min = extract_token_usage("Short output", "Short prompt")
    assert t_min == 1000

    # 5. Empty inputs return 0
    assert fallback_token_heuristic("", "") == 0
    assert extract_token_counts("", "") == (0, 0, 0)


@pytest.mark.asyncio
async def test_resolve_harness_for_node():
    config = GlobalConfig()
    mock_state = AsyncMock()
    manager = QuotaManager(config, mock_state)

    project = ProjectConfig(
        name="test-proj",
        repo="org/test-proj",
        local_path=Path("."),
        nodes={
            "architect": NodeConfig(enabled=True, harness="claude"),
            "devtest": NodeConfig(enabled=True, harness="antigravity"),
        },
    )

    assert manager.resolve_harness_for_node(project, "architect") == "claude"
    assert manager.resolve_harness_for_node(project, "devtest") == "antigravity"
    # Fallback to NodeConfig default when node not explicitly configured
    assert manager.resolve_harness_for_node(project, "supervisor") == "claude"
    assert manager.resolve_harness_for_node(project, "reviewer") == "claude"
    assert manager.resolve_harness_for_node(project, "bau") == "claude"


@pytest.mark.asyncio
async def test_global_harness_shared_consumption(tmp_path: Path):
    """
    Scenario 1: Project A consumption decrements Project B remaining quota
    under shared harness pool.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                )
            },
        )
    )

    quota_mgr = QuotaManager(config, state_manager)

    # Project A records usage
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="devtest",
        issue_number=42,
        prompt_tokens=100_000,
        completion_tokens=20_000,
        total_tokens=120_000,
    )

    res = await quota_mgr.check_harness_capacity("antigravity")
    assert res.used == 120_000
    assert res.remaining == 1_880_000
    assert res.allowed is True

    # Informative breakdown
    breakdown = await quota_mgr.get_informative_breakdown("antigravity")
    assert breakdown["by_project"]["graph-engineering"] == 100.0
    assert breakdown["by_node"]["devtest"] == 100.0

    # Project B records usage
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="crosstrainingapp",
        node_name="reviewer",
        issue_number=10,
        prompt_tokens=60_000,
        completion_tokens=20_000,
        total_tokens=80_000,
    )

    res2 = await quota_mgr.check_harness_capacity("antigravity")
    assert res2.used == 200_000
    assert res2.remaining == 1_800_000

    breakdown2 = await quota_mgr.get_informative_breakdown("antigravity")
    assert breakdown2["by_project"]["graph-engineering"] == 60.0
    assert breakdown2["by_project"]["crosstrainingapp"] == 40.0
    assert breakdown2["by_node"]["devtest"] == 60.0
    assert breakdown2["by_node"]["reviewer"] == 40.0


@pytest.mark.asyncio
async def test_multi_hour_window_calculations(tmp_path: Path):
    """
    Scenario 2: 5-hour rolling window runway validation (Claude pool).
    Given harness "claude" has window_token_limit=5,000,000 and avg_tokens_per_hour=300,000
    And total global consumption in the last 5 hours is 4,880,000 tokens
    When `check_harness_capacity("claude")` is called
    Then remaining quota (120,000) is less than required runway (150,000)
    And allowed=False, remaining=120000, required=150000
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                )
            },
        )
    )

    quota_mgr = QuotaManager(config, state_manager)

    now_utc = datetime.now(timezone.utc)
    old_time = (now_utc - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    # Event 6 hours ago (should be excluded from 5h window)
    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj",
        node_name="architect",
        issue_number=1,
        prompt_tokens=500_000,
        completion_tokens=100_000,
        total_tokens=600_000,
        created_at=old_time,
    )

    # Event 2 hours ago (included in 5h window)
    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj",
        node_name="architect",
        issue_number=2,
        prompt_tokens=4_000_000,
        completion_tokens=880_000,
        total_tokens=4_880_000,
        created_at=recent_time,
    )

    res = await quota_mgr.check_harness_capacity("claude")
    assert res.used == 4_880_000
    assert res.remaining == 120_000
    assert res.required == 150_000
    assert res.allowed is False
    assert res.deficit == 30_000
    assert res.velocity == 976_000.0
    assert res.eta_seconds > 0


@pytest.mark.asyncio
async def test_window_replenishment_clears_throttle(tmp_path: Path):
    """
    Scenario 3: Window replenishment automatically clears throttle.
    When enough time elapses that the rolling token sum drops below 4,850,000,
    check_harness_capacity returns allowed=True.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                )
            },
        )
    )

    quota_mgr = QuotaManager(config, state_manager)

    # All events are aged past 5 hours
    old_time = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj",
        node_name="architect",
        issue_number=1,
        prompt_tokens=4_000_000,
        completion_tokens=880_000,
        total_tokens=4_880_000,
        created_at=old_time,
    )

    res = await quota_mgr.check_harness_capacity("claude")
    assert res.used == 0
    assert res.remaining == 5_000_000
    assert res.allowed is True
    assert res.eta_seconds == 0


@pytest.mark.asyncio
async def test_informative_breakdown_multi_node(tmp_path: Path):
    """
    Scenario 4: Informative breakdown by project and node summing to 100%.
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=2_000_000,
                    avg_tokens_per_hour=400_000,
                )
            }
        )
    )

    quota_mgr = QuotaManager(config, state_manager)

    # devtest: 50,000, reviewer: 30,000, bau: 20,000 = total 100,000
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=40_000,
        completion_tokens=10_000,
        total_tokens=50_000,
    )
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="reviewer",
        issue_number=2,
        prompt_tokens=25_000,
        completion_tokens=5_000,
        total_tokens=30_000,
    )
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="bau",
        issue_number=3,
        prompt_tokens=15_000,
        completion_tokens=5_000,
        total_tokens=20_000,
    )

    breakdown = await quota_mgr.get_informative_breakdown("antigravity")
    assert breakdown["by_project"]["graph-engineering"] == 100.0
    assert breakdown["by_node"]["devtest"] == 50.0
    assert breakdown["by_node"]["reviewer"] == 30.0
    assert breakdown["by_node"]["bau"] == 20.0
    assert sum(breakdown["by_node"].values()) == 100.0


@pytest.mark.asyncio
async def test_mocked_state_manager():
    """
    Tests QuotaManager with mocked StateManager / TokenUsageReader for unit isolation.
    """
    mock_state = AsyncMock()
    mock_state.get_window_token_usage.return_value = 100_000
    mock_state.get_usage_breakdown.return_value = {
        "by_project": {"p1": 70_000, "p2": 30_000},
        "by_node": {"n1": 60_000, "n2": 40_000},
    }

    quota_settings = QuotaSettings(
        buffer_minutes=30,
        harnesses={
            "custom": HarnessQuotaConfig(
                window_hours=2.0,
                window_token_limit=1_000_000,
                avg_tokens_per_hour=200_000,
            )
        },
    )

    mgr = QuotaManager(quota_settings, mock_state)
    res = await mgr.check_harness_capacity("custom")
    assert res.used == 100_000
    assert res.remaining == 900_000
    assert res.required == 100_000
    assert res.allowed is True
    assert res.velocity == 50_000.0

    breakdown = await mgr.get_informative_breakdown("custom")
    assert breakdown["by_project"]["p1"] == 70.0
    assert breakdown["by_project"]["p2"] == 30.0
    assert breakdown["by_node"]["n1"] == 60.0
    assert breakdown["by_node"]["n2"] == 40.0


def test_quota_manager_fail_fast_validation():
    """
    Asserts that QuotaManager fails fast with TypeError when misconfigured.
    """
    mock_state = AsyncMock()

    # Invalid config types
    with pytest.raises(TypeError, match="Invalid config type"):
        QuotaManager("invalid_string", mock_state)  # type: ignore

    with pytest.raises(TypeError, match="Invalid config type"):
        QuotaManager(123, mock_state)  # type: ignore

    with pytest.raises(TypeError, match="Invalid config type"):
        QuotaManager(None, mock_state)  # type: ignore

    # None state_manager
    with pytest.raises(TypeError, match="state_manager cannot be None"):
        QuotaManager(GlobalConfig(), None)  # type: ignore


def test_token_usage_reader_protocol(tmp_path: Path):
    """
    Asserts that StateManager satisfies the runtime-checkable TokenUsageReader protocol.
    """
    from orchestrator.quota import TokenUsageReader

    state_manager = StateManager(tmp_path / "state.db")
    assert isinstance(state_manager, TokenUsageReader)


# ===========================================================================
# Acceptance Criteria Tests for Issue #137 (Multi-Window Quota & Runway)
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_dual_window_dashboard_metrics(tmp_path: Path):
    """
    Scenario: Dual-window dashboard metrics
      Given a harness with both a short-window and weekly WindowLimitConfig
      When QuotaManager.calculate_dashboard_metrics(harness_name) is called
      Then it returns remaining tokens, percentage, and window size for both windows independently
      And percentages are computed via calculate_remaining / limit consistently with existing single-window logic
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
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
    # Event 2 hours ago (within both 5h short window and 168h weekly window): 1,200,000 tokens
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

    # Event 20 hours ago (outside 5h short window, within 168h weekly window): 3,800,000 tokens
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

    # When calculate_dashboard_metrics is called
    metrics = await quota_mgr.calculate_dashboard_metrics("claude")

    # Then it returns remaining tokens, percentage, and window size for both windows independently
    assert isinstance(metrics, DashboardQuotaMetrics)
    assert metrics.harness_name == "claude"

    # Short Window (5h): used = 1,200,000, limit = 5,000,000, remaining = 3,800,000
    assert isinstance(metrics.short_window, WindowMetric)
    assert metrics.short_window.window_hours == 5.0
    assert metrics.short_window.limit == 5_000_000
    assert metrics.short_window.used == 1_200_000
    assert metrics.short_window.remaining == 3_800_000
    # percentage = (3,800,000 / 5,000,000) * 100 = 76.0%
    assert metrics.short_window.percentage == 76.0

    # Weekly Window (168h): used = 1,200,000 + 3,800,000 = 5,000,000, limit = 20,000,000, remaining = 15,000,000
    assert metrics.weekly_window is not None
    assert isinstance(metrics.weekly_window, WindowMetric)
    assert metrics.weekly_window.window_hours == 168.0
    assert metrics.weekly_window.limit == 20_000_000
    assert metrics.weekly_window.used == 5_000_000
    assert metrics.weekly_window.remaining == 15_000_000
    # percentage = (15,000,000 / 20,000,000) * 100 = 75.0%
    assert metrics.weekly_window.percentage == 75.0

    # Verify dict and alias access consistency
    assert metrics.short.remaining == 3_800_000
    assert metrics.weekly.remaining == 15_000_000
    assert metrics["short"]["remaining"] == 3_800_000
    assert metrics["weekly"]["remaining"] == 15_000_000
    assert metrics.remaining_short == 3_800_000
    assert metrics.remaining_weekly == 15_000_000


@pytest.mark.asyncio
async def test_scenario_predictive_runway_forecast(tmp_path: Path):
    """
    Scenario: Predictive runway forecast
      Given the "claude" harness has 3,800,000 tokens remaining in its short window
      And the configured or measured burn rate is 300,000 tokens/hour
      When the operational forecast is evaluated
      Then it returns a runway of approximately 12.6 hours
      And when burn rate is 0 (idle), it returns an "Idle" / infinite runway result without raising ZeroDivisionError
    """
    # 1. Pure calculation tests
    remaining = 3_800_000
    burn_rate = 300_000

    runway = calculate_runway(remaining, burn_rate)
    # 3,800,000 / 300,000 = 12.6666... approx 12.6 - 12.7 hours
    assert 12.6 <= runway <= 12.7
    assert round(runway, 1) == 12.7
    assert format_runway(runway) == "12.7h"

    forecast = calculate_operational_runway(remaining, burn_rate)
    assert isinstance(forecast, RunwayForecast)
    assert 12.6 <= forecast.runway_hours <= 12.7
    assert forecast.is_idle is False
    assert forecast.formatted == "12.7h"

    # 2. Idle / Zero burn rate test without raising ZeroDivisionError
    idle_runway = calculate_runway(remaining, 0)
    assert math.isinf(idle_runway)
    assert format_runway(idle_runway) == "Idle"

    idle_forecast = calculate_operational_runway(remaining, 0)
    assert isinstance(idle_forecast, RunwayForecast)
    assert math.isinf(idle_forecast.runway_hours)
    assert idle_forecast.is_idle is True
    assert idle_forecast.formatted == "Idle"
    assert str(idle_forecast) == "Idle"

    # Negative burn rate safety
    neg_forecast = calculate_operational_runway(remaining, -500)
    assert math.isinf(neg_forecast.runway_hours)
    assert neg_forecast.is_idle is True
    assert neg_forecast.formatted == "Idle"

    # 3. Integration with QuotaManager
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            harnesses={
                "claude": HarnessQuotaConfig(
                    window_hours=5.0,
                    window_token_limit=5_000_000,
                    avg_tokens_per_hour=300_000,
                )
            }
        )
    )
    quota_mgr = QuotaManager(config, state_manager)

    # 1,200,000 used -> 3,800,000 remaining
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

    mgr_forecast = await quota_mgr.calculate_runway_forecast("claude")
    assert 12.6 <= mgr_forecast.runway_hours <= 12.7
    assert mgr_forecast.formatted == "12.7h"

    # With explicit burn_rate override = 0
    mgr_idle_forecast = await quota_mgr.calculate_runway_forecast("claude", burn_rate=0)
    assert math.isinf(mgr_idle_forecast.runway_hours)
    assert mgr_idle_forecast.formatted == "Idle"


def test_scenario_formatted_replenishment_countdown():
    """
    Scenario: Formatted replenishment countdown
      Given a harness quota state with a known ETA in seconds
      When the countdown is formatted for display
      Then short ETAs render as "Resets in 26 min" and longer/weekly ETAs render as "Resets Sun, 00:00"
      And 0 seconds ETA at full capacity renders as "Full Capacity (0s)"
    """
    # 1. Zero ETA at full capacity
    assert format_replenishment_countdown(0) == "Full Capacity (0s)"
    assert format_replenishment_countdown(-10) == "Full Capacity (0s)"
    assert format_reset_countdown(0) == "Full Capacity (0s)"

    # 2. Short ETA: 26 minutes (1560 seconds) -> "Resets in 26 min"
    assert format_replenishment_countdown(1560) == "Resets in 26 min"
    assert format_replenishment_countdown(26 * 60) == "Resets in 26 min"
    assert format_reset_countdown(1560) == "Resets in 26 min"

    # Other short ETA variants (< 60s, hours + mins)
    assert format_replenishment_countdown(45) == "Resets in 45s"
    assert format_replenishment_countdown(3600) == "Resets in 1h"
    assert format_replenishment_countdown(3720) == "Resets in 1h 2m"

    # 3. Longer / weekly ETA: e.g. resets on Sunday 00:00
    # Reference now: Thursday 2026-09-03 00:00:00 UTC
    ref_now = datetime(2026, 9, 3, 0, 0, 0, tzinfo=timezone.utc)
    # 3 days later is Sunday 2026-09-06 00:00:00 UTC (3 * 86400 = 259200 seconds)
    eta_sunday = 259200
    assert format_replenishment_countdown(eta_sunday, now=ref_now) == "Resets Sun, 00:00"

    # Explicit reset_time provided
    sun_target = datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc)
    assert format_replenishment_countdown(100, reset_time=sun_target) == "Resets Sun, 00:00"

    # Window hours >= 24 with weekly ETA
    assert format_replenishment_countdown(3600, now=datetime(2026, 9, 5, 23, 0, 0, tzinfo=timezone.utc), window_hours=168.0) == "Resets Sun, 00:00"

    # 4. QuotaCheckResult.formatted_countdown
    res_full = QuotaCheckResult(
        harness_name="claude",
        allowed=True,
        remaining=5_000_000,
        required=150_000,
        used=0,
        limit=5_000_000,
        velocity=0.0,
        eta_seconds=0,
        deficit=0,
        window_hours=5.0,
    )
    assert res_full.formatted_countdown == "Full Capacity (0s)"

    res_short = QuotaCheckResult(
        harness_name="claude",
        allowed=False,
        remaining=100_000,
        required=150_000,
        used=4_900_000,
        limit=5_000_000,
        velocity=980_000.0,
        eta_seconds=1560,
        deficit=50_000,
        window_hours=5.0,
    )
    assert res_short.formatted_countdown == "Resets in 26 min"


@pytest.mark.asyncio
async def test_scenario_critical_threshold_logging(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """
    Scenario: Critical threshold logging
      Given a harness short-window remaining capacity drops below 15%
      When check_harness_capacity (or the new dashboard metrics evaluator) runs
      Then a warning is emitted to the orchestrator log stream: "[WARN] [quota:<harness>] Quota critical (<15% remaining)."
    """
    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    config = GlobalConfig(
        quota=QuotaSettings(
            buffer_minutes=30,
            harnesses={
                "antigravity": HarnessQuotaConfig(
                    window_hours=1.0,
                    window_token_limit=1_000_000,
                    avg_tokens_per_hour=200_000,
                )
            },
        )
    )
    quota_mgr = QuotaManager(config, state_manager)

    # 1. Normal capacity (80% remaining -> 200k used): no warning emitted
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="proj",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=150_000,
        completion_tokens=50_000,
        total_tokens=200_000,
    )

    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        caplog.clear()
        res_ok = await quota_mgr.check_harness_capacity("antigravity")
        assert res_ok.remaining == 800_000
        assert res_ok.is_critical is False
        assert not any("Quota critical" in rec.message for rec in caplog.records)

    # 2. Capacity drops below 15% (e.g. 880k used -> 120k remaining = 12% < 15%)
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="proj",
        node_name="devtest",
        issue_number=2,
        prompt_tokens=500_000,
        completion_tokens=180_000,
        total_tokens=680_000,
    )

    # When check_harness_capacity runs
    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        caplog.clear()
        res_crit = await quota_mgr.check_harness_capacity("antigravity")
        assert res_crit.remaining == 120_000
        assert res_crit.is_critical is True

        # Then a warning is emitted to the orchestrator log stream:
        # "[WARN] [quota:<harness>] Quota critical (<15% remaining)."
        warning_records = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING and "[quota:antigravity] Quota critical (<15% remaining)." in rec.message
        ]
        assert len(warning_records) >= 1

    # When calculate_dashboard_metrics runs
    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        caplog.clear()
        dashboard_metrics = await quota_mgr.calculate_dashboard_metrics("antigravity")
        assert dashboard_metrics.short_window.remaining == 120_000
        assert dashboard_metrics.short_window.percentage == 12.0

        warning_records = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING and "[quota:antigravity] Quota critical (<15% remaining)." in rec.message
        ]
        assert len(warning_records) >= 1

