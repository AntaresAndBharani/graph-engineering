from __future__ import annotations

import time
from pathlib import Path
import pytest

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.nodes.architect import run_architect_node
from orchestrator.nodes.reviewer import run_reviewer_node


def test_architect_config_defaults():
    cfg = NodeConfig(
        research_harness="antigravity",
        research_model="gemini-3.7-flash-high",
        research_interval_seconds=604800,
        review_trigger="needs-architect-review",
    )
    assert cfg.research_harness == "antigravity"
    assert cfg.research_model == "gemini-3.7-flash-high"
    assert cfg.research_interval_seconds == 604800
    assert cfg.review_trigger == "needs-architect-review"


@pytest.mark.asyncio
async def test_architect_zero_token_gating_when_all_idle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    # Create dummy .graph/architecture.md so sync is not required
    graph_dir = tmp_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "architecture.md").write_text("# Architecture Standards\n", encoding="utf-8")

    # Record recent run
    await state_manager.record_node_run("architect_research", "org/repo")

    config = GlobalConfig()
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "architect": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                research_harness="antigravity",
                research_model="gemini-3.7-flash-high",
            )
        },
    )

    ran, msg = await run_architect_node(project, config, state_manager)
    assert ran is False
    assert "Idle (0 tokens)" in msg


@pytest.mark.asyncio
async def test_reviewer_zero_token_gating_idle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    state_manager = StateManager(db_file)
    await state_manager.init_db()

    config = GlobalConfig()
    project = ProjectConfig(
        name="test-repo",
        repo="org/repo",
        local_path=str(tmp_path),
        nodes={
            "reviewer": NodeConfig(
                enabled=True,
                harness="claude",
                model="claude-sonnet-5",
                label_trigger="architect-approved",
            )
        },
    )

    ran, msg = await run_reviewer_node(project, config, state_manager)
    assert ran is False
    assert "Idle (0 tokens)" in msg
