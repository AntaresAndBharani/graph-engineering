### Phase 1: Functional & DX (Developer Experience) Review

**Workflow Analysis & SDLC Observability Journey**
Transforming the `SDLCProgressWidget` from a flat list into a hierarchically ordered, PR-aware project management board requires deterministic state mapping. The workflow operates as follows:

1. **Bulk Ingestion:** `orchestrator/poller.py` fetches open issues and cross-references their linked Pull Requests via a single bulk API call (e.g., GraphQL or `--json statusCheckRollup`) to circumvent GitHub secondary rate limits.
2. **Non-Destructive Persistence:** Data is upserted into `orchestrator/db.py` (`sdlc_items`). The schema is updated safely via `ALTER TABLE` dynamically at startup, strictly prohibiting table drops to preserve historical Blackboard state.
3. **Smart Visibility Rendering:** `orchestrator/ui/widgets.py` groups subtasks under parent stories using `sequence_order ASC`. To preserve terminal UX, updates are applied via stable row keys (`update_cell`), preventing the cursor from jumping during the 2.0s refresh ticks.

**Edge Cases & Resilience Strategy**

* **The Orphaned Subtask Trap:** If a parent story is manually closed but its subtasks remain active, filtering strictly by `parent.state == 'closed'` will invisibly drop the subtasks. **Strategy:** Apply the "Smart Visibility Rule"—a story tree is hidden *only* if the parent AND 100% of its child subtasks are closed.
* **API Rate Exhaustion:** Polling individual PR statuses per issue triggers rate limits. **Strategy:** A single bulk fetch per polling cycle must retrieve PR state (`OPEN`/`MERGED`) and CI status (`PASS`/`PENDING`/`FAIL`) for all linked items.
* **Schema Evolution on Restart:** Abrupt terminations or updates must not wipe `sdlc_items`. **Strategy:** Use `PRAGMA table_info(sdlc_items)` in `init_db()` to safely inject `pr_status` and `pr_ci_details` if missing.
* **TUI Cursor Stutter:** **Strategy:** Assign stable text keys (e.g., `story_454`, `subtask_455`) in Textual's `DataTable` to ensure `update_cell` modifies values in-place without triggering `RowHighlighted` resets.

**Acceptance Criteria (BDD Format)**

```gherkin
Scenario: Smart Visibility rendering prevents orphaned subtask loss
  Given a parent story is marked "closed" but contains active child subtasks
  When the UI fetches the SDLC hierarchy from SQLite
  Then the query must return the parent story and the open subtasks
  And the TUI must render the parent as a root node to maintain hierarchical context for the active subtasks.

Scenario: Single-pass bulk fetching prevents GitHub rate limits
  Given the background poller initiates a sync cycle for "graph-engineering"
  When fetching issue and Pull Request states
  Then the poller must execute a single bulk API query to retrieve PR linkages, state, and CI status rollups
  And it must strictly avoid individual per-issue PR queries.

Scenario: In-place TUI diffing maintains cursor stability
  Given the TUI dashboard is rendering the hierarchical SDLC tree
  And the operator has highlighted a subtask at row index 4
  When the 2.0s background refresh updates the PR status column
  Then the DataTable must use stable string keys to apply "update_cell"
  And the cursor must remain locked at row index 4 without visual stutter.

Scenario: Non-destructive database schema migration
  Given a previous version of the SQLite database exists without PR status columns
  When the orchestrator daemon initializes
  Then it must execute an "ALTER TABLE" command to append the new columns
  And existing SDLC item tracking data must remain completely intact.

```

**CLI UX Guidelines**

* **Hierarchical Formatting:** Use Unicode structural characters: `├─` for standard children, `└─` for the terminal child.
* **PR Status Badges:** Utilize Rich markup for the PR column: `[green]MERGED[/green]`, `[yellow]PENDING[/yellow]`, `[red]FAIL[/red]`.
* **Empty States:** If no active stories exist, display a dimmed fallback row: `[dim]No active SDLC items found.[/dim]`.

---

### Phase 2: Architectural & Implementation Plan

**Codebase Impact**

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/db.py` | **Modify** | Safely alter `sdlc_items` schema. Add `get_active_sdlc_hierarchy()` enforcing the "Smart Visibility Rule". |
| `orchestrator/poller.py` | **Modify** | Update `poll_project_sdlc_items` to extract PR states via bulk GitHub API queries (GraphQL or batched REST). |
| `orchestrator/ui/widgets.py` | **Modify** | Refactor `SDLCProgressWidget` to parse tree logic, apply stable row keys, and format Unicode tree characters. |

**Technical Constraints**

* **Database Concurrency:** All SQLite operations must execute asynchronously (`aiosqlite`) with `PRAGMA journal_mode=WAL` to prevent read locks during UI refreshes.
* **Textual DOM Boundaries:** Never use `self.clear()` on the `DataTable` during polling updates.

**Execution Steps**

1. **Database Schema Safeties (`orchestrator/db.py`)**
* Inspect existing columns via `PRAGMA table_info(sdlc_items)`.
* Safely execute `ALTER TABLE sdlc_items ADD COLUMN pr_status TEXT DEFAULT NULL;` if missing.
* Implement `get_active_sdlc_hierarchy(project_name)` using a `LEFT JOIN` or nested grouping that returns parents if `parent.state != 'closed' OR child.state != 'closed'`.


2. **Poller Bulk Ingestion (`orchestrator/poller.py`)**
* Update the GitHub fetch mechanism to request `statusCheckRollup` and `pull_request` metadata in a single pass.
* Parse `Parent: #<id>` from issue bodies.
* Map the extracted CI/PR statuses to the issues and execute `db.upsert_sdlc_item()`.


3. **Stable Widget Rendering (`orchestrator/ui/widgets.py`)**
* Update `SDLCProgressWidget` initialization to include the "PR Status" column.
* Map incoming hierarchy data to stable row keys (e.g., `f"issue_{item.id}"`).
* Apply Unicode prefixes (`├─ ` / `└─ `) dynamically based on the subtask's index within the parent's grouped array.
* Execute `table.update_cell(row_key, col_id, value)` for all updates.


4. **Validation (`tests/test_widgets.py` & `tests/test_db.py`)**
* Add a test mocking a closed parent with an open child to ensure `get_active_sdlc_hierarchy` returns both.
* Add a test verifying `SDLCProgressWidget` correctly applies the `└─` prefix to the final item in a subtask list.

---

## 🔗 GitHub Reference
- **GitHub Issue:** [Issue #95](https://github.com/AntaresAndBharani/graph-engineering/issues/95)
- **Label:** `needs-triage`

## 🔨 Subtasks
- [ ] feat(db): schema migration for pr_status and get_active_sdlc_hierarchy in StateManager
- [ ] feat(poller): single-pass bulk PR and CI statusCheckRollup extraction in poll_project_sdlc_items
- [ ] feat(ui): hierarchical tree rendering and PR status badge display in SDLCProgressWidget
- [ ] test(ui, poller, db): comprehensive unit and BDD tests for SDLC hierarchy and smart visibility