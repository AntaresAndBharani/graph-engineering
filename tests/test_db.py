from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import time
import pytest
import aiosqlite
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
    # Initial creation and idempotency test
    await manager.init_db()
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
            "pr_status": "TEXT",
            "pr_ci_details": "TEXT",
            "created_at": "REAL",
            "updated_at": "REAL",
        }
        for name, expected_type in expected_sdlc_cols.items():
            assert name in col_map, f"Missing column {name} in sdlc_items"
            assert col_map[name]["type"] == expected_type

        assert col_map["project_name"]["pk"] > 0
        assert col_map["issue_number"]["pk"] > 0

        cursor = await db.execute("PRAGMA index_list(sdlc_items);")
        sdlc_indexes = await cursor.fetchall()
        sdlc_idx_names = [idx[1] for idx in sdlc_indexes]
        assert "idx_sdlc_parent" in sdlc_idx_names
        assert "idx_sdlc_lookahead" in sdlc_idx_names
        assert "idx_sdlc_lock" in sdlc_idx_names

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

        # Check idx_anomalies_project_time index
        cursor = await db.execute("PRAGMA index_list(anomaly_events);")
        indexes = await cursor.fetchall()
        idx_names = [idx[1] for idx in indexes]
        assert "idx_anomalies_project_time" in idx_names


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
            "pr_status": "OPEN",
            "pr_ci_details": "RUNNING",
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
    assert retrieved[0]["pr_status"] == "OPEN"
    assert retrieved[0]["pr_ci_details"] == "RUNNING"
    assert retrieved[1]["issue_number"] == 102
    assert retrieved[1]["linked_pr"] is None
    assert retrieved[1]["pr_status"] is None
    assert retrieved[1]["pr_ci_details"] is None

    # Upsert idempotency (updating issue 101)
    updated_items = [
        {
            "issue_number": 101,
            "title": "Story: Implement Auth (Updated)",
            "state": "CLOSED",
            "labels": "done",
            "linked_pr": 201,
            "pr_status": "MERGED",
            "pr_ci_details": "PASS",
        }
    ]
    await manager.sync_project_sdlc_items("alpha", updated_items)
    retrieved_after = await manager.get_sdlc_items("alpha")
    assert len(retrieved_after) == 2
    assert retrieved_after[0]["title"] == "Story: Implement Auth (Updated)"
    assert retrieved_after[0]["state"] == "CLOSED"
    assert retrieved_after[0]["pr_status"] == "MERGED"
    assert retrieved_after[0]["pr_ci_details"] == "PASS"

    # Multi-project isolation
    await manager.sync_project_sdlc_items("beta", [{"issue_number": 999, "title": "Beta Task"}])
    assert len(await manager.get_sdlc_items("beta")) == 1
    assert len(await manager.get_sdlc_items("alpha")) == 2


