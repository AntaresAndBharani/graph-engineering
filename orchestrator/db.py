from __future__ import annotations

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
            await db.commit()

    async def register_daemon(self, pid: int) -> None:
        """Registers the active daemon process ID and clears any stop requests."""
        now = time.time()
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
