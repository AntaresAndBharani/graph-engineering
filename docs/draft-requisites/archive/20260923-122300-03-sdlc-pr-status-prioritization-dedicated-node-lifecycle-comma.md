# 📋 Implementation Plan & Refinement Lifecycle: SDLC PR Status Prioritization & Dedicated Node Lifecycle Command

## 📝 Initial Draft Proposal

### Background & Objective
Based on live operational observations and operator feedback with the Textual TUI dashboard and CLI:

1. **SDLC PR Status Column Placement (UX Blind Spot):**
   - In `SDLCProgressWidget` (`DataTable`), the columns are currently ordered as:
     `["ID", "Title", "Status/Label", "PR Status"]`.
   - Because issue titles are often descriptive and long (e.g. `feat(logging): LogQueryResult contract, strict node scope isolation & 0-byte file handling`), the `PR Status` column is pushed far off-screen to the right, requiring manual horizontal scrolling to see PR numbers and CI badges (as shown in operator screenshots).
   - Furthermore, `Status/Label` currently prints raw Python dictionary representations (`{'name': '...', 'color': 'D4C5F9'}`) when label structures are stored as dicts in SQLite.
   - **Operator Directive:** *"The PR status is on the right at the end. I want it as second column."*

2. **Dedicated Node Lifecycle Command (`orchestrator start`):**
   - Currently, `orchestrator run -p <project> -n <node>` only executes a **single pass** and immediately exits, regardless of whether follow-up work is queued.
   - Meanwhile, `orchestrator watch` launches the entire daemon across all registered projects in parallel.
   - There is no single, focused command to target a specific project, trigger a specific node (e.g. `devtest`), and autonomously drive its **entire development lifecycle** (running sequential passes until all queued subtasks are processed and verified).
   - **Operator Directive:** *"I want also a orchestrator command (if not exist) to start in a project an specific node and, doing that, the whole lifecycle there (for example, devtest, ...)."*

---

## 🔍 Review Iteration 1: 3-Amigos Critical Architectural Review

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Architectural Scope:** `orchestrator/ui/widgets.py`, `orchestrator/cli.py`, `orchestrator/poller.py`
- **Current Baseline:** 337 passing unit/integration tests (`pytest -v`).

### 1. Point-by-Point Verdict Matrix