@pytest.mark.asyncio
async def test_non_destructive_schema_evolution_pr_status_and_ci(tmp_path: Path):
    """
    Scenario: Non-destructive schema evolution
      Given the orchestrator daemon initializes on an existing database without pr_status
      When StateManager.init_db() runs
      Then it must ALTER TABLE to add pr_status and pr_ci_details without dropping existing rows
    """
    import aiosqlite
    db_path = tmp_path / "legacy_state.db"

    # 1. Create a legacy table without pr_status and pr_ci_details
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE sdlc_items (
                project_name TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                parent_issue_id INTEGER,
                item_type TEXT DEFAULT 'SUBTASK',
                sequence_order INTEGER DEFAULT 0,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                labels TEXT,
                linked_pr INTEGER,
                created_at REAL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (project_name, issue_number)
            );
            """
        )
        await db.execute(
            """
            INSERT INTO sdlc_items (project_name, issue_number, title, state, updated_at)
            VALUES ('graph-engineering', 450, 'Legacy Pre-existing Item', 'OPEN', 1700000000.0);
            """
        )
        await db.commit()

    # 2. Run StateManager.init_db()
    manager = StateManager(db_path)
    await manager.init_db()

    # 3. Verify columns exist now and previous row is completely intact
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(sdlc_items);")
        columns = await cursor.fetchall()
        col_names = [col[1] for col in columns]
        assert "pr_status" in col_names
        assert "pr_ci_details" in col_names

    # Check preserved legacy row
    items = await manager.get_sdlc_items("graph-engineering")
    assert len(items) == 1
    assert items[0]["issue_number"] == 450
    assert items[0]["title"] == "Legacy Pre-existing Item"
    assert items[0]["pr_status"] is None
    assert items[0]["pr_ci_details"] is None

    # 4. Upsert new item with pr_status and pr_ci_details
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [
            {
                "issue_number": 450,
                "title": "Legacy Pre-existing Item (Updated)",
                "pr_status": "OPEN",
                "pr_ci_details": "PASS",
            }
        ],
    )
    updated_items = await manager.get_sdlc_items("graph-engineering")
    assert len(updated_items) == 1
    assert updated_items[0]["pr_status"] == "OPEN"
    assert updated_items[0]["pr_ci_details"] == "PASS"


@pytest.mark.asyncio
async def test_smart_visibility_keeps_orphaned_open_subtasks_visible(tmp_path: Path):
    """
    Scenario: Smart visibility keeps orphaned open subtasks visible
      Given parent story #454 is "closed"
      And child subtask #456 is still "open"
      When get_active_sdlc_hierarchy() is called
      Then it must return parent story #454 with child subtask #456
      And subtasks must be ordered by sequence_order ASC, issue_number ASC
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Insert parent story #454 (closed) and subtasks #456 (open) & #455 (closed)
    items = [
        {
            "issue_number": 454,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "Story: Large Refactoring",
            "state": "CLOSED",
            "labels": "done",
            "parent_issue_id": None,
        },
        {
            "issue_number": 456,
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "title": "Subtask: Cleanup Old Modules",
            "state": "OPEN",
            "labels": "ready-for-dev",
            "parent_issue_id": 454,
            "linked_pr": 501,
            "pr_status": "OPEN",
            "pr_ci_details": "PASS",
        },
        {
            "issue_number": 455,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Subtask: Initial Extraction",
            "state": "CLOSED",
            "labels": "done",
            "parent_issue_id": 454,
            "linked_pr": 500,
            "pr_status": "MERGED",
            "pr_ci_details": "PASS",
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    hierarchy = await manager.get_active_sdlc_hierarchy("graph-engineering")
    assert len(hierarchy) == 1

    root_story = hierarchy[0]
    assert root_story["issue_number"] == 454
    assert root_story["state"] == "CLOSED"
    assert len(root_story["subtasks"]) == 2

    # Check ordering: sequence_order ASC, issue_number ASC
    assert root_story["subtasks"][0]["issue_number"] == 455
    assert root_story["subtasks"][0]["sequence_order"] == 1
    assert root_story["subtasks"][0]["state"] == "CLOSED"

    assert root_story["subtasks"][1]["issue_number"] == 456
    assert root_story["subtasks"][1]["sequence_order"] == 2
    assert root_story["subtasks"][1]["state"] == "OPEN"
    assert root_story["subtasks"][1]["pr_status"] == "OPEN"
    assert root_story["subtasks"][1]["pr_ci_details"] == "PASS"


@pytest.mark.asyncio
async def test_fully_closed_trees_are_hidden(tmp_path: Path):
    """
    Scenario: Fully closed trees are hidden
      Given parent story #500 and all its children are "closed"
      When get_active_sdlc_hierarchy() is called
      Then story #500 and its children must not be returned
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Story #500 and all its children are CLOSED
    items = [
        {
            "issue_number": 500,
            "item_type": "STORY",
            "sequence_order": 1,
            "title": "Story: Legacy Migration",
            "state": "CLOSED",
            "parent_issue_id": None,
        },
        {
            "issue_number": 501,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Subtask: DB Schema",
            "state": "CLOSED",
            "parent_issue_id": 500,
        },
        {
            "issue_number": 502,
            "item_type": "SUBTASK",
            "sequence_order": 2,
            "title": "Subtask: Seed Data",
            "state": "MERGED",
            "parent_issue_id": 500,
        },
        # Story #600 is an ACTIVE story with an open subtask
        {
            "issue_number": 600,
            "item_type": "STORY",
            "sequence_order": 2,
            "title": "Story: Active Feature",
            "state": "OPEN",
            "parent_issue_id": None,
        },
        {
            "issue_number": 601,
            "item_type": "SUBTASK",
            "sequence_order": 1,
            "title": "Subtask: Implement Handler",
            "state": "OPEN",
            "parent_issue_id": 600,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    hierarchy = await manager.get_active_sdlc_hierarchy("graph-engineering")

    # Story #500 is completely excluded, only Story #600 is returned
    assert len(hierarchy) == 1
    assert hierarchy[0]["issue_number"] == 600
    assert len(hierarchy[0]["subtasks"]) == 1
    assert hierarchy[0]["subtasks"][0]["issue_number"] == 601


@pytest.mark.asyncio
async def test_sdlc_hierarchy_edge_cases_and_standalone_items(tmp_path: Path):
    """
    Tests edge cases:
    - Empty project returns empty list.
    - Standalone open issues (without children or parent) are returned.
    - Standalone closed issues (without children or parent) are hidden.
    - Orphaned subtask with non-existent parent_issue_id is returned if open.
    - Multi-project hierarchy isolation.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # 1. Empty project
    assert await manager.get_active_sdlc_hierarchy("empty-project") == []

    # 2. Standalone open & closed items
    items = [
        {
            "issue_number": 10,
            "title": "Standalone Open Bug",
            "state": "OPEN",
            "parent_issue_id": None,
        },
        {
            "issue_number": 20,
            "title": "Standalone Closed Bug",
            "state": "CLOSED",
            "parent_issue_id": None,
        },
        {
            "issue_number": 30,
            "title": "Orphaned Open Subtask",
            "state": "OPEN",
            "parent_issue_id": 9999,  # Parent not present in DB
        },
    ]
    await manager.sync_project_sdlc_items("proj_a", items)

    hierarchy_a = await manager.get_active_sdlc_hierarchy("proj_a")
    assert len(hierarchy_a) == 2
    ids = [h["issue_number"] for h in hierarchy_a]
    assert 10 in ids
    assert 30 in ids
    assert 20 not in ids

    # 3. Multi-project isolation
    items_b = [
        {
            "issue_number": 100,
            "title": "Project B Story",
            "state": "OPEN",
            "parent_issue_id": None,
        }
    ]
    await manager.sync_project_sdlc_items("proj_b", items_b)

    hierarchy_b = await manager.get_active_sdlc_hierarchy("proj_b")
    assert len(hierarchy_b) == 1
    assert hierarchy_b[0]["issue_number"] == 100


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


@pytest.mark.asyncio
async def test_token_usage_events_schema_and_indexes(tmp_path: Path):
    """
    Verifies token_usage_events table schema, column types, default created_at,
    and indices: idx_token_usage_harness_time and idx_token_usage_project_node.
    """
    import aiosqlite

    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    # Initial creation and idempotency test
    await manager.init_db()
    await manager.init_db()

    async with aiosqlite.connect(manager.db_path) as db:
        # Check token_usage_events table
        cursor = await db.execute("PRAGMA table_info(token_usage_events);")
        columns = await cursor.fetchall()
        col_map = {col[1]: {"type": col[2].upper(), "pk": col[5], "notnull": col[3]} for col in columns}

        expected_cols = {
            "id": "INTEGER",
            "harness_name": "TEXT",
            "model_name": "TEXT",
            "project_name": "TEXT",
            "node_name": "TEXT",
            "issue_number": "INTEGER",
            "prompt_tokens": "INTEGER",
            "completion_tokens": "INTEGER",
            "total_tokens": "INTEGER",
            "created_at": "TIMESTAMP",
        }
        for name, expected_type in expected_cols.items():
            assert name in col_map, f"Missing column {name} in token_usage_events"
            assert col_map[name]["type"] == expected_type, f"Column {name} expected type {expected_type}, got {col_map[name]['type']}"

        assert col_map["id"]["pk"] == 1
        assert col_map["harness_name"]["notnull"] == 1
        assert col_map["model_name"]["notnull"] == 1
        assert col_map["project_name"]["notnull"] == 1
        assert col_map["node_name"]["notnull"] == 1
        assert col_map["issue_number"]["notnull"] == 0
        assert col_map["prompt_tokens"]["notnull"] == 1
        assert col_map["completion_tokens"]["notnull"] == 1
        assert col_map["total_tokens"]["notnull"] == 1

        # Check indexes
        cursor = await db.execute("PRAGMA index_list(token_usage_events);")
        indexes = await cursor.fetchall()
        idx_names = [idx[1] for idx in indexes]
        assert "idx_token_usage_harness_time" in idx_names
        assert "idx_token_usage_project_node" in idx_names


@pytest.mark.asyncio
async def test_record_token_usage_event_scenario(tmp_path: Path):
    """
    Scenario: Recording a token usage event
      Given a harness "antigravity" execution completes for project "graph-engineering" node "devtest"
      When record_token_usage_event is called with prompt_tokens=1000, completion_tokens=500, total_tokens=1500
      Then a row is inserted into token_usage_events with created_at in UTC
      And project_name="graph-engineering" and node_name="devtest" are stored
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="devtest",
        issue_number=52,
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
    )

    events = await manager.get_token_usage_events("antigravity")
    assert len(events) == 1
    event = events[0]
    assert event["harness_name"] == "antigravity"
    assert event["model_name"] == "gemini-3.7-flash"
    assert event["project_name"] == "graph-engineering"
    assert event["node_name"] == "devtest"
    assert event["issue_number"] == 52
    assert event["prompt_tokens"] == 1000
    assert event["completion_tokens"] == 500
    assert event["total_tokens"] == 1500
    assert isinstance(event["created_at"], str)

    # Verify created_at parses as UTC ISO datetime
    created_dt = datetime.strptime(event["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    assert abs((now_utc - created_dt).total_seconds()) < 60


@pytest.mark.asyncio
async def test_rolling_window_sum_timezone_safety(tmp_path: Path):
    """
    Scenario: Rolling window sum is timezone-safe
      Given events exist at various UTC timestamps for harness "claude"
      When get_window_token_usage("claude", window_hours=5.0) is called
      Then only events with created_at >= (utcnow - 5 hours) are summed
      And the calculation is unaffected by local machine timezone
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    now_utc = datetime.now(timezone.utc)

    # Event 1: 1 hour ago (within 5h window) -> 100,000 tokens
    t1 = now_utc - timedelta(hours=1)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="architect",
        issue_number=10,
        prompt_tokens=80000,
        completion_tokens=20000,
        total_tokens=100000,
        created_at=t1,
    )

    # Event 2: 4 hours ago (within 5h window) -> 250,000 tokens
    t2 = now_utc - timedelta(hours=4)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="crosstrainingapp",
        node_name="devtest",
        issue_number=20,
        prompt_tokens=200000,
        completion_tokens=50000,
        total_tokens=250000,
        created_at=t2,
    )

    # Event 3: 5.5 hours ago (OUTSIDE 5h window) -> 500,000 tokens
    t3 = now_utc - timedelta(hours=5, minutes=30)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="bau",
        issue_number=5,
        prompt_tokens=400000,
        completion_tokens=100000,
        total_tokens=500000,
        created_at=t3,
    )

    # Event 4: 10 hours ago (OUTSIDE 5h window) -> 1,000,000 tokens
    t4 = now_utc - timedelta(hours=10)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="other-app",
        node_name="reviewer",
        issue_number=1,
        prompt_tokens=800000,
        completion_tokens=200000,
        total_tokens=1000000,
        created_at=t4,
    )

    # 5-hour window sum should be Event 1 + Event 2 = 100,000 + 250,000 = 350,000
    usage_5h = await manager.get_window_token_usage("claude", window_hours=5.0)
    assert usage_5h == 350000

    # 2-hour window sum should be Event 1 only = 100,000
    usage_2h = await manager.get_window_token_usage("claude", window_hours=2.0)
    assert usage_2h == 100000

    # 12-hour window sum should be all 4 events = 1,850,000
    usage_12h = await manager.get_window_token_usage("claude", window_hours=12.0)
    assert usage_12h == 1850000


