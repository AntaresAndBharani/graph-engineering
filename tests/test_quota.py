from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from orchestrator.config import (
    GlobalConfig,
    HarnessQuotaConfig,
    NodeConfig,
    ProjectConfig,
    QuotaSettings,
)
from orchestrator.db import StateManager
from orchestrator.quota import (
    QuotaCheckResult,
    QuotaManager,
    extract_token_usage,
    extract_token_counts,
    fallback_token_heuristic,
)


@pytest.mark.asyncio
async def test_multi_hour_window_calculations():
    """
    Gherkin Scenario: 5-hour rolling window runway validation (Claude pool)
      Given harness "claude" has window_token_limit=5,000,000 over 5 hours and avg_tokens_per_hour=300,000
      And total consumption in the last 5 hours is 4,880,000 tokens
      When check_harness_capacity("claude") is called
      Then remaining is 120,000 and required runway is 150,000
      And allowed is False
      And eta_seconds projects when the oldest excess event ages out of the window
    """
    state_mock = MagicMock(spec=StateManager)
    state_mock.get_window_token_sum = AsyncMock(return_value=4_880_000)

    now_utc = datetime.now(timezone.utc)
    # Excess is 4,880,000 - (5,000,000 - 150,000) = 4,880,000 - 4,850,000 = 30,000 tokens.
    # Event 1 (4h ago): 10,000 tokens
    # Event 2 (3h ago): 25,000 tokens (Accumulated = 35,000 >= 30,000 -> ages out in 2h = 7200s)
    ev1_dt = (now_utc - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
    ev2_dt = (now_utc - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    state_mock.get_window_events = AsyncMock(
        return_value=[
            {"id": 1, "total_tokens": 10_000, "created_at": ev1_dt},
            {"id": 2, "total_tokens": 25_000, "created_at": ev2_dt},
        ]
    )

    quota_settings = QuotaSettings(
        buffer_minutes=30,
        harnesses={
            "claude": HarnessQuotaConfig(
                window_hours=5.0,
                window_token_limit=5_000_000,
                avg_tokens_per_hour=300_000,
            )
        },
    )
    manager = QuotaManager(quota_settings, state_mock)
    result = await manager.check_harness_capacity("claude")

    assert isinstance(result, QuotaCheckResult)
    assert result.harness_name == "claude"
    assert result.limit == 5_000_000
    assert result.used == 4_880_000
    assert result.remaining == 120_000
    assert result.required == 150_000
    assert result.allowed is False
    assert result.deficit == 30_000
    assert result.velocity == round(4_880_000 / 5.0, 2)
    # Event 2 was created 3h ago, window is 5h => ages out in ~2h (7200s +/- 5s)
    assert 7190 <= result.eta_seconds <= 7210
    assert "h" in result.formatted_eta or "m" in result.formatted_eta


@pytest.mark.asyncio
async def test_window_replenishment_clears_throttle():
    """
    Gherkin Scenario: Window replenishment clears the throttle
      Given harness "claude" was throttled with remaining < required
      When enough historical events age past the 5-hour window such that remaining >= required
      Then check_harness_capacity("claude") returns allowed=True
    """
    state_mock = MagicMock(spec=StateManager)
    # Total consumption drops to 4,800,000 (remaining 200,000 >= 150,000 required)
    state_mock.get_window_token_sum = AsyncMock(return_value=4_800_000)

    quota_settings = QuotaSettings(
        buffer_minutes=30,
        harnesses={
            "claude": HarnessQuotaConfig(
                window_hours=5.0,
                window_token_limit=5_000_000,
                avg_tokens_per_hour=300_000,
            )
        },
    )
    manager = QuotaManager(quota_settings, state_mock)
    result = await manager.check_harness_capacity("claude")

    assert result.allowed is True
    assert result.remaining == 200_000
    assert result.required == 150_000
    assert result.deficit == 0
    assert result.eta_seconds == 0
    assert result.formatted_eta == "0s"


@pytest.mark.asyncio
async def test_velocity_and_eta_projections():
    """
    Verifies burn velocity calculation and replenishment ETA fallback projection
    when no events are in the event ledger.
    """
    state_mock = MagicMock(spec=StateManager)
    state_mock.get_window_token_sum = AsyncMock(return_value=1_900_000)
    state_mock.get_window_events = AsyncMock(return_value=[])

    quota_settings = QuotaSettings(
        buffer_minutes=30,
        harnesses={
            "antigravity": HarnessQuotaConfig(
                window_hours=1.0,
                window_token_limit=2_000_000,
                avg_tokens_per_hour=400_000,
            )
        },
    )
    manager = QuotaManager(quota_settings, state_mock)
    result = await manager.check_harness_capacity("antigravity")

    # Limit: 2.0M, Buffer: 30m -> Required: 200,000. Used: 1.9M -> Remaining: 100,000. Deficit: 100,000.
    assert result.allowed is False
    assert result.remaining == 100_000
    assert result.required == 200_000
    assert result.deficit == 100_000
    assert result.velocity == 1_900_000.0
    # Fallback ETA: (excess / avg_tph) * 3600 = (100_000 / 400_000) * 3600 = 900s (15m)
    assert result.eta_seconds == 900
    assert result.formatted_eta == "15m"


def test_token_extraction_regex_and_fallback():
    """
    Gherkin Scenario: Empirical fallback token heuristic
      Given a harness CLI output with no structured token counts
      And prompt length 4000 chars and stdout length 4000 chars
      When the fallback heuristic runs
      Then it returns max(1000, floor(8000/4)) = 2000
    """
    # 1. Fallback heuristic pure function
    prompt_4k = "x" * 4000
    stdout_4k = "y" * 4000
    h_tokens = fallback_token_heuristic(prompt_4k, stdout_4k)
    assert h_tokens == 2000

    # Small output fallback clamp to minimum 1000
    assert fallback_token_heuristic("short", "output") == 1000

    # Empty inputs
    assert fallback_token_heuristic("", "") == 0

    # 2. extract_token_usage with unstructured text
    p_tok, c_tok, tot_tok = extract_token_usage(stdout_4k, prompt_4k)
    assert tot_tok == 2000
    assert p_tok == 1000
    assert c_tok == 1000

    # 3. Structured JSON extraction
    json_stdout = '{"usage": {"prompt_tokens": 1250, "completion_tokens": 450, "total_tokens": 1700}}'
    p, c, t = extract_token_usage(json_stdout, "some prompt")
    assert p == 1250
    assert c == 450
    assert t == 1700

    # 4. Anthropic style JSON
    anthropic_json = '{"type": "message", "usage": {"input_tokens": 800, "output_tokens": 200}}'
    p, c, t = extract_token_usage(anthropic_json)
    assert p == 800
    assert c == 200
    assert t == 1000

    # 5. Regex text matching
    regex_stdout = """
    Execution completed in 4.2s.
    Prompt Tokens: 3,450
    Completion Tokens: 1,200
    Total Tokens: 4,650
    """
    p, c, t = extract_token_usage(regex_stdout)
    assert p == 3450
    assert c == 1200
    assert t == 4650

    # 6. Alias verification
    assert extract_token_counts == extract_token_usage


@pytest.mark.asyncio
async def test_get_informative_breakdown():
    """
    Gherkin Scenario: Informative breakdown percentages
      Given usage of 600,000 project A tokens and 400,000 project B tokens for harness "antigravity" in-window
      When get_informative_breakdown("antigravity") is called
      Then it reports project A at 60% and project B at 40%
    """
    state_mock = MagicMock(spec=StateManager)
    state_mock.get_window_breakdown = AsyncMock(
        return_value={
            "by_project": {"project_a": 600_000, "project_b": 400_000},
            "by_node": {"devtest": 500_000, "reviewer": 300_000, "bau": 200_000},
        }
    )

    manager = QuotaManager(GlobalConfig(), state_mock)
    breakdown = await manager.get_informative_breakdown("antigravity")

    assert breakdown["by_project"]["project_a"] == 60.0
    assert breakdown["by_project"]["project_b"] == 40.0
    assert breakdown["by_node"]["devtest"] == 50.0
    assert breakdown["by_node"]["reviewer"] == 30.0
    assert breakdown["by_node"]["bau"] == 20.0
    assert breakdown["total_tokens"] == 1_000_000


def test_resolve_harness_for_node():
    """
    Tests harness resolution by node configuration and architectural defaults.
    """
    manager = QuotaManager(GlobalConfig(), MagicMock(spec=StateManager))

    # Explicit node configuration
    custom_project = ProjectConfig(
        name="custom",
        repo="org/custom",
        local_path=".",
        nodes={
            "devtest": NodeConfig(harness="devin"),
            "architect": NodeConfig(harness="openai"),
        },
    )
    assert manager.resolve_harness_for_node(custom_project, "devtest") == "devin"
    assert manager.resolve_harness_for_node(custom_project, "architect") == "openai"

    # Default architectural fallbacks
    empty_project = ProjectConfig(name="empty", repo="org/empty", local_path=".")
    assert manager.resolve_harness_for_node(empty_project, "architect") == "claude"
    assert manager.resolve_harness_for_node(empty_project, "supervisor") == "claude"
    assert manager.resolve_harness_for_node(empty_project, "reviewer") == "claude"
    assert manager.resolve_harness_for_node(empty_project, "devtest") == "antigravity"
    assert manager.resolve_harness_for_node(empty_project, "bau") == "antigravity"


def test_formatted_eta_variations():
    """
    Tests formatted ETA output for various second counts.
    """
    res_0 = QuotaCheckResult("c", True, 10, 5, 0, 10, 0.0, 0, 0, 1.0)
    assert res_0.formatted_eta == "0s"

    res_sec = QuotaCheckResult("c", False, 0, 5, 10, 10, 0.0, 45, 5, 1.0)
    assert res_sec.formatted_eta == "45s"

    res_min_sec = QuotaCheckResult("c", False, 0, 5, 10, 10, 0.0, 860, 5, 1.0)
    assert res_min_sec.formatted_eta == "14m 20s"

    res_hrs_min_sec = QuotaCheckResult("c", False, 0, 5, 10, 10, 0.0, 7325, 5, 1.0)
    assert res_hrs_min_sec.formatted_eta == "2h 2m 5s"


@pytest.mark.asyncio
async def test_real_sqlite_state_manager_quota_integration(tmp_path: Path):
    """
    Full integration test with SQLite WAL StateManager recording events and querying windows.
    """
    db_file = tmp_path / "test_quota_state.db"
    state = StateManager(db_file)
    await state.init_db()

    now_utc = datetime.now(timezone.utc)
    t1 = (now_utc - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
    t2 = (now_utc - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    t_old = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    # Record events in window
    await state.record_token_usage_event("antigravity", "gemini-flash", "proj1", "devtest", 10, 100, 400, 500, created_at=t1)
    await state.record_token_usage_event("antigravity", "gemini-flash", "proj2", "reviewer", 11, 200, 800, 1000, created_at=t2)
    # Record event outside 1h window
    await state.record_token_usage_event("antigravity", "gemini-flash", "proj1", "devtest", 9, 500, 2000, 2500, created_at=t_old)

    # 1h window sum should only include t1 and t2 = 1500 tokens
    sum_1h = await state.get_window_token_sum("antigravity", 1.0)
    assert sum_1h == 1500

    # 3h window sum includes all three = 4000 tokens
    sum_3h = await state.get_window_token_sum("antigravity", 3.0)
    assert sum_3h == 4000

    # Breakdown in 1h window
    manager = QuotaManager(GlobalConfig(), state)
    breakdown = await manager.get_informative_breakdown("antigravity")
    assert breakdown["by_project"]["proj1"] == 33.3
    assert breakdown["by_project"]["proj2"] == 66.7
    assert breakdown["by_node"]["devtest"] == 33.3
    assert breakdown["by_node"]["reviewer"] == 66.7
