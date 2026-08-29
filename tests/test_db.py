from __future__ import annotations

from pathlib import Path
import time
import pytest
from orchestrator.db import StateManager


@pytest.mark.asyncio
async def test_db_lock_lifecycle(tmp_path: Path):
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # 1. Acquire Lock
    acquired = await manager.acquire_lock("100", "my-org/repo", "architect", ttl_minutes=30)
    assert acquired is True

    # 2. Duplicate lock attempt while active -> Should fail
    dup = await manager.acquire_lock("100", "my-org/repo", "architect", ttl_minutes=30)
    assert dup is False

    # 3. Different node type -> Can acquire
    dev_lock = await manager.acquire_lock("100", "my-org/repo", "devtest", ttl_minutes=30)
    assert dev_lock is True

    # 4. Release Lock
    await manager.release_lock("100", "my-org/repo", "architect")

    # 5. Re-acquire after release -> Should succeed
    reacquired = await manager.acquire_lock("100", "my-org/repo", "architect", ttl_minutes=30)
    assert reacquired is True


@pytest.mark.asyncio
async def test_db_ttl_expiration(tmp_path: Path):
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Set TTL to negative (already expired)
    await manager.acquire_lock("200", "my-org/repo", "architect", ttl_minutes=-10)

    # Cleanup expired locks
    cleaned = await manager.cleanup_expired_locks()
    assert cleaned == 1

    jobs = await manager.get_active_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "FAILED"
    assert "TTL Expired" in jobs[0]["error_message"]


@pytest.mark.asyncio
async def test_pr_artifacts_blackboard_lifecycle(tmp_path: Path):
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # 1. Initially empty
    assert await manager.get_pr_artifact("org/repo", 437) is None
    assert await manager.list_pr_artifacts("org/repo") == []

    # 2. Upsert artifact
    await manager.upsert_pr_artifact(
        repo="org/repo",
        pr_number=437,
        node_name="reviewer",
        status="APPROVED_WITH_CONFLICT",
        comment="Code passed architectural review; git merge conflict on branch.",
    )

    art = await manager.get_pr_artifact("org/repo", 437)
    assert art is not None
    assert art["pr_number"] == 437
    assert art["repo"] == "org/repo"
    assert art["node_name"] == "reviewer"
    assert art["status"] == "APPROVED_WITH_CONFLICT"
    assert "git merge conflict" in art["comment"]

    # 3. Idempotent overwrite (upsert without IntegrityError)
    await manager.upsert_pr_artifact(
        repo="org/repo",
        pr_number=437,
        node_name="devtest",
        status="CONFLICT_RESOLVED",
        comment="Git merge conflicts resolved and pushed.",
    )

    art_updated = await manager.get_pr_artifact("org/repo", 437)
    assert art_updated is not None
    assert art_updated["status"] == "CONFLICT_RESOLVED"
    assert art_updated["node_name"] == "devtest"

    # 4. Multi-project isolation (same PR number across different repos)
    await manager.upsert_pr_artifact(
        repo="org/other-repo",
        pr_number=437,
        node_name="reviewer",
        status="APPROVED",
        comment="Different project PR 437.",
    )
    art_other = await manager.get_pr_artifact("org/other-repo", 437)
    assert art_other["status"] == "APPROVED"

    list_all = await manager.list_pr_artifacts()
    assert len(list_all) == 2

    # 5. Delete artifact
    await manager.delete_pr_artifact("org/repo", 437)
    assert await manager.get_pr_artifact("org/repo", 437) is None
    assert len(await manager.list_pr_artifacts("org/repo")) == 0
    assert len(await manager.list_pr_artifacts("org/other-repo")) == 1