@pytest.mark.asyncio
async def test_global_pooling_across_projects(tmp_path: Path):
    """
    Scenario: Global pooling across projects
      Given project "graph-engineering" records 120000 tokens for harness "antigravity"
      And project "crosstrainingapp" records 80000 tokens for the same harness
      When get_window_token_usage("antigravity", window_hours=1.0) is called
      Then the returned sum is 200000
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="devtest",
        issue_number=101,
        prompt_tokens=100000,
        completion_tokens=20000,
        total_tokens=120000,
    )

    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="crosstrainingapp",
        node_name="reviewer",
        issue_number=202,
        prompt_tokens=60000,
        completion_tokens=20000,
        total_tokens=80000,
    )

    usage = await manager.get_window_token_usage("antigravity", window_hours=1.0)
    assert usage == 200000


@pytest.mark.asyncio
async def test_usage_breakdown_by_project_and_node(tmp_path: Path):
    """
    Scenario: Usage breakdown by project and node
      Given multiple events recorded across projects and nodes for harness "antigravity"
      When get_usage_breakdown("antigravity", window_hours=1.0) is called
      Then it returns token totals keyed by project_name
      And token totals keyed by node_name
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Event 1: graph-engineering / devtest: 60,000 tokens
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=50000,
        completion_tokens=10000,
        total_tokens=60000,
    )

    # Event 2: graph-engineering / reviewer: 40,000 tokens
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="reviewer",
        issue_number=2,
        prompt_tokens=30000,
        completion_tokens=10000,
        total_tokens=40000,
    )

    # Event 3: crosstrainingapp / devtest: 50,000 tokens
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="crosstrainingapp",
        node_name="devtest",
        issue_number=3,
        prompt_tokens=40000,
        completion_tokens=10000,
        total_tokens=50000,
    )

    # Event 4: other harness event (should be excluded from antigravity breakdown)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="architect",
        issue_number=4,
        prompt_tokens=70000,
        completion_tokens=30000,
        total_tokens=100000,
    )

    breakdown = await manager.get_usage_breakdown("antigravity", window_hours=1.0)

    # By project: graph-engineering = 100,000 (60k+40k), crosstrainingapp = 50,000
    assert "by_project" in breakdown
    assert breakdown["by_project"]["graph-engineering"] == 100000
    assert breakdown["by_project"]["crosstrainingapp"] == 50000

    # Also accessible via "projects" alias
    assert breakdown["projects"]["graph-engineering"] == 100000

    # By node: devtest = 110,000 (60k+50k), reviewer = 40,000
    assert "by_node" in breakdown
    assert breakdown["by_node"]["devtest"] == 110000
    assert breakdown["by_node"]["reviewer"] == 40000

    # Also accessible via "nodes" alias
    assert breakdown["nodes"]["devtest"] == 110000


@pytest.mark.asyncio
async def test_token_usage_empty_and_isolation(tmp_path: Path):
    """
    Verifies that querying an empty database or non-existent harness returns 0 / empty dicts.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Empty window sum returns 0
    assert await manager.get_window_token_usage("nonexistent") == 0

    # Empty multi-window usage returns (0, 0)
    assert await manager.get_multi_window_usage("nonexistent") == (0, 0)

    # Empty breakdown returns empty dicts
    breakdown = await manager.get_usage_breakdown("nonexistent")
    assert breakdown["by_project"] == {}
    assert breakdown["by_node"] == {}

    # Empty raw events list
    events = await manager.get_token_usage_events("nonexistent")
    assert events == []


@pytest.mark.asyncio
async def test_multi_window_token_usage_dual_window_calculation(tmp_path: Path):
    """
    Scenario: Single query returns both window totals
      Given `token_usage_events` contains rows for a harness spanning more than 168 hours
      When `get_multi_window_usage(harness_name, short_window_hours, long_window_hours=168.0)` is called
      Then it returns both the short-window token total and the long-window (weekly) token total
      And both totals are computed from UTC cutoffs (`now - window_hours`) with zero timezone drift
      And the call completes in a single SQL round-trip (no duplicate queries against the table)
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    now_utc = datetime.now(timezone.utc)

    # Event 1: 2 hours ago (within 5h short window AND 168h weekly window) -> 150,000 tokens
    t1 = now_utc - timedelta(hours=2)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="architect",
        issue_number=101,
        prompt_tokens=100000,
        completion_tokens=50000,
        total_tokens=150000,
        created_at=t1,
    )

    # Event 2: 4 hours ago (within 5h short window AND 168h weekly window) -> 200,000 tokens
    t2 = now_utc - timedelta(hours=4)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="crosstrainingapp",
        node_name="devtest",
        issue_number=102,
        prompt_tokens=150000,
        completion_tokens=50000,
        total_tokens=200000,
        created_at=t2,
    )

    # Event 3: 24 hours ago (OUTSIDE 5h short window, INSIDE 168h weekly window) -> 500,000 tokens
    t3 = now_utc - timedelta(hours=24)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="devtest",
        issue_number=103,
        prompt_tokens=400000,
        completion_tokens=100000,
        total_tokens=500000,
        created_at=t3,
    )

    # Event 4: 120 hours ago (5 days ago, OUTSIDE 5h, INSIDE 168h weekly window) -> 1,000,000 tokens
    t4 = now_utc - timedelta(hours=120)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="reviewer",
        issue_number=104,
        prompt_tokens=800000,
        completion_tokens=200000,
        total_tokens=1000000,
        created_at=t4,
    )

    # Event 5: 170 hours ago (>168h, OUTSIDE BOTH short and weekly windows) -> 2,000,000 tokens
    t5 = now_utc - timedelta(hours=170)
    await manager.record_token_usage_event(
        harness_name="claude",
        model_name="claude-3-7-sonnet",
        project_name="graph-engineering",
        node_name="bau",
        issue_number=105,
        prompt_tokens=1500000,
        completion_tokens=500000,
        total_tokens=2000000,
        created_at=t5,
    )

    # Event 6: Different harness event within 1h (should be ignored for claude query)
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="graph-engineering",
        node_name="architect",
        issue_number=106,
        prompt_tokens=50000,
        completion_tokens=20000,
        total_tokens=70000,
        created_at=now_utc - timedelta(hours=1),
    )

    # Short window (5h) total: Event 1 (150k) + Event 2 (200k) = 350k
    # Long window (168h) total: Event 1 (150k) + Event 2 (200k) + Event 3 (500k) + Event 4 (1000k) = 1,850,000
    # Event 5 (170h ago) is excluded from both
    short_total, long_total = await manager.get_multi_window_usage(
        harness_name="claude",
        short_window_hours=5.0,
        long_window_hours=168.0,
    )

    assert short_total == 350000
    assert long_total == 1850000


