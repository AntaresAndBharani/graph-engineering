from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import aiosqlite


class StateManager:
    """
    Asynchronous state and lock manager using SQLite in WAL mode.
    Handles concurrency, deduplication, and TTL lock recovery.
    """

    def __init__(self, db_path: Path | str):
        expanded = os.path.expandvars(os.path.expanduser(str(db_path)))
        self.db_path = Path(expanded).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        """Initializes SQLite database and tables with WAL mode."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS active_jobs (
                    issue_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    PRIMARY KEY (issue_id, repo, node_type)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS node_runs (
                    node_type TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    last_run_at REAL NOT NULL,
                    PRIMARY KEY (node_type, repo)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daemon_control (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS project_states (
                    project_name TEXT PRIMARY KEY,
                    is_paused INTEGER DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS pr_artifacts (
                    repo TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    node_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (repo, pr_number)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS po_tracking (
                    repo TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    body_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    gherkin_ac TEXT,
                    blockers TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (repo, issue_number)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sdlc_items (
                    project_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    parent_issue_id INTEGER,
                    item_type TEXT DEFAULT 'SUBTASK',
                    sequence_order INTEGER DEFAULT 0,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    labels TEXT,
                    linked_pr INTEGER,
                    pr_status TEXT,
                    pr_ci_details TEXT,
                    created_at REAL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (project_name, issue_number)
                );
                """
            )
            cursor = await db.execute("PRAGMA table_info(sdlc_items);")
            cols = [row[1] for row in await cursor.fetchall()]
            if cols:
                if "parent_issue_id" not in cols:
                    await db.execute("ALTER TABLE sdlc_items ADD COLUMN parent_issue_id INTEGER DEFAULT NULL;")
                if "item_type" not in cols:
                    await db.execute("ALTER TABLE sdlc_items ADD COLUMN item_type TEXT DEFAULT 'SUBTASK';")
                if "sequence_order" not in cols:
                    await db.execute("ALTER TABLE sdlc_items ADD COLUMN sequence_order INTEGER DEFAULT 0;")
                if "created_at" not in cols:
                    await db.execute("ALTER TABLE sdlc_items ADD COLUMN created_at REAL DEFAULT NULL;")
                if "pr_status" not in cols:
                    await db.execute("ALTER TABLE sdlc_items ADD COLUMN pr_status TEXT DEFAULT NULL;")
                if "pr_ci_details" not in cols:
                    await db.execute("ALTER TABLE sdlc_items ADD COLUMN pr_ci_details TEXT DEFAULT NULL;")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdlc_parent ON sdlc_items(project_name, parent_issue_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdlc_lookahead ON sdlc_items(project_name, state, created_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdlc_lock ON sdlc_items(project_name, parent_issue_id, sequence_order, issue_number);"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS anomaly_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    issue_number INTEGER,
                    node_name TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_anomalies_project_time ON anomaly_events(project_name, created_at);"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    harness_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    issue_number INTEGER,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
                );
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_usage_harness_time ON token_usage_events(harness_name, created_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_usage_project_node ON token_usage_events(project_name, node_name, created_at);"
            )
            await db.commit()

    async def register_daemon(self, pid: int) -> None:
        """Registers the active daemon process ID and clears any stop requests."""
        now = time.time()
        # Clean any orphaned RUNNING locks left by prior dead processes
        await self.cleanup_orphaned_running_jobs()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('status', 'RUNNING', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = 'RUNNING', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('pid', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;",
                (str(pid), now),
            )
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('stop_requested', '0', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.commit()

    async def unregister_daemon(self) -> None:
        """Unregisters the daemon on clean shutdown."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('status', 'STOPPED', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = 'STOPPED', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.execute(
                "DELETE FROM daemon_control WHERE key = 'pid';"
            )
            await db.commit()

    async def request_stop(self) -> Optional[int]:
        """
        Signals a safe stop to all running workers.
        Returns the registered daemon PID if currently recorded.
        """
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('stop_requested', '1', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('status', 'STOP_REQUESTED', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = 'STOP_REQUESTED', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.commit()

            cursor = await db.execute("SELECT value FROM daemon_control WHERE key = 'pid';")
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    return int(row[0])
                except (ValueError, TypeError):
                    return None
            return None

    async def is_stop_requested(self) -> bool:
        """Checks whether a safe stop has been requested."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute("SELECT value FROM daemon_control WHERE key = 'stop_requested';")
            row = await cursor.fetchone()
            return bool(row and row[0] == "1")

    async def clear_stop_request(self) -> None:
        """Clears the stop request flag."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('stop_requested', '0', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.execute(
                "UPDATE daemon_control SET value = 'STOPPED', updated_at = ? WHERE key = 'status' AND value = 'STOP_REQUESTED';",
                (now,),
            )
            await db.commit()

    async def request_reload(self) -> Optional[int]:
        """
        Signals an in-memory hot-reload to the running daemon.
        Returns the registered daemon PID if currently active.
        """
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('reload_requested', '1', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.commit()

            cursor = await db.execute("SELECT value FROM daemon_control WHERE key = 'pid';")
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    return int(row[0])
                except (ValueError, TypeError):
                    return None
            return None

    async def is_reload_requested(self) -> bool:
        """Checks whether an in-memory hot-reload has been requested."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute("SELECT value FROM daemon_control WHERE key = 'reload_requested';")
            row = await cursor.fetchone()
            return bool(row and row[0] == "1")

    async def clear_reload_request(self) -> None:
        """Clears the reload request flag."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "INSERT INTO daemon_control (key, value, updated_at) VALUES ('reload_requested', '0', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;",
                (now,),
            )
            await db.commit()

    async def get_daemon_info(self) -> Dict[str, Any]:
        """Returns the current status, PID, and stop requested flag for daemon."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute("SELECT key, value FROM daemon_control;")
            rows = await cursor.fetchall()
            return {r[0]: r[1] for r in rows}

    async def acquire_lock(
        self,
        issue_id: str | int,
        repo: str,
        node_type: str,
        ttl_minutes: int = 30,
    ) -> bool:
        """
        Attempts to acquire an execution lock for (issue_id, repo, node_type).
        Returns True if acquired, False if currently locked by an unexpired job.
        """
        issue_str = str(issue_id)
        now = time.time()
        expires_at = now + (ttl_minutes * 60)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")

            # Check existing lock
            cursor = await db.execute(
                """
                SELECT status, expires_at, retry_count
                FROM active_jobs
                WHERE issue_id = ? AND repo = ? AND node_type = ?
                """,
                (issue_str, repo, node_type),
            )
            row = await cursor.fetchone()

            if row:
                status, existing_expires, retry_count = row
                # If currently running and unexpired, lock cannot be acquired
                if status == "RUNNING" and existing_expires > now:
                    return False

                # Otherwise update existing entry
                await db.execute(
                    """
                    UPDATE active_jobs
                    SET status = 'RUNNING', started_at = ?, expires_at = ?, error_message = NULL
                    WHERE issue_id = ? AND repo = ? AND node_type = ?
                    """,
                    (now, expires_at, issue_str, repo, node_type),
                )
            else:
                # Insert new lock
                await db.execute(
                    """
                    INSERT INTO active_jobs (issue_id, repo, node_type, status, started_at, expires_at, retry_count)
                    VALUES (?, ?, ?, 'RUNNING', ?, ?, 0)
                    """,
                    (issue_str, repo, node_type, now, expires_at),
                )

            await db.commit()
            return True

    async def release_lock(
        self,
        issue_id: str | int,
        repo: str,
        node_type: str,
    ) -> None:
        """Releases/deletes the lock upon successful task completion."""
        issue_str = str(issue_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                DELETE FROM active_jobs
                WHERE issue_id = ? AND repo = ? AND node_type = ?
                """,
                (issue_str, repo, node_type),
            )
            await db.commit()

    async def fail_job(
        self,
        issue_id: str | int,
        repo: str,
        node_type: str,
        error_message: str,
    ) -> int:
        """Marks a job as FAILED and increments its retry count."""
        issue_str = str(issue_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                UPDATE active_jobs
                SET status = 'FAILED',
                    retry_count = retry_count + 1,
                    error_message = ?
                WHERE issue_id = ? AND repo = ? AND node_type = ?
                """,
                (error_message, issue_str, repo, node_type),
            )
            await db.commit()

            cursor = await db.execute(
                """
                SELECT retry_count FROM active_jobs
                WHERE issue_id = ? AND repo = ? AND node_type = ?
                """,
                (issue_str, repo, node_type),
            )
            row = await cursor.fetchone()
            return row[0] if row else 1

    async def cleanup_orphaned_running_jobs(self) -> int:
        """
        Reclaims any RUNNING locks left behind by dead, interrupted, or prior daemon processes.
        Transitions their status to FAILED to prevent 30-minute deadlock freezes.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                UPDATE active_jobs
                SET status = 'FAILED', error_message = 'Orphaned lock reclaimed on startup'
                WHERE status = 'RUNNING'
                """
            )
            count = cursor.rowcount
            await db.commit()
            return count if count > 0 else 0

    async def cleanup_expired_locks(self) -> int:
        """Identifies expired RUNNING locks and updates them to FAILED."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                UPDATE active_jobs
                SET status = 'FAILED', error_message = 'Lock TTL Expired (Daemon Interruption)'
                WHERE status = 'RUNNING' AND expires_at <= ?
                """,
                (now,),
            )
            count = cursor.rowcount
            await db.commit()
            return count if count > 0 else 0

    async def get_active_jobs(self) -> List[Dict[str, Any]]:
        """Returns all currently active or failed jobs."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT issue_id, repo, node_type, status, started_at, expires_at, retry_count, error_message
                FROM active_jobs
                ORDER BY started_at DESC
                """
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def clear_all_locks(self, stale_only: bool = False) -> int:
        """Cleans locks from database."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            if stale_only:
                cursor = await db.execute(
                    "DELETE FROM active_jobs WHERE status = 'FAILED' OR expires_at <= ?",
                    (now,),
                )
            else:
                cursor = await db.execute("DELETE FROM active_jobs")
            count = cursor.rowcount
            await db.commit()
            return count if count > 0 else 0

    async def get_last_run(self, node_type: str, repo: str) -> Optional[float]:
        """Retrieves the timestamp of the last execution for a node_type and repo."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                "SELECT last_run_at FROM node_runs WHERE node_type = ? AND repo = ?",
                (node_type, repo),
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else None

    async def record_node_run(self, node_type: str, repo: str) -> None:
        """Records the current timestamp as the last run time for a node_type and repo."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO node_runs (node_type, repo, last_run_at)
                VALUES (?, ?, ?)
                ON CONFLICT(node_type, repo) DO UPDATE SET last_run_at = excluded.last_run_at
                """,
                (node_type, repo, now),
            )
            await db.commit()

    async def pause_project(self, project_name: str) -> None:
        """Sets a project to paused state in SQLite database."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO project_states (project_name, is_paused, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(project_name) DO UPDATE SET is_paused = 1, updated_at = excluded.updated_at
                """,
                (project_name, now),
            )
            await db.commit()

    async def resume_project(self, project_name: str) -> None:
        """Sets a project to active (resumed) state in SQLite database."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO project_states (project_name, is_paused, updated_at)
                VALUES (?, 0, ?)
                ON CONFLICT(project_name) DO UPDATE SET is_paused = 0, updated_at = excluded.updated_at
                """,
                (project_name, now),
            )
            await db.commit()

    async def is_project_paused(self, project_name: str) -> bool:
        """Checks if a project has been paused by the user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                "SELECT is_paused FROM project_states WHERE project_name = ?",
                (project_name,),
            )
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

    async def get_paused_projects(self) -> set[str]:
        """Returns a set of all currently paused project names."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                "SELECT project_name FROM project_states WHERE is_paused = 1"
            )
            rows = await cursor.fetchall()
            return {row[0] for row in rows}

    # =========================================================================
    # Blackboard Pattern: PR Artifacts & Cross-Node Context Storage
    # =========================================================================

    async def upsert_pr_artifact(
        self,
        repo: str,
        pr_number: int,
        node_name: str,
        status: str,
        comment: str,
    ) -> None:
        """
        Stores or updates a PR review/decision artifact in the Blackboard database.
        Idempotent operation (INSERT OR REPLACE).
        """
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO pr_artifacts (repo, pr_number, node_name, status, comment, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo, pr_number) DO UPDATE SET
                    node_name = excluded.node_name,
                    status = excluded.status,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at
                """,
                (repo, pr_number, node_name, status, comment, now),
            )
            await db.commit()

    async def get_pr_artifact(self, repo: str, pr_number: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves a PR artifact by repository and PR number from the Blackboard database.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT repo, pr_number, node_name, status, comment, updated_at
                FROM pr_artifacts
                WHERE repo = ? AND pr_number = ?
                """,
                (repo, pr_number),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_pr_artifact(self, repo: str, pr_number: int) -> None:
        """
        Removes a PR artifact from the Blackboard database.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "DELETE FROM pr_artifacts WHERE repo = ? AND pr_number = ?",
                (repo, pr_number),
            )
            await db.commit()

    async def list_pr_artifacts(self, repo: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists all PR artifacts recorded on the Blackboard, optionally filtered by repo.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            if repo:
                cursor = await db.execute(
                    """
                    SELECT repo, pr_number, node_name, status, comment, updated_at
                    FROM pr_artifacts
                    WHERE repo = ?
                    ORDER BY updated_at DESC
                    """,
                    (repo,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT repo, pr_number, node_name, status, comment, updated_at
                    FROM pr_artifacts
                    ORDER BY updated_at DESC
                    """
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # =========================================================================
    # Blackboard Pattern: PO Tracking (Product Owner Proxy Issue Evaluation)
    # =========================================================================

    async def upsert_po_tracking(
        self,
        repo: str,
        issue_number: int,
        body_hash: str,
        status: str,
        gherkin_ac: Optional[str] = None,
        blockers: Optional[str] = None,
    ) -> None:
        """
        Stores or updates a PO tracking record in the Blackboard database.
        Idempotent operation (INSERT OR REPLACE).
        """
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO po_tracking (repo, issue_number, body_hash, status, gherkin_ac, blockers, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo, issue_number) DO UPDATE SET
                    body_hash = excluded.body_hash,
                    status = excluded.status,
                    gherkin_ac = excluded.gherkin_ac,
                    blockers = excluded.blockers,
                    updated_at = excluded.updated_at
                """,
                (repo, issue_number, body_hash, status, gherkin_ac, blockers, now),
            )
            await db.commit()

    async def get_po_tracking(self, repo: str, issue_number: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves a PO tracking record by repository and issue number.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT repo, issue_number, body_hash, status, gherkin_ac, blockers, updated_at
                FROM po_tracking
                WHERE repo = ? AND issue_number = ?
                """,
                (repo, issue_number),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_po_tracking(self, repo: str, issue_number: int) -> None:
        """
        Removes a PO tracking record from the Blackboard database.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "DELETE FROM po_tracking WHERE repo = ? AND issue_number = ?",
                (repo, issue_number),
            )
            await db.commit()

    async def list_po_trackings(self, repo: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists all PO tracking records from the Blackboard, optionally filtered by repo.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            if repo:
                cursor = await db.execute(
                    """
                    SELECT repo, issue_number, body_hash, status, gherkin_ac, blockers, updated_at
                    FROM po_tracking
                    WHERE repo = ?
                    ORDER BY updated_at DESC
                    """,
                    (repo,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT repo, issue_number, body_hash, status, gherkin_ac, blockers, updated_at
                    FROM po_tracking
                    ORDER BY updated_at DESC
                    """
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # =========================================================================
    # SDLC Items & Anomaly Memory Layer (Zero-HTTP UI Architecture)
    # =========================================================================

    async def sync_project_sdlc_items(
        self,
        project_name: str,
        items: List[Dict[str, Any]],
    ) -> None:
        """
        Upserts active SDLC items (issues/subtasks/PRs) for a project.
        """
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            for item in items:
                issue_number = int(item.get("issue_number") or item.get("id") or item.get("number", 0))
                title = str(item.get("title", ""))
                state = str(item.get("state") or item.get("status") or "OPEN")
                raw_labels = item.get("labels")
                if isinstance(raw_labels, (list, tuple, set)):
                    labels_str = ", ".join(str(lbl) for lbl in raw_labels)
                else:
                    labels_str = str(raw_labels) if raw_labels is not None else ""
                linked_pr = item.get("linked_pr")
                linked_pr_val = int(linked_pr) if linked_pr is not None else None
                pr_status = item.get("pr_status")
                pr_status_val = str(pr_status) if pr_status is not None else None
                pr_ci_details = item.get("pr_ci_details")
                pr_ci_details_val = str(pr_ci_details) if pr_ci_details is not None else None
                updated_at = float(item.get("updated_at", now))
                raw_created = item.get("created_at")
                created_at_val = float(raw_created) if raw_created is not None else updated_at
                parent_issue_id = item.get("parent_issue_id")
                parent_val = int(parent_issue_id) if parent_issue_id is not None else None
                item_type = str(item.get("item_type") or "SUBTASK").upper()
                sequence_order = int(item.get("sequence_order", 0))

                await db.execute(
                    """
                    INSERT INTO sdlc_items (
                        project_name, issue_number, parent_issue_id, item_type, sequence_order,
                        title, state, labels, linked_pr, pr_status, pr_ci_details, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_name, issue_number) DO UPDATE SET
                        parent_issue_id = COALESCE(excluded.parent_issue_id, sdlc_items.parent_issue_id),
                        item_type = excluded.item_type,
                        sequence_order = excluded.sequence_order,
                        title = excluded.title,
                        state = excluded.state,
                        labels = excluded.labels,
                        linked_pr = excluded.linked_pr,
                        pr_status = COALESCE(excluded.pr_status, sdlc_items.pr_status),
                        pr_ci_details = COALESCE(excluded.pr_ci_details, sdlc_items.pr_ci_details),
                        created_at = COALESCE(sdlc_items.created_at, excluded.created_at),
                        updated_at = excluded.updated_at
                    """,
                    (project_name, issue_number, parent_val, item_type, sequence_order, title, state, labels_str, linked_pr_val, pr_status_val, pr_ci_details_val, created_at_val, updated_at),
                )
            await db.commit()

    async def get_sdlc_items(self, project_name: str) -> List[Dict[str, Any]]:
        """
        Retrieves all active SDLC items for a specific project from SQLite.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT project_name, issue_number, parent_issue_id, item_type, sequence_order,
                       title, state, labels, linked_pr, pr_status, pr_ci_details, created_at, updated_at
                FROM sdlc_items
                WHERE project_name = ?
                ORDER BY issue_number ASC
                """,
                (project_name,),
            )
            rows = await cursor.fetchall()
            items = []
            for row in rows:
                r = dict(row)
                if "status" not in r:
                    r["status"] = r.get("state")
                items.append(r)
            return items

    async def get_active_sdlc_hierarchy(self, project_name: str) -> List[Dict[str, Any]]:
        """
        Retrieves active SDLC hierarchy for a project grouped by parent story with smart visibility.
        - Queries all sdlc_items for the project.
        - Groups subtasks under parent_issue_id in Python.
        - Smart Visibility: a story tree is excluded ONLY when the parent AND 100% of its
          child subtasks are CLOSED/MERGED. If any child is open, the parent is retained as root.
        - Subtasks are ordered by sequence_order ASC, issue_number ASC.
        """
        def _is_closed(state: Optional[str]) -> bool:
            if not state:
                return False
            s = str(state).strip().upper()
            return s in ("CLOSED", "MERGED", "DONE", "STATUS:CLOSED", "STATUS:MERGED", "STATUS:DONE")

        rows = await self.get_sdlc_items(project_name)
        if not rows:
            return []

        items = []
        for row in rows:
            r = dict(row)
            if "status" not in r:
                r["status"] = r.get("state")
            r["subtasks"] = []
            r["children"] = []
            items.append(r)

        items_by_id = {item["issue_number"]: item for item in items}
        roots: List[Dict[str, Any]] = []
        children_by_parent: Dict[int, List[Dict[str, Any]]] = {}

        for item in items:
            parent_id = item.get("parent_issue_id")
            if parent_id is not None and parent_id in items_by_id and parent_id != item["issue_number"]:
                children_by_parent.setdefault(parent_id, []).append(item)
            else:
                roots.append(item)

        # Assign sorted children to roots
        for root in roots:
            r_id = root["issue_number"]
            subtasks = children_by_parent.get(r_id, [])
            subtasks.sort(key=lambda s: (s.get("sequence_order", 0) or 0, s.get("issue_number", 0) or 0))
            root["subtasks"] = subtasks
            root["children"] = subtasks

        # Smart Visibility Filter
        active_hierarchy: List[Dict[str, Any]] = []
        for root in roots:
            root_closed = _is_closed(root.get("state"))
            subtasks = root.get("subtasks", [])
            if subtasks:
                all_subtasks_closed = all(_is_closed(c.get("state")) for c in subtasks)
            else:
                all_subtasks_closed = True

            # Exclude ONLY when parent AND 100% of children are closed
            if root_closed and all_subtasks_closed:
                continue

            active_hierarchy.append(root)

        active_hierarchy.sort(key=lambda r: (r.get("sequence_order", 0) or 0, r.get("issue_number", 0) or 0))
        return active_hierarchy

    async def get_active_story(self, project_name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the active open/in-progress story for a project if one exists.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT project_name, issue_number, parent_issue_id, item_type, sequence_order,
                       title, state, labels, linked_pr, pr_status, pr_ci_details, created_at, updated_at
                FROM sdlc_items
                WHERE project_name = ? AND item_type = 'STORY' AND state != 'CLOSED' AND state != 'MERGED'
                  AND UPPER(state) NOT IN ('PLANNED', 'STATUS:PLANNED')
                ORDER BY sequence_order ASC, issue_number ASC
                LIMIT 1
                """,
                (project_name,),
            )
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                if "status" not in res:
                    res["status"] = res.get("state")
                return res
            return None

    async def get_pending_subtasks(self, project_name: str, parent_id: int) -> List[Dict[str, Any]]:
        """
        Retrieves all child subtasks for a parent story ordered by sequence and issue number.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT project_name, issue_number, parent_issue_id, item_type, sequence_order,
                       title, state, labels, linked_pr, pr_status, pr_ci_details, created_at, updated_at
                FROM sdlc_items
                WHERE project_name = ? AND parent_issue_id = ?
                ORDER BY sequence_order ASC, issue_number ASC
                """,
                (project_name, parent_id),
            )
            rows = await cursor.fetchall()
            items = []
            for row in rows:
                r = dict(row)
                if "status" not in r:
                    r["status"] = r.get("state")
                items.append(r)
            return items

    async def get_next_queued_subtask(self, project_name: str, parent_id: int) -> Optional[Dict[str, Any]]:
        """
        Finds the next queued subtask waiting to be promoted for a parent story.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT project_name, issue_number, parent_issue_id, item_type, sequence_order,
                       title, state, labels, linked_pr, pr_status, pr_ci_details, created_at, updated_at
                FROM sdlc_items
                WHERE project_name = ? AND parent_issue_id = ? AND state != 'CLOSED' AND state != 'MERGED'
                  AND (labels LIKE '%queued%' OR labels LIKE '%status:queued%')
                ORDER BY sequence_order ASC, issue_number ASC
                LIMIT 1
                """,
                (project_name, parent_id),
            )
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                if "status" not in res:
                    res["status"] = res.get("state")
                return res
            return None

    async def count_planned_stories(self, project_name: str) -> int:
        """
        Counts the number of planned stories/items with status 'PLANNED' for a given project.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM sdlc_items
                WHERE project_name = ? AND (UPPER(state) = 'PLANNED' OR UPPER(state) = 'STATUS:PLANNED')
                """,
                (project_name,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def get_oldest_planned_story(self, project_name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the oldest planned story with status 'PLANNED' (earliest created_at timestamp)
        for a given project.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT project_name, issue_number, parent_issue_id, item_type, sequence_order,
                       title, state, labels, linked_pr, pr_status, pr_ci_details, created_at, updated_at
                FROM sdlc_items
                WHERE project_name = ? AND (UPPER(state) = 'PLANNED' OR UPPER(state) = 'STATUS:PLANNED')
                ORDER BY COALESCE(created_at, updated_at) ASC, sequence_order ASC, issue_number ASC
                LIMIT 1
                """,
                (project_name,),
            )
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                if "status" not in res:
                    res["status"] = res.get("state")
                return res
            return None

    async def promote_planned_story(
        self,
        project_name: str,
        story_id: int | str,
        new_status: str = "ACTIVE",
    ) -> bool:
        """
        Promotes a planned story's status to 'ACTIVE' (or specified new_status) in an atomic WAL transaction.
        Returns True if the story was found and updated, False otherwise.
        """
        issue_number = int(story_id)
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                UPDATE sdlc_items
                SET state = ?, updated_at = ?
                WHERE project_name = ? AND issue_number = ?
                """,
                (new_status, now, project_name, issue_number),
            )
            updated = cursor.rowcount > 0
            await db.commit()
            return updated

    async def get_active_locked_story_id(self, project_name: str) -> Optional[int]:
        """
        Resolves the single active locked parent story ID for a project using CTE logic.
        Returns the issue_number of the active locked story if one exists, else None.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            active_story_query = """
                WITH ActiveStory AS (
                    SELECT issue_number AS active_story_id
                    FROM sdlc_items
                    WHERE project_name = ?
                      AND UPPER(item_type) = 'STORY'
                      AND UPPER(state) NOT IN ('CLOSED', 'MERGED', 'DONE', 'STATUS:CLOSED', 'STATUS:MERGED', 'STATUS:DONE', 'PLANNED', 'STATUS:PLANNED')
                      AND (
                          EXISTS (
                              SELECT 1 FROM sdlc_items sub
                              WHERE sub.project_name = sdlc_items.project_name
                                AND sub.parent_issue_id = sdlc_items.issue_number
                                AND UPPER(sub.state) NOT IN ('CLOSED', 'MERGED', 'DONE', 'STATUS:CLOSED', 'STATUS:MERGED', 'STATUS:DONE')
                          )
                          OR NOT EXISTS (
                              SELECT 1 FROM sdlc_items sub2
                              WHERE sub2.project_name = sdlc_items.project_name
                                AND sub2.parent_issue_id = sdlc_items.issue_number
                          )
                      )
                    ORDER BY sequence_order ASC, issue_number ASC
                    LIMIT 1
                )
                SELECT active_story_id FROM ActiveStory;
            """
            cursor = await db.execute(active_story_query, (project_name,))
            row = await cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
            return None

    async def get_next_devtest_task(self, project_name: str) -> Optional[int]:
        """
        Deterministic CTE query that resolves the single active locked parent story
        and returns only its next sequential subtask, with blocked-story quarantine
        and standalone/planned-promotion fallbacks.

        Workflow:
        1. CTE ActiveStory: Resolves the single active (non-CLOSED/MERGED, non-PLANNED) STORY item
           that has uncompleted subtasks (or is open), ordered by sequence_order ASC, issue_number ASC.
        2. If an active story exists:
           - Returns the next sequential uncompleted subtask under parent_issue_id = active_story_id.
           - If the next subtask is 'blocked' / 'orchestration-failed', returns None (lock is held, no fallback).
           - If the next subtask is 'ready-for-dev', returns its issue_number.
           - If the next subtask is not 'ready-for-dev' (e.g. queued or in-progress), returns None (lock held).
        3. If no active story is locked:
           - Fallback 1: Standalone tasks with parent_issue_id IS NULL and 'ready-for-dev',
             ordered by sequence_order ASC, issue_number ASC.
           - Fallback 2: Oldest planned story promotion: promotes the oldest PLANNED story to ACTIVE,
             unlocks its first queued subtask to 'ready-for-dev', and returns that subtask's issue_number.
        4. Returns None if no eligible task is found.
        """
        def _is_blocked(state: Optional[str], labels: Optional[str]) -> bool:
            if state:
                s = str(state).strip().upper()
                if any(b in s for b in ("BLOCKED", "ORCHESTRATION-FAILED", "ORCHESTRATION_FAILED", "ORCHESTRATION:FAILED")):
                    return True
            if labels:
                lbls = str(labels).lower()
                if any(b in lbls for b in ("blocked", "status:blocked", "orchestration-failed", "status:orchestration-failed")):
                    return True
            return False

        def _is_in_progress(state: Optional[str], labels: Optional[str]) -> bool:
            if state:
                s = str(state).strip().upper()
                if any(p in s for p in ("IN_PROGRESS", "IN-PROGRESS", "STATUS:IN-PROGRESS")):
                    return True
            if labels:
                lbls = str(labels).lower()
                if any(p in lbls for p in ("in-progress", "status:in-progress")):
                    return True
            return False

        def _is_ready_for_dev(state: Optional[str], labels: Optional[str]) -> bool:
            if labels:
                lbls = str(labels).lower()
                if any(r in lbls for r in ("ready-for-dev", "status:ready-for-dev")):
                    return True
            if state:
                s = str(state).strip().upper()
                if s in ("READY-FOR-DEV", "STATUS:READY-FOR-DEV", "READY_FOR_DEV"):
                    return True
            return False

        active_story_id = await self.get_active_locked_story_id(project_name)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")

            if active_story_id is not None:
                # Active Story Lock is held! Query next sequential uncompleted child subtask
                subtask_cursor = await db.execute(
                    """
                    SELECT issue_number, state, labels, sequence_order
                    FROM sdlc_items
                    WHERE project_name = ?
                      AND parent_issue_id = ?
                      AND UPPER(state) NOT IN ('CLOSED', 'MERGED', 'DONE', 'STATUS:CLOSED', 'STATUS:MERGED', 'STATUS:DONE')
                    ORDER BY sequence_order ASC, issue_number ASC
                    LIMIT 1;
                    """,
                    (project_name, active_story_id),
                )
                subtask_row = await subtask_cursor.fetchone()
                if subtask_row:
                    sub_id = int(subtask_row["issue_number"])
                    sub_state = subtask_row["state"]
                    sub_labels = subtask_row["labels"]

                    if _is_blocked(sub_state, sub_labels) or _is_in_progress(sub_state, sub_labels):
                        return None

                    # Return lowest uncompleted subtask in active story (queued or ready-for-dev)
                    return sub_id
                else:
                    # Active story has no child subtasks; check if story itself is unblocked and not in-progress
                    story_cursor = await db.execute(
                        """
                        SELECT issue_number, state, labels
                        FROM sdlc_items
                        WHERE project_name = ? AND issue_number = ?
                        """,
                        (project_name, active_story_id),
                    )
                    story_row = await story_cursor.fetchone()
                    if story_row:
                        if _is_blocked(story_row["state"], story_row["labels"]) or _is_in_progress(story_row["state"], story_row["labels"]):
                            return None
                        return int(story_row["issue_number"])
                    return None

            # 2. Fallback 1: Standalone tasks (parent_issue_id IS NULL and not a STORY)
            standalone_cursor = await db.execute(
                """
                SELECT issue_number, state, labels, sequence_order
                FROM sdlc_items
                WHERE project_name = ?
                  AND parent_issue_id IS NULL
                  AND (item_type IS NULL OR UPPER(item_type) != 'STORY')
                  AND UPPER(state) NOT IN ('CLOSED', 'MERGED', 'DONE', 'STATUS:CLOSED', 'STATUS:MERGED', 'STATUS:DONE', 'PLANNED', 'STATUS:PLANNED')
                  AND (labels LIKE '%ready-for-dev%' OR labels LIKE '%status:ready-for-dev%' OR UPPER(state) IN ('READY-FOR-DEV', 'STATUS:READY-FOR-DEV'))
                ORDER BY sequence_order ASC, issue_number ASC
                LIMIT 1;
                """,
                (project_name,),
            )
            standalone_row = await standalone_cursor.fetchone()
            if standalone_row:
                s_id = int(standalone_row["issue_number"])
                s_state = standalone_row["state"]
                s_labels = standalone_row["labels"]
                if not _is_blocked(s_state, s_labels) and not _is_in_progress(s_state, s_labels):
                    return s_id
                return None

            # 3. Fallback 2: Oldest planned story promotion
            planned_cursor = await db.execute(
                """
                SELECT issue_number, title, state, labels, created_at, updated_at
                FROM sdlc_items
                WHERE project_name = ? AND (UPPER(state) = 'PLANNED' OR UPPER(state) = 'STATUS:PLANNED')
                ORDER BY COALESCE(created_at, updated_at) ASC, sequence_order ASC, issue_number ASC
                LIMIT 1;
                """,
                (project_name,),
            )
            planned_row = await planned_cursor.fetchone()
            if planned_row:
                planned_story_id = int(planned_row["issue_number"])
                now = time.time()
                await db.execute(
                    """
                    UPDATE sdlc_items
                    SET state = 'ACTIVE', updated_at = ?
                    WHERE project_name = ? AND issue_number = ?
                    """,
                    (now, project_name, planned_story_id),
                )
                await db.commit()

                # Find its first unclosed child subtask
                p_sub_cursor = await db.execute(
                    """
                    SELECT issue_number, state, labels, sequence_order
                    FROM sdlc_items
                    WHERE project_name = ?
                      AND parent_issue_id = ?
                      AND UPPER(state) NOT IN ('CLOSED', 'MERGED', 'DONE', 'STATUS:CLOSED', 'STATUS:MERGED', 'STATUS:DONE')
                    ORDER BY sequence_order ASC, issue_number ASC
                    LIMIT 1;
                    """,
                    (project_name, planned_story_id),
                )
                p_sub_row = await p_sub_cursor.fetchone()
                if p_sub_row:
                    p_sub_id = int(p_sub_row["issue_number"])
                    p_sub_state = p_sub_row["state"]
                    p_sub_labels = p_sub_row["labels"] or ""

                    # If not already ready-for-dev, promote/unlock it
                    if not _is_ready_for_dev(p_sub_state, p_sub_labels) and not _is_blocked(p_sub_state, p_sub_labels):
                        new_labels = p_sub_labels
                        if "status:queued" in new_labels:
                            new_labels = new_labels.replace("status:queued", "status:ready-for-dev")
                        elif "queued" in new_labels:
                            new_labels = new_labels.replace("queued", "ready-for-dev")
                        elif not new_labels.strip():
                            new_labels = "ready-for-dev"
                        else:
                            new_labels = f"{new_labels}, ready-for-dev"

                        await db.execute(
                            """
                            UPDATE sdlc_items
                            SET labels = ?, state = 'OPEN', updated_at = ?
                            WHERE project_name = ? AND issue_number = ?
                            """,
                            (new_labels, now, project_name, p_sub_id),
                        )
                        await db.commit()
                        p_sub_labels = new_labels
                        p_sub_state = "OPEN"

                    if _is_blocked(p_sub_state, p_sub_labels):
                        return None
                    return p_sub_id
                else:
                    return planned_story_id

            return None

    async def record_anomaly_event(
        self,
        project_name: str,
        node_name: str,
        error_type: str,
        error_message: str,
        issue_number: Optional[int] = None,
    ) -> None:
        """
        Records an execution anomaly / retry event with timestamp into anomaly_events.
        """
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO anomaly_events (project_name, issue_number, node_name, error_type, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_name, issue_number, node_name, error_type, error_message, now),
            )
            await db.commit()

    async def get_recent_anomalies(
        self,
        project_name: Optional[str] = None,
        hours: float = 24.0,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves anomaly events within the given hours window (default 24.0h),
        optionally filtered by project_name.
        """
        cutoff = time.time() - (hours * 3600.0)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            if project_name:
                cursor = await db.execute(
                    """
                    SELECT id, project_name, issue_number, node_name, error_type, error_message, created_at
                    FROM anomaly_events
                    WHERE project_name = ? AND created_at >= ?
                    ORDER BY created_at DESC
                    """,
                    (project_name, cutoff),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT id, project_name, issue_number, node_name, error_type, error_message, created_at
                    FROM anomaly_events
                    WHERE created_at >= ?
                    ORDER BY created_at DESC
                    """,
                    (cutoff,),
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_project_state_fingerprint(self, project_name: Optional[str]) -> str:
        """
        Returns a lightweight composite string fingerprint of the project's current
        SDLC items, anomalies, and token usage events.
        Enables non-destructive UI caching to avoid redundant SQLite re-queries.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=5000;")
            if not project_name:
                cursor = await db.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM token_usage_events;")
                t_row = await cursor.fetchone()
                t_part = f"{t_row[0]}:{t_row[1]}" if t_row else "0:0"
                return f"global:{t_part}"

            # 1. SDLC items fingerprint
            cursor = await db.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), 0) FROM sdlc_items WHERE project_name = ?;",
                (project_name,),
            )
            sdlc_row = await cursor.fetchone()
            sdlc_part = f"{sdlc_row[0]}:{sdlc_row[1]}" if sdlc_row else "0:0"

            # 2. Anomaly events fingerprint
            cursor = await db.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM anomaly_events WHERE project_name = ?;",
                (project_name,),
            )
            anomaly_row = await cursor.fetchone()
            anomaly_part = f"{anomaly_row[0]}:{anomaly_row[1]}" if anomaly_row else "0:0"

            # 3. Token usage events fingerprint
            cursor = await db.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM token_usage_events WHERE project_name = ?;",
                (project_name,),
            )
            token_row = await cursor.fetchone()
            token_part = f"{token_row[0]}:{token_row[1]}" if token_row else "0:0"

            return f"{project_name}:{sdlc_part}:{anomaly_part}:{token_part}"

    # =========================================================================
    # Token Usage Events & Quota Ledger (Global Multi-Window Gating)
    # =========================================================================

    async def record_token_usage_event(
        self,
        harness_name: str,
        model_name: str,
        project_name: str,
        node_name: str,
        issue_number: Optional[int],
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        created_at: Optional[str | datetime | float | int] = None,
    ) -> None:
        """
        Records a token usage event with UTC timestamp into token_usage_events.
        """
        if created_at is None:
            created_at_val = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(created_at, datetime):
            if created_at.tzinfo is not None:
                created_at_val = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_at_val = created_at.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(created_at, (int, float)):
            created_at_val = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_at_val = str(created_at)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """
                INSERT INTO token_usage_events (
                    harness_name, model_name, project_name, node_name, issue_number,
                    prompt_tokens, completion_tokens, total_tokens, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    harness_name,
                    model_name,
                    project_name,
                    node_name,
                    issue_number,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    created_at_val,
                ),
            )
            await db.commit()

    async def get_window_token_usage(
        self,
        harness_name: str,
        window_hours: float = 1.0,
    ) -> int:
        """
        Sums total_tokens for events where created_at >= now(UTC) - window_hours,
        using strict UTC comparisons.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            cursor = await db.execute(
                """
                SELECT SUM(total_tokens)
                FROM token_usage_events
                WHERE harness_name = ? AND created_at >= ?
                """,
                (harness_name, cutoff_str),
            )
            row = await cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
            return 0

    async def get_usage_breakdown(
        self,
        harness_name: str,
        window_hours: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Returns per-project_name and per-node_name token sums within the window for the given harness.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")

            cursor_proj = await db.execute(
                """
                SELECT project_name, SUM(total_tokens)
                FROM token_usage_events
                WHERE harness_name = ? AND created_at >= ?
                GROUP BY project_name
                """,
                (harness_name, cutoff_str),
            )
            proj_rows = await cursor_proj.fetchall()
            by_project = {row[0]: int(row[1]) for row in proj_rows}

            cursor_node = await db.execute(
                """
                SELECT node_name, SUM(total_tokens)
                FROM token_usage_events
                WHERE harness_name = ? AND created_at >= ?
                GROUP BY node_name
                """,
                (harness_name, cutoff_str),
            )
            node_rows = await cursor_node.fetchall()
            by_node = {row[0]: int(row[1]) for row in node_rows}

            return {
                "by_project": by_project,
                "by_node": by_node,
                "projects": by_project,
                "nodes": by_node,
            }

    async def get_token_usage_events(
        self,
        harness_name: Optional[str] = None,
        window_hours: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves raw token usage events, optionally filtered by harness_name and window_hours.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")

            query = (
                "SELECT id, harness_name, model_name, project_name, node_name, "
                "issue_number, prompt_tokens, completion_tokens, total_tokens, created_at "
                "FROM token_usage_events"
            )
            params: list[Any] = []
            conditions: list[str] = []

            if harness_name:
                conditions.append("harness_name = ?")
                params.append(harness_name)

            if window_hours is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
                cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
                conditions.append("created_at >= ?")
                params.append(cutoff_str)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY created_at ASC"

            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]





