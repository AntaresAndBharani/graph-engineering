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


@pytest.mark.asyncio
async def test_po_tracking_blackboard_lifecycle(tmp_path: Path):
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # 1. Initially empty
    assert await manager.get_po_tracking("org/repo", 15) is None
    assert await manager.list_po_trackings("org/repo") == []

    # 2. Upsert PO tracking record
    await manager.upsert_po_tracking(
        repo="org/repo",
        issue_number=15,
        body_hash="hash-12345",
        status="NEEDS_HUMAN_CLARIFICATION",
        gherkin_ac=None,
        blockers="Missing business rules",
    )

    record = await manager.get_po_tracking("org/repo", 15)
    assert record is not None
    assert record["repo"] == "org/repo"
    assert record["issue_number"] == 15
    assert record["body_hash"] == "hash-12345"
    assert record["status"] == "NEEDS_HUMAN_CLARIFICATION"
    assert record["gherkin_ac"] is None
    assert record["blockers"] == "Missing business rules"
    assert record["updated_at"] > 0

    # 3. Idempotent update on conflict
    await manager.upsert_po_tracking(
        repo="org/repo",
        issue_number=15,
        body_hash="hash-67890",
        status="PO_APPROVED",
        gherkin_ac="Feature: Test\nScenario: A\nGiven B\nWhen C\nThen D",
        blockers=None,
    )

    updated = await manager.get_po_tracking("org/repo", 15)
    assert updated is not None
    assert updated["body_hash"] == "hash-67890"
    assert updated["status"] == "PO_APPROVED"
    assert "Feature: Test" in updated["gherkin_ac"]
    assert updated["blockers"] is None

    # 4. Multi-repo isolation and listing
    await manager.upsert_po_tracking(
        repo="org/other-repo",
        issue_number=15,
        body_hash="other-hash",
        status="PO_APPROVED",
    )

    all_records = await manager.list_po_trackings()
    assert len(all_records) == 2

    repo_records = await manager.list_po_trackings("org/repo")
    assert len(repo_records) == 1
    assert repo_records[0]["repo"] == "org/repo"

    # 5. Delete record
    await manager.delete_po_tracking("org/repo", 15)
    assert await manager.get_po_tracking("org/repo", 15) is None
    assert len(await manager.list_po_trackings("org/repo")) == 0
    assert len(await manager.list_po_trackings("org/other-repo")) == 1


@pytest.mark.asyncio
async def test_po_tracking_schema_columns_and_composite_pk(tmp_path: Path):
    """
    Verifies Scenario 1: Schema creation is idempotent, columns match specification,
    and composite primary key (repo, issue_number) is enforced.
    """
    import aiosqlite
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)

    # Initial creation
    await manager.init_db()
    # Idempotency check: running init_db() multiple times should succeed without error
    await manager.init_db()

    async with aiosqlite.connect(manager.db_path) as db:
        cursor = await db.execute("PRAGMA table_info(po_tracking);")
        columns = await cursor.fetchall()
        # columns format: (cid, name, type, notnull, dflt_value, pk)
        col_map = {col[1]: {"type": col[2].upper(), "notnull": col[3], "pk": col[5]} for col in columns}

        expected_columns = {
            "repo": "TEXT",
            "issue_number": "INTEGER",
            "body_hash": "TEXT",
            "status": "TEXT",
            "gherkin_ac": "TEXT",
            "blockers": "TEXT",
            "updated_at": "REAL",
        }

        for col_name, expected_type in expected_columns.items():
            assert col_name in col_map, f"Missing column: {col_name}"
            assert col_map[col_name]["type"] == expected_type, f"Column {col_name} expected {expected_type}, got {col_map[col_name]['type']}"

        # Verify composite primary key on (repo, issue_number)
        assert col_map["repo"]["pk"] > 0, "repo must be part of primary key"
        assert col_map["issue_number"]["pk"] > 0, "issue_number must be part of primary key"
        assert col_map["body_hash"]["pk"] == 0, "body_hash must not be primary key"