@pytest.mark.asyncio
async def test_multi_window_token_usage_empty_and_zero_handling(tmp_path: Path):
    """
    Scenario: Empty ledger returns zero usage
      Given no rows exist for the harness in `token_usage_events`
      When `get_multi_window_usage()` is called
      Then both returned totals are 0, with no exceptions raised
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Empty DB call
    short_usage, long_usage = await manager.get_multi_window_usage(
        harness_name="nonexistent",
        short_window_hours=5.0,
        long_window_hours=168.0,
    )
    assert short_usage == 0
    assert long_usage == 0

    # Default parameters call
    short_default, long_default = await manager.get_multi_window_usage("nonexistent")
    assert short_default == 0
    assert long_default == 0


@pytest.mark.asyncio
async def test_multi_window_token_usage_global_pooling_and_custom_windows(tmp_path: Path):
    """
    Scenario: Global multi-project pooling and flexible window definitions
      Given events across multiple projects for harness "antigravity"
      When get_multi_window_usage is called with custom window hours
      Then totals aggregate across all projects accurately in a single round trip
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    now_utc = datetime.now(timezone.utc)

    # Project 1 Event: 30 mins ago -> 80,000 tokens
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="project-alpha",
        node_name="devtest",
        issue_number=1,
        prompt_tokens=60000,
        completion_tokens=20000,
        total_tokens=80000,
        created_at=now_utc - timedelta(minutes=30),
    )

    # Project 2 Event: 3 hours ago -> 120,000 tokens
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="project-beta",
        node_name="reviewer",
        issue_number=2,
        prompt_tokens=100000,
        completion_tokens=20000,
        total_tokens=120000,
        created_at=now_utc - timedelta(hours=3),
    )

    # Project 3 Event: 12 hours ago -> 200,000 tokens
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="gemini-3.7-flash",
        project_name="project-gamma",
        node_name="architect",
        issue_number=3,
        prompt_tokens=160000,
        completion_tokens=40000,
        total_tokens=200000,
        created_at=now_utc - timedelta(hours=12),
    )

    # Test 1h short window vs 24h long window:
    # 1h window: Project 1 only = 80,000
    # 24h window: Project 1 (80k) + Project 2 (120k) + Project 3 (200k) = 400,000
    short_1h, long_24h = await manager.get_multi_window_usage(
        harness_name="antigravity",
        short_window_hours=1.0,
        long_window_hours=24.0,
    )
    assert short_1h == 80000
    assert long_24h == 400000

    # Inverted window test (short_window > long_window)
    res_inv = await manager.get_multi_window_usage(
        harness_name="antigravity",
        short_window_hours=24.0,
        long_window_hours=1.0,
    )
    assert res_inv == (400000, 80000)


@pytest.mark.asyncio
async def test_get_project_state_fingerprint(tmp_path: Path):
    """
    Asserts StateManager.get_project_state_fingerprint produces deterministic
    fingerprints that update dynamically when SDLC items, anomalies, or tokens change.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # 1. Initially empty
    fp_global_empty = await manager.get_project_state_fingerprint(None)
    assert fp_global_empty == "global:0:0"

    fp_p1_empty = await manager.get_project_state_fingerprint("p1")
    assert fp_p1_empty == "p1:0:0:0:0:0:0"

    # 2. Add SDLC item
    await manager.sync_project_sdlc_items(
        "p1",
        [{"issue_number": 10, "title": "T10", "labels": "ready-for-dev", "updated_at": 1000.0}],
    )
    fp_p1_sdlc = await manager.get_project_state_fingerprint("p1")
    assert fp_p1_sdlc != fp_p1_empty
    assert "p1:1:1000.0:" in fp_p1_sdlc

    # 3. Add anomaly event
    await manager.record_anomaly_event(
        project_name="p1",
        node_name="devtest",
        error_type="Timeout",
        error_message="Msg",
    )
    fp_p1_anomaly = await manager.get_project_state_fingerprint("p1")
    assert fp_p1_anomaly != fp_p1_sdlc
    assert ":1:1:" in fp_p1_anomaly or ":1:1" in fp_p1_anomaly

    # 4. Add token usage event
    await manager.record_token_usage_event(
        harness_name="antigravity",
        model_name="flash",
        project_name="p1",
        node_name="devtest",
        issue_number=10,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )
    fp_p1_tokens = await manager.get_project_state_fingerprint("p1")
    assert fp_p1_tokens != fp_p1_anomaly
    assert fp_p1_tokens.endswith(":1:1")

    # Global fingerprint also updated
    fp_global = await manager.get_project_state_fingerprint(None)
    assert fp_global == "global:1:1"


@pytest.mark.asyncio
async def test_count_planned_stories_scenario(tmp_path: Path):
    """
    Scenario: Count planned stories for a project
      Given SQLite sdlc_items contains 2 rows for project "demo" with status "PLANNED"
      When count_planned_stories("demo") is called
      Then it returns 2
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Empty initially
    assert await manager.count_planned_stories("demo") == 0

    items = [
        {
            "issue_number": 50,
            "title": "Story: User Auth",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 1,
        },
        {
            "issue_number": 51,
            "title": "Story: Profile Dashboard",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 2,
        },
        {
            "issue_number": 52,
            "title": "Story: In-progress feature",
            "state": "IN_PROGRESS",
            "item_type": "STORY",
            "sequence_order": 0,
        },
    ]
    await manager.sync_project_sdlc_items("demo", items)

    count = await manager.count_planned_stories("demo")
    assert count == 2


