The proposed Story Locking mechanism correctly identifies the fatal flaw of blind `limit=1` polling, but the architectural boundaries assigned to `run_devtest_node` in the draft review are flawed. In a decoupled orchestrator, the agent node (`orchestrator/nodes/devtest.py`) must remain a stateless executor. It should not query SQLite or fetch specific issues from GitHub. The orchestration layer (`orchestrator/cli.py` and `orchestrator/poller.py`) must handle queue resolution and pass the strictly locked issue into the node.

### Phase 1: Functional & DX (Developer Experience) Review

**Workflow Analysis & Multi-Agent Node Flow**

1. **Bulk Ingestion:** `orchestrator/poller.py` fetches all open issues and updates the local SQLite replica (`orchestrator/db.py`).
2. **Queue Resolution (The Lock):** `orchestrator/cli.py` calls `db.get_next_devtest_task(project.name)`. The database enforces the Story Lock by returning the lowest ID subtask belonging to the lowest ID active story.
3. **Targeted Fetch:** `orchestrator/cli.py` fetches the exact issue payload from GitHub using `fetch_issue_by_number(target_id)`.
4. **Stateless Execution:** `orchestrator/nodes/devtest.py` receives the exact issue, executes the harness, merges the PR, and returns a completion status.

**Edge Cases & Resilience Strategy**

* **Blocked Pipeline Deadlocks:** If a subtask transitions to `status:blocked` (e.g., repeated CI failures), the SQLite query must exclude it. However, to prevent DevTest from jumping to Story B, the query must still see Story A as the active lock. DevTest will gracefully idle, logging `[WARN] Project locked on blocked Story #90. Awaiting operator intervention.`
* **Standalone Issue Contention:** Standalone bugs (`item_type='STANDALONE'`) must not interrupt an active Story. The query logic must prioritize: `(1) Subtasks of active stories -> (2) Planned stories -> (3) Standalone tasks`.
* **Database Concurrency:** Multiple async loops querying `get_next_devtest_task` simultaneously must use `aiosqlite` with `PRAGMA read_uncommitted = true` or WAL mode to prevent locking overhead during rapid polling.

**Acceptance Criteria (BDD Format)**

```gherkin
Scenario: SQLite enforces strict Story Lock workload resolution
  Given SQLite contains active Story A with Subtasks #93 and #94 in "ready-for-dev"
  And active Story B with Subtask #98 in "ready-for-dev"
  When the concurrency loop queries "get_next_devtest_task"
  Then the database must return ONLY Subtask #93
  And Subtask #98 must be completely ignored until Story A is closed.

Scenario: Centralized concurrency loop injects locked issue into node
  Given "get_next_devtest_task" returns target ID #93
  When "orchestrator/cli.py" processes the project cycle
  Then it must fetch issue #93 directly via "fetch_issue_by_number"
  And it must pass the explicit issue payload to "DevTestNode.execute"
  And the node must not perform independent GitHub polling.

Scenario: Pipeline halts on blocked active story
  Given Story A is locked and its next subtask #93 is labeled "status:blocked"
  When the concurrency loop attempts to fetch the next task
  Then the query must return None
  And the orchestrator must log a terminal warning requiring operator intervention
  And it must NOT unlock or transition to Story B.

```

**CLI UX Guidelines**

* **Active Lock Broadcasting:** Terminal output via `orchestrator/logging.py` must explicitly state the lock constraint to reassure the operator:
* `[INFO] [graph-engineering] Story Lock Active: Parent #90. Dispatched Subtask #93 to devtest.`
* `[WARN] [crosstrainingapp] Story Lock Blocked: Parent #450 requires manual intervention on Subtask #452.`



---

### Phase 2: Architectural & Implementation Plan

**Codebase Impact & Component Updates**

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/db.py` | **Modify** | Implement hardened `get_next_devtest_task` utilizing CTEs (Common Table Expressions) to definitively lock the active parent story. |
| `orchestrator/cli.py` | **Modify** | Update `run_project_cycle` to query the specific locked ID from the database and pass it directly to the DevTest node. |
| `orchestrator/poller.py` | **Modify** | Remove any generic `fetch_issues_with_label(limit=1)` calls tied to DevTest execution routes. |
| `tests/test_db.py` | **Modify** | Add unit tests verifying CTE query logic under cross-story contention scenarios. |

**Technical Constraints & Safeguards**

* **Architectural Boundary Enforcement:** `orchestrator/nodes/devtest.py` must not import `orchestrator/db.py` or query queues. It remains a pure function: `execute(issue, gh, harness) -> NodeResult`.
* **Query Performance:** The sorting logic requires a composite index to avoid table scans: `CREATE INDEX idx_sdlc_lock ON sdlc_items(pipeline_status, parent_issue_id, sequence_order);`.

**Execution Steps**

1. **Database Query Hardening (`orchestrator/db.py`)**
* Inject the required composite indices during `init_db()`.
* Implement `get_next_devtest_task(project_name: str) -> int | None` using a CTE to isolate the lock:
```sql
WITH ActiveStory AS (
    SELECT issue_number AS active_story_id
    FROM sdlc_items
    WHERE project_name = ?
      AND item_type = 'STORY'
      AND UPPER(state) NOT IN ('CLOSED', 'MERGED')
      AND (labels LIKE '%architect-processed%' OR labels LIKE '%status:in-progress%' OR UPPER(state) = 'OPEN')
    ORDER BY sequence_order ASC, issue_number ASC
    LIMIT 1
)
SELECT issue_number
FROM sdlc_items
WHERE project_name = ?
  AND parent_issue_id = (SELECT active_story_id FROM ActiveStory)
  AND UPPER(state) NOT IN ('CLOSED', 'MERGED')
  AND (labels LIKE '%ready-for-dev%' OR labels LIKE '%status:ready-for-dev%')
ORDER BY sequence_order ASC, issue_number ASC
LIMIT 1;
```

2. **Concurrency Loop Refactor (`orchestrator/cli.py`)**
* In `_project_worker_loop`, invoke `locked_id = await db.get_next_devtest_task(project.name)`.
* If `locked_id` is returned, call `gh.get_issue(locked_id)`.
* Pass the exact issue payload to `devtest_node.execute(issue)`.

3. **Node Cleanup (`orchestrator/nodes/devtest.py`)**
* Strip any internal queue management or label-based blind fetching.
* Ensure `promote_next_planned_story()` strictly validates `parent_issue_id` matching before promoting siblings to `ready-for-dev`.

4. **Testing Verification (`tests/test_db.py` & `tests/test_cli.py`)**
* Write `test_db_enforces_story_lock_over_chronological_ids`: Seed DB with Story A (ID 90, Subtasks 95, 96) and Story B (ID 85, Subtasks 88, 89). Assert that if Story A is `in-progress`, Subtask 95 is returned, ignoring the numerically lower Subtask 88.
* Verify the CLI loop accurately passes the locked issue payload to the mocked DevTest node.

---

## 🔗 GitHub Reference
- **GitHub Issue:** [Issue #109](https://github.com/AntaresAndBharani/graph-engineering/issues/109)
- **Label:** `needs-triage`

## 🔨 Subtasks
- [x] feat(db): StateManager.get_next_devtest_task CTE query with strict Story Lock and standalone fallback
- [ ] feat(devtest): deterministic locked issue dispatch in run_devtest_node Phase 3
- [ ] feat(logging, ui): terminal log broadcasting and SDLC widget visual indicator for active Story Lock
- [ ] test(db, devtest): unit and integration tests for CTE story locking, blocked stories, and non-contention