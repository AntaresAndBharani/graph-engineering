from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from orchestrator.config import GlobalConfig, HarnessQuotaConfig, ProjectConfig, QuotaSettings
from orchestrator.db import StateManager
from orchestrator.quota import QuotaManager


@pytest.mark.asyncio
async def test_resolve_harness_for_node():
    config = GlobalConfig()
    manager = QuotaManager(config, None)  # type: ignore

    project = ProjectConfig(
        name="test-proj",
        repo="org/test-proj",
        local_path=Path("."),
        nodes={
            "architect": {"enabled": True, "harness": "claude"},
            "devtest": {"enabled": True, "harness": "antigravity"},
        },
    )

    assert manager.resolve_harness_for_node(project, "architect") == "claude"
    assert manager.resolve_harness_for_node(project, "devtest") == "antigravity"
    # Fallback when node not configured
    assert manager.resolve_harness_for_node(project, "supervisor") == "claude"
    assert manager.resolve_harness_for_node(project, "bau") == "antigravity"


@pytest.mark.asyncio
async def test_global_harness_shared_consumption(tmp_path: Path):
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
        project_name="project-a",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=100000,
        completion_tokens=20000,
        total_tokens=120000,
    )

    res = await quota_mgr.check_harness_capacity("antigravity")
    assert res.used == 120000
    assert res.remaining == 1880000
    assert res.allowed is True

    # Informative breakdown
    breakdown = await quota_mgr.get_informative_breakdown("antigravity")
    assert breakdown["by_project"]["project-a"] == 100.0
    assert breakdown["by_node"]["devtest"] == 100.0


@pytest.mark.asyncio
async def test_multi_hour_window_calculations(tmp_path: Path):
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

    # Event 6 hours ago (should be excluded from 5h window)
    now_utc = datetime.now(timezone.utc)
    old_time = (now_utc - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj",
        node_name="architect",
        issue_number=1,
        prompt_tokens=500000,
        completion_tokens=100000,
        total_tokens=600000,
        created_at=old_time,
    )

    await state_manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-sonnet-5",
        project_name="proj",
        node_name="architect",
        issue_number=2,
        prompt_tokens=4000000,
        completion_tokens=880000,
        total_tokens=4880000,
        created_at=recent_time,
    )

    res = await quota_mgr.check_harness_capacity("claude")
    assert res.used == 4880000  # old 600k excluded
    assert res.remaining == 120000
    assert res.required == 150000
    assert res.allowed is False
    assert res.deficit == 30000
    assert res.eta_seconds > 0


@pytest.mark.asyncio
async def test_window_replenishment_clears_throttle(tmp_path: Path):
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

    # Event 2 hours ago (aged out)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    await state_manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="proj",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=1500000,
        completion_tokens=400000,
        total_tokens=1900000,
        created_at=old_time,
    )

    res = await quota_mgr.check_harness_capacity("antigravity")
    assert res.used == 0
    assert res.remaining == 2_000_000
    assert res.allowed is True