@pytest.mark.asyncio
async def test_get_oldest_planned_story_scenario(tmp_path: Path):
    """
    Scenario: Get oldest planned story
      Given SQLite sdlc_items contains multiple "PLANNED" stories for project "demo" with different created_at timestamps
      When get_oldest_planned_story("demo") is called
      Then it returns the story record with the earliest created_at
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # Empty initially
    assert await manager.get_oldest_planned_story("demo") is None

    base_time = 1700000000.0
    items = [
        {
            "issue_number": 62,
            "title": "Story: Newer Story",
            "state": "PLANNED",
            "item_type": "STORY",
            "created_at": base_time + 300.0,
            "sequence_order": 2,
        },
        {
            "issue_number": 60,
            "title": "Story: Oldest Story",
            "state": "PLANNED",
            "item_type": "STORY",
            "created_at": base_time,
            "sequence_order": 1,
        },
        {
            "issue_number": 61,
            "title": "Story: Middle Story",
            "state": "PLANNED",
            "item_type": "STORY",
            "created_at": base_time + 150.0,
            "sequence_order": 3,
        },
    ]
    await manager.sync_project_sdlc_items("demo", items)

    oldest = await manager.get_oldest_planned_story("demo")
    assert oldest is not None
    assert oldest["issue_number"] == 60
    assert oldest["title"] == "Story: Oldest Story"
    assert oldest["state"] == "PLANNED"
    assert oldest["status"] == "PLANNED"
    assert oldest["created_at"] == base_time


@pytest.mark.asyncio
async def test_promote_planned_story_scenario(tmp_path: Path):
    """
    Scenario: Promote a planned story
      Given story #60 exists in SQLite with status "PLANNED"
      When promote_planned_story("demo", 60) is called
      Then the story's status is updated to "ACTIVE" (or equivalent active state) in the same WAL transaction
      And the update is atomic under the existing TTL/lock conventions of StateManager
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 60,
            "title": "Story: Core Engine",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 1,
        },
        {
            "issue_number": 61,
            "title": "Story: Extension Pack",
            "state": "PLANNED",
            "item_type": "STORY",
            "sequence_order": 2,
        },
    ]
    await manager.sync_project_sdlc_items("demo", items)
    assert await manager.count_planned_stories("demo") == 2

    # Promote story #60
    promoted = await manager.promote_planned_story("demo", 60)
    assert promoted is True

    # Check updated status
    sdlc_items = await manager.get_sdlc_items("demo")
    story_60 = next(s for s in sdlc_items if s["issue_number"] == 60)
    assert story_60["state"] == "ACTIVE"
    assert story_60["status"] == "ACTIVE"

    # Planned count should now be 1
    assert await manager.count_planned_stories("demo") == 1

    # Oldest planned story is now #61
    oldest = await manager.get_oldest_planned_story("demo")
    assert oldest is not None
    assert oldest["issue_number"] == 61


