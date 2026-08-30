from __future__ import annotations

from datetime import datetime, timezone, timedelta
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

    # Empty breakdown returns empty dicts
    breakdown = await manager.get_usage_breakdown("nonexistent")
    assert breakdown["by_project"] == {}
    assert breakdown["by_node"] == {}

    # Empty raw events list
    events = await manager.get_token_usage_events("nonexistent")
    assert events == []


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





