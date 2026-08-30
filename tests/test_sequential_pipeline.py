from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.nodes.architect import build_triage_prompt
from orchestrator.nodes.devtest import _advance_parent_and_unlock_next_subtask


@pytest.mark.asyncio
async def test_db_sdlc_items_hierarchical_columns_and_helpers(tmp_path: Path):
    """Tests SQLite schema extensions and StateManager story/subtask query helpers."""
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project_name = "test-project"

    items = [
        {
            "issue_number": 50,
            "title": "Epic: User Authentication",
            "state": "OPEN",
            "labels": ["architect-processed"],
            "item_type": "STORY",
            "sequence_order": 0,
        },
        {
            "issue_number": 51,
            "parent_issue_id": 50,
            "title": "Subtask 1: Database Migration",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "item_type": "SUBTASK",
            "sequence_order": 1,
        },
        {
            "issue_number": 52,
            "parent_issue_id": 50,
            "title": "Subtask 2: Auth Endpoints",
            "state": "OPEN",
            "labels": ["queued"],
            "item_type": "SUBTASK",
            "sequence_order": 2,
        },
    ]

    await state_manager.sync_project_sdlc_items(project_name, items)

    # 1. Test get_active_story
    story = await state_manager.get_active_story(project_name)
    assert story is not None
    assert story["issue_number"] == 50
    assert story["item_type"] == "STORY"

    # 2. Test get_pending_subtasks
    subtasks = await state_manager.get_pending_subtasks(project_name, 50)
    assert len(subtasks) == 2
    assert [s["issue_number"] for s in subtasks] == [51, 52]

    # 3. Test get_next_queued_subtask
    queued = await state_manager.get_next_queued_subtask(project_name, 50)
    assert queued is not None
    assert queued["issue_number"] == 52


def test_architect_build_triage_prompt_queued_instructions():
    """Verifies that Architect triage prompt instructs 1st subtask active and remaining queued."""
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=".",
    )

    prompt = build_triage_prompt(
        project=project,
        issue_id=50,
        issue_title="Epic: User Auth",
        trigger="needs-triage",
        output_label="ready-for-dev",
        processed_label="architect-processed",
    )

    assert "Create Subtask 1 (Active)" in prompt
    assert "ready-for-dev" in prompt
    assert "Create Subtasks 2..N (Queued)" in prompt
    assert "queued" in prompt
    assert "architect-processed" in prompt


@pytest.mark.asyncio
async def test_devtest_advances_parent_and_unlocks_next_queued_subtask(tmp_path: Path, monkeypatch):
    """Verifies that completing a subtask checks off parent checklist and unlocks the next queued subtask."""
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    subtask_data = {
        "number": 51,
        "title": "Subtask 1: Migration",
        "body": "Acceptance criteria.\n\nParent: #50",
        "labels": [{"name": "dev-implemented"}],
    }

    parent_data = {
        "number": 50,
        "title": "Epic: User Authentication",
        "body": "# Description\n\n## Subtasks\n- [ ] #51 - Migration\n- [ ] #52 - Auth API\n",
        "state": "OPEN",
        "labels": [{"name": "architect-processed"}],
    }

    children_data = [
        {"number": 51, "title": "Subtask 1: Migration", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
        {"number": 52, "title": "Subtask 2: Auth API", "state": "OPEN", "labels": [{"name": "queued"}]},
    ]

    executed_cmds = []

    async def mock_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        cmd_str = " ".join(str(a) for a in args)
        mock_p = AsyncMock()
        mock_p.returncode = 0
        mock_p.wait = AsyncMock(return_value=0)

        if "issue view 51" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(subtask_data).encode("utf-8"), b""))
        elif "issue view 50" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(parent_data).encode("utf-8"), b""))
        elif "issue list" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(children_data).encode("utf-8"), b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "C:\\Program Files\\GitHub CLI\\gh.exe")

    await _advance_parent_and_unlock_next_subtask(project, state_manager, 51)

    unlocked = any("edit" in c and "52" in c and "ready-for-dev" in c for c in executed_cmds)
    assert unlocked is True


@pytest.mark.asyncio
async def test_devtest_closes_parent_when_all_subtasks_closed(tmp_path: Path, monkeypatch):
    """Verifies that completing the final child subtask automatically closes the parent story."""
    project = ProjectConfig(
        name="graph-engineering",
        repo="AntaresAndBharani/graph-engineering",
        local_path=str(tmp_path),
    )
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    subtask_data = {
        "number": 52,
        "title": "Subtask 2: Auth API",
        "body": "Acceptance criteria.\n\nParent: #50",
        "labels": [{"name": "dev-implemented"}],
    }

    parent_data = {
        "number": 50,
        "title": "Epic: User Authentication",
        "body": "# Description\n\n## Subtasks\n- [x] #51 - Migration\n- [ ] #52 - Auth API\n",
        "state": "OPEN",
        "labels": [{"name": "architect-processed"}],
    }

    children_data = [
        {"number": 51, "title": "Subtask 1: Migration", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
        {"number": 52, "title": "Subtask 2: Auth API", "state": "CLOSED", "labels": [{"name": "dev-implemented"}]},
    ]

    executed_cmds = []

    async def mock_exec(*args, **kwargs):
        executed_cmds.append(list(args))
        cmd_str = " ".join(str(a) for a in args)
        mock_p = AsyncMock()
        mock_p.returncode = 0
        mock_p.wait = AsyncMock(return_value=0)

        if "issue view 52" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(subtask_data).encode("utf-8"), b""))
        elif "issue view 50" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(parent_data).encode("utf-8"), b""))
        elif "issue list" in cmd_str:
            import json
            mock_p.communicate = AsyncMock(return_value=(json.dumps(children_data).encode("utf-8"), b""))
        else:
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
        return mock_p

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "C:\\Program Files\\GitHub CLI\\gh.exe")

    await _advance_parent_and_unlock_next_subtask(project, state_manager, 52)

    closed_parent = any("issue" in c and "close" in c and "50" in c for c in executed_cmds)
    assert closed_parent is True

    sdlc_items = await state_manager.get_sdlc_items(project.name)
    assert len(sdlc_items) > 0
    parent_sdlc = next(item for item in sdlc_items if item["issue_number"] == 50)
    assert parent_sdlc["state"] == "CLOSED"
