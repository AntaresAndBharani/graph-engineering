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


@pytest.mark.asyncio
async def test_cleanup_orphaned_running_jobs(tmp_path: Path):
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Create an unexpired RUNNING lock (simulating an interrupted process)
    await manager.acquire_lock("300", "my-org/repo", "architect", ttl_minutes=30)
    jobs_before = await manager.get_active_jobs()
    assert len(jobs_before) == 1
    assert jobs_before[0]["status"] == "RUNNING"

    # Startup reclamation
    reclaimed = await manager.cleanup_orphaned_running_jobs()
    assert reclaimed == 1

    jobs_after = await manager.get_active_jobs()
    assert len(jobs_after) == 1
    assert jobs_after[0]["status"] == "FAILED"
    assert "Orphaned lock" in jobs_after[0]["error_message"]

    # Now acquire_lock should succeed immediately
    acquired = await manager.acquire_lock("300", "my-org/repo", "architect", ttl_minutes=30)
    assert acquired is True


@pytest.mark.asyncio
async def test_sdlc_items_and_anomaly_events_schema_creation(tmp_path: Path):
    """
    Scenario: Schema creation
    Given the StateManager initializes the database
    When migrations run
    Then tables sdlc_items and anomaly_events, and idx_anomalies_project_time must exist.
    """
    import aiosqlite
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    async with aiosqlite.connect(manager.db_path) as db:
        # Check sdlc_items table
        cursor = await db.execute("PRAGMA table_info(sdlc_items);")
        columns = await cursor.fetchall()
        col_map = {col[1]: {"type": col[2].upper(), "pk": col[5]} for col in columns}

        expected_sdlc_cols = {
            "project_name": "TEXT",
            "issue_number": "INTEGER",
            "title": "TEXT",
            "state": "TEXT",
            "labels": "TEXT",
            "linked_pr": "INTEGER",
            "updated_at": "REAL",
        }
        for name, expected_type in expected_sdlc_cols.items():
            assert name in col_map, f"Missing column {name} in sdlc_items"
            assert col_map[name]["type"] == expected_type

        assert col_map["project_name"]["pk"] > 0
        assert col_map["issue_number"]["pk"] > 0

        # Check anomaly_events table
        cursor = await db.execute("PRAGMA table_info(anomaly_events);")
        columns = await cursor.fetchall()
        col_map_anom = {col[1]: {"type": col[2].upper(), "pk": col[5]} for col in columns}

        expected_anom_cols = {
            "id": "INTEGER",
            "project_name": "TEXT",
            "issue_number": "INTEGER",
            "node_name": "TEXT",
            "error_type": "TEXT",
            "error_message": "TEXT",
            "created_at": "REAL",
        }
        for name, expected_type in expected_anom_cols.items():
            assert name in col_map_anom, f"Missing column {name} in anomaly_events"
            assert col_map_anom[name]["type"] == expected_type


@pytest.mark.asyncio
async def test_sdlc_items_sync_and_query_lifecycle(tmp_path: Path):
    """
    Scenario: Sync and query SDLC items
    Given a project has active issues/PRs
    When sync_project_sdlc_items is called
    Then rows are upserted and returned by get_sdlc_items.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Empty state query
    assert await manager.get_sdlc_items("alpha") == []

    # Sync items for alpha
    items = [
        {
            "issue_number": 101,
            "title": "Story: Implement Auth",
            "state": "OPEN",
            "labels": ["ready-for-dev", "priority:high"],
            "linked_pr": 201,
        },
        {
            "issue_number": 102,
            "title": "Bug: Fix Memory Leak",
            "state": "OPEN",
            "labels": "needs-architect-review",
            "linked_pr": None,
        },
    ]
    await manager.sync_project_sdlc_items("alpha", items)

    # Query items for alpha
    retrieved = await manager.get_sdlc_items("alpha")
    assert len(retrieved) == 2
    assert retrieved[0]["issue_number"] == 101
    assert retrieved[0]["title"] == "Story: Implement Auth"
    assert "ready-for-dev" in retrieved[0]["labels"]
    assert retrieved[0]["linked_pr"] == 201
    assert retrieved[1]["issue_number"] == 102
    assert retrieved[1]["linked_pr"] is None

    # Upsert idempotency (updating issue 101)
    updated_items = [
        {
            "issue_number": 101,
            "title": "Story: Implement Auth (Updated)",
            "state": "CLOSED",
            "labels": "done",
            "linked_pr": 201,
        }
    ]
    await manager.sync_project_sdlc_items("alpha", updated_items)
    retrieved_after = await manager.get_sdlc_items("alpha")
    assert len(retrieved_after) == 2
    assert retrieved_after[0]["title"] == "Story: Implement Auth (Updated)"
    assert retrieved_after[0]["state"] == "CLOSED"

    # Multi-project isolation
    await manager.sync_project_sdlc_items("beta", [{"issue_number": 999, "title": "Beta Task"}])
    assert len(await manager.get_sdlc_items("beta")) == 1
    assert len(await manager.get_sdlc_items("alpha")) == 2


@pytest.mark.asyncio
async def test_anomaly_events_record_and_24h_window_query(tmp_path: Path):
    """
    Scenario: Record anomaly event and query with 24h window
    Given anomaly_events contains rows older and newer than 24h
    When get_recent_anomalies is called
    Then only rows within the window are returned.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Initially empty
    assert await manager.get_recent_anomalies("alpha") == []
    assert await manager.get_recent_anomalies() == []

    # Record recent anomaly for alpha
    await manager.record_anomaly_event(
        project_name="alpha",
        node_name="devtest",
        error_type="HarnessTimeout",
        error_message="Subprocess timed out after 900s",
        issue_number=101,
    )

    # Record recent anomaly for beta
    await manager.record_anomaly_event(
        project_name="beta",
        node_name="reviewer",
        error_type="MergeConflict",
        error_message="Git conflict on branch feat/x",
    )

    # Query without filter -> returns both
    all_recent = await manager.get_recent_anomalies()
    assert len(all_recent) == 2

    # Query filtered by project
    alpha_recent = await manager.get_recent_anomalies("alpha")
    assert len(alpha_recent) == 1
    assert alpha_recent[0]["node_name"] == "devtest"
    assert alpha_recent[0]["error_type"] == "HarnessTimeout"
    assert alpha_recent[0]["issue_number"] == 101

    # Insert an old anomaly (> 25 hours ago)
    old_time = time.time() - (25 * 3600)
    import aiosqlite
    async with aiosqlite.connect(manager.db_path) as db:
        await db.execute(
            """
            INSERT INTO anomaly_events (project_name, issue_number, node_name, error_type, error_message, created_at)
            VALUES ('alpha', 100, 'architect', 'SLAExceeded', 'Issue triage exceeded SLA', ?)
            """,
            (old_time,),
        )
        await db.commit()

    # Query with default 24h window -> old anomaly should be pruned
    filtered_24h = await manager.get_recent_anomalies("alpha", hours=24.0)
    assert len(filtered_24h) == 1
    assert filtered_24h[0]["node_name"] == "devtest"

    # Query with 48h window -> old anomaly is included
    filtered_48h = await manager.get_recent_anomalies("alpha", hours=48.0)
    assert len(filtered_48h) == 2