@pytest.mark.asyncio
async def test_planned_stories_lookahead_edge_cases_and_multi_project_isolation(tmp_path: Path):
    """
    Tests edge cases: non-existent story promotion, custom new_status,
    case insensitivity (status:planned), and multi-project isolation.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    # 1. Promote non-existent story -> returns False
    res = await manager.promote_planned_story("demo", 999)
    assert res is False

    # 2. Multi-project isolation
    items_p1 = [
        {"issue_number": 10, "title": "P1 Story", "state": "planned", "created_at": 100.0}
    ]
    items_p2 = [
        {"issue_number": 20, "title": "P2 Story", "state": "PLANNED", "created_at": 200.0}
    ]
    await manager.sync_project_sdlc_items("proj1", items_p1)
    await manager.sync_project_sdlc_items("proj2", items_p2)

    assert await manager.count_planned_stories("proj1") == 1
    assert await manager.count_planned_stories("proj2") == 1
    assert await manager.count_planned_stories("proj3") == 0

    p1_oldest = await manager.get_oldest_planned_story("proj1")
    assert p1_oldest is not None
    assert p1_oldest["issue_number"] == 10

    # 3. Custom status promotion (e.g. READY-FOR-DEV)
    promoted = await manager.promote_planned_story("proj1", 10, new_status="READY-FOR-DEV")
    assert promoted is True

    p1_items = await manager.get_sdlc_items("proj1")
    assert p1_items[0]["state"] == "READY-FOR-DEV"
    assert await manager.count_planned_stories("proj1") == 0
    assert await manager.get_oldest_planned_story("proj1") is None
    # proj2 unchanged
    assert await manager.count_planned_stories("proj2") == 1


@pytest.mark.asyncio
async def test_strict_story_lock_prevents_cross_story_pickup(tmp_path: Path):
    """
    Scenario: Strict Story Lock Prevents Cross-Story Pickup
      Given SQLite contains active Story A (#90) with subtasks #93 and #94 in "ready-for-dev"
      And Story B (#95) with subtask #98 in "ready-for-dev"
      When DevTest queries get_next_devtest_task
      Then the query must return ONLY subtask #93
      And subtask #98 must be completely ignored until Story A is closed.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 90,
            "title": "Story A: Payment Integration",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1: Stripe API Client",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        },
        {
            "issue_number": 94,
            "title": "Subtask A2: Webhook Handler",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 2,
        },
        {
            "issue_number": 95,
            "title": "Story B: User Profiles",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 2,
        },
        {
            "issue_number": 98,
            "title": "Subtask B1: Avatar Upload",
            "item_type": "SUBTASK",
            "parent_issue_id": 95,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    # 1. First query: Story A is locked, lowest sequential subtask #93 is returned
    task = await manager.get_next_devtest_task("graph-engineering")
    assert task == 93

    # 2. Complete subtask #93 -> next sequential subtask #94 under Story A is returned
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [{"issue_number": 93, "state": "CLOSED", "labels": "dev-implemented"}],
    )
    task2 = await manager.get_next_devtest_task("graph-engineering")
    assert task2 == 94

    # 3. Complete subtask #94 and close Story A -> Story B unlocks subtask #98
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [
            {"issue_number": 94, "state": "CLOSED", "labels": "dev-implemented"},
            {"issue_number": 90, "state": "CLOSED", "labels": "dev-implemented"},
        ],
    )
    task3 = await manager.get_next_devtest_task("graph-engineering")
    assert task3 == 98


@pytest.mark.asyncio
async def test_blocked_pipeline_halts_without_skipping_stories(tmp_path: Path):
    """
    Scenario: Blocked Pipeline Halts Without Skipping Stories
      Given active Story A is locked
      And its next subtask #93 has transitioned to "status:blocked" or "orchestration-failed"
      When DevTest queries get_next_devtest_task
      Then the query must return None
      And it must NOT skip or dispatch subtasks from Story B.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 90,
            "title": "Story A: Data Migration",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1: Schema Migration",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["status:blocked", "ready-for-dev"],
            "sequence_order": 1,
        },
        {
            "issue_number": 94,
            "title": "Subtask A2: Data Seeder",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 2,
        },
        {
            "issue_number": 95,
            "title": "Story B: Auth Engine",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 2,
        },
        {
            "issue_number": 98,
            "title": "Subtask B1: Token Generator",
            "item_type": "SUBTASK",
            "parent_issue_id": 95,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    # Subtask #93 is blocked -> get_next_devtest_task returns None (no skip to #94 or Story B #98)
    task = await manager.get_next_devtest_task("graph-engineering")
    assert task is None

    # Test with orchestration-failed label
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [{"issue_number": 93, "labels": ["orchestration-failed"]}],
    )
    task_failed = await manager.get_next_devtest_task("graph-engineering")
    assert task_failed is None


@pytest.mark.asyncio
async def test_autonomous_story_promotion_after_full_completion(tmp_path: Path):
    """
    Scenario: Autonomous Story Promotion After Full Completion
      Given 100% of subtasks for Story A are closed and merged
      When the query re-evaluates ActiveStory
      Then it must promote the oldest planned Story B to active status
      And unlock Story B's first subtask #98 as the next candidate.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    base_time = 1700000000.0
    items = [
        # Story A and its subtasks are 100% closed/merged
        {
            "issue_number": 90,
            "title": "Story A: Completed Story",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
            "created_at": base_time,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "CLOSED",
            "sequence_order": 1,
        },
        {
            "issue_number": 94,
            "title": "Subtask A2",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "MERGED",
            "sequence_order": 2,
        },
        # Story B is PLANNED with queued subtask #98
        {
            "issue_number": 95,
            "title": "Story B: Next Planned Feature",
            "item_type": "STORY",
            "state": "PLANNED",
            "sequence_order": 2,
            "created_at": base_time + 100.0,
        },
        {
            "issue_number": 98,
            "title": "Subtask B1",
            "item_type": "SUBTASK",
            "parent_issue_id": 95,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    # Re-evaluates ActiveStory: Story A is completed -> promotes Story B and unlocks #98
    task = await manager.get_next_devtest_task("graph-engineering")
    assert task == 98

    # Verify Story B was promoted to ACTIVE in SQLite
    sdlc_items = await manager.get_sdlc_items("graph-engineering")
    story_b = next(s for s in sdlc_items if s["issue_number"] == 95)
    assert story_b["state"] == "ACTIVE"

    # Verify Subtask #98 was unlocked with ready-for-dev
    sub_98 = next(s for s in sdlc_items if s["issue_number"] == 98)
    assert "ready-for-dev" in sub_98["labels"]
    assert sub_98["state"] == "OPEN"


@pytest.mark.asyncio
async def test_standalone_task_fallback_and_priority(tmp_path: Path):
    """
    Scenario: Standalone Task Priority Fallback
      Given no active story is locked
      And SQLite contains Standalone Task #40 with "ready-for-dev"
      And Planned Story #50 with subtask #51 ("queued")
      When DevTest queries get_next_devtest_task
      Then Standalone Task #40 must be returned first
      And Story #50 must remain in PLANNED state.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 40,
            "title": "Standalone Bug: Fix Memory Leak",
            "item_type": "STANDALONE",
            "parent_issue_id": None,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        },
        {
            "issue_number": 50,
            "title": "Story: Planned Work",
            "item_type": "STORY",
            "state": "PLANNED",
            "sequence_order": 2,
        },
        {
            "issue_number": 51,
            "title": "Subtask 1",
            "item_type": "SUBTASK",
            "parent_issue_id": 50,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    task = await manager.get_next_devtest_task("graph-engineering")
    assert task == 40

    # Planned story #50 was not prematurely promoted
    assert await manager.count_planned_stories("graph-engineering") == 1

    # After closing task #40, planned story #50 is promoted and returns #51
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [{"issue_number": 40, "state": "CLOSED"}],
    )
    next_task = await manager.get_next_devtest_task("graph-engineering")
    assert next_task == 51
    assert await manager.count_planned_stories("graph-engineering") == 0


@pytest.mark.asyncio
async def test_active_story_takes_precedence_over_standalone_task(tmp_path: Path):
    """
    Scenario: Active Story Takes Precedence Over Standalone Task
      Given Active Story A (#90) has subtask #93 in "ready-for-dev"
      And Standalone Task #40 is also in "ready-for-dev"
      When DevTest queries get_next_devtest_task
      Then the query must return Subtask #93 (Standalone Task #40 is ignored while Story A is active).
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 90,
            "title": "Story A",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        },
        {
            "issue_number": 40,
            "title": "Standalone Bug",
            "item_type": "STANDALONE",
            "parent_issue_id": None,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 0,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    task = await manager.get_next_devtest_task("graph-engineering")
    assert task == 93


@pytest.mark.asyncio
async def test_active_story_next_subtask_in_progress_holds_lock(tmp_path: Path):
    """
    Scenario: Active Story Next Subtask In Progress Holds Lock
      Given Active Story A (#90) has subtask #93 in state "IN_PROGRESS"
      And subtask #94 is in "ready-for-dev"
      And Story B (#95) has subtask #98 in "ready-for-dev"
      When DevTest queries get_next_devtest_task
      Then the query must return None (lock is held, no out-of-order execution, no skip to Story B).
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 90,
            "title": "Story A",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "IN_PROGRESS",
            "labels": ["in-progress"],
            "sequence_order": 1,
        },
        {
            "issue_number": 94,
            "title": "Subtask A2",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 2,
        },
        {
            "issue_number": 95,
            "title": "Story B",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 2,
        },
        {
            "issue_number": 98,
            "title": "Subtask B1",
            "item_type": "SUBTASK",
            "parent_issue_id": 95,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    task = await manager.get_next_devtest_task("graph-engineering")
    assert task is None


@pytest.mark.asyncio
async def test_get_next_devtest_task_multi_project_isolation(tmp_path: Path):
    """
    Scenario: Multi-Project Isolation for get_next_devtest_task
      Given Project A has Story #90 with subtask #93
      And Project B has Story #200 with subtask #201
      When get_next_devtest_task is queried per project
      Then each project strictly resolves its own locked tasks.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items_a = [
        {"issue_number": 90, "item_type": "STORY", "state": "OPEN"},
        {"issue_number": 93, "item_type": "SUBTASK", "parent_issue_id": 90, "state": "OPEN", "labels": "ready-for-dev"},
    ]
    items_b = [
        {"issue_number": 200, "item_type": "STORY", "state": "OPEN"},
        {"issue_number": 201, "item_type": "SUBTASK", "parent_issue_id": 200, "state": "OPEN", "labels": "ready-for-dev"},
    ]
    await manager.sync_project_sdlc_items("proj_a", items_a)
    await manager.sync_project_sdlc_items("proj_b", items_b)

    assert await manager.get_next_devtest_task("proj_a") == 93
    assert await manager.get_next_devtest_task("proj_b") == 201
    assert await manager.get_next_devtest_task("nonexistent") is None


@pytest.mark.asyncio
async def test_active_story_without_subtasks_ready_and_blocked(tmp_path: Path):
    """
    Scenario: Active story without subtasks directly returns story ID if ready, None if blocked
      Given Active Story #70 has no child subtasks and is 'ready-for-dev'
      When get_next_devtest_task is queried
      Then it returns 70
      When Story #70 is updated to 'status:blocked'
      Then get_next_devtest_task returns None.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 70,
            "title": "Standalone Story without Subtasks",
            "item_type": "STORY",
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 1,
        }
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    assert await manager.get_next_devtest_task("graph-engineering") == 70

    # Transition to blocked
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [{"issue_number": 70, "labels": ["status:blocked"]}],
    )
    assert await manager.get_next_devtest_task("graph-engineering") is None


@pytest.mark.asyncio
async def test_blocked_story_quarantine_prevents_standalone_fallback(tmp_path: Path):
    """
    Scenario: Blocked Story Quarantine Prevents Standalone Fallback
      Given Active Story A (#90) has subtask #93 in 'status:blocked'
      And Standalone Task #40 is in 'ready-for-dev'
      When get_next_devtest_task is queried
      Then it must return None (the pipeline is strictly halted on Story A; no fallback to Standalone #40).
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 90,
            "title": "Story A",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["status:blocked"],
            "sequence_order": 1,
        },
        {
            "issue_number": 40,
            "title": "Standalone Task",
            "item_type": "STANDALONE",
            "parent_issue_id": None,
            "state": "OPEN",
            "labels": ["ready-for-dev"],
            "sequence_order": 0,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    task = await manager.get_next_devtest_task("graph-engineering")
    assert task is None


@pytest.mark.asyncio
async def test_oldest_planned_story_tiebreaking_sequence_and_created_at(tmp_path: Path):
    """
    Scenario: Oldest Planned Story Promotion Order
      Given no active story is open
      And Planned Story B (#95, created_at=2000, sequence=2)
      And Planned Story C (#96, created_at=1000, sequence=1)
      When get_next_devtest_task runs
      Then Story C (#96) must be promoted first due to earlier created_at.
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 95,
            "title": "Story B",
            "item_type": "STORY",
            "state": "PLANNED",
            "sequence_order": 2,
            "created_at": 2000.0,
        },
        {
            "issue_number": 98,
            "title": "Subtask B1",
            "item_type": "SUBTASK",
            "parent_issue_id": 95,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 1,
        },
        {
            "issue_number": 96,
            "title": "Story C",
            "item_type": "STORY",
            "state": "PLANNED",
            "sequence_order": 1,
            "created_at": 1000.0,
        },
        {
            "issue_number": 99,
            "title": "Subtask C1",
            "item_type": "SUBTASK",
            "parent_issue_id": 96,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    task = await manager.get_next_devtest_task("graph-engineering")
    # Story C was created earlier (1000.0 vs 2000.0), so its subtask #99 is returned
    assert task == 99

    sdlc_items = await manager.get_sdlc_items("graph-engineering")
    story_c = next(s for s in sdlc_items if s["issue_number"] == 96)
    assert story_c["state"] == "ACTIVE"
    story_b = next(s for s in sdlc_items if s["issue_number"] == 95)
    assert story_b["state"] == "PLANNED"


@pytest.mark.asyncio
async def test_get_next_devtest_task_picks_lowest_open_queued_subtask_in_oldest_story(tmp_path: Path):
    """
    Scenario: DevTest picks lowest open queued subtask in oldest user story
      Given Active Story A (#90) has subtasks #93 (queued) and #94 (queued)
      And Story B (#95) has subtask #98 (queued)
      When DevTest queries get_next_devtest_task
      Then it returns subtask #93
      When subtask #93 is closed
      Then DevTest queries get_next_devtest_task and returns #94
      When subtask #94 is closed and Story A is closed
      Then DevTest queries get_next_devtest_task and returns #98 (Story B).
    """
    db_path = tmp_path / "state.db"
    manager = StateManager(db_path)
    await manager.init_db()

    items = [
        {
            "issue_number": 90,
            "title": "Story A",
            "item_type": "STORY",
            "state": "OPEN",
            "sequence_order": 1,
            "created_at": 1000.0,
        },
        {
            "issue_number": 93,
            "title": "Subtask A1",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 1,
        },
        {
            "issue_number": 94,
            "title": "Subtask A2",
            "item_type": "SUBTASK",
            "parent_issue_id": 90,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 2,
        },
        {
            "issue_number": 95,
            "title": "Story B",
            "item_type": "STORY",
            "state": "PLANNED",
            "sequence_order": 2,
            "created_at": 2000.0,
        },
        {
            "issue_number": 98,
            "title": "Subtask B1",
            "item_type": "SUBTASK",
            "parent_issue_id": 95,
            "state": "OPEN",
            "labels": ["queued"],
            "sequence_order": 1,
        },
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    # 1. First subtask of oldest story
    task1 = await manager.get_next_devtest_task("graph-engineering")
    assert task1 == 93

    # 2. Close subtask #93 -> next is #94
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [{"issue_number": 93, "state": "CLOSED", "labels": ["merged"]}],
    )
    task2 = await manager.get_next_devtest_task("graph-engineering")
    assert task2 == 94

    # 3. Close subtask #94 and Story A (#90) -> moves to Story B (#95) -> returns #98
    await manager.sync_project_sdlc_items(
        "graph-engineering",
        [
            {"issue_number": 94, "state": "CLOSED", "labels": ["merged"]},
            {"issue_number": 90, "state": "CLOSED", "labels": ["merged"]},
        ],
    )
    task3 = await manager.get_next_devtest_task("graph-engineering")
    assert task3 == 98


@pytest.mark.asyncio
async def test_reconcile_completed_stories_auto_closes_story_when_all_subtasks_closed(tmp_path: Path):
    """
    Given a parent Story with 2 child subtasks
    When both child subtasks are transitioned to CLOSED
    And reconcile_completed_stories is invoked
    Then the parent story is transitioned to state='CLOSED' and labeled 'dev-implemented'
    """
    db_file = tmp_path / "state.db"
    manager = StateManager(db_file)
    await manager.init_db()

    items = [
        {"issue_number": 100, "item_type": "STORY", "state": "OPEN", "labels": ["architect-processed"]},
        {"issue_number": 101, "parent_issue_id": 100, "item_type": "SUBTASK", "state": "CLOSED", "labels": ["merged"]},
        {"issue_number": 102, "parent_issue_id": 100, "item_type": "SUBTASK", "state": "CLOSED", "labels": ["merged"]},
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    closed_count = await manager.reconcile_completed_stories("graph-engineering")
    assert closed_count == 1

    sdlc_items = await manager.get_sdlc_items("graph-engineering")
    story_map = {item["issue_number"]: item for item in sdlc_items}
    assert story_map[100]["state"] == "CLOSED"
    assert "dev-implemented" in story_map[100]["labels"]


@pytest.mark.asyncio
async def test_reconcile_untracked_closed_issues_marks_missing_issues_closed(tmp_path: Path):
    """
    Given SQLite has 3 open issues for a project
    When a GitHub polling sweep returns only issue #101 as open
    And reconcile_untracked_closed_issues is called with {101}
    Then issues #100 and #102 are transitioned to state='CLOSED' in SQLite
    """
    db_file = tmp_path / "state.db"
    manager = StateManager(db_file)
    await manager.init_db()

    items = [
        {"issue_number": 100, "state": "OPEN", "labels": ["ready-for-dev"]},
        {"issue_number": 101, "state": "OPEN", "labels": ["ready-for-dev"]},
        {"issue_number": 102, "state": "OPEN", "labels": ["queued"]},
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    closed_count = await manager.reconcile_untracked_closed_issues("graph-engineering", {101})
    assert closed_count == 2

    sdlc_items = await manager.get_sdlc_items("graph-engineering")
    item_map = {item["issue_number"]: item for item in sdlc_items}
    assert item_map[100]["state"] == "CLOSED"
    assert item_map[101]["state"] == "OPEN"
    assert item_map[102]["state"] == "CLOSED"


@pytest.mark.asyncio
async def test_get_next_devtest_task_never_returns_parent_story_id(tmp_path: Path):
    """
    Given a parent Story where all subtasks are closed but the story itself was not yet marked closed
    When get_next_devtest_task is invoked
    Then it returns None (never returns the parent story ID for coding)
    """
    db_file = tmp_path / "state.db"
    manager = StateManager(db_file)
    await manager.init_db()

    items = [
        {"issue_number": 109, "item_type": "STORY", "state": "OPEN", "labels": ["architect-processed"]},
        {"issue_number": 111, "parent_issue_id": 109, "item_type": "SUBTASK", "state": "CLOSED", "labels": ["merged"]},
        {"issue_number": 112, "parent_issue_id": 109, "item_type": "SUBTASK", "state": "CLOSED", "labels": ["merged"]},
    ]
    await manager.sync_project_sdlc_items("graph-engineering", items)

    task = await manager.get_next_devtest_task("graph-engineering")
    assert task is None


# ---------------------------------------------------------------------------
# Acceptance Criteria Tests for Issue #164: Clean Label Persistence & Migration
# ---------------------------------------------------------------------------


def test_sanitize_labels_unit():
    """
    Unit tests for sanitize_labels across all input variants.
    """
    from orchestrator.db import sanitize_labels

    # 1. Structured dict objects
    assert sanitize_labels({"name": "ready-for-dev"}) == "ready-for-dev"
    assert sanitize_labels([{"name": "ready-for-dev"}, {"name": "enhancement"}]) == "ready-for-dev, enhancement"

    # 2. Python dict representations (legacy SQLite strings)
    assert sanitize_labels("{'name': 'ready-for-dev'}") == "ready-for-dev"
    assert sanitize_labels("{'name': 'ready-for-dev'}, {'name': 'enhancement'}") == "ready-for-dev, enhancement"
    assert sanitize_labels("{'id': 123, 'name': 'bug', 'color': 'd73a4a'}") == "bug"

    # 3. JSON formatted strings
    assert sanitize_labels('[{"name": "ready-for-dev"}]') == "ready-for-dev"

    # 4. Standard clean strings and lists of strings
    assert sanitize_labels("ready-for-dev, enhancement") == "ready-for-dev, enhancement"
    assert sanitize_labels(["ready-for-dev", "enhancement"]) == "ready-for-dev, enhancement"

    # 5. Empty / None cases
    assert sanitize_labels("") == ""
    assert sanitize_labels(None) == ""
    assert sanitize_labels([]) == ""


@pytest.mark.asyncio
async def test_scenario_clean_label_persistence_no_raw_dict(tmp_path: Path):
    """
    Scenario: Raw Python dict representations must never be persisted in SQLite
      Given raw dict structures or dict representations in labels
      When sync_project_sdlc_items is called
      Then sdlc_items.labels stores clean comma-separated label names
      And raw Python dict representations are never persisted.
    """
    db_file = tmp_path / "state.db"
    manager = StateManager(db_file)
    await manager.init_db()

    items = [
        {
            "issue_number": 201,
            "title": "Item with list of dict labels",
            "state": "OPEN",
            "labels": [{"id": 1, "name": "ready-for-dev"}, {"id": 2, "name": "enhancement"}],
        },
        {
            "issue_number": 202,
            "title": "Item with single dict label",
            "state": "OPEN",
            "labels": {"name": "architect-processed"},
        },
        {
            "issue_number": 203,
            "title": "Item with python dict string repr",
            "state": "OPEN",
            "labels": "{'name': 'queued'}",
        },
    ]
    await manager.sync_project_sdlc_items("label-test", items)

    stored = await manager.get_sdlc_items("label-test")
    item_map = {item["issue_number"]: item for item in stored}

    assert item_map[201]["labels"] == "ready-for-dev, enhancement"
    assert "{" not in item_map[201]["labels"]

    assert item_map[202]["labels"] == "architect-processed"
    assert "{" not in item_map[202]["labels"]

    assert item_map[203]["labels"] == "queued"
    assert "{" not in item_map[203]["labels"]


@pytest.mark.asyncio
async def test_scenario_idempotent_sqlite_migration_cleans_legacy_dicts(tmp_path: Path):
    """
    Scenario: Existing rows with dict representations must be cleaned during database initialization
      Given existing rows in sdlc_items with raw python dict representations
      When init_db() is called
      Then all rows with dict representations are migrated to clean comma-separated label names
      And calling init_db() repeatedly is idempotent.
    """
    db_file = tmp_path / "state.db"
    manager = StateManager(db_file)
    await manager.init_db()

    # Manually inject legacy rows with raw dict strings into sdlc_items
    async with aiosqlite.connect(db_file) as db:
        await db.execute(
            """
            INSERT INTO sdlc_items (project_name, issue_number, title, state, labels, updated_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            ("legacy-proj", 101, "Legacy Story 1", "OPEN", "{'name': 'ready-for-dev'}", 1000.0),
        )
        await db.execute(
            """
            INSERT INTO sdlc_items (project_name, issue_number, title, state, labels, updated_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            ("legacy-proj", 102, "Legacy Story 2", "OPEN", "{'name': 'ready-for-dev'}, {'name': 'enhancement'}", 1000.0),
        )
        await db.execute(
            """
            INSERT INTO sdlc_items (project_name, issue_number, title, state, labels, updated_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            ("legacy-proj", 103, "Already Clean Story", "OPEN", "architect-processed", 1000.0),
        )
        await db.commit()

    # Run migration via init_db()
    await manager.init_db()

    # Verify rows were cleaned
    rows = await manager.get_sdlc_items("legacy-proj")
    row_map = {r["issue_number"]: r for r in rows}

    assert row_map[101]["labels"] == "ready-for-dev"
    assert row_map[102]["labels"] == "ready-for-dev, enhancement"
    assert row_map[103]["labels"] == "architect-processed"

    # Idempotency check: run init_db() again
    await manager.init_db()
    rows_after = await manager.get_sdlc_items("legacy-proj")
    row_map_after = {r["issue_number"]: r for r in rows_after}

    assert row_map_after[101]["labels"] == "ready-for-dev"
    assert row_map_after[102]["labels"] == "ready-for-dev, enhancement"
    assert row_map_after[103]["labels"] == "architect-processed"