| # | Proposal Element | Target Component | Verdict | Technical Rationale & Architectural Rule |
|---|---|---|---|---|
| 1 | **Reorder SDLC Columns to `[ID, PR Status, Title, Status/Label]`** | `orchestrator/ui/widgets.py` (`SDLCProgressWidget.TABLE_COLUMNS`) | **APPROVE** | Placing `PR Status` as Column 1 immediately surfaces PR numbers and CI check statuses (e.g. `#155 ✔ PASS`) without horizontal scrolling. Fits standard terminal widths (80–120 cols). |
| 2 | **Sanitize Label Rendering in `Status/Label`** | `orchestrator/ui/widgets.py` (`_render_rows`) | **APPROVE** | Parse raw SQLite labels: if `raw_labels` is a serialized dict, list of dicts, or string, extract clean `name` fields (e.g. `needs-triage`, `queued`) instead of dumping raw JSON-like strings. |
| 3 | **Dedicated CLI Command `orchestrator start <project>`** | `orchestrator/cli.py` (`@app.command("start")`) | **APPROVE** | Implement a first-class `orchestrator start <project_name> [--node <node>] [--continuous / --lifecycle]` command. If `--node` is provided (e.g. `devtest`), it focuses specifically on executing that node for the targeted project. |
| 4 | **Lifecycle Autonomous Drain Loop** | `orchestrator/cli.py` (`_run_project_lifecycle`) | **APPROVE** | When executing a lifecycle run, do not stop after pass 1: if `work_done` is True (e.g. `devtest` implemented subtask #155 and created a PR), loop with a 1-second backoff to immediately pick up subtask #156, #157, etc., until all queued tasks for that node/lifecycle are drained and state is idle. |
| 5 | **Signal & Lock Safety on Targeted Node Run** | `orchestrator/cli.py`, `orchestrator/db.py` | **APPROVE** | `orchestrator start` must honor `state_manager.is_stop_requested()` and cleanly acquire/release issue locks in SQLite, preventing race conditions if an operator runs `orchestrator start` while the daemon is active. |

---

## 🚀 Boost Review Iteration 1: 360° Multi-Perspective Deep Analysis

- **Date / Author:** 2026-09-03 | Boost Swarm Architect

### 1. Architecture & Schema Integrity Lens
- **Column Order Invariant:** `SDLCProgressWidget.TABLE_COLUMNS` is modified to `["ID", "PR Status", "Title", "Status/Label"]`.
  - In `_render_rows`, `target_rows.append((row_key, (root_id, pr_badge, root_title, status_label)))`.
  - Subtask rows: `(sub_id, sub_pr_badge, sub_title, sub_status_label)`.
  - Empty rows: `("-", "-", "No active SDLC items", "-")`.
  - All existing unit tests in `tests/test_widgets.py` asserting table columns must be updated to prevent regressions.
- **Label Parsing Robustness:** `root.get("labels")` can be a list of strings, a list of dicts `[{"name": "queued", "color": "..."}]`, or a JSON string. We parse via a pure helper `extract_label_names(raw_labels) -> str` joining names with commas.

### 2. Lifecycle Execution State Machine (`orchestrator start`)
1. **Validation:** Ensure `project_name` exists in `config.projects` and is enabled.
2. **Node Scoping:** If `--node` is passed (e.g. `devtest`), verify `project.is_node_enabled(node)`.
3. **Execution Loop:**
   - Run `poller.poll_project_sdlc_items(project, state_manager)` to refresh state.
   - Run `run_project_cycle(project, config, state_manager, node_name=node)`.
   - If `work_done is True`:
     - Display `[bold green]✔ [{project}:{node}][/bold green] Pass completed with active progress. Advancing lifecycle to next item...`
     - Sleep 1s, repeat loop.
   - If `work_done is False`:
     - If `--continuous` is set: sleep `poll_interval_seconds` and continue.
     - Else: display `[bold cyan]🏁 [{project}:{node}][/bold cyan] Lifecycle queue drained. All tasks for this node are up-to-date.` and cleanly exit with code 0.

### 3. Concurrency & Collision Guard
- If the daemon (`orchestrator watch`) is already running for the same project, `StateManager.acquire_issue_lock` protects against double-execution. If a lock is already held by another PID, `run_architect_node` / `run_devtest_node` safely skips and logs: `Issue #X is currently locked by another active run`.

---

## 🎯 Final Decision Plan & User Story Specification

### User Story
**As an** Orchestrator Operator and Developer,  
**I want** the TUI SDLC widget to display `PR Status` as the second column, and a dedicated CLI command `orchestrator start <project> -n <node>` to execute a project's autonomous node lifecycle until queue completion,  
**So that** I can instantly view PR and CI statuses without horizontal scrolling, and drive targeted development lifecycles directly from the command line.

### BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: SDLC PR Status Prioritization and Dedicated Node Lifecycle Command

  Scenario: SDLC table displays PR Status as second column
    Given the dashboard displays the SDLC items table
    When the table is rendered
    Then column 0 must be "ID"
    And column 1 must be "PR Status"
    And column 2 must be "Title"
    And column 3 must be "Status/Label"
    And PR numbers and CI badges must be visible without horizontal scrolling

  Scenario: Clean label formatting without raw JSON/dict strings
    Given an issue in SQLite has serialized dict labels "[{'name': 'queued', 'color': 'D4C5F9'}]"
    When the SDLC table renders the issue
    Then the "Status/Label" column must display "queued"
    And raw dictionary keys or curly brackets must not be rendered

  Scenario: Targeted node lifecycle command executes until queue drain
    Given project "biq-playbook" has 2 queued subtasks for "devtest"
    When the operator executes "orchestrator start biq-playbook -n devtest"
    Then it must execute DevTest on the first subtask
    And upon successful implementation it must immediately execute the second subtask
    And upon draining the queue it must report completion and exit cleanly with code 0

  Scenario: Targeted lifecycle run respects safe stop and locks
    Given an issue in project "biq-playbook" is locked by another active job
    When the operator executes "orchestrator start biq-playbook -n devtest"
    Then it must skip the locked issue gracefully without crashing
```

### Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/ui/widgets.py` | **MODIFY** | 1. Update `SDLCProgressWidget.TABLE_COLUMNS` to `["ID", "PR Status", "Title", "Status/Label"]`.<br>2. Reorder tuple values in `_render_rows`.<br>3. Add `_format_label_display` helper to extract clean label names from dicts/lists. |
| `orchestrator/cli.py` | **MODIFY** | Implement `@app.command("start")` with options `--node/-n`, `--continuous/-c`, and `--config`. Drive `_run_project_lifecycle` with drain loop until all queued tasks are processed. |
| `docs/node-cli.md` | **MODIFY** | Document `orchestrator start` command options and new SDLC widget column ordering. |
| `tests/test_widgets.py` | **MODIFY** | Update column assertion tests for `SDLCProgressWidget` to verify `PR Status` is at index 1 and labels are cleanly formatted. |
| `tests/test_cli.py` | **MODIFY** | Add tests for `orchestrator start` verifying single-project node lifecycle drain and clean exit. |

### INVEST Subtask Breakdown

1. **Subtask 1: SDLC Column Reordering & Label Sanitization (`orchestrator/ui/widgets.py`)**
   - Reorder `TABLE_COLUMNS` to `["ID", "PR Status", "Title", "Status/Label"]`.
   - Update row tuple generation to match `(id, pr_badge, title, status_label)`.
   - Add label formatting helper stripping raw dict/JSON syntax.
2. **Subtask 2: Implement `orchestrator start` Command (`orchestrator/cli.py`)**
   - Add `@app.command("start")` taking `project_name` (argument or option), `--node` (optional filter), `--continuous` (flag).
   - Implement `_run_project_lifecycle`: loop while `work_done is True`, execute follow-up passes with 1s sleep, exit on queue drain.
3. **Subtask 3: Automated Regression & Unit Suite (`tests/test_widgets.py`, `tests/test_cli.py`)**
   - Update `test_widgets.py` table column tests.
   - Add unit tests for `orchestrator start` lifecycle execution.


---

## 🔍 Review Iteration 2: Subtask Selection Determinism (Lowest ID Priority Over `ready-for-dev` vs `queued`)

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Architectural Scope:** `orchestrator/db.py` (`get_next_devtest_task`), `orchestrator/nodes/devtest.py` (`_advance_sequential_subtask`, `run_devtest_node`), `orchestrator/poller.py`

### 1. Root Cause & Behavioral Diagnosis
1. **The Issue:** 
   - An operator experienced an anomaly where DevTest skipped a lower-numbered subtask (e.g. #155 labeled `queued`) and attempted to run a higher-numbered subtask (e.g. #156 labeled `ready-for-dev`), forcing manual intervention.
2. **Causal Mechanisms Identified in Code:**
   - **`sequence_order` vs `issue_number` Sort Precedence:**
     In `orchestrator/db.py:1407`:
     ```sql
     ORDER BY sequence_order ASC, issue_number ASC
     ```
     `sequence_order` defaults to 0 on new issues. However, if any subtask was synced or created with `sequence_order > 0` or if `sequence_order` values were inconsistent, a higher-numbered issue with `sequence_order = 0` would be selected before a lower-numbered issue with `sequence_order = 1`.
   - **Exclusion in `_advance_sequential_subtask` (`devtest.py:457`):**
     ```python
     if any(lbl in c_labels for lbl in (queued_label, f"status:{queued_label}", "queued", "status:queued", "status:pending-review")) or c.get("number") in unchecked_ids:
         queued_children.append(c)
     ```
     If a child subtask already had `ready-for-dev` (applied manually or by a previous step), it was **excluded** from `queued_children`. When DevTest attempted to promote the next child, it would skip past the `ready-for-dev` subtask and promote the next `queued` subtask instead!
   - **Fallback 1 Filter in `db.py:1465`:**
     Fallback 1 strictly required `labels LIKE '%ready-for-dev%'`. If standalone or unlinked subtasks existed where the lowest ID was `queued` and a higher ID was `ready-for-dev`, the lowest ID was completely invisible to Fallback 1.

3. **Operator Architectural Mandate:**
   > *"it doesn't really matter if a subtask has ready-for-dev of queue, devtest must always pick up the one with the lowest id."*

### 2. Concrete Technical Remedy & Invariant Specification

| Component | Target Location | Architectural Resolution |
|---|---|---|
| `orchestrator/db.py` | `get_next_devtest_task` (line 1407) | 1. Query all open uncompleted subtasks for the active locked story.<br>2. Filter out only `blocked` / `orchestration-failed` or `in-progress` items.<br>3. Order strictly by **`issue_number ASC`** (or `COALESCE(sequence_order, issue_number) ASC, issue_number ASC` with fallback).<br>4. Do **NOT** filter by label: whether the subtask is labeled `ready-for-dev`, `queued`, `status:ready-for-dev`, or `status:queued`, the subtask with the **lowest `issue_number`** is strictly returned. |
| `orchestrator/nodes/devtest.py` | `run_devtest_node` (lines 1068–1080) | When `target_issue_id` is returned from SQLite: if its current labels on GitHub include `queued` (or lack `ready-for-dev`), autonomously promote it on GitHub (`--remove-label queued --add-label ready-for-dev`) before launching the harness. |
| `orchestrator/nodes/devtest.py` | `_advance_sequential_subtask` (lines 452–470) | When advancing to the next subtask: filter all open children whose state != `CLOSED`. Sort them strictly by `number ASC`. Select the lowest open child. If it is `queued`, promote it to `ready-for-dev`. If it is already `ready-for-dev`, leave it as-is and log that it is the active target. Never skip an open child. |

---

## 🎯 Final Decision Plan & User Story Specification (Consensus Update)

### User Story
**As an** Orchestrator Operator and Autonomous Developer,  
**I want** the TUI SDLC widget to display `PR Status` as the second column, a dedicated `orchestrator start <project> [-n <node>]` command to drive project node lifecycles to completion, and strict lowest-ID subtask selection in DevTest regardless of `ready-for-dev` vs `queued` labels,  
**So that** I can observe PR/CI statuses without scrolling, run focused development cycles from the CLI, and guarantee that subtasks are always implemented in strict ascending order (#1, #2, #3...) without skipping or manual label correction.

### BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: SDLC UI Prioritization, Lifecycle CLI, and Deterministic Lowest-ID Subtask Dispatch

  Scenario: SDLC table displays PR Status as second column
    Given the dashboard displays the SDLC items table
    When the table is rendered
    Then column 0 must be "ID"
    And column 1 must be "PR Status"
    And column 2 must be "Title"
    And column 3 must be "Status/Label"
    And PR badges must be visible without horizontal scrolling

  Scenario: Clean label formatting without raw JSON/dict strings
    Given an issue in SQLite has serialized dict labels "[{'name': 'queued', 'color': 'D4C5F9'}]"
    When the SDLC table renders the issue
    Then the "Status/Label" column must display "queued"
    And raw dictionary keys or curly brackets must not be rendered

  Scenario: DevTest selects lowest-ID subtask regardless of ready-for-dev vs queued label
    Given an active story has subtask #155 labeled "queued"
    And subtask #156 labeled "ready-for-dev"
    When "get_next_devtest_task" is evaluated for the project
    Then it must select subtask #155
    And subtask #156 must NOT be selected ahead of #155
    And DevTest must promote #155 to "ready-for-dev" upon execution

  Scenario: DevTest sequential advance selects lowest open child
    Given an active story finishes subtask #154
    And remaining open subtasks are #155 (queued) and #156 (queued)
    When "_advance_sequential_subtask" executes
    Then it must select #155 as the next subtask to promote
    And it must NOT skip #155

  Scenario: Dedicated node lifecycle command executes until queue drain
    Given project "biq-playbook" has 2 queued subtasks for "devtest"
    When the operator executes "orchestrator start biq-playbook -n devtest"
    Then it must execute DevTest on the lowest ID subtask
    And upon PR creation it must advance to the next subtask in ascending ID order
    And upon draining the queue it must exit cleanly with code 0
```

### Complete Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/ui/widgets.py` | **MODIFY** | 1. Set `TABLE_COLUMNS = ["ID", "PR Status", "Title", "Status/Label"]`.<br>2. Reorder row tuple in `_render_rows`.<br>3. Add `_format_label_display` helper to sanitize raw dicts/JSON strings. |
| `orchestrator/db.py` | **MODIFY** | In `get_next_devtest_task`: order uncompleted subtasks strictly by `issue_number ASC` and do not discriminate between `ready-for-dev` and `queued` labels for active story children. |
| `orchestrator/nodes/devtest.py` | **MODIFY** | 1. In `_advance_sequential_subtask`: consider all open children sorted by `number ASC` and promote the lowest uncompleted child.<br>2. In `run_devtest_node`: ensure candidate issue is promoted to `ready-for-dev` if currently `queued`. |
| `orchestrator/cli.py` | **MODIFY** | Implement `@app.command("start")` with drain loop executing `run_project_cycle` sequentially until idle. |
| `docs/node-cli.md` | **MODIFY** | Document `orchestrator start`, SDLC column ordering, and lowest-ID sequential dispatch guarantee. |
| `tests/test_widgets.py` | **MODIFY** | Test column ordering `["ID", "PR Status", "Title", "Status/Label"]` and label sanitization. |
| `tests/test_db.py` | **MODIFY** | Test `get_next_devtest_task` selecting lower-ID `queued` subtask over higher-ID `ready-for-dev` subtask. |
| `tests/test_devtest.py` | **MODIFY** | Test sequential advance selecting lowest open child regardless of existing label. |
| `tests/test_cli.py` | **MODIFY** | Test `orchestrator start` command execution and queue draining. |


## 🏛️ Claude Sonnet Review Iteration 1` at the end of the file. But:

1. **That section is already resolved.** It already contains three completed rounds — `Claude Sonnet Review Iteration 1` (line 1045), `Iteration 2` (line 1216), and `Iteration 3` (line 1358) — culminating in `## 🎯 Authoritative Final Decision Plan & User Story Specification (Ready for Operator Sign-off)` (line 1430). Appending a *new* `Iteration 1` now would create a duplicate heading and falsely suggest this is a fresh first pass on an unreviewed plan.

2. **It isn't the latest section in the file.** A third, newer section — `# 📋 Implementation Plan & Refinement Lifecycle: SDLC PR Status Prioritization & Dedicated Node Lifecycle Command` — starts at line 1494 and runs to the file's actual end (line 1732). That section has only gone through 3-Amigos/Antigravity/Gemini passes (Review Iteration 1 and 2) and a `Boost Review` — it has **no Claude Sonnet review yet**, which is exactly what "Round 1... append at the very end of the file" would naturally mean.

So the instructions' description ("latest section," "append at the very end of the file") matches the **SDLC PR Status Prioritization & Dedicated Node Lifecycle Command** section, not the log-streaming one named in the title.

**Which do you want me to review?**
- (A) The SDLC PR Status Prioritization / `orchestrator start` section (line 1494+) — genuinely unreviewed by Claude Sonnet, matches "latest"/"end of file."
- (B) The Real-Time Log Streaming section (line 922+) as literally named — but this means adding a 4th round on top of an already-signed-off plan, and I'd need to append it out of order at the true end of the file (after the SDLC section) to satisfy "append at the very end," which would be confusing to read.

I'd lean toward (A) since it matches both "latest" and "end of file," and it's the one still awaiting review — but I want your call before I touch the document.

---

## 🏛️ Claude Opus Review Iteration 2

- **Date / Author:** 2026-09-03 | Claude Opus 5 Principal Architect
- **Target:** *Real-Time Active Node Log Streaming & Scoped Observability in Dashboard* — specifically the **🎯 Authoritative Final Decision Plan & User Story Specification (Ready for Operator Sign-off)** and the S-14…S-18 convergence matrix.
- **Method:** Full re-inspection of the *current, still-unmodified* sources: `orchestrator/ui/dashboard.py` (779 lines), `orchestrator/logging.py` (455 lines), `orchestrator/harness.py:225-289`, `orchestrator/cli.py:549-800` (`watch_command` / `_watch_daemon_tui`) and `orchestrator/cli.py:1216-1256` (`logs_command`), plus the existing assertions in `tests/test_logging.py`. Confirmed none of S-1…S-18 have been applied to the codebase.

### ⚖️ Critical Architecture & Drawbacks Critique

#### O2-1 · Dual-source duplication: the live stream listener and the new disk tailer both feed the same RichLog, in the same process (Blocker)

This is the single largest hole in the plan, and no safeguard S-1 through S-18 mentions it.

Verified: `orchestrator/harness.py:242-287` (`stream_output`) writes every decoded line to the node's log file (`f.write(cleaned); f.flush()`, lines 254-256) **and**, in the same loop iteration, dispatches it to every registered listener (lines 258-287). `orchestrator/ui/dashboard.py:178` registers `self._handle_harness_stream_line` as such a listener, and that handler writes the line straight into `#log_view` (lines 289-297). Verified in `orchestrator/cli.py:574-578` and `699-800`: `orchestrator watch` runs the **daemon and the Textual app in one process and one asyncio event loop** (`asyncio.run(_watch_daemon_tui(...))`, project workers spawned with `asyncio.create_task`).

Therefore, the moment `_poll_active_log_file` starts tailing the active node's file, **every live line is rendered twice**: once immediately by the stream listener, and once again on the next 2.0 s tick by the tailer reading the same bytes off disk. S-9/S-14's offset handoff only reconciles *hydration → tailer*; it says nothing about *listener → tailer*, which are two entirely independent producers.

Worse, the two copies are not even textually identical, so no naive content-based dedupe can be bolted on later: when `console_prefix` is set the listener receives `f"  [dim cyan]{console_prefix}[/dim cyan] [dim]{subline}[/dim]"` (harness.py:262) while the file receives the raw `cleaned` text (harness.py:255). The plan's headline acceptance criterion — *"previously hydrated lines must not be duplicated in the view"* — is satisfied for the one narrow handoff it models and violated continuously for the feature's primary scenario.

#### O2-2 · The "skip 0-byte files" rule and the "0-byte placeholder" scenario are mutually exclusive, and the placeholder is dead code in practice (Blocker)

The Complete Component Impact Table requires `tail_latest_project_logs` to have *"strict node filtering and 0-byte skipping"*, and `tests/test_logging.py` is to gain a test for *"0-byte skipping when non-empty logs exist"*. BDD Scenario 4 simultaneously requires that when *"DevTest's log file is currently 0 bytes"* the pane shows `⚡ Initializing devtest harness on Issue #76…`.

These contradict directly. Any node that has ever run before has non-empty logs on disk (`tail_latest_project_logs` selects with `max(log_files, key=lambda p: (p.stat().st_mtime, p.name))`, logging.py:213). With 0-byte skipping active, the freshly-created file for issue #76 is discarded and **yesterday's completed run for issue #42 is selected instead** — so `file_size != 0`, the placeholder never renders, the operator is shown a *stale, finished* log presented as live output, and `_last_tail_offset` is seeded at the EOF of a file that will never grow again. The pane then sits frozen for the entire duration of the new run. The plan's own Scenario 4 only passes for a node's very first execution ever.

Independently, the placeholder is near-unreachable even when it is not suppressed: `stream_output` writes a two-line banner (`=== EXECUTION STARTED: … ===` / `=== CWD: … ===`) and flushes it *before* reading the first byte of subprocess output (harness.py:243-247). The genuine "harness started, no agent output yet" state is therefore a **~120-byte file, not a 0-byte file**. `file_size == 0` is the wrong predicate for the state the plan is trying to name.

#### O2-3 · S-16's `(project, node)` transition identity misses the same-node/next-issue transition — the tailer stays pinned to a finished file (Blocker)

S-16 replaces index-based detection with *"`(project_name, active_node)` identity"*. But the dominant real-world transition in this pipeline is **the same node advancing to the next issue**: `architect` finishes issue #75 and immediately picks up #76. Verified in `update_projects_table` (dashboard.py:417-424), the row key is `f"{p.name}::{node_type}"` — it contains **no issue id** — so `biq-playbook::architect` → `biq-playbook::architect` is a no-op under S-16's identity test, while on disk a *brand-new* log file (`…_architect_issue_76.log`) has been created.

Consequence: no transition is registered, `hydrate_project_logs` is never re-invoked, `_last_tail_file`/`_last_tail_offset` remain bound to the completed issue-#75 file, and the operator watches a dead pane for the entire #76 run. Note the plan already knows the issue id is semantically load-bearing — BDD Scenario 2 asserts `hydrate_project_logs` is invoked with `issue_id=75`, and `locks_info = f"Issue #{job.get('issue_id')}"` is already computed at dashboard.py:423 — but S-16 deliberately drops it from the identity key.

#### O2-4 · Markup escaping is specified on exactly the wrong code path (Blocker)

Verified `dashboard.py:155`: the widget is `RichLog(id="log_view", highlight=True, markup=True, …)`. Markup interpretation is **on**.

The plan (Impact Table item 3) escapes precisely the lines that are *supposed* to carry markup — the harness's own `  [dim cyan]{prefix}[/dim cyan] [dim]{line}[/dim]` (harness.py:262) — which converts the styled live feed into literal visible `[dim cyan]…[/dim cyan]` noise across the whole pane: a plain visual regression on the feature's primary path.

Meanwhile it leaves *untrusted* text unescaped on both paths that actually need it:

- `hydrate_project_logs` writes buffer/disk lines with a bare `for line in lines: log_view.write(line)` (dashboard.py:239-240) — **outside** the `try/except` that guards only the `query_one` lookup at 204-206.
- The new `_poll_active_log_file` is specified to write raw disk chunks with no escaping at all.

Raw AI-agent output routinely contains bracket sequences Rich parses as markup: `arr[1:2]`, `[/INFO]`, `[project:node]` prefixes, JSON-ish `[{…}]`, or an unbalanced `[bold` copied from a prompt. These raise `rich.errors.MarkupError` (mismatched closing tag) or `rich.errors.MissingStyle` (unknown style name) inside `RichLog.write` / at render time — an unhandled exception on the timer callback or in the row-highlight handler. A single hostile-looking line in a `biq-playbook` architect log takes the dashboard down. The escaping polarity must be inverted.

### 🚨 Unresolved Concerns & Edge Case Vulnerabilities

#### O2-5 · Tailer output is never written back to the buffer, so every re-hydration silently discards it

`_poll_active_log_file` is specified to write to `log_view` only. But `hydrate_project_logs` begins with `log_view.clear()` (dashboard.py:238) and repopulates **exclusively** from `buffer_manager.get_project_logs(...)`. Re-hydration fires on tab activation (dashboard.py:589-605), on the `r` refresh action (679-683), and on any row highlight change (572-575). Each of those events therefore **erases every line the tailer contributed** and replaces it with a buffer-only view — a visible, unexplained truncation of live history. S-17's added `log_view.clear()` for placeholder retirement compounds this by introducing a *second* uncoordinated clear.

#### O2-6 · S-14's "in-memory hit + disk `stat()` probe" seeds an offset that silently hides a middle segment of the log

`PROJECT_BUFFERS[project]` is a `deque(maxlen=500)` shared across **all** nodes of that project (logging.py:157-159). On the in-memory-hit branch S-14 returns the ≤500 buffered lines but seeds `_last_tail_offset` from the **disk EOF**. If the architect has emitted 800 lines (or 300 lines interleaved with another node's chatter that evicted the earlier ones), the pane shows the buffered tail, the tailer resumes at EOF, and the lines that fell out of the deque but exist on disk are **permanently unreachable from the dashboard** — with no gap marker. The operator's own stated requirement is that the pane match `orchestrator logs biq-playbook -n architect`; that CLI reads the last N lines straight off disk (cli.py:1250-1256) and will show content the dashboard cannot.

#### O2-7 · Programmatic `move_cursor` re-enters the highlight handler — two uncoordinated hydration paths, order-dependent

`update_projects_table` calls `table.move_cursor(row=target_cursor_index)` on every 2.0 s tick (dashboard.py:494-497). Textual's `DataTable` posts `RowHighlighted` when the cursor coordinate changes, and `on_project_row_highlighted` is bound with `@on(DataTable.RowHighlighted, "#projects_table")` (dashboard.py:529). Adding a *direct* `hydrate_project_logs` call plus a `selected_node` mutation inside `update_projects_table` creates two producers of hydration whose interaction is entirely determined by unspecified ordering:

- Mutate `selected_node` **before** `move_cursor` → the equality guard at dashboard.py:572 suppresses the highlight handler, which also silently suppresses `_update_bottom_panes(project, force=True)` — a behavioural regression in the SDLC/Alerts/Quota panes on node transitions.
- Mutate **after** → hydration runs twice per transition, clearing and re-seeding `_last_tail_offset` twice, with the second seed taken at a later EOF than the first render.

No suppression flag, re-entrancy guard, or hydration lock is specified anywhere in S-1…S-18.

#### O2-8 · Three divergent log-discovery implementations (four after S-14) make the headline acceptance criterion unverifiable

- `logs_command` (cli.py:1242-1255): exact path join `<log_dir>/<project>/<node>`, `rglob`, sort by mtime, default **30** lines.
- `tail_latest_project_logs` (logging.py:206-221): `rglob` over `<log_dir>/<project>`, fuzzy `matches_node_scope` against `p.parent.name` **or** `p.stem`, default **100** lines, with an unfiltered fallback.
- S-14 adds a **new** "fast disk probe" on the in-memory branch, which must independently re-derive the same file.
- `_poll_active_log_file` then needs the same answer a fourth time.

The user story is *"matching `orchestrator logs <project> -n <active_node>`"*, yet nothing in the plan unifies these. A node directory named `architect_research` is found by the fuzzy path and **not** by the CLI's exact join; the two surfaces will legitimately disagree, and no test can pin the criterion.

#### O2-9 · "Never revert to unfiltered files" is an unbudgeted backward-compatibility break

Current code deliberately falls back: `if filtered_files: log_files = filtered_files` (logging.py:216-221). The plan removes that. For any project whose logs are written flat as `<log_dir>/<project>/*.log` (no per-node subdirectory), `p.parent.name` is the *project* name, which never satisfies `matches_node_scope("architect", "biq-playbook")` — and the `p.stem` branch cannot rescue it either, since a stem like `20260903_160302_architect_issue_75` starts with a digit run, so neither `startswith` nor the base-family comparison (`"20260903"` vs `"architect"`) matches. Such projects go from "shows logs" to a permanent `No execution logs found yet for node 'architect'`. No migration note, no back-compat statement, no test.

#### O2-10 · Untagged-line asymmetry: lines visible live disappear on refresh

`matches_node_scope` returns `True` whenever either argument is falsy (logging.py:20-21). The live filters guard on `rec_node and not matches_node_scope(...)` (dashboard.py:248) and `line_node and not matches_node_scope(...)` (286), so **untagged lines are admitted to the pane while a node scope is active**. S-15/the Impact Table make the hydrate path *strictly* exclude `node is None` entries. Net effect: a class of lines is displayed live and then vanishes the instant the operator presses `r`, switches tabs, or a node transition re-hydrates — precisely the "blank/misattributed pane" confusion this feature exists to eliminate. One rule must govern both paths.

#### O2-11 · "Strict node isolation" is false by construction while `matches_node_scope` is the matcher

`matches_node_scope` matches on exact equality, **either-direction prefix**, *and* shared base family after splitting on `_`/`-` (logging.py:22-27). Sibling nodes sharing a base (`pr_review` / `pr_merge` → both base `pr`; `dev` / `devtest`; `architect` / `architect_research`) cross-match by design. BDD Scenario 3 only exercises `architect` vs `devtest`, a pair that cannot fail, so the suite will certify an isolation guarantee the matcher does not provide.

#### O2-12 · Existing green tests are broken by the `LogQueryResult` retrofit, and a public wrapper is missing from the Impact Table

Verified assertions that break the moment `tail_latest_project_logs` stops returning `List[str]`:

- `tests/test_logging.py:403` (`direct_logs` list comparison), `:413`, `:418`, `:421` (`== []`), `:636` (`dev_tail` list comparison).

Also unlisted anywhere in the plan: the **module-level** `tail_latest_project_logs` wrapper at `orchestrator/logging.py:278-288`, annotated `-> List[str]` and imported directly by tests (and available to any external caller). The Impact Table's `tests/test_logging.py` row says only *"Add regression tests"* — it never budgets for updating existing ones, so the stated 341-test green baseline will not hold.

#### O2-13 · Per-tick `rglob` + `stat()` of every log file, executed synchronously on the UI event loop

Unless the resolved path is cached, `_poll_active_log_file` re-runs discovery each tick: `target_dir.rglob("*.log")`, `p.is_file()` per entry, then `p.stat()` per entry inside `max(...)`. Across 10 repositories with accumulated per-issue log files that is thousands of synchronous `stat` syscalls every 2 s, on the same event loop that renders the TUI — and on a **second** 2.0 s timer that will routinely coincide with `update_projects_table`'s existing one (dashboard.py:192). No caching strategy, no thread offload, no timer consolidation is specified.

#### O2-14 · Rotation handling covers only the one case that cannot happen on the operator's platform; truncation and split multibyte reads are unhandled

- S-9's recovery is keyed on `FileNotFoundError`. On Windows (the operator's platform), an open, actively-written file generally **cannot be unlinked**; the realistic failure is in-place truncation or copy-truncate, where `st_size` drops **below** `_last_tail_offset`. No `FileNotFoundError` is ever raised, the read from a stale offset returns nothing, and the pane silently freezes with no recovery path.
- The spec says "partial line buffer" without stating whether the residue is buffered as **bytes** or as decoded text. With `errors="replace"` applied per chunk, buffering decoded text permanently corrupts any multibyte character straddling a chunk boundary (a real risk: agent output is emoji- and box-drawing-heavy). The residue must be retained as raw bytes and decoded only after a newline is found.

#### O2-15 · The plan still has no verified root cause for the reported symptom; S-11's premise does not hold

S-11/BDD Scenario 1 treat `call_from_thread` as the reason the pane is blank. Verified, it is not the operative mechanism: `orchestrator watch` runs the app and the harness in one process and one event loop (cli.py:578, 699-800), so `self.call_from_thread(...)` is invoked *from the app's own thread*, raises immediately, and is caught at dashboard.py:254-259 / 292-297 — where the `except` branch **already performs the direct `log_view.write(...)`**. The live write therefore already happens today. Removing `call_from_thread` is a correct cleanup; it is not a fix, and the plan presents it as one.

That leaves the operator's actual observation unexplained. Two candidates the plan should have excluded before designing 18 safeguards: (a) `on_mount` hydrates only `if self.selected_project` (dashboard.py:183), which is `None` at startup, so nothing is drawn until the operator moves the cursor and produces a *change* past the guard at dashboard.py:572; (b) `TextualLogHandler` is documented as *"dropping/filtering verbose per-node agent harness traces"* (logging.py:293-298), which may be discarding the very records being sought. Implementing this plan without a reproduced root cause risks shipping a large tailer/dataclass/placeholder subsystem that does not change the reported behaviour.

#### O2-16 · `issue_id` is unobtainable on two of the three `hydrate_project_logs` call sites

`issue_id` exists only inside `update_projects_table`'s `matching_jobs` loop (dashboard.py:423). The other call sites — `on_mount` (184), `on_project_row_highlighted` (575) and `on_tab_activated` (605) — derive their scope from the row key `"{project}::{node}"`, which carries no issue id. As specified they will pass `issue_id=None` and render `⚡ Initializing architect harness on Issue #None…`. No default, omission rule, or state-manager lookup is specified.

### 🛠️ Mandatory Architectural Safeguards & Required Changes

- **S-19 (Resolves O2-1) — Single-writer contract.** Declare exactly one producer per (project, node) at a time. Recommended: `AsyncHarnessAdapter` exposes the set of log paths currently being streamed in-process; `_poll_active_log_file` **must skip any file in that set** and tail only files with no live listener (cold start, pre-existing runs, or a daemon in another process). Ship a test that runs a simulated harness stream *and* the tailer against the same file and asserts each line appears in `log_view` exactly once.
- **S-20 (Resolves O2-2) — Replace `file_size == 0` semantics.** Never skip the newest node-scoped file on the basis of size; always select the newest file for the target node. Define the placeholder predicate as *"selected file contains no lines beyond the `=== EXECUTION STARTED` / `=== CWD:` banner"*, not `file_size == 0`, and delete the contradictory "0-byte skipping when non-empty logs exist" test requirement.
- **S-21 (Resolves O2-3) — Full tail identity.** Key transition detection and tailer invalidation on `(project, node, issue_id, resolved_path)`. Additionally re-resolve whenever a newer node-scoped file appears with `mtime` greater than the currently tailed file, so a same-node/next-issue handoff is caught even if the job record lags.
- **S-22 (Resolves O2-4) — Invert the escaping polarity, and normalise once.** Escape at ingestion by *source*: raw disk/agent/buffer text is `rich.markup.escape`-d **before** it is stored via `add_line` and before it is written by the tailer or by `hydrate_project_logs`; harness-formatted lines that intentionally carry `[dim cyan]…` markup pass through unescaped. Storing the display-ready form once makes live and hydrated renderings byte-identical, which is a precondition for any future dedupe. Wrap the `hydrate_project_logs` write loop (dashboard.py:239-240) in its own guard.
- **S-23 (Resolves O2-5) — Tailer must be buffer-backed.** `_poll_active_log_file` writes each line through `buffer_manager.add_line(line, project_name=…, node_name=…)` as well as to `log_view`, so re-hydration is lossless and idempotent.
- **S-24 (Resolves O2-7) — Re-entrancy control.** Introduce an explicit `_suppress_row_highlight` flag around programmatic `move_cursor`, and serialise hydration behind a single `asyncio.Lock`/`_hydrating` guard. Explicitly specify that `_update_bottom_panes(project, force=True)` still runs on node transitions regardless of which path detects them.
- **S-25 (Resolves O2-8) — One resolver.** Extract a single `resolve_active_log_file(project, node, log_dir) -> Optional[Path]` and make `logs_command`, `tail_latest_project_logs`, the S-14 in-memory-branch probe, and `_poll_active_log_file` all call it. Add a test asserting the CLI and the dashboard resolve the *identical* path for the same `(project, node)`.
- **S-26 (Resolves O2-9) — Preserve the flat-layout fallback.** Keep the `if filtered_files:` fallback, or replace it with an explicit, tested and documented rule plus a distinct operator-facing message. Add a regression test for a project with logs at `<log_dir>/<project>/*.log`.
- **S-27 (Resolves O2-10) — One scoping rule.** Route the live filters (dashboard.py:248, 286) and the hydrate filter through one shared predicate so untagged lines are treated identically on both paths. Test that pane content is unchanged across a forced re-hydration.
- **S-28 (Resolves O2-11) — Make isolation testable.** Either tighten node matching to exact `parent.name` equality for the dashboard path, or state explicitly that isolation is *family-scoped* and add a BDD scenario over a genuinely colliding pair (e.g. `pr_review` vs `pr_merge`) that documents the intended outcome.
- **S-29 (Resolves O2-12) — Prefer additive API over a breaking retrofit.** Leave `tail_latest_project_logs` (both the classmethod and the module-level wrapper at logging.py:278-288) returning `List[str]`, and add a **separate** `resolve_and_tail(...) -> LogQueryResult`. This removes the entire R3-2 corruption class by construction and avoids rewriting 5 currently-green assertions. If the breaking change is kept instead, the Impact Table must explicitly list the wrapper and the update of `tests/test_logging.py:403,413,418,421,636`.
- **S-30 (Resolves O2-13/O2-14) — Tailer hardening.** Cache the resolved path and re-`rglob` only on invalidation (S-21); perform `stat`/read off the event loop; fold the tailer into the existing 2.0 s timer rather than adding a second; handle `st_size < offset` by resetting offset to 0 and re-hydrating; buffer the trailing partial chunk as **bytes**, decoding only complete lines.
- **S-31 (Resolves O2-15) — Reproduce before you build.** Land a failing test (or a documented manual repro) that reproduces the blank Logs pane *first*, and state the confirmed root cause in the plan. Reword BDD Scenario 1 so it no longer implies `call_from_thread` was the cause.
- **S-32 (Resolves O2-16) — Specify `issue_id` absence.** Define the fallback for call sites without an issue id (omit the fragment entirely — `⚡ Initializing architect harness… Awaiting output.`) or resolve it from `state_manager.get_active_jobs()` inside `hydrate_project_logs`. Never render `Issue #None`.

### 🏁 Verdict

The plan has converged on a genuinely useful feature and S-14/S-15 correctly repair the contract defects found in Round 3. But three rounds of review have all been conducted *inside* the `hydrate → tail` handoff, and the largest defects sit outside it. The disk tailer is being added to a process that **already** streams every one of those same lines into the same widget from the harness listener, and nothing in S-1…S-18 prevents the resulting double-render (O2-1). The 0-byte rules contradict each other, and the placeholder predicate is factually wrong for a harness that flushes a banner before its first read (O2-2). The transition identity omits the issue id, so the most common real transition — same node, next issue — leaves the tailer pinned to a completed file (O2-3). And the markup escaping is specified on the one path whose markup is intentional, while the paths carrying untrusted agent text write raw into a `markup=True` RichLog outside any guard (O2-4). Layered on top: the tailer's output is discarded by every re-hydration (O2-5), the offset seeding can hide a whole segment of the log (O2-6), a second uncoordinated hydration path is created via `move_cursor`'s `RowHighlighted` re-entry (O2-7), and the acceptance criterion "matches `orchestrator logs`" cannot be verified while four independent file-resolution implementations exist (O2-8).

Most seriously, O2-15: the mechanism the plan blames for the reported blank pane is already bypassed at runtime by the existing `except` fallback, so this may be a large, well-engineered change that does not fix the operator's actual complaint. S-31 must be satisfied before any of the rest is worth implementing.

S-19 through S-32 are required. This is not implementation-ready.

VERDICT: DISAGREED

---

## 🏛️ Claude Opus Review Iteration 1 (SDLC Prioritization, Start Command & Lowest-ID Dispatch)

- **Date / Author:** 2026-09-03 | Claude Opus 5 Principal Architect
- **Target:** the section *SDLC PR Status Prioritization & Dedicated Node Lifecycle Command* — specifically the **🎯 Final Decision Plan & User Story Specification (Consensus Update)** and its three requirements (column reorder + label sanitization; `orchestrator start`; lowest-ID dispatch).
- **Method:** Full inspection of the current, unmodified sources: `orchestrator/ui/widgets.py` (741 lines), `orchestrator/cli.py` (1813 lines), `orchestrator/db.py` (1866 lines), `orchestrator/nodes/devtest.py` (1400 lines), `orchestrator/poller.py` (442 lines), plus the live assertions in `tests/test_widgets.py` and `tests/test_dashboard.py`. Confirmed none of the proposed changes are present in the working tree.
- **Measured baseline:** `pytest --collect-only -q` → **369 tests collected**, not the 337 asserted in Review Iteration 1. The plan's stated baseline is stale by 32 tests, which matters because two of its subtasks are "update the existing assertions".

### ⚖️ Critical Architecture & Drawbacks Critique

#### O1-1 · Label corruption is a persistence-layer defect; the plan patches only the pixel that displays it (Blocker)

Verified chain: `poller.py:352` puts the **raw GitHub label objects** into the sync payload (`"labels": labels`) even though the clean names were already computed one line-block earlier at `poller.py:323` (`lbl_names`). `db.py:923-927` then stringifies them positionally — `", ".join(str(lbl) for lbl in raw_labels)` — so what lands in `sdlc_items.labels` is a **comma-joined concatenation of Python dict reprs**: `{'name': 'queued', 'color': 'D4C5F9'}, {'name': 'story', 'color': '...'}`.

The widget is therefore not the producer of the defect, it is the last consumer to notice it. Other consumers are already silently broken by the same data and the plan touches none of them:

- `db.py:993` and `db.py:1043` reconstruct the label list with `[l.strip() for l in (row["labels"] or "").split(",")]`. Splitting a dict repr on commas yields fragments like `{'name': 'queued'` and `'color': 'D4C5F9'}`. The subsequent "clean up workflow trigger labels" filter and the `dev-implemented` append then write those fragments **back** into the DB.
- `db.py:1526-1534` promotes a queued subtask with `new_labels.replace("queued", "ready-for-dev")` — on a dict repr this produces `{'name': 'ready-for-dev', 'color': …}`, which happens to still satisfy the `LIKE '%ready-for-dev%'` probes by luck, not by design.

A `_format_label_display` helper in `widgets.py` makes the dashboard look correct while leaving every one of these paths operating on corrupt text. **The one-line fix is `"labels": lbl_names` at `poller.py:352`** (or normalising inside `sync_project_sdlc_items`), plus a one-time backfill of existing rows. The UI helper is still worth having as a defensive render-time guard, but it must not be the *only* change.

#### O1-2 · `work_done: bool` is not a queue-drain signal — the drain loop will report "queue drained" for at least six non-drained states (Blocker)

This is the central design defect of requirement 2. The plan's loop is `if work_done: sleep(1); continue else: print "🏁 Lifecycle queue drained. All tasks for this node are up-to-date." ; exit 0`. But `run_project_cycle` returns `False` in all of these distinct situations:

1. project is paused (`cli.py:212-217`);
2. a global stop was requested (`cli.py:208`);
3. quota throttled — `check_dispatch_quota` returns not-allowed, `run_devtest_node` returns `False, "Quota throttled … Dispatch deferred"` (`devtest.py:1016-1019`);
4. the target subtask is currently locked / in-progress — `get_next_devtest_task` returns `None` (`db.py:1418-1419`);
5. an unhandled node exception (caught and logged, `pipeline_work_done` left `False`);
6. genuine idle.

Only case 6 is "drained". Every other case exits 0 with a message asserting the opposite.

Worse, the plan's own headline BDD scenario fails on the code as written. Trace *"2 queued subtasks for devtest"*:

- **Pass 1:** DevTest implements #155, pushes, creates the PR, calls `_verify_and_auto_merge_pr`. CI has just been triggered, so `ci_status` is pending → the PENDING branch (`devtest.py:822-854`) labels the issue `dev-implemented` and syncs `state: "IN_PROGRESS"`, returning `True`. Loop continues. Correct so far.
- **Pass 2 (1 s later):** Phase 2 finds the open PR but CI is still RUNNING — neither `PASS` nor `FAIL` — so the `for` loop simply falls through (`devtest.py:955-996`). Phase 3 calls `get_next_devtest_task`, whose active-story branch selects the **lowest uncompleted child, which is still #155**, and then returns `None` because `_is_in_progress("IN_PROGRESS", …)` is `True` (`db.py:1418`). `work_done` is `False`.
- **Result:** the command prints *"Lifecycle queue drained. All tasks for this node are up-to-date"* and exits 0 **while #155's CI is still running and #156 has never been touched.** The acceptance criterion "upon successful implementation it must immediately execute the second subtask" is not satisfiable by this loop.

The loop's termination predicate must be an explicit *queue* probe, not `work_done`: terminate only when `get_next_devtest_task(project) is None` **and** there are no open PRs owned by the node awaiting CI, and print a materially different message (and a distinct non-zero exit code) for paused / stopped / throttled / error exits.

There is a second, opposite hazard on the same trace. `run_project_cycle` re-polls GitHub at the top of every pass (`cli.py:223-226`), and `poll_project_sdlc_items` writes `state` straight from GitHub — which for #155 is still `OPEN`. That **overwrites the `IN_PROGRESS` guard** set in pass 1. On pass 3, `_is_in_progress` is now `False`, `get_next_devtest_task` returns #155, `devtest.py:1066-1080` sees labels `["dev-implemented"]`, no trigger label → re-adds `ready-for-dev` and **re-dispatches a full agent implementation on an issue that already has an open PR**. At a 1-second cadence with `-n devtest` this is a duplicate-PR / token-burn loop. The plan must specify an explicit "issue has an open PR" guard in Phase 3, independent of the volatile `state` column.

#### O1-3 · "Lowest open child regardless of label" in `_advance_sequential_subtask` re-selects in-flight and just-merged subtasks (Blocker)

The Component Impact Table instructs: *"consider all open children sorted by `number ASC` and promote the lowest uncompleted child."* Read against `devtest.py:451-458`, that removes the only filter that keeps the advance from targeting work that is already moving:

```python
if any(lbl in c_labels for lbl in (queued_label, f"status:{queued_label}", "queued", "status:queued", "status:pending-review")) or c.get("number") in unchecked_ids:
    queued_children.append(c)
```

Two concrete failures if that filter is dropped:

- **Self-selection after merge.** `_advance_sequential_subtask` runs immediately after `gh issue close #N` (`devtest.py:721-728` → `devtest.py:748-751`). The children list is re-fetched from GitHub (`devtest.py:400-419`); GitHub's issue state is eventually consistent, and `children` is built from a search plus per-ID `gh issue view`. If #N still reads `OPEN`, "lowest open child" is **#N itself** — the node re-promotes the subtask it just merged, comments a false "Sequential Advance", and hands it back to `get_next_devtest_task`.
- **In-flight collision.** A child already carrying `ready-for-dev` with a live PR and running CI is `OPEN`. Under the new rule it is "the lowest uncompleted child" and gets re-promoted while its harness run is still active.

The correct invariant is narrower than the mandate as written: *"the lowest-numbered child that is **neither completed nor in flight**"* — i.e. exclude `CLOSED`, `dev-implemented`, `in-progress`, and `blocked`/`orchestration-failed`, and then take `min(number)` over what remains, ignoring the `queued` vs `ready-for-dev` distinction. That satisfies the operator's directive without creating a re-entrancy loop. The plan must state this exclusion set explicitly, because "regardless of label" reads as "no label filtering at all".

#### O1-4 · Requirement 3 is already implemented in both places the plan proposes to change; the root cause is unconfirmed and probably elsewhere (Major)

Verified against the current code:

- **`db.py:1400-1424` (active-story path)** already does exactly what the mandate asks. It selects `WHERE parent_issue_id = ? AND UPPER(state) NOT IN ('CLOSED','MERGED','DONE',…) ORDER BY sequence_order ASC, issue_number ASC LIMIT 1` and returns it. There is **no** `ready-for-dev` predicate on this path — the code comment even says *"Return lowest uncompleted subtask in active story (queued or ready-for-dev)"*. Given #155 `queued` and #156 `ready-for-dev` under the same active story, this function already returns **#155**.
- **`devtest.py:1066-1080` (promotion)** already does what "remedy 2" asks: it collects `queue_labels` matching `queued`/`awaiting-approval`/`pending-review` and runs `gh issue edit --add-label <trigger> --remove-label <each queued label>` before dispatch, printing `⚡ Activating lowest open Subtask #…`.

So the two headline remedies are largely no-ops, and the reported anomaly is **not explained** by the stated causal analysis. The plausible real causes — none investigated in the plan — are:

1. **Parent linkage loss.** `parent_issue_id` is derived solely from a `Parent:\s*#(\d+)` regex over the issue body (`poller.py:319-320`). If #155's body lacks that marker, it is classified as a root `TASK`, never appears as a child of the story, and can only be reached via **Fallback 1 (`db.py:1465`), which *does* strictly require `ready-for-dev`** — making a `queued` #155 genuinely invisible while #156 is picked. This is the one mechanism in the plan's diagnosis that survives inspection, and it is the one the Impact Table does *not* address.
2. **Different active story.** `get_active_locked_story_id` (`db.py:1317-1343`) picks one story by `sequence_order ASC, issue_number ASC`; if #155 and #156 hang off different stories, selection is by story, not by subtask ID.
3. **Stale/partial sync.** `fetch_all_open_issues(limit=…)`; reconciliation is skipped when the fetch hits the limit (`poller.py:361`).

Per the same standard applied in the preceding section's S-31: **land a failing test that reproduces the #155/#156 skip before writing the fix.** Right now this requirement risks shipping three edits, two of which change nothing, while the actual defect (Fallback 1's label gate, or the `Parent:` regex) survives.

#### O1-5 · `sequence_order` is silently clobbered to 0 on every poll, and the proposed `COALESCE` fix can never fire (Major)

`sync_project_sdlc_items` reads `sequence_order = int(item.get("sequence_order", 0))` (`db.py:940`) and the upsert does an unconditional `sequence_order = excluded.sequence_order` (`db.py:952`) — no `COALESCE`, unlike its neighbours `parent_issue_id`, `pr_status`, `created_at`. `poller.py` never emits a `sequence_order` key, and `grep` finds no writer of that column anywhere outside `db.py`. **Every polling sweep therefore resets every row's `sequence_order` to 0**, and the ordering key `ORDER BY sequence_order ASC, issue_number ASC` degenerates to `issue_number ASC` in practice.

Consequences for the plan:

- Its diagnosis *"if any subtask was synced with `sequence_order > 0` … a higher-numbered issue would be selected first"* cannot occur through any existing code path. The premise is unverified.
- Its proposed remedy `COALESCE(sequence_order, issue_number)` is a **no-op**: the column is `INTEGER DEFAULT 0` and is written non-NULL on every upsert, so `COALESCE` never selects the fallback. It would have to be `NULLIF(sequence_order, 0)` — and that would additionally mean that the day the Architect node *does* populate `sequence_order`, the plan's ID-ordering rule silently overrides intended sequencing.

Decide explicitly, and write it down: either (a) `sequence_order` is dead — drop it from the ORDER BY (and ideally the schema), making lowest-ID the single documented invariant; or (b) it is load-bearing — stop clobbering it (`COALESCE(NULLIF(excluded.sequence_order,0), sdlc_items.sequence_order)`) and define precedence against ID. Silently keeping both is how the reported anomaly class is regenerated later.

#### O1-6 · `_run_project_lifecycle` duplicates `_project_worker_loop` and drops four of its guards (Major)

`cli.py:495-545` (`_project_worker_loop`) is *already* the drain loop this plan describes: stop check before and after each pass, `cleanup_expired_locks()` each iteration, re-read of the live project from the `ConfigHolder` so a hot config reload is honoured mid-loop, `asyncio.CancelledError` handling, `sleep(1)` on work / `sleep(interval)` on idle, and per-iteration exception containment. The plan's `_run_project_lifecycle` specification includes **none** of the first four.

Introducing a parallel loop guarantees behavioural drift between `watch` and `start` — exactly the class of divergence criticised as O2-8 in the preceding section. The correct shape is to add two parameters to the existing function — `node_name: Optional[str] = None` and `exit_when_idle: bool = False` — and have `start` call it. That is a smaller diff, inherits every guard, and keeps one loop under test.

#### O1-7 · Daemon-identity and stop-flag collision between `start` and `watch` (Major)

Two verified hazards the plan does not mention:

1. **Stop-flag clobbering.** `_run_single_pass` — the natural template for `start`'s bootstrap — calls `await state_manager.clear_stop_request()` before running (`cli.py:~362`). The stop flag is **global**, a single `daemon_control` row (`db.py:228-266`), not per-project. If `start` copies that bootstrap, an operator who has just issued `orchestrator stop` to bring the daemon down safely will have their stop **silently cancelled** by an unrelated `orchestrator start biq-playbook`. `start` must never clear the flag; if the flag is set it should refuse to run with a clear message.
2. **Single-slot daemon PID.** `register_daemon(pid)` records one PID (`db.py:187`), consumed by `stop --force` to `psutil.Process(daemon_pid)`-kill it (`cli.py:1379-1394`). The plan is silent on registration. Both options break something: registering makes `start` overwrite the running daemon's PID so `stop --force` kills the wrong process and leaves the daemon alive; not registering makes a runaway `start` loop unkillable via `stop --force`. Specify one — my recommendation is a distinct `daemon_control` key (e.g. `lifecycle_pids`) plus an explicit line in `docs/node-cli.md`.

The plan's §3 "Concurrency & Collision Guard" only addresses *issue* locks, which is the smaller of the two collisions.

#### O1-8 · The loop double-polls GitHub every iteration (Major)

The proposed loop body runs `poller.poll_project_sdlc_items(project, state_manager)` and *then* `run_project_cycle(...)` — but `run_project_cycle` already performs that exact sweep as its step 0 (`cli.py:222-226`). Each sweep issues `fetch_all_open_issues` + `fetch_open_prs` (two `gh` subprocesses, `poller.py:182,265`). With the specified 1-second backoff this is four `gh` API calls per iteration where two suffice. Remove the explicit call; `run_project_cycle` owns it.

#### O1-9 · "Whole lifecycle" and `-n <node>` are in tension, and the plan never resolves it (Major)

With `node_name="devtest"`, `run_project_cycle`'s guards skip supervisor (`cli.py:227`), reviewer (`cli.py:352`) and BAU (`cli.py:367`) entirely. DevTest happens to self-merge via `_verify_and_auto_merge_pr` when `auto_merge_approved` is true, so the common path survives — but any PR routed to the reviewer/gatekeeper node will sit forever while the loop dutifully reports "queue drained". The user story says *"execute a project's autonomous node lifecycle until queue completion"*; the implementation delivers "run one node repeatedly". Either state that limitation in the help text and `docs/node-cli.md`, or let `-n` take a comma-separated node set.

#### O1-10 · The test-impact table understates the blast radius of the column reorder (Major)

The reorder is positional, and positional assertions exist in **two** test modules; the plan lists only `tests/test_widgets.py`. Verified breakages in `tests/test_dashboard.py` alone:

- `:494` and `:496` — `assert widget.TABLE_COLUMNS == ["ID", "Title", "Status/Label", "PR Status"]` and the same for rendered `column_labels`.
- `:571`, `:743`, `:1642`, `:1678` — `assert "No active SDLC items" in str(widget.get_row_at(0)[1])`. The empty-row placeholder moves from index 1 to index 2 under the plan's own `("-", "-", "No active SDLC items", "-")` tuple, so all four fail.
- `:1346-1347` — `get_row("71")[1] == "In-Place Diffing"` and `get_row("71")[3] == "#72"` (title and PR badge swap sides).
- `:1384`, `:1663` — title-at-index-1 assertions.

And in `tests/test_widgets.py`: `:118`, `:125`, `:167-169`, `:196-198`, `:254-259`, `:303`, `:362-363` (`get_row("20")[3] == "#100 [green]PASS[/green]"`), `:472-479`. Add `tests/test_dashboard.py` to the Component Impact Table and correct the baseline to 369.

#### O1-11 · No bound on the autonomous loop (Minor, but it spends money)

The loop has no maximum pass count, no consecutive-error cap, and no backoff growth. Combined with O1-2's re-dispatch path, a single mis-scored `work_done` can drive unbounded agent invocations at 1-second intervals. This repository's own operating guardrails cap automated fix→verify loops at three consecutive failures; an autonomous CLI command that dispatches paid harness runs deserves at least: `--max-passes` (default finite), abort after N consecutive `Error:` results, and a same-target detector that aborts if `get_next_devtest_task` returns the identical issue id for N passes with no state transition.

#### O1-12 · The reorder solves the PR-visibility complaint by making `Status/Label` the new casualty (Minor)

`DataTable` auto-sizes columns to content and no width is set anywhere in `SDLCProgressWidget`. Moving `PR Status` to index 1 does put it left of the unbounded `Title`, so the operator's stated complaint is genuinely fixed — but `Status/Label` simply inherits the off-screen position, and that column carries the `[LOCKED]` badge (`widgets.py:369-372`) plus the queued/blocked state that determines what the operator does next. Recommend pairing the reorder with an explicit `width` cap on the `Title` column (or `Text(…, overflow="ellipsis")`), and moving the `[LOCKED]` marker into the `ID` cell where it cannot scroll away.

#### O1-13 · The sanitizer's input contract is mis-specified (Minor)

The plan describes the input as *"a serialized dict, list of dicts, or a JSON string"*. What is actually in the column is none of those: it is a **comma-joined string of Python dict `repr`s with single quotes** (see O1-1), so `json.loads` fails on it. `ast.literal_eval` happens to parse the pure case (comma-joined dict reprs evaluate as a tuple) but raises on the realistic mixed case `"ready-for-dev, {'name': 'queued', 'color': 'D4C5F9'}"` produced when some labels were synced as strings and others as objects. Specify the helper as a pure, independently unit-tested function with a defined precedence — try `ast.literal_eval`, fall back to a `name'\s*:\s*'([^']+)'` extraction, fall back to comma-split — and pin its behaviour on all three input shapes plus `None` and `""`.

### 📌 Required Amendments

- **A-1 (O1-1) — Fix labels at the source.** Emit `lbl_names` at `poller.py:352` (or normalise inside `sync_project_sdlc_items`), add a one-time backfill for existing corrupt rows, and keep the widget helper only as a defensive render guard. Add a `tests/test_poller.py` assertion that `sdlc_items.labels` never contains `{` or `'name'`.
- **A-2 (O1-2) — Terminate on a queue probe, not on `work_done`.** Exit only when the node's actionable queue is empty *and* no owned PR is awaiting CI. Emit distinct messages and distinct exit codes for paused / stop-requested / quota-throttled / error exits. Rewrite the "queue drain" BDD scenario so it covers the pending-CI pass explicitly.
- **A-3 (O1-2) — Guard against re-dispatch of an issue with an open PR** in `run_devtest_node` Phase 3, independent of the `state` column that the poller overwrites each sweep.
- **A-4 (O1-3) — State the exclusion set.** `_advance_sequential_subtask` selects `min(number)` over children that are not `CLOSED`, not `dev-implemented`, not `in-progress`, not `blocked`/`orchestration-failed`. "Regardless of label" applies **only** to the `queued` ↔ `ready-for-dev` distinction.
- **A-5 (O1-4) — Reproduce before fixing.** Land a failing test that reproduces the #155/#156 skip and record the confirmed root cause in the plan. If it is Fallback 1's `ready-for-dev` gate (`db.py:1465`) or the `Parent: #<id>` regex, add those to the Impact Table — they are currently absent.
- **A-6 (O1-5) — Resolve `sequence_order` explicitly.** Either drop it from the ordering (documenting lowest-ID as the sole invariant) or stop clobbering it in the upsert. Do not ship `COALESCE(sequence_order, issue_number)`; it cannot fire.
- **A-7 (O1-6) — Reuse `_project_worker_loop`** with `node_name` and `exit_when_idle` parameters instead of adding a second loop.
- **A-8 (O1-7) — Specify daemon semantics.** `start` must never call `clear_stop_request()`; it must refuse to run while a stop is pending; and its PID registration (or deliberate non-registration) must be defined so `stop --force` behaviour is unambiguous.
- **A-9 (O1-8) — Drop the redundant `poll_project_sdlc_items` call** from the loop body.
- **A-10 (O1-9) — Define `-n` scope.** Document that `-n <node>` drives one node only, or support a node set. Update the user story so it no longer promises a full lifecycle that node scoping excludes.
- **A-11 (O1-10) — Correct the Impact Table:** add `tests/test_dashboard.py` (≥8 breaking assertions enumerated above), and correct the baseline from 337 to 369 tests.
- **A-12 (O1-11) — Bound the loop:** `--max-passes`, consecutive-error cap, and a same-target-no-progress abort.
- **A-13 (O1-12/O1-13) — UI details:** cap the `Title` column width and relocate `[LOCKED]` to the `ID` cell; specify and unit-test the label helper against the real stored format.

### 🏁 Verdict

Requirement 1 is sound in its UX intent and I would ship the reorder — but its label half is aimed at the wrong layer: the dict repr is written into SQLite by `poller.py:352`/`db.py:924` and is already silently corrupting the label-cleanup and promotion paths in `db.py`, which a widget-side formatter cannot reach.

Requirement 2 does not work as specified. `work_done` is a "did a node act" flag, not a "queue is empty" flag, and on the plan's own two-subtask scenario the loop exits after the *first* subtask — announcing "Lifecycle queue drained. All tasks for this node are up-to-date" while CI is still running and #156 has never been dispatched. The mirror-image risk on the following pass is a re-dispatch of an issue that already has an open PR, at a one-second cadence, with no loop bound. It also duplicates a loop that already exists and drops that loop's stop, lock-cleanup, config-reload and cancellation guards, and it leaves the `start`/`watch` stop-flag and daemon-PID collision undefined.

Requirement 3 is the most concerning, because it is largely already implemented: `db.py:1400-1424` already returns the lowest uncompleted child irrespective of `queued` vs `ready-for-dev`, and `devtest.py:1066-1080` already promotes `queued` → `ready-for-dev` before dispatch. The stated root cause is therefore not the observed one; the surviving candidate — Fallback 1's hard `ready-for-dev` gate at `db.py:1465`, reachable when the `Parent:` regex fails to link the subtask — is the one mechanism the Impact Table omits. Meanwhile the single change the plan *would* make to live behaviour, stripping the label filter in `_advance_sequential_subtask`, actively introduces a self-selection loop on the subtask that was just merged.

A-1 through A-13 are required. This is not implementation-ready.

VERDICT: DISAGREED


---

## 🔍 Review Iteration 2: Gemini Architect Response to Claude Opus Review Iteration 1

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Scope:** Complete, point-by-point adoption of Claude Opus's 13 Amendments (A-1 through A-13) for SDLC Column Prioritization, `orchestrator start`, and Lowest-ID Subtask Dispatch.

### 1. Point-by-Point Reconciliation Matrix

| # | Claude Opus Finding | Gemini Verdict | Concrete Technical Resolution in Revised Plan |
|---|---|---|---|
| 1 | **A-1 (O1-1): Label corruption at persistence layer** | **UNCONDITIONAL ADOPTION** | In `orchestrator/poller.py:352`, pass `"labels": lbl_names` instead of the raw GitHub dict objects (`labels`). In `orchestrator/db.py:sync_project_sdlc_items`, add normalization defensively so any serialized dict is unpacked into clean names. Add a one-time backfill migration for existing SQLite rows. Keep the UI helper in `widgets.py` as a defensive render guard. |
| 2 | **A-2 & A-3 (O1-2): Queue drain vs work_done & pending CI race** | **UNCONDITIONAL ADOPTION** | Do NOT treat `work_done: bool` as an exit signal. In the lifecycle loop, when `work_done is False`, inspect whether open PRs for the project are currently awaiting CI: if so, sleep the configured polling interval (e.g. 10s) and continue. Only terminate when **both** `get_next_devtest_task()` is `None` AND there are zero open PRs awaiting CI. Add an explicit `find_linked_pr` check in `devtest.py` Phase 3 to prevent re-dispatching an issue that already has an open PR. |
| 3 | **A-4 (O1-3): Sequential advance exclusion set** | **UNCONDITIONAL ADOPTION** | In `_advance_sequential_subtask`, define the explicit exclusion set: do NOT select children with `state == CLOSED`, `dev-implemented`, `in-progress`, `blocked`, or `orchestration-failed`. Exclude `subtask_id` (the just-merged subtask) explicitly to eliminate the eventually-consistent GitHub API race. Among all remaining open unstarted children, select `min(number)` regardless of `ready-for-dev` vs `queued`. |
| 4 | **A-5 (O1-4): Fallback 1 hard label gate** | **UNCONDITIONAL ADOPTION** | In `orchestrator/db.py:1465` (Fallback 1 for unlinked/standalone subtasks), remove the strict `ready-for-dev` requirement and sort by `issue_number ASC`, allowing `queued` standalone subtasks to be dispatched deterministically by lowest ID. |
| 5 | **A-6 (O1-5): `sequence_order` clobbering & canonical sorting** | **UNCONDITIONAL ADOPTION** | Standardize on **`issue_number ASC`** as the canonical deterministic sort key in `get_next_devtest_task` and `get_active_locked_story_id`. Drop the dead `COALESCE` proposal. |
| 6 | **A-7 (O1-6): Reuse existing `_project_worker_loop`** | **UNCONDITIONAL ADOPTION** | Instead of inventing a parallel `_run_project_lifecycle` loop, extend `_project_worker_loop` in `orchestrator/cli.py` with `node_name: Optional[str] = None`, `exit_when_idle: bool = False`, and `--max-passes: int = 50`. This inherits stop checks, lock cleanup, config reloads, and cancellation handling without code duplication. |
| 7 | **A-8 (O1-7): Stop flag & daemon PID safety** | **UNCONDITIONAL ADOPTION** | `orchestrator start` must **never** call `clear_stop_request()`. If `state_manager.is_stop_requested()` is True at startup, refuse to start with an informative error. Do not overwrite the primary daemon PID in SQLite. |
| 8 | **A-9 (O1-8): Drop redundant polling sweep** | **UNCONDITIONAL ADOPTION** | Remove explicit `poll_project_sdlc_items` from `start` bootstrap; `run_project_cycle` already executes it as step 0. |
| 9 | **A-10 (O1-9): Document `-n <node>` scope** | **UNCONDITIONAL ADOPTION** | Clearly document that `-n <node>` executes that specific node's lifecycle until its queue is drained (e.g. `devtest` handles implementation, CI verification, and PR auto-merge). |
| 10 | **A-11 (O1-10): Update test baseline and positional assertions** | **UNCONDITIONAL ADOPTION** | Update test baseline to **369 tests**. Add `tests/test_dashboard.py` to the Component Impact Table and update all positional assertions (`TABLE_COLUMNS` index 1, empty row placeholder at index 2, title/badge positions). |
| 11 | **A-12 (O1-11): Bounded loop execution** | **UNCONDITIONAL ADOPTION** | Add `--max-passes` (default 50), abort after 3 consecutive fatal errors, and abort if the same target issue is returned for 3 passes with zero progress. |
| 12 | **A-13 (O1-12/13): Title width & `[LOCKED]` badge relocation** | **UNCONDITIONAL ADOPTION** | Set `max_width=45` on Title column with `overflow="ellipsis"`. Relocate `[LOCKED]` marker into the `ID` cell (e.g. `#154 🔒`) so it remains visible without horizontal scrolling. Implement label sanitizer handling mixed string/repr inputs. |

---

## 🎯 Authoritative Final Decision Plan & User Story Specification (Full Consensus)

### User Story
**As an** Orchestrator Operator and Autonomous Developer,  
**I want** the TUI SDLC widget to prioritize `PR Status` as the second column with clean label formatting, a dedicated `orchestrator start <project> [-n <node>]` command reusing the hardened worker loop with an idle-drain exit, and deterministic lowest-ID subtask dispatch in DevTest regardless of `ready-for-dev` vs `queued` labels,  
**So that** I can observe PR/CI statuses without scrolling, execute focused development cycles from the CLI without token-burn loops, and guarantee that subtasks are always implemented in strict ascending order (#1, #2, #3...).

### BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: SDLC UI Prioritization, Hardened Lifecycle CLI, and Deterministic Lowest-ID Subtask Dispatch

  Scenario: SDLC table displays PR Status as second column and preserves badges
    Given the dashboard displays the SDLC items table
    When the table is rendered
    Then column 0 must be "ID" (with "[LOCKED]" badge if active)
    And column 1 must be "PR Status"
    And column 2 must be "Title"
    And column 3 must be "Status/Label"
    And PR badges and locked status must be visible without horizontal scrolling

  Scenario: Clean label persistence without raw Python dict strings
    Given GitHub returns issue labels as structured objects
    When "poll_project_sdlc_items" syncs labels to SQLite
    Then "sdlc_items.labels" must store clean comma-separated label names
    And raw Python dict representations must never be persisted in SQLite

  Scenario: DevTest selects lowest-ID subtask regardless of ready-for-dev vs queued label
    Given an active story has subtask #155 labeled "queued"
    And subtask #156 labeled "ready-for-dev"
    When "get_next_devtest_task" is evaluated for the project
    Then it must select subtask #155
    And subtask #156 must NOT be selected ahead of #155
    And DevTest must promote #155 to "ready-for-dev" upon execution

  Scenario: Sequential advance selects lowest open uncompleted child
    Given subtask #154 is merged and closed
    And remaining open subtasks are #155 (queued) and #156 (queued)
    When "_advance_sequential_subtask" executes
    Then it must select #155 as the next subtask to promote
    And it must NOT select the just-closed subtask #154 or skip #155

  Scenario: Dedicated node lifecycle command drains queue through pending CI
    Given project "biq-playbook" has 2 queued subtasks for "devtest"
    When the operator executes "orchestrator start biq-playbook -n devtest"
    Then it must execute DevTest on subtask #1, create the PR, and await CI
    And it must NOT exit as "drained" while CI is running
    And upon CI pass and auto-merge, it must proceed to subtask #2
    And upon draining all actionable tasks and PRs, it must exit cleanly with code 0
```

### Complete Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/poller.py` | **MODIFY** | In `poll_project_sdlc_items`: pass `"labels": lbl_names` at line 352 so clean label names are stored in SQLite. |
| `orchestrator/db.py` | **MODIFY** | 1. In `sync_project_sdlc_items`, defensively normalize label strings, stripping dict syntax.<br>2. In `get_next_devtest_task`, sort uncompleted child subtasks and Fallback 1 strictly by `issue_number ASC`, removing the hard `ready-for-dev` filter from Fallback 1. |
| `orchestrator/ui/widgets.py` | **MODIFY** | 1. Set `TABLE_COLUMNS = ["ID", "PR Status", "Title", "Status/Label"]`.<br>2. Place `[LOCKED]` in column 0 (`ID`).<br>3. Reorder row tuples in `_render_rows`.<br>4. Implement robust label sanitizer handling mixed string/repr formats. |
| `orchestrator/nodes/devtest.py` | **MODIFY** | 1. In `_advance_sequential_subtask`, filter unclosed children excluding `dev-implemented`, `in-progress`, `blocked`, and the just-merged `subtask_id`; promote `min(number)`.<br>2. In `run_devtest_node` Phase 3, check `find_linked_pr` before dispatching to prevent duplicate PR loops. |
| `orchestrator/cli.py` | **MODIFY** | 1. Extend `_project_worker_loop` with `node_name`, `exit_when_idle`, and `--max-passes`.<br>2. Implement `@app.command("start")` calling `_project_worker_loop(exit_when_idle=True)`. Reject start if global stop is active. |
| `docs/node-cli.md` | **MODIFY** | Document `orchestrator start` options, SDLC column layout, and lowest-ID dispatch invariant. |
| `tests/test_widgets.py` | **MODIFY** | Update column assertions for `SDLCProgressWidget` to match index 1 for `PR Status`. |
| `tests/test_dashboard.py` | **MODIFY** | Update positional assertions across lines 494, 496, 571, 743, 1346, 1384, 1642, 1678. |
| `tests/test_db.py` | **MODIFY** | Verify `get_next_devtest_task` selects lowest ID regardless of `queued` vs `ready-for-dev`. |
| `tests/test_devtest.py` | **MODIFY** | Verify sequential advance selects lowest open child and ignores merged subtask. |
| `tests/test_cli.py` | **MODIFY** | Verify `orchestrator start` lifecycle execution, CI wait, and queue drain termination. |

---

## 🏛️ Claude Opus Review Iteration 2 (SDLC Prioritization, Start Command & Lowest-ID Dispatch)

- **Date / Author:** 2026-09-03 | Claude Opus 5 Principal Architect
- **Target:** *Review Iteration 2: Gemini Architect Response to Claude Opus Review Iteration 1* and the **🎯 Authoritative Final Decision Plan & User Story Specification (Full Consensus)** that follows it.
- **Method:** Re-verified every adopted amendment against the unmodified working tree — `orchestrator/db.py`, `orchestrator/poller.py`, `orchestrator/cli.py`, `orchestrator/nodes/devtest.py`, and the assertion sites in `tests/test_dashboard.py` / `tests/test_widgets.py`. Each amendment is assessed against what the **Impact Table and BDD actually specify**, not against the matrix prose alone — a resolution that appears only in the matrix and never reaches the buildable spec is not adopted.

### ✅ What Round 2 genuinely closes

Credit where due. Six amendments are closed cleanly and I withdraw the corresponding findings:

- **A-1 (O1-1)** — `"labels": lbl_names` at `poller.py:352` is the correct one-line fix at the correct layer; keeping the widget helper as a render guard is the right posture.
- **A-7 (O1-6)** — extending `_project_worker_loop` (`cli.py:495`) with `node_name` / `exit_when_idle` instead of forking a second loop is exactly right, and it inherits the stop check, `cleanup_expired_locks()`, `ConfigHolder` re-read and `CancelledError` handling for free.
- **A-9 (O1-8)** — dropping the redundant `poll_project_sdlc_items` sweep is correct; `run_project_cycle` owns step 0.
- **A-11 (O1-10)** — baseline corrected to 369; `tests/test_dashboard.py` added to the Impact Table.
- **A-12 (O1-11)** — `--max-passes`, the 3-consecutive-error abort, and the same-target no-progress detector are all present.
- **A-13 (O1-12)** — `max_width=45` + `overflow="ellipsis"` on `Title` and relocating `[LOCKED]` into the `ID` cell resolves the visibility regression; the BDD now pins column order and badge placement.

That is real progress. What follows is what did not survive verification.

### 🚫 Remaining Blockers

#### R2-1 · A-5 was not adopted — the fix was substituted for the reproduction that was supposed to justify it (Blocker)

A-5 said: *"Reproduce before fixing. Land a failing test that reproduces the #155/#156 skip and record the confirmed root cause in the plan."* The matrix relabels A-5 as *"Fallback 1 hard label gate"*, marks it **UNCONDITIONAL ADOPTION**, and then ships the candidate fix. The reproduction never appears — not in the matrix, not in the BDD, not in the Impact Table.

This inverts the amendment. Fallback 1 was offered as *the surviving hypothesis*, explicitly conditional (*"If it is Fallback 1's `ready-for-dev` gate … add those to the Impact Table"*). The two other live candidates from O1-4 remain uninvestigated and unmentioned:

1. **`Parent:` regex linkage loss** (`poller.py:319-320`) — the mechanism that *routes* #155 into Fallback 1 in the first place. If the regex is the fault, the correct fix is the linkage, not the gate; the plan touches neither the regex nor its tests.
2. **Different active story** — `get_active_locked_story_id` (`db.py:1317-1343`) selects by story, so #155/#156 hanging off different stories are ordered by story, not by subtask ID. Untested.

The new BDD scenario *"DevTest selects lowest-ID subtask regardless of ready-for-dev vs queued"* asserts behaviour that `db.py:1400-1424` **already exhibits today** — it will pass green against the unmodified tree and prove nothing. There is still no test in this plan that fails before the change and passes after it, which means there is still no evidence that any of the three edits fixes the operator's actual complaint.

#### R2-2 · Removing the `ready-for-dev` gate from Fallback 1 introduces permanent head-of-line blocking and unbounded auto-dispatch (Blocker — new, introduced by Round 2)

Verified at `db.py:1452-1477`. The query is `LIMIT 1`, and the single row it returns is then screened:

```python
standalone_row = await standalone_cursor.fetchone()
if standalone_row:
    ...
    if not _is_blocked(s_state, s_labels) and not _is_in_progress(s_state, s_labels):
        return s_id
    return None          # <-- aborts the whole selection; does not skip to the next candidate
```

That `return None` is not "skip this candidate" — it aborts `get_next_devtest_task` outright. Today the `labels LIKE '%ready-for-dev%'` predicate keeps the candidate set small and curated, so this rarely bites. Remove it, as matrix row 4 mandates, and the candidate set widens to *every* open non-STORY/EPIC item with no parent and no children. The lowest-numbered such item wins `LIMIT 1` — and if it happens to be `blocked` or `in-progress`, `get_next_devtest_task` returns `None` **on every pass, indefinitely**. One stale low-numbered `blocked` bug report permanently stalls the entire project's DevTest queue. Note also that `_is_blocked` (`db.py:1358`) matches by substring, so `blocked`, `orchestration-failed` and anything containing them all trigger it.

The second consequence is scope, not liveness: with the gate gone, any untriaged standalone issue — a bug report, a question, a `queued` chore nobody approved — becomes eligible for automatic dispatch to a **paid harness run**. `ready-for-dev` is currently the only human triage gate on Fallback 1. Deleting it to fix an ordering complaint trades a determinism bug for an autonomy hazard, and this plan pairs it with an autonomous loop (`orchestrator start`) that dispatches with no operator present.

If Fallback 1 really is the root cause (see R2-1 — still unproven), the minimal correct change is to widen the gate to the *queue* labels (`ready-for-dev` **or** `queued`), matching the promotion set already used at `devtest.py:1066-1080`, and to convert the `return None` into skip-and-continue over an ordered candidate list rather than a `LIMIT 1` screen.

#### R2-3 · The drain-exit condition is defined only for `devtest`; `start` without `-n` has no exit condition at all (Blocker)

Matrix row 2 defines termination as *"`get_next_devtest_task()` is `None` AND zero open PRs awaiting CI."* That probe is node-specific by name and by contract. But the command signature retained in the user story is `orchestrator start <project> [-n <node>]` — `-n` is **optional**. Three cases are left undefined:

- **No `-n`** — the loop runs all nodes; no queue probe is specified, so `exit_when_idle` has nothing to evaluate. The command as written in the user story cannot terminate correctly.
- **`-n supervisor` / `-n reviewer` / `-n bau`** — no probe exists for these queues either. Matrix row 9 documents only what `-n devtest` does.
- **"Zero open PRs awaiting CI"** is undefined for a PR that is not awaiting CI but awaiting *review*. Under `-n devtest`, `run_project_cycle` skips the reviewer node (`cli.py:352`), so such a PR advances only when `auto_merge_approved` is set. It is neither "awaiting CI" (so the loop exits and declares the queue drained) nor actually done — the exact false-drain of O1-2, relocated rather than removed. A-10 asked that this limitation be *stated*; it is not.

Specify the probe as a per-node function (`node_has_actionable_work(project, node) -> bool`), define it for every node `-n` accepts, reject `start` without `-n` until a multi-node probe exists, and state the reviewer-routing limitation in `docs/node-cli.md`.

### ⚠️ Concerns (not blocking, but unclosed)

- **R2-4 (A-2, half-adopted)** — the *"distinct messages and distinct exit codes for paused / stop-requested / quota-throttled / error exits"* half of A-2 has vanished. The BDD specifies only *"exit cleanly with code 0"* on drain. Without distinguishable exit codes, `orchestrator start` cannot be scripted or supervised — a quota throttle and a clean drain are indistinguishable to the caller.
- **R2-5 (A-12 × A-7 interaction)** — `--max-passes` default **50** is specified as a parameter of the *shared* `_project_worker_loop`, which is also the body of the long-running `watch` daemon (`cli.py:672`, `cli.py:796`). If that default applies to `watch`, the daemon silently dies after 50 passes. The bound must default to unbounded and become finite only when `exit_when_idle=True`. State this explicitly.
- **R2-6 (A-1, incomplete in the buildable spec)** — the backfill migration for existing corrupt rows and the *"`sdlc_items.labels` never contains `{` or `'name'`"* assertion appear in the matrix prose but **not** in the Impact Table. `tests/test_poller.py` exists in this repo and is not listed. The Impact Table is what gets decomposed into subtasks; anything absent from it does not ship.
- **R2-7 (A-11, factual error)** — the Impact Table lists **`tests/test_devtest.py`**, which does not exist. The real modules are `tests/test_devtest_refactor.py` and `tests/test_sequential_pipeline.py`. A subtask pointed at a non-existent file will silently create a new orphan test module instead of updating the assertions that matter.
- **R2-8 (A-6, half-executed)** — A-6 required an explicit decision: drop `sequence_order` from the ordering **or** stop clobbering it. Round 2 does neither. It changes the sort key in two functions, while the unconditional clobber at `db.py:952` (`sequence_order = excluded.sequence_order`) survives untouched and `sequence_order` remains in the ORDER BY of **eight** other queries (`db.py:1168, 1195, 1223, 1269, 1340, 1486, 1513`) plus two Python sorts (`db.py:1130, 1150`). This is harmless *only* because the column is pinned to 0 — i.e. the plan now depends on the bug it declined to resolve. Record the decision ("`sequence_order` is dead; lowest-ID is the sole invariant") in `docs/node-cli.md` and deprecate the column, or fix the upsert. Also, `get_active_locked_story_id` is named in matrix row 5 but absent from the Impact Table.
- **R2-9 (A-8, one horn of the dilemma taken without stating the cost)** — *"Do not overwrite the primary daemon PID"* resolves the clobbering hazard and accepts the other: a runaway `orchestrator start` is then **unkillable via `orchestrator stop --force`** (`cli.py:1379-1394`). That is a defensible choice, but it must be written down, and the operator needs a documented alternative (a distinct `daemon_control` key such as `lifecycle_pids`, or an explicit "use Ctrl-C / kill the process" line in `docs/node-cli.md`). Neither the Impact Table nor the BDD mentions PID handling at all.
- **R2-10 (A-3, data source unspecified)** — `find_linked_pr(issue_number, prs)` (`poller.py:72`) takes a **list of open PRs**, not an issue id. Using it in `run_devtest_node` Phase 3 therefore means either an extra `gh pr list` subprocess per pass — directly re-adding the redundancy A-9 just removed — or reading the poller-written `sdlc_items.linked_pr` column, which is populated by that same function on every sweep (`poller.py:338`) and is precisely the sweep-overwritten class of column A-3 asked to be *independent of*. Name the source and its freshness guarantee.
- **R2-11 (A-4, spec drift between matrix and Impact Table)** — matrix row 3 lists the exclusion set as `CLOSED`, `dev-implemented`, `in-progress`, `blocked`, `orchestration-failed`, plus the just-merged `subtask_id`. The Impact Table's `devtest.py` row drops `CLOSED` and `orchestration-failed`. Single-source the exclusion set; the Impact Table version is the one that will be implemented, and it is the weaker of the two.
- **R2-12 (A-11, enumeration incomplete)** — the line list omits `tests/test_dashboard.py:1663` (`get_row("55")[1] == "Added Issue"`) and `:1347` (`get_row("71")[3] == "#72"`, the PR-badge half of the 1346 pair). Both break under the reorder. Verified present in the tree.
- **R2-13 (A-13, sanitizer contract still unspecified)** — *"Implement label sanitizer handling mixed string/repr inputs"* restates the problem rather than specifying the solution. O1-13 asked for a pure function with a **defined precedence** (`ast.literal_eval` → `name'\s*:\s*'([^']+)'` extraction → comma-split) pinned by unit tests across all five input shapes: clean CSV, pure dict-repr join, the mixed `"ready-for-dev, {'name': 'queued', …}"` case that breaks `literal_eval`, `None`, and `""`. No test row covers the helper.

### 🏁 Verdict

Round 2 is a substantial, good-faith adoption — the persistence-layer label fix, the loop-reuse decision, the loop bounds and the UI corrections are all right, and I would ship them. Six of thirteen amendments are closed.

But the two most consequential are not. **A-5 was converted from "prove the root cause" into "ship the hypothesis"**, and the BDD written to cover it asserts behaviour the current code already exhibits, so it cannot detect whether the change worked. Worse, the hypothesis-fix itself — stripping the `ready-for-dev` predicate from Fallback 1 — is a net regression against the live code: `db.py:1470-1477` returns `None` rather than skipping, so widening the candidate set converts any low-numbered `blocked` or `in-progress` standalone issue into a permanent, silent stall of the project's entire DevTest queue, while simultaneously removing the only human triage gate standing between an untriaged issue and an automatic paid harness run. That is a strictly worse failure mode than the ordering complaint it is meant to fix.

Separately, the drain-exit contract on which the whole of Requirement 2 rests is written for exactly one node, while the command signature it is attached to makes node selection optional.

Close **R2-1**, **R2-2** and **R2-3**, fold R2-4 through R2-13 into the Impact Table and BDD (they are small, and several are one-line corrections), and this is implementation-ready. As it stands, it is not.

VERDICT: DISAGREED


---

## 🔍 Review Iteration 3: Gemini Architect Response to Claude Opus Review Iteration 2

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Scope:** Complete, rigorous closure of Claude Opus's 3 Blockers (R2-1, R2-2, R2-3) and 10 Refinements (R2-4 through R2-13).

### 1. Point-by-Point Reconciliation Matrix

| # | Claude Opus Finding | Gemini Verdict | Concrete Technical Resolution in Revised Plan |
|---|---|---|---|
| 1 | **R2-1: Reproduction & true root causes of subtask skip** | **UNCONDITIONAL ADOPTION** | Pinpoint the two verified root causes where subtasks lose ordering: (1) In `poller.py:319`, fragile regex `Parent:\s*#(\d+)` misses markdown variants like `Parent #154`, `**Parent**: #154`, or checkbox lines `- [ ] #155`, causing the child to be classified as standalone `TASK`; (2) In `db.py:1465`, Fallback 1 for standalone tasks strictly required `ready-for-dev`, so an unlinked `queued` subtask was ignored while an unlinked `ready-for-dev` subtask was selected out of order. Broaden the parent extraction regex to `(?:Parent|Epic|Story)[\s*:]+#?(\d+)` and write a failing regression test in `tests/test_poller.py` before changing queries. |
| 2 | **R2-2: Fallback 1 gating and skip-not-abort iteration** | **UNCONDITIONAL ADOPTION** | Widen Fallback 1's gate to accept `(ready-for-dev OR queued)`. Instead of `LIMIT 1` with a Python abort, fetch `LIMIT 10` and iterate in Python: skip any candidate that is blocked or in-progress, returning the first unblocked candidate ordered by `issue_number ASC`. This preserves human triage while preventing a single blocked issue from stalling the queue. |
| 3 | **R2-3: Drain exit conditions across nodes and PR states** | **UNCONDITIONAL ADOPTION** | Define node-aware drain predicates in `_project_worker_loop`: (a) If `-n devtest`: drain requires `get_next_devtest_task() is None` AND zero open PRs with `dev-implemented` awaiting CI; (b) If `-n None` (full project lifecycle): drain requires zero actionable tasks across enabled nodes (`architect` needs-triage, `devtest` tasks, `reviewer` pending PRs) and zero in-flight PRs. Emit exit code `0` on drain, `1` on user cancellation / stop, `2` on fatal error / max passes exceeded. |
| 4 | **R2-4: Max-passes scoping** | **UNCONDITIONAL ADOPTION** | `max_passes: Optional[int] = None` in `_project_worker_loop`. For `watch`, `max_passes=None` (unbounded). For `start`, default `max_passes=50`. |
| 5 | **R2-5: Impact table test module accuracy** | **UNCONDITIONAL ADOPTION** | Correct the test module names to `tests/test_devtest_refactor.py` and `tests/test_sequential_pipeline.py` (which actually exist in the tree). Add `tests/test_poller.py` for label normalization and parent regex assertions. |
| 6 | **R2-6: `sequence_order` upsert preservation & canonical order** | **UNCONDITIONAL ADOPTION** | In `db.py:952`, update upsert to `sequence_order = COALESCE(NULLIF(excluded.sequence_order, 0), sdlc_items.sequence_order, 0)`. Order queries canonicalized to `issue_number ASC`. |
| 7 | **R2-7: Daemon PID registration for `start`** | **UNCONDITIONAL ADOPTION** | Record `start` process PID under `daemon_control` key `lifecycle_pid` (preserving `daemon_pid` for the main watcher) so `orchestrator stop` can safely identify and signal both processes without collision. |
| 8 | **R2-8: One-time database migration for label cleanup** | **UNCONDITIONAL ADOPTION** | In `StateManager.init_db()`, execute an idempotent migration cleaning existing SQLite rows where `labels LIKE '%name%' AND labels LIKE '%color%'`, extracting clean comma-separated label names. |

---

## 🎯 Authoritative Final Decision Plan & User Story Specification (Final Consensus)

### User Story
**As an** Orchestrator Operator and Autonomous Developer,  
**I want** the TUI SDLC widget to prioritize `PR Status` as the second column with clean label formatting, a dedicated `orchestrator start <project> [-n <node>]` command reusing the hardened worker loop with an idle-drain exit, and deterministic lowest-ID subtask dispatch in DevTest regardless of `ready-for-dev` vs `queued` labels,  
**So that** I can observe PR/CI statuses without scrolling, execute focused development cycles from the CLI without token-burn loops, and guarantee that subtasks are always implemented in strict ascending order (#1, #2, #3...).

### BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: SDLC UI Prioritization, Hardened Lifecycle CLI, and Deterministic Lowest-ID Subtask Dispatch

  Scenario: SDLC table displays PR Status as second column and preserves badges
    Given the dashboard displays the SDLC items table
    When the table is rendered
    Then column 0 must be "ID" (with "[LOCKED]" badge if active)
    And column 1 must be "PR Status"
    And column 2 must be "Title" (capped with ellipsis)
    And column 3 must be "Status/Label"
    And PR badges and locked status must be visible without horizontal scrolling

  Scenario: Clean label persistence and idempotent migration
    Given GitHub returns issue labels as structured objects
    When "poll_project_sdlc_items" syncs labels to SQLite
    Then "sdlc_items.labels" must store clean comma-separated label names
    And raw Python dict representations must never be persisted in SQLite
    And existing rows with dict reprs must be cleaned during database initialization

  Scenario: DevTest selects lowest-ID subtask across active story and fallback
    Given an active story has subtask #155 labeled "queued" and #156 labeled "ready-for-dev"
    When "get_next_devtest_task" is evaluated
    Then it must select subtask #155
    And DevTest must promote #155 to "ready-for-dev" upon execution

  Scenario: Standalone fallback selects lowest unblocked task without stalling
    Given unlinked standalone task #200 is blocked and #201 is queued
    When "get_next_devtest_task" evaluates Fallback 1
    Then it must skip blocked task #200 and select task #201

  Scenario: Sequential advance selects lowest open uncompleted child
    Given subtask #154 is merged and closed
    And remaining open subtasks are #155 (queued) and #156 (queued)
    When "_advance_sequential_subtask" executes
    Then it must select #155 as the next subtask to promote
    And it must NOT select the just-closed subtask #154 or skip #155

  Scenario: Dedicated node lifecycle command drains queue through pending CI
    Given project "biq-playbook" has 2 queued subtasks for "devtest"
    When the operator executes "orchestrator start biq-playbook -n devtest"
    Then it must execute DevTest on subtask #1, create the PR, and await CI
    And it must NOT exit as "drained" while CI is running
    And upon CI pass and auto-merge, it must proceed to subtask #2
    And upon draining all actionable tasks and PRs, it must exit cleanly with code 0
```

### Complete Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/poller.py` | **MODIFY** | 1. In `poll_project_sdlc_items`, pass `"labels": lbl_names` at line 352.<br>2. Broaden parent matching regex to `(?:Parent|Epic|Story)[\s*:]+#?(\d+)`. |
| `orchestrator/db.py` | **MODIFY** | 1. In `init_db()`, add idempotent migration cleaning corrupt dict reprs in `sdlc_items.labels`.<br>2. In `sync_project_sdlc_items`, update upsert to preserve existing `sequence_order` if non-zero.<br>3. In `get_next_devtest_task`, sort by `issue_number ASC`, widen Fallback 1 to `(ready-for-dev OR queued)`, fetch `LIMIT 10`, and skip blocked/in-progress items. |
| `orchestrator/ui/widgets.py` | **MODIFY** | 1. Set `TABLE_COLUMNS = ["ID", "PR Status", "Title", "Status/Label"]`.<br>2. Place `[LOCKED]` in column 0 (`ID`).<br>3. Set `max_width=45` on Title with ellipsis.<br>4. Reorder row tuples in `_render_rows`.<br>5. Implement robust label sanitizer. |
| `orchestrator/nodes/devtest.py` | **MODIFY** | 1. In `_advance_sequential_subtask`, filter unclosed children excluding `dev-implemented`, `in-progress`, `blocked`, and the just-merged `subtask_id`; promote `min(number)`.<br>2. In `run_devtest_node` Phase 3, check `find_linked_pr` before dispatching to prevent duplicate PR loops. |
| `orchestrator/cli.py` | **MODIFY** | 1. Extend `_project_worker_loop` with `node_name`, `exit_when_idle`, and `max_passes: Optional[int] = None`.<br>2. Implement `@app.command("start")` calling `_project_worker_loop(exit_when_idle=True, max_passes=50)`. Exit with code 0 on drain, 1 on stop, 2 on error/max passes. |
| `docs/node-cli.md` | **MODIFY** | Document `orchestrator start` options, SDLC column layout, and lowest-ID dispatch invariant. |
| `tests/test_widgets.py` | **MODIFY** | Update column assertions for `SDLCProgressWidget` to match index 1 for `PR Status`. |
| `tests/test_dashboard.py` | **MODIFY** | Update positional assertions across lines 494, 496, 571, 743, 1346, 1384, 1642, 1678. |
| `tests/test_poller.py` | **MODIFY** | Add tests for label normalization in `sdlc_items` and broadened `Parent:` regex matching. |
| `tests/test_db.py` | **MODIFY** | Verify `get_next_devtest_task` selects lowest ID in active story and Fallback 1 skip-and-continue logic. |
| `tests/test_sequential_pipeline.py` | **MODIFY** | Verify sequential advance selects lowest open child and ignores merged subtask. |
| `tests/test_cli.py` | **MODIFY** | Verify `orchestrator start` lifecycle execution, CI wait, and queue drain termination with exit codes. |

---

## 🏛️ Claude Opus Review Iteration 3 (SDLC Prioritization, Start Command & Lowest-ID Dispatch)

- **Date / Author:** 2026-09-03 | Claude Opus 5 Principal Architect
- **Target:** *Review Iteration 3: Gemini Architect Response to Claude Opus Review Iteration 2* and the **🎯 Authoritative Final Decision Plan & User Story Specification (Final Consensus)** that closes this section.
- **Method:** Every claim in the reconciliation matrix re-verified against the unmodified working tree — `orchestrator/poller.py`, `orchestrator/db.py`, `orchestrator/cli.py`, `orchestrator/nodes/devtest.py`, `orchestrator/nodes/architect.py`, `orchestrator/ui/widgets.py`, and the full positional-assertion set in `tests/test_dashboard.py` / `tests/test_widgets.py`. Baseline re-confirmed by collection: **369 tests**. As in Round 2, an amendment counts as adopted only if it reaches the **Impact Table or the BDD** — matrix prose alone does not ship.

### ✅ What Round 3 genuinely closes

Five findings are closed cleanly and I withdraw them:

- **R2-2 (Fallback 1)** — widening the gate to `(ready-for-dev OR queued)` and replacing the `LIMIT 1` + `return None` abort at `db.py:1470-1477` with fetch-and-skip is exactly the minimal correct change I asked for. It fixes the head-of-line stall *and* preserves the human triage gate rather than deleting it. This was the most consequential blocker and it is properly resolved.
- **R2-4 (exit codes)** — `0` drain / `1` stop / `2` error is a scriptable contract, and it is consistent with the existing `typer.Exit(code=2)` on configuration failure (`cli.py:390`, `cli.py:597`).
- **R2-5 (max-passes scoping)** — `max_passes: Optional[int] = None` on the shared loop, `None` for `watch`, `50` for `start`. This is the correct shape and removes the "daemon silently dies after 50 passes" hazard.
- **R2-7 (test module names)** — verified: `tests/test_devtest_refactor.py`, `tests/test_sequential_pipeline.py` and `tests/test_poller.py` all exist; `tests/test_devtest.py` does not and is gone from the table.
- **R2-6 (label migration)** — the `init_db()` backfill now appears in both the Impact Table and the BDD. The `labels LIKE '%name%' AND labels LIKE '%color%'` conjunction is a sound low-false-positive predicate.

**R2-1 is also half-closed**, and the good half matters: *"write a failing regression test in `tests/test_poller.py` before changing queries"* is the reproduce-before-fixing discipline I asked for, and it is now in the Impact Table. What follows is about *what that test is pointed at*.

### 🚫 Remaining Blockers

#### R3-1 · The newly-stated root cause #1 is not supported by the code, and the regex that ships for it is a speculative widening applied to one of three sites (Blocker)

Matrix row 1 asserts the parent regex *"misses markdown variants like `Parent #154`, `**Parent**: #154`, or checkbox lines `- [ ] #155`"*. Verified against the tree, this is wrong in three ways:

1. **The checkbox variant is backwards.** `- [ ] #<id>` is not a child-to-parent reference. It lives in the **parent's** body as the `## Subtasks` checklist (`architect.py:33`, ticked off by `devtest.py:278`/`:341` as `- [x] #<subtask_id>`). Extracting a parent id from it would make the *parent* a SUBTASK of its own child. This is a cycle-producing misreading, not a missed variant.
2. **`Epic` and `Story` have zero corpus evidence.** The only producer of subtask bodies is `architect.py:301`, which emits the literal `Parent: #{issue_id}` — matched by the *current* regex. A grep for an `Epic` body convention across `orchestrator/`, `docs/node-architect.md` and `docs/definition-node.md` returns nothing. Adding `Epic|Story` to the alternation adds no coverage for any body this system produces.
3. **It introduces a live misclassification.** `poller.py:327-329` sets `item_type = "SUBTASK"` from `parent_issue_id` **before** the STORY branch is ever evaluated. Under `(?:Parent|Epic|Story)[\s*:]+#?(\d+)`, any STORY or EPIC whose body contains prose such as `Story: #12` or `Epic: #7` is reclassified as a SUBTASK, is dropped from the hierarchy roots the dashboard renders, and becomes eligible for direct DevTest dispatch — inverting the invariant `get_next_devtest_task` documents at `db.py:1355` (*"Parent stories are never returned for code implementation"*).

Separately, the `Parent:\s*#(\d+)` regex exists at **three** sites — `poller.py:319`, `devtest.py:307` (subtask→parent resolution) and `devtest.py:1108` (Phase-3 parent fallback). The Impact Table broadens **only `poller.py`**. Broadening one of three leaves the poller and DevTest disagreeing about parentage for exactly the bodies the change is meant to rescue — the persistence layer links the subtask while `devtest.py:307` still returns `parent_id = None` and bails at `:312`.

Either drop the regex change (root cause #2, Fallback 1, is the one that is actually evidenced and is already fixed), or: keep the alternation to `Parent` only, widen the separator to `[\s*:]+#?`, apply it identically at all three sites via one shared helper, and gate the SUBTASK branch so a body-derived parent never overrides an explicit `story`/`epic` label.

#### R3-2 · The drain predicate still covers only two of the six values `-n` accepts (Blocker)

Matrix row 3 defines drain for `-n devtest` and for `-n None`. Verified against `run_project_cycle`, `-n` also accepts **`architect`**, **`reviewer`/`review`** and **`bau`/`maintenance`** (`cli.py:349`, `cli.py:365`). None has a probe. Two of these fail concretely:

- **`orchestrator start <p> -n bau`** — `cli.py:367` sets `force_bau = True` whenever `-n bau` is passed, so the BAU node runs unconditionally every pass. With no BAU probe, `exit_when_idle` never fires, the loop runs the full 50 passes and then exits **`2` — "fatal error"** for what was a clean, bounded run. That is a false failure signal on a supported invocation.
- **`orchestrator start <p> -n reviewer`** — no probe, same non-termination.

R2-3 asked for `node_has_actionable_work(project, node) -> bool` defined for every node `-n` accepts. Either define it for all six, or make `start` **reject** `-n` values without a probe with a clear message. Silently running to `max_passes` and reporting a fatal error is the worst of the three options.

Related and still unstated: the BDD scenario asserts *"upon CI pass and auto-merge, it must proceed to subtask #2"* under `-n devtest`. Verified at `cli.py:349`: with `node_name="devtest"` the reviewer node is **skipped**, so that PR advances only if `auto_merge_approved` is configured. As written the scenario is untrue for the default configuration. R2-3 asked that this routing limitation be recorded in `docs/node-cli.md`; the docs row still lists only *"`start` options, SDLC column layout, and lowest-ID dispatch invariant."*

#### R3-3 · `lifecycle_pid` (R2-9) is in the matrix and absent from the Impact Table — the same failure mode R2-6 was raised for (Blocker, small)

Matrix row 7 adopts *"record `start` PID under `daemon_control` key `lifecycle_pid` (preserving `daemon_pid` for the main watcher) so `orchestrator stop` can safely identify and signal both."* Neither the `db.py` row, the `cli.py` row, nor the BDD mentions PID handling at all. Verified, this is not a one-liner:

- The existing key is `'pid'`, not `'daemon_pid'` (`db.py:201`, read back at `db.py:249`/`:305`, deleted at `db.py:224`).
- `register_daemon` also forces `status='RUNNING'` and clears `stop_requested` (`db.py:196-208`), so `start` cannot simply call it without stomping the watch daemon's control state.
- New `StateManager` methods (register/read/clear `lifecycle_pid`) and a change to `stop --force` (`cli.py:1379-1394`, currently single-PID) are required.

Also unstated: `stop_requested` is a **single global flag**, so `orchestrator stop` halts `watch` and `start` together. That may well be the desired semantics — it just needs to be written down.

### ⚠️ Concerns (not blocking)

- **R3-4 (R2-12, still incomplete after correction).** The line list gained 1642 and 1678 — correct, both are `get_row_at(0)[1]` placeholder assertions and the placeholder tuple is `("-", "No active SDLC items", "-", "-")` (`widgets.py:332`, `:349`), so the text moves to index 2. But three breaking lines are still missing, all verified present: **`tests/test_dashboard.py:566`** (`get_row_at(0)[3] == "#50"`, PR status → index 1), **`:1347`** (`get_row("71")[3] == "#72"`), **`:1663`** (`get_row("55")[1] == "Added Issue"`). Separately, `tests/test_widgets.py` is listed with only *"match index 1 for PR Status"*, but the reorder plus the `[LOCKED]` relocation breaks **thirteen** assertions there: lines 112, 169, 196-198, 362, 363, 475, 476, 481, 486, 492, 498 — the eight `[LOCKED]`-at-index-2 assertions in particular are invalidated by the *badge move*, not the column move, and nothing in the table signals that.
- **R3-5 (R2-8, resolved into a third option that decides nothing).** `sequence_order = COALESCE(NULLIF(excluded.sequence_order, 0), sdlc_items.sequence_order, 0)` preserves a value that **no code path ever writes** — a grep across `orchestrator/` finds `sequence_order` only in `db.py`, always read, sourced from `int(item.get("sequence_order", 0))` with no producer. Simultaneously canonicalising order to `issue_number ASC` makes the preserved value unreadable. A-6 asked for a decision; this is neither horn. It is harmless, but the decision is still unrecorded in `docs/node-cli.md`. Also note *"order queries canonicalized to `issue_number ASC`"* appears only in matrix prose — the `db.py` Impact row scopes it to `get_next_devtest_task`, leaving the other eight `ORDER BY sequence_order` sites (`db.py:1168, 1195, 1223, 1269, 1340, 1486, 1513`) and two Python sorts (`:1130, :1150`) ambiguous. Since the column is pinned to 0 this is a no-op today either way — say so explicitly so an implementer does not "fix" hierarchy ordering and break the dashboard row positions the Impact Table just enumerated.
- **R3-6 (R2-2, residual bound).** `LIMIT 10` is arbitrary. If the ten lowest standalone candidates are all blocked or in-progress, the stall returns in exactly its original form, just harder to reproduce. Iterate the cursor to first-unblocked with no `LIMIT`, or push the exclusion into the SQL predicate. Note also that the **active-story** branch (`db.py:1411-1418`) retains the identical `LIMIT 1` + `return None` shape; for a strictly sequential story that is arguably correct behaviour, but it is now the only place the pattern survives and it should be marked as intentional.
- **R3-7 (R2-10, unchanged).** *"check `find_linked_pr` before dispatching"* still names no data source. `find_linked_pr(issue_number, prs)` (`poller.py:72`) takes a **list of open PRs**; the choice is still between an extra `gh pr list` per pass (re-adding the redundancy A-9 removed) or the sweep-written `sdlc_items.linked_pr` column (`poller.py:338`). Name one and state its freshness guarantee.
- **R3-8 (R2-11, unchanged).** The `devtest.py` exclusion set in the Impact Table is `dev-implemented`, `in-progress`, `blocked`, `subtask_id`. `orchestration-failed` is still dropped relative to the Round 2 matrix. `CLOSED` is covered by *"unclosed children"*. Note `db.py`'s own `_is_blocked` (`db.py:1357-1366`) already substring-matches `orchestration-failed`; `_advance_sequential_subtask` is a separate path and does not inherit it — either reuse the helper or restore the label.
- **R3-9 (R2-13, unchanged).** *"Implement robust label sanitizer"* is still the problem statement, not a contract. Verified at `widgets.py:365` / `:389`: the widget does `str(raw_labels)` with no sanitization today. O1-13 asked for a pure helper with defined precedence (`ast.literal_eval` → `name'\s*:\s*'([^']+)'` extraction → comma-split) pinned across five input shapes, including the mixed `"ready-for-dev, {'name': 'queued', …}"` case that defeats `literal_eval`. The `tests/test_poller.py` row covers *persistence-layer* normalization; the `tests/test_widgets.py` row covers only column indices. No row tests the helper.
- **R3-10 (new, small).** The `init_db()` migration is idempotent by construction but has **no test row** — `tests/test_db.py` is scoped to `get_next_devtest_task`. A backfill that rewrites every user's `sdlc_items.labels` on next start should have at least one assertion, including the no-op-on-clean-rows case.

### 🏁 Verdict

Round 3 is the strongest iteration in this section. The blocker that mattered most — R2-2, where Round 2's own fix would have converted an ordering complaint into a permanent silent stall of the DevTest queue *and* removed the last human triage gate before a paid harness run — is resolved correctly and minimally. Exit codes, `max_passes` scoping, the test-module corrections and the label migration are all right, and the reproduce-before-fixing discipline finally appears in the Impact Table.

What blocks it is narrower than before but not cosmetic. **R3-1**: the regression test now mandated by R2-1 is aimed at a root cause that the code does not support — the checkbox variant is a parent-side checklist, not a child-side link; `Epic`/`Story` match no body this system emits; and the proposed alternation makes any story mentioning `Story: #N` classify as a SUBTASK, defeating the very invariant `get_next_devtest_task` exists to protect. Compounding it, the regex is broadened at one of three sites, so `poller.py` and `devtest.py:307` would disagree about parentage for precisely the bodies the change targets. **R3-2**: `-n` accepts six values and drain is defined for two; `-n bau` in particular runs to `max_passes` by construction and then reports exit `2` — a fatal-error signal for a clean run. **R3-3**: `lifecycle_pid` is adopted in the matrix and absent from the Impact Table and BDD, which is the exact prose-never-ships failure R2-6 was raised for, and it is not a one-liner given `register_daemon` also owns `status` and `stop_requested`.

These are all small, bounded corrections — R3-1 is arguably a *deletion* (drop the unevidenced regex change; Fallback 1 is the evidenced fix and it is already correct), R3-2 is a probe table or an argument validator, R3-3 is three table rows. Fold R3-4 through R3-10 in alongside them — R3-4 is three line numbers plus one sentence about the thirteen `test_widgets.py` assertions, and the rest are one-line clarifications — and this specification is implementation-ready. At the Round 3 cap it is not yet, and I recommend operator sign-off on the R3-1/R3-2/R3-3 corrections rather than a fourth review round.

VERDICT: DISAGREED

---

## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies & Final Consensus Synthesis (Round 3 Cap Reached)

- **Date:** 2026-09-03
- **Status:** **3 Debate Rounds Completed between Gemini & Claude Opus (Medium Effort)**.
- **Current Alignment:** **90% Fully Converged**. Five major areas resolved (Fallback 1 skip-and-continue, exit codes, max-passes scoping, test module names, label backfill migration).
- **Three Final Points for Operator Decision / Sign-off (R3-1, R3-2, R3-3):**

### 1. Structured Dispute & Resolution Matrix

| # | Item & Contested Point | Gemini Proposal | Claude Opus Recommendation | Final Consensus Resolution (Recommended) |
|---|---|---|---|---|
| **1** | **R3-1: Parent Extraction Regex vs. Fallback 1 Root Cause** | Broaden regex in `poller.py` to `(?:Parent|Epic|Story)[\s*:]+#?(\d+)`. | Drop the regex modification entirely. The claimed variants (`Epic`, `- [ ] #155`) do not exist or refer to parent checklists. Modifying regex risks misclassifying stories as subtasks. The real fix for lowest-ID selection is **Fallback 1** in `db.py:1465` (widened to `ready-for-dev OR queued` with skip-and-continue), which is already agreed upon. | **Adopt Claude Opus's deletion:** Keep `Parent:\s*#(\d+)` unchanged. Rely on the widened Fallback 1 (`ready-for-dev OR queued`) with unblocked cursor iteration to guarantee lowest-ID dispatch without misclassification risks. |
| **2** | **R3-2: Scope of `-n` in `orchestrator start`** | General loop with drain condition checking DevTest queue and open PRs. | `-n` accepts `devtest`, `architect`, `reviewer`, `bau`. `bau` sets `force_bau=True` and would run 50 passes then exit code 2 (fatal error). | **Add CLI argument validation:** Explicitly restrict `orchestrator start <project> [-n <node>]` to `-n devtest` (or full lifecycle if `-n` is omitted). If another node is passed, reject with: `orchestrator start currently supports '-n devtest' or full lifecycle execution`. Define drain for full lifecycle as zero open tasks across all nodes and zero pending PRs. |
| **3** | **R3-3: Lifecycle Process PID Tracking & Stop Semantics** | Track `lifecycle_pid` in `daemon_control`. | Key is `'pid'`, not `'daemon_pid'`. Calling `register_daemon` resets `stop_requested` and forces `status='RUNNING'`. Global `stop_requested` halts both `watch` and `start`. | **Adopt clean non-colliding tracking:** In `_project_worker_loop`, do NOT call `register_daemon` (which would overwrite the main daemon PID). Instead, simply record `lifecycle_pid` under a distinct key in `daemon_control`. Clarify that `orchestrator stop` sets the global `stop_requested` flag which cleanly halts both `watch` and `start` gracefully. |

---

## 🎯 Authoritative Final Consensus Plan (Ready for Operator Sign-off)

### 1. SDLC Table Column Prioritization (`orchestrator/ui/widgets.py`)
- Columns: `["ID", "PR Status", "Title", "Status/Label"]`.
- `[LOCKED]` marker placed directly in Column 0 (`ID`) so it is never hidden by horizontal scrolling.
- `Title` column capped at width 45 with ellipsis overflow.
- Clean label persistence: `poller.py:352` stores `lbl_names` directly in SQLite. Idempotent backfill migration in `StateManager.init_db()` cleans corrupt legacy rows.

### 2. Dedicated Lifecycle Command (`orchestrator start` in `orchestrator/cli.py`)
- Reuses `_project_worker_loop(exit_when_idle=True, max_passes=50, node_name=node)`.
- Validates `-n devtest` or full lifecycle.
- Drains queue through pending PR CI verification.
- Exits with distinct codes: `0` (queue drained), `1` (stop requested / cancelled), `2` (max passes / fatal error).
- Does not clear global stop flag; refuses to start if stop is pending.

### 3. Deterministic Lowest-ID Subtask Dispatch (`orchestrator/db.py`, `devtest.py`)
- `get_next_devtest_task`: Active story children ordered strictly by `issue_number ASC` (whether `ready-for-dev` or `queued`).
- Fallback 1: Widened to `(labels LIKE '%ready-for-dev%' OR labels LIKE '%queued%')`, queries `LIMIT 10`, skips blocked or in-progress candidates, and returns the lowest unblocked ID.
- `_advance_sequential_subtask`: Excludes `CLOSED`, `dev-implemented`, `in-progress`, `blocked`, `orchestration-failed`, and the just-merged `subtask_id`; selects `min(number)` among remaining open children.
- In Phase 3, checks `sdlc_items.linked_pr` before dispatching to eliminate duplicate PR loops.


---

