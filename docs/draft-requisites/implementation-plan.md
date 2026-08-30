### Phase 1: Functional & DX (Developer Experience) Review

**Workflow Analysis & Multi-Node Observability Journey**
With the introduction of dual-level parallelism, the 1:1 relationship between a project and an active node no longer exists. A single project (e.g., `crosstrainingapp`) can concurrently execute the `architect` and `devtest` nodes. The current `ProjectStatusTable` forces a single row per project, leading to "state clobbering" where one node overwrites the other's status, making it impossible to isolate the log streams for concurrent workers.

To resolve this, the TUI must transition to a **Compound Row Strategy**. If a project has multiple active nodes, the table dynamically spawns a distinct row for each node (e.g., `crosstrainingapp | architect` and `crosstrainingapp | devtest`). When an operator selects a specific node's row, the `RichLog` pane dynamically filters the project's memory buffer to tail *only* that node's execution stream, while the `SDLCProgressWidget` remains anchored to the overarching project.

**Edge Cases & Resilience Strategy**

* **Idle Project Fallback:** If a project has zero active nodes, the UI must not hide the project entirely. It must render a single row (e.g., `crosstrainingapp | Idle`) so the operator can still select it to view the SDLC backlog and global project logs.
* **Rapid Row Thrashing:** As nodes complete tasks and spin up/down, rows will dynamically appear and disappear. To prevent the user's cursor from wildly jumping, the `row_key` must be a stable composite (`f"{project_name}::{node_name}"`), utilizing in-place Textual updates to preserve layout stability.
* **Global Node Artifacts:** Framework-level logs emitted outside a specific node (e.g., `orchestrator/poller.py` or database migrations) should be categorized under a virtual `SYSTEM` node so they are visible when an `Idle` row is selected.

**Acceptance Criteria (BDD Format)**

```gherkin
Scenario: Dynamic multi-row rendering for concurrent nodes
  Given the "crosstrainingapp" project has both "architect" and "devtest" nodes active
  When the periodic UI refresh executes
  Then the ProjectStatusTable must render two distinct rows for the project
  And Row 1 must display "crosstrainingapp" with Active Node "architect"
  And Row 2 must display "crosstrainingapp" with Active Node "devtest".

Scenario: Node-isolated log tailing upon row selection
  Given the ProjectStatusTable displays a row for "graph-engineering | devtest"
  When the operator highlights this specific row
  Then the RichLog pane must clear and hydrate using ONLY log records emitted by the "devtest" node
  And live incoming logs from the concurrent "architect" node must be suppressed from this specific view.

Scenario: Graceful idle state representation
  Given all nodes for "crosstrainingapp" complete their tasks and transition to idle
  When the orchestrator state syncs
  Then the table must collapse any multi-node rows into a single "crosstrainingapp | Idle" row
  And selecting this row must display the aggregate historical logs for the entire project.

```

**CLI UX Guidelines**

* **Visual Hierarchy in Table:** To avoid repeating the repository name endlessly, use dimming or indentation for sibling node rows (e.g., `crosstrainingapp | devtest`, ` ├─ architect`).
* **Log Tab Scoping:** Dynamically update the Textual tab or pane border to explicitly state the active filter: `Logs [Scope: crosstrainingapp | devtest]`.

---

### Phase 2: Architectural & Implementation Plan

**Codebase Impact & Component Updates**

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/ui/widgets.py` | **Modify** | Update `sync_projects()` in `ProjectStatusTable` to accept a list of `(project, node_state)` tuples and manage composite `row_key`s. |
| `orchestrator/ui/dashboard.py` | **Modify** | Update `DataTable.RowHighlighted` handler to extract `node_name` from the composite key and pass it to the log hydrator. |
| `orchestrator/logging.py` | **Modify** | Update `ProjectLogBufferManager` to store logs as a tuple `(node_name, log_line)` to support instantaneous read-time filtering. |
| `orchestrator/db.py` | **Modify** | Update state fetching methods to return a list of active workers per project rather than a flattened project state. |

**Technical Constraints & Safeguards**

* **Memory Efficiency:** Do not create separate nested deques for every possible node, as this fractures memory limits. Maintain the single `collections.deque(maxlen=500)` per project in `ProjectLogBufferManager`, but store tuples: `deque[(str, str)]` (Node, Line). Filter via list comprehension at read time (`[line for n, line in buffer if n == target_node]`).
* **Textual DOM Indexing:** When generating `row_key`s, use a strict delimiter (e.g., `::`) so `dashboard.py` can reliably execute `project, node = row_key.split("::")`.

**Execution Steps**

1. **Enhance Log Memory Structures (`orchestrator/logging.py`)**
* Modify `ProjectLogBufferManager.append` to accept `node_name` alongside `project_name` and `line`.
* Update the internal storage to append `(node_name, line)` to the project's deque.
* Update `get_logs(project_name, node_name=None)` to return the filtered list of lines if a specific node is requested, or all lines if `node_name` is `"Idle"` or `None`.


2. **Refactor State Extraction (`orchestrator/db.py`)**
* Ensure the polling data structure passed to the UI returns a flattened list of active entities. If a project has two active nodes, it must yield two dictionary payloads, one for each active node context. If zero, yield one payload with `"active_node": "Idle"`.


3. **Implement Composite Row Keys (`orchestrator/ui/widgets.py`)**
* In `ProjectStatusTable.sync_projects()`, construct `row_key = f"{p['name']}::{p.get('active_node', 'Idle')}"`.
* Execute `self.update_cell()` for existing keys and `self.add_row()` for new keys.
* Remove stale keys (e.g., when a node transitions from active to idle).


4. **Wire Node-Aware Routing (`orchestrator/ui/dashboard.py`)**
* In the `RowHighlighted` event handler, split the `row_key.value` to extract `selected_project` and `selected_node`.
* Update the `SDLCProgressWidget` using `selected_project`.
* Hydrate the `RichLog` by passing both `selected_project` and `selected_node` to the buffer manager. Update the pane title/border to reflect the isolated scope.

---

## 🔗 GitHub Reference
- **GitHub Issue:** [Issue #101](https://github.com/AntaresAndBharani/graph-engineering/issues/101)
- **Label:** `needs-triage`

## 🔨 Subtasks
- [ ] feat(logging): ProjectLogBufferManager with (node_name, line) tuples and node-scoped disk tailing
- [ ] feat(harness): AsyncHarnessAdapter stream listener with (project_name, node_name, line)
- [ ] feat(ui): compound row rendering in #projects_table and node-isolated RichLog hydration in DashboardApp
- [ ] test(ui, logging): unit and integration tests for multi-node rows and log stream filtering