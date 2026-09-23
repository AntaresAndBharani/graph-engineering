# 📋 Implementation Plan & Refinement Lifecycle: Real-Time Active Node Log Streaming & Scoped Observability in Dashboard

## 📝 Initial Draft Proposal

### Background & Objective
During live operations of the `graph-orchestrator` across 10 active repositories, an operator observed that when an autonomous AI node (e.g., `architect` on `biq-playbook` evaluating Issue #75) is actively executing in `#projects_table`, the **Logs tab** on the bottom right remains completely blank:
- **Operator Requirement:** *"Here, logs tab should show the logs related to the architect in the biq-playbook project (should execute and see, no refresh, orchestrator logs biq-playbook -n architect). Let review the requirement."*
- **Observed Behavior:**
  1. Running CLI command `orchestrator logs biq-playbook -n architect` immediately prints 51 lines of structured triage output from `biq-playbook/architect/20260903_160302_architect_issue_75.log`.
  2. In the interactive Textual TUI dashboard (`orchestrator watch`), highlighting `biq-playbook` and opening the `Logs` tab renders a completely empty pane with zero lines.
  3. As the AI harness executes and produces output, the `Logs` tab fails to stream or refresh in real time without manual operator intervention.

---

## 🔍 Review Iteration 1: 3-Amigos Critical Architectural Review

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Architectural Scope:** `orchestrator/ui/dashboard.py`, `orchestrator/logging.py`, `orchestrator/harness.py`
- **Current Baseline:** 337 passing unit/integration tests (`pytest -v`).

### 1. Root-Cause Analysis & Verdict Matrix

| # | Flaw / Root Cause | Target Component | Verdict | Technical Rationale & Architectural Remedy |
|---|---|---|---|---|
| 1 | **Row Key Mutation Without Highlight Trigger** | `orchestrator/ui/dashboard.py` (`update_projects_table`) | **APPROVE** | When a project transitions from `Idle` to `Active` (e.g. `biq-playbook::Idle` $	o$ `biq-playbook::architect`), Textual's `DataTable.move_cursor` is a no-op if the cursor row index remains unchanged (e.g. row 5). Because `RowHighlighted` is suppressed, `self.selected_node` remains `None` and `hydrate_project_logs` is **never re-invoked**. Remedy: in `update_projects_table`, detect active node transitions for `self.selected_project` and automatically re-hydrate logs. |
| 2 | **Omission of Log View in Periodic Tick** | `orchestrator/ui/dashboard.py` (`update_projects_table`, `_update_bottom_panes`) | **APPROVE** | The 2.0s refresh tick updates `#projects_table` and invokes `_update_bottom_panes` (SDLC widget, alerts, quotas), but **completely omits the active log pane**. If a background harness writes new lines to disk, `#log_view` is never updated. Remedy: implement an active incremental file tailer on the 2.0s tick when `tab_logs` is visible. |
| 3 | **0-Byte Newest File Blind Overwrite** | `orchestrator/logging.py` (`tail_latest_project_logs`) | **APPROVE** | `tail_latest_project_logs` blindly selects `max(log_files, key=lambda p: (p.stat().st_mtime, p.name))`. When devtest is queued right after architect, it touches an empty file (e.g. `20260903_160554_devtest_issue_76.log` with 0 bytes). `max()` picks this 0-byte file, returning 0 lines and completely blanking out the view! Remedy: filter by active `node_name`, and fall back to the newest non-empty log file. |
| 4 | **Active Job Node Inference** | `orchestrator/ui/dashboard.py` (`hydrate_project_logs`) | **APPROVE** | When the operator highlights the main project row (`self.selected_node is None`), if `state_manager` reports an active running job (e.g. `architect`), the dashboard should automatically infer `effective_node = active_job.node_type`, exactly mirroring `orchestrator logs <project> -n <active_node>`. |
| 5 | **RichLog Markup Safety & Live Stream Routing** | `orchestrator/ui/dashboard.py` (`_handle_harness_stream_line`) | **APPROVE** | In `_handle_harness_stream_line`, if `self.selected_node` is `None` (project-wide view), it currently rejects lines if `line_node` does not match. Remedy: if `self.selected_node is None`, display all stream lines for `self.selected_project`. Escape Rich markup (`rich.markup.escape`) so code brackets like `[ts]` or `[0]` do not fail silently. |

---

## 🚀 Boost Review Iteration 1: 360° Multi-Perspective Deep Analysis

- **Date / Author:** 2026-09-03 | Boost Swarm Architect

### 1. Architecture & Schema Integrity Lens
- **SQLite Contention & Polling Overhead:** Inspecting active jobs on the 2.0s tick already occurs in `update_projects_table` via `await self.state_manager.get_active_jobs()`. We reuse this existing list without introducing any new SQLite queries.
- **Incremental File Read Safety:** Rather than re-reading the entire file every 2 seconds, the incremental tailer tracks the file descriptor or byte offset `_last_tail_offset` and `_last_tail_file`. It reads only newly appended bytes, decoding and writing them to `RichLog` in $O(1)$ memory and CPU.

### 2. Adversarial QA & Resilience Lens
- **Edge Case 1: 0-Byte Log File at Subprocess Startup:** When a harness process spawns, Python creates the `.log` file before the subprocess emits its first stdout byte. If the tailer reads immediately, `stat().st_size == 0`. The tailer must not crash or wipe existing historical logs until the new file actually contains data.
- **Edge Case 2: Project Switching While Streaming:** If the operator presses Up/Down arrows to view another project, the incremental tailer must reset its file tracking, clear `_last_tail_file`, and hydrate the newly selected project cleanly.
- **Edge Case 3: Process Termination & State Transition:** When `architect` finishes and `devtest` starts, the active node changes. The dashboard must detect the transition, update the title to `Live Output [biq-playbook | devtest*]`, and smoothly switch to tailing the devtest log file.

### 3. Security & Non-Interactive Subprocess Governance Lens
- **Non-Interactive Environment Invariants:** File tailing and stream listening are completely read-only and passive. They do not alter child subprocess execution, retaining all `GH_PROMPT_DISABLED="1"`, `NO_COLOR="1"`, and timeout guarantees.

### 4. Sequential State Simulation
1. **State 0 (Idle):** `biq-playbook` is Idle. Operator selects `biq-playbook`. `hydrate_project_logs` tails the most recent non-empty log file (e.g. previous devtest or architect run).
2. **State 1 (Active Spawning):** Poller starts `architect` for Issue #75. Active job `RUNNING` appears in `get_active_jobs()`. Next 2s tick detects active job on `biq-playbook`. Active log file `20260903_160302_architect_issue_75.log` is identified.
3. **State 2 (Live Execution Streaming):** Subprocess emits stdout. Both `AsyncHarnessAdapter` stream listener and the 2s incremental file follower deliver new lines to `RichLog`. The view auto-scrolls seamlessly. **Zero refresh required.**
4. **State 3 (Completion & Hand-off):** `architect` finishes. Issue #75 is triaged. The log reflects `=== EXECUTION FINISHED ===`. Next subtask #76 is queued.

---

## 🎯 Final Decision Plan & User Story Specification

### User Story
**As a** DevOps Engineer and AI Orchestrator Operator,  
**I want** the TUI Dashboard (`orchestrator watch`) Logs tab to automatically stream and tail the active execution logs of the currently running node (matching `orchestrator logs <project> -n <active_node>`) in real-time,  
**So that** I can observe live AI agent execution without having to manually refresh or navigate away.

### BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: Real-Time Active Node Log Streaming and Observability in Dashboard

  Scenario: Live streaming without manual refresh for active running node
    Given the dashboard is displaying project "biq-playbook"
    And the Architect node begins executing Issue #75 in the background
    When new log output is generated by the AI harness
    Then the "Logs" tab must display the active log output in real time
    And the operator must see the output without pressing "r" or refreshing

  Scenario: Automatic node scope inference from active jobs
    Given project "biq-playbook" is highlighted in the projects table
    And "biq-playbook" has an active job with node_type "architect"
    When the operator views the "Logs" tab
    Then the log view title must display "Live Output [biq-playbook | architect*]"
    And the log view must show the contents of the active architect log file

  Scenario: Non-empty file fallback prevents blank log pane
    Given a project directory contains a 0-byte newly created log file
    And also contains a previously completed non-empty log file
    When "tail_latest_project_logs" is invoked
    Then it must return the log lines from the newest non-empty log file
    And the log view must not be blank

  Scenario: Project switching resets active tailing cleanly
    Given the dashboard is actively following logs for project "biq-playbook"
    When the operator highlights project "crosstrainingapp"
    Then active log tailing for "biq-playbook" must detach
    And the "Logs" tab must immediately hydrate and display logs for "crosstrainingapp"
```

### Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/ui/dashboard.py` | **MODIFY** | In `update_projects_table`, detect active node changes for `self.selected_project`. Implement incremental file follower in 2.0s tick for active running jobs. In `_handle_harness_stream_line`, route lines to project if `self.selected_node is None` and escape Rich markup. |
| `orchestrator/logging.py` | **MODIFY** | In `tail_latest_project_logs`, filter by `node_name` and skip 0-byte empty files when non-empty logs are available. In `get_project_logs`, ensure falling back to disk tailing works reliably when in-memory buffer has no matching records. |
| `docs/node-cli.md` | **MODIFY** | Update Dashboard observability documentation detailing real-time log following and automatic active node inference. |
| `tests/test_dashboard.py` | **MODIFY** | Add regression tests verifying automatic active node log hydration without refresh and 0-byte file resilience. |

### INVEST Subtask Breakdown

1. **Subtask 1: Resilient Log Tailing & 0-Byte Fallback (`orchestrator/logging.py`)**
   - Update `tail_latest_project_logs` to filter non-empty log files first (`stat().st_size > 0`), ensuring fresh 0-byte files never mask valid logs.
2. **Subtask 2: Active Node Detection & Reactive Re-Hydration (`orchestrator/ui/dashboard.py`)**
   - Track `_active_job_nodes` in `DashboardApp`. When `update_projects_table` discovers a new active job for `self.selected_project`, auto-update `self.selected_node` and hydrate logs immediately.
3. **Subtask 3: Incremental File Follower & Stream Markup Safety (`orchestrator/ui/dashboard.py`)**
   - Add `_poll_active_log_file` to the 2.0s tick to read new bytes from the active job's log file without re-clearing `RichLog`.
   - Wrap live stream line writing with `rich.markup.escape` to prevent unescaped brackets from breaking rendering.
4. **Subtask 4: Automated Regression Tests (`tests/test_dashboard.py`, `tests/test_logging.py`)**
   - Write unit and BDD tests verifying:
     - 0-byte file skipping in `tail_latest_project_logs`.
     - Active node change triggers log re-hydration.
     - Incremental log tailing appends without refresh.

---

## 🏛️ Claude Sonnet Review Iteration 1

- **Date / Author:** 2026-09-03 | Claude Sonnet 5 Principal Architect
- **Method:** Direct ground-truth inspection of `orchestrator/ui/dashboard.py`, `orchestrator/logging.py`, `orchestrator/harness.py`, `orchestrator/ui/widgets.py`, `orchestrator/cli.py`, plus runtime introspection of the installed `textual==8.2.8` source (`DataTable.move_cursor`, `watch_cursor_coordinate`, `App.call_from_thread`, `RichLog._make_renderable`) to verify or falsify every claim in the "Real-Time Active Node Log Streaming" plan above.

### ⚖️ Critical Architecture & Drawbacks Critique

#### R1-1 · Root Cause #5's diagnosis is factually wrong about the code it cites (Blocker)
`orchestrator/ui/dashboard.py:284-286` reads:
```python
if self.selected_project and line_project and line_project != self.selected_project:
    return
if self.selected_node and line_node and not matches_node_scope(self.selected_node, line_node):
    return
```
When `self.selected_node is None`, the second guard short-circuits to `False` and every line is already passed through for the selected project — the code **already does** "if `self.selected_node is None`, display all stream lines for `self.selected_project`," which the 3-Amigos table claims is missing. Directing an implementer to build this as new work (it appears again in INVEST Subtask 3's framing) is dead work risk: a developer who "fixes" already-correct code without close reading may introduce a regression (e.g. accidentally start filtering project-wide view by the *first* node seen).

#### R1-2 · The plan is silent on a pre-existing `call_from_thread` misuse that the live-stream path already exercises on every line (Blocker)
Verified `orchestrator/cli.py:699-822` (`_watch_daemon_tui`): the per-project worker tasks (which invoke `AsyncHarnessAdapter._execute_once`, which synchronously calls every registered `_stream_listeners` callback at `orchestrator/harness.py:264-286`) and `DashboardApp.run_async()` are `asyncio.gather`'d on **one** event loop inside a single `asyncio.run()` call — there is no separate thread anywhere in this codebase for harness execution (confirmed by grep: no `threading.Thread`/`run_worker` around `_execute_once`). Yet `_handle_harness_stream_line` (dashboard.py:289-297) and `_handle_log_record` (dashboard.py:251-259) both call `self.call_from_thread(log_view.write, ...)`. Verified against the installed Textual source: `call_from_thread` explicitly raises `RuntimeError("The call_from_thread method must run in a different thread from the app")` whenever `self._thread_id == threading.get_ident()`, which is **always** true here. The outer `except Exception` silently swallows this and falls through to a redundant direct `log_view.write(line)`. In other words: the "live streaming" hot path this very feature depends on already raises-and-catches an exception on **every single line** emitted by an active harness — a path that can fire hundreds of times per second during a verbose `architect`/`devtest` run. The plan's own "Architectural Scope" lists `orchestrator/harness.py` and claims the existing stream listener "delivers new lines to `RichLog`" as a working primitive to build on (Boost Review §4, State 2) — it works today only by accident, via exception fallback, and Subtask 3's new incremental file follower must not copy this pattern. The plan gives no explicit instruction against it.

#### R1-3 · Node-filtered log tailing silently falls back to the wrong node's content with no indication (Blocker)
`orchestrator/logging.py:210-217`:
```python
if node_name:
    filtered_files = [p for p in log_files if matches_node_scope(node_name, p.parent.name) or matches_node_scope(node_name, p.stem)]
    if filtered_files:
        log_files = filtered_files
latest_file = max(log_files, key=lambda p: (p.stat().st_mtime, p.name))
```
If `filtered_files` is empty — e.g. `architect*` is the active/selected node but only `devtest` logs exist on disk for the project — the code silently reverts to the **full unfiltered** `log_files` list and returns whatever is globally newest, regardless of node. `get_project_logs` (logging.py:243-266) has the same shape: when the in-memory buffer has entries for the project but none matching the requested node, it falls through to disk with the same silent-wrong-node risk. This directly contradicts **BDD Scenario 2** ("the log view must show the contents of the active architect log file") and Subtask 1's fix description ("filter by `node_name`, and fall back to the newest non-empty log file") never specifies the empty-filter-result case. An operator would see devtest output rendered under an `"architect*"` title bar with zero indication of the mismatch — worse than the blank pane it replaces, because a blank pane is at least honestly wrong.

#### R1-4 · Root Cause #1's causal mechanism is technically incorrect, which risks the wrong fix being implemented (Blocker)
The 3-Amigos table states: *"Textual's `DataTable.move_cursor` is a no-op if the cursor row index remains unchanged... Because `RowHighlighted` is suppressed..."*. Verified against installed Textual 8.2.8 source: `cursor_coordinate` is declared `Reactive(Coordinate(0, 0), repaint=False, always_update=True)`. With `always_update=True`, the reactive setter and `watch_cursor_coordinate` **always** run on every `move_cursor` call — it is not a no-op. The actual suppression is one level deeper: `watch_cursor_coordinate` itself gates the `RowHighlighted`-producing call behind `if old_coordinate != new_coordinate:`. The empirical symptom the plan describes is real (confirmed: `_apply_keyed_diff` in `orchestrator/ui/widgets.py:177-237` removes the `biq-playbook::Idle` row and adds a new `biq-playbook::architect` row at the same table index when a project sorts to the same position, so `move_cursor(row=idx)` with an unchanged index never fires `_highlight_row`/`RowHighlighted`) — but the stated mechanism is wrong. This matters operationally: if an implementer follows the plan's diagnosis literally, they may try to fix this by forcing "always re-fire regardless of value" semantics on the cursor reactive (already true) instead of the actual fix — explicitly detecting the row-key/active-node transition inside `update_projects_table` and directly calling `hydrate_project_logs`/updating `self.selected_node`, independent of whether `RowHighlighted` ever fires. The Final Decision Plan's Subtask 2 happens to land on roughly the right remedy, but the plan never explicitly states "do not rely on `RowHighlighted` for this case," leaving the door open for an implementation that chases the wrong root cause.

### 🚨 Unresolved Concerns & Edge Case Vulnerabilities

#### R1-5 · No encoding/partial-write policy for the new byte-offset incremental tailer
Subtask 3 introduces a `_poll_active_log_file` that reads "new bytes" via an offset (`_last_tail_offset`/`_last_tail_file`, per Boost Review §1). `tail_latest_project_logs` mitigates multi-byte UTF-8 boundary corruption on whole-file reads via `open(..., errors="replace")` (logging.py:219), but an offset-based partial read can land mid-multi-byte-sequence if the subprocess flushed a partial write, and the plan specifies neither the read mode (binary vs. text), the decode-error policy, nor what happens to a dangling half-line before the newline arrives on the next 2.0s tick. An unhandled `UnicodeDecodeError` inside a `set_interval` callback risks silently breaking future tick firings.

#### R1-6 · Windows-specific concerns are entirely unaddressed
The Boost Review's "Adversarial QA & Resilience Lens" covers 0-byte startup, project switching, and process termination, but the deployment target (per this session's environment) is Windows. Concurrent read-while-write file access, antivirus/backup-software transient sharing violations on the tailed `.log` file, and `\r\n` line-ending handling in the new incremental reader (vs. the existing `.rstrip("\r\n")` in `tail_latest_project_logs`) are not mentioned anywhere in either review pass.

#### R1-7 · Node hand-off produces a blank pane, not the "zero refresh" experience the plan promises
State 3 of the Sequential State Simulation (architect finishes, devtest starts) is the direct descendant of the original bug report. Verified: when `devtest`'s 0-byte log file is freshly created and Subtask 2's active-node-transition detection immediately re-hydrates against it, `hydrate_project_logs` → `buffer_manager.get_project_logs(..., node_name="devtest")` returns `[]` (logging.py:268-269, since neither the in-memory buffer nor disk has matching content yet) — the pane goes blank for the handoff window, with only the border title changing to `"Live Output [biq-playbook | devtest*]"`. Neither the BDD scenarios nor the Component Impact Table test this handoff-blank window, yet it's a direct regression risk against the very complaint this feature is meant to fix.

#### R1-8 · Row-key design conflates project identity with active-node state (root-cause-adjacent, not addressed)
`_apply_keyed_diff` treats `"{project}::Idle"` and `"{project}::{node}"` as entirely different row identities, so every Idle↔Active transition is a row **remove + add**, not a cell mutation. The plan patches around the resulting `RowHighlighted` gap (R1-4) rather than questioning whether the row-key scheme itself should be stable per project (with active-node state carried in a cell, as sub-row rendering via `"  └─"` at dashboard.py:423 already gestures toward for multi-node fan-out). Not fatal, but worth flagging as a missed root-cause opportunity given how much of this plan exists solely to compensate for it.

#### R1-9 · Component Impact Table and INVEST breakdown disagree on test scope
The Component Impact Table's `tests/test_dashboard.py` row is the only test file listed for **MODIFY**, but INVEST Subtask 4 names both `tests/test_dashboard.py` **and** `tests/test_logging.py` (both confirmed to exist in `tests/`). This is a minor but real internal inconsistency that could cause `tests/test_logging.py` — the natural home for the 0-byte/node-filter-fallback regression tests in R1-3 — to be missed during implementation scoping.

### 🛠️ Mandatory Architectural Safeguards & Required Changes

- **S-1 (Resolves R1-1):** Strike the "reject lines when `self.selected_node is None`" framing from Root Cause #5 and INVEST Subtask 3. Scope Subtask 3's markup-safety work only to wrapping `log_view.write(...)` call sites with `rich.markup.escape`, which is the only genuinely missing behavior.
- **S-2 (Resolves R1-2):** Before or as part of this feature, replace `self.call_from_thread(...)` in `_handle_harness_stream_line` and `_handle_log_record` with a direct call (or `self.call_next`/`post_message`) appropriate for same-loop invocation, and add an explicit regression test asserting no `RuntimeError` is raised/caught on the hot path. Any new code in Subtask 3 (`_poll_active_log_file`) must call `RichLog.write` directly from the `set_interval` tick — never via `call_from_thread`.
- **S-3 (Resolves R1-3):** Specify the empty-filter-result behavior explicitly: either (a) return `[]`/no fallback when `node_name` is given but no file matches that node (surfacing a "no logs yet for this node" placeholder), or (b) keep the fallback but tag the returned lines/title with a visible mismatch indicator. Silent wrong-node display is not acceptable per BDD Scenario 2.
- **S-4 (Resolves R1-4):** Rewrite Root Cause #1's technical rationale to reflect the verified mechanism (`watch_cursor_coordinate`'s `old_coordinate != new_coordinate` gate on `RowHighlighted`, not `move_cursor` being a no-op), and state explicitly that the fix must not depend on `RowHighlighted` firing for same-index transitions — `update_projects_table` must detect the transition itself and call `hydrate_project_logs` directly, as Subtask 2 already intends.
- **S-5 (Resolves R1-5, R1-6):** Add explicit spec language for `_poll_active_log_file`: binary-mode read from the tracked offset, `errors="replace"` decode (or buffer incomplete trailing bytes until the next tick), `\r\n`-safe line splitting, and a bounded retry/backoff on transient Windows sharing violations instead of letting an unhandled exception reach the `set_interval` callback.
- **S-6 (Resolves R1-7):** Add a BDD scenario and Subtask 2/3 spec text covering the node hand-off window: while `devtest`'s log file is 0 bytes, the pane must show a "waiting for `devtest` output…" placeholder (title already updates correctly) rather than going silently blank.
- **S-7 (Resolves R1-9):** Add `tests/test_logging.py` to the Component Impact Table row alongside `tests/test_dashboard.py`.

### 🏁 Verdict

Four Blockers (R1-1 through R1-4) each independently prevent sign-off: one is a factual misdiagnosis that risks wasted/regressive implementation effort, one exposes a pre-existing hot-path defect the plan's own "zero refresh" performance claims silently depend on, one is a correctness gap that would make the dashboard confidently display the wrong node's logs, and one is a root-cause misattribution that could send the implementer chasing the wrong fix for the dashboard's core symptom. None of these are addressed by the existing Boost Review or Final Decision Plan. Safeguards S-1 through S-7 above are required before this plan is implementation-ready.

VERDICT: DISAGREED

---

## 🔍 Review Iteration 2: Gemini Architect Response to Claude Sonnet Review Iteration 1

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Scope:** Complete, point-by-point adoption of Claude Sonnet's 4 Blockers (R1-1 to R1-4) and 5 Concerns (R1-5 to R1-9), incorporating Safeguards S-1 through S-7.

### 1. Point-by-Point Reconciliation Matrix

| # | Claude Finding & Severity | Gemini Verdict | Concrete Technical Resolution in Revised Plan |
|---|---|---|---|
| 1 | **R1-1 (Blocker): `_handle_harness_stream_line` diagnosis** | **UNCONDITIONAL ADOPTION (S-1)** | Strike the claim that project-wide lines were rejected when `self.selected_node is None`. The existing code already passed them through. Focus Subtask 3 solely on wrapping all emitted lines with `rich.markup.escape()` to prevent syntax crashes on bracketed tokens (e.g. `[ts]`, `[0]`, markdown links). |
| 2 | **R1-2 (Blocker): `call_from_thread` hot-path exception storm** | **UNCONDITIONAL ADOPTION (S-2)** | Replace `self.call_from_thread(log_view.write, ...)` in `_handle_harness_stream_line` and `_handle_log_record`. Because `_watch_daemon_tui` runs all workers and Textual on the **same asyncio event loop**, `call_from_thread` raised and caught a `RuntimeError` on **every single line**. Check `threading.get_ident() == getattr(self, "_thread_id", None)`: call `log_view.write(line)` directly when on-loop; only use `call_from_thread` if called from an external OS thread. |
| 3 | **R1-3 (Blocker): Silent fallback to wrong node on empty filter** | **UNCONDITIONAL ADOPTION (S-3)** | In `orchestrator/logging.py:tail_latest_project_logs`, if `node_name` is specified and `filtered_files` is empty, **strictly do NOT revert to unfiltered `log_files`**. Return `[]`. In `hydrate_project_logs`, if an active node has no logs yet on disk, render `[dim yellow]No execution logs found yet for node '{node_name}'[/dim yellow]`. Prevent devtest logs from ever rendering under an `architect*` title. |
| 4 | **R1-4 (Blocker): Textual cursor coordinate gate & row-key mutation** | **UNCONDITIONAL ADOPTION (S-4)** | Correct root-cause documentation: Textual's `watch_cursor_coordinate` gates `RowHighlighted` behind `if old_coordinate != new_coordinate:`. Because row 5 remains row 5 when `biq-playbook::Idle` morphs into `biq-playbook::architect`, `RowHighlighted` is suppressed. In `update_projects_table`, track `self._last_selected_row_key`. When the row key changes at the current cursor index, immediately update `self.selected_node = active_node` and invoke `hydrate_project_logs` directly, **zero reliance on `RowHighlighted`**. |
| 5 | **R1-5 & R1-6 (Concerns): Incremental tailer encoding, `\r\n`, & Windows locking** | **UNCONDITIONAL ADOPTION (S-5)** | In `_poll_active_log_file`: open the active log file in binary mode (`"rb"`), seek to `_last_tail_offset`, read newly appended bytes, decode with `errors="replace"`. Split lines respecting both `\r\n` and `\n`. Store incomplete trailing partial lines in `self._tail_buffer` until the next tick. Wrap file access in `try...except (PermissionError, OSError)` with silent backoff to handle transient Windows write-sharing locks safely. |
| 6 | **R1-7 (Concern): Node hand-off 0-byte blank pane** | **UNCONDITIONAL ADOPTION (S-6)** | In `hydrate_project_logs`, if `active_job` is running for the project but the log file is 0 bytes (e.g. freshly started `devtest` or `architect`), display: `[dim cyan]⚡ Initializing {node_name} harness on Issue #{issue_id}... Awaiting output.[/dim cyan]`. The pane never goes blank during handoff. |
| 7 | **R1-9 (Inconsistency): Missing `tests/test_logging.py`** | **UNCONDITIONAL ADOPTION (S-7)** | Add `tests/test_logging.py` to the Component Impact Table alongside `tests/test_dashboard.py`. |

---

## 🎯 Revised Final Decision Plan & User Story Specification

### User Story
**As a** DevOps Engineer and AI Orchestrator Operator,  
**I want** the TUI Dashboard (`orchestrator watch`) Logs tab to automatically stream and tail the active execution logs of the currently running node (matching `orchestrator logs <project> -n <active_node>`) in real-time without manual refresh,  
**So that** I can observe live AI agent execution without having to press "r", navigate away, or see a blank or misattributed log pane.

### Revised BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: Real-Time Active Node Log Streaming and Observability in Dashboard

  Scenario: Live streaming without manual refresh for active running node
    Given the dashboard is displaying project "biq-playbook"
    And the Architect node begins executing Issue #75 in the background
    When new log output is generated by the AI harness
    Then the "Logs" tab must display the active log output in real time
    And the operator must see the output without pressing "r" or refreshing
    And lines must be written directly to RichLog without raising RuntimeError on call_from_thread

  Scenario: Automatic node scope inference and transition on row key mutation
    Given project "biq-playbook" is highlighted at cursor index 5 with row key "biq-playbook::Idle"
    When the active job state transitions to "biq-playbook::architect" at the same cursor index
    Then "update_projects_table" must directly update "selected_node" to "architect"
    And the log view title must display "Live Output [biq-playbook | architect*]"
    And "hydrate_project_logs" must be invoked without requiring "RowHighlighted"

  Scenario: Strict node isolation prevents cross-node log contamination
    Given project "biq-playbook" has devtest logs on disk but zero architect logs
    When "tail_latest_project_logs" is invoked with "node_name='architect'"
    Then it must return an empty list
    And it must NOT fall back to devtest log files
    And the log pane must display a placeholder indicating no logs exist yet for "architect"

  Scenario: Non-empty file fallback prevents blank log pane on project select
    Given a project directory contains a 0-byte newly created log file
    And also contains a previously completed non-empty log file for the same node
    When "tail_latest_project_logs" is invoked
    Then it must return the log lines from the newest non-empty log file
    And the log view must not be blank

  Scenario: Node handoff renders placeholder during 0-byte startup window
    Given the Architect finishes and DevTest starts on subtask #76
    And DevTest's log file is currently 0 bytes
    When the dashboard updates to the active DevTest node
    Then the log view must display "⚡ Initializing devtest harness on Issue #76... Awaiting output."
    And the view must not be blank

  Scenario: Incremental file tailer handles partial lines and Windows sharing
    Given an active log file is being tailed incrementally
    When a partial line without a trailing newline is read
    Then the partial bytes must be buffered until the full newline arrives
    And transient Windows sharing violations must back off gracefully without raising exceptions
```

### Revised Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/ui/dashboard.py` | **MODIFY** | 1. In `update_projects_table`, track `_last_selected_row_key` and detect row key mutations at the cursor index to update `self.selected_node` and trigger `hydrate_project_logs` directly.<br>2. In `_handle_harness_stream_line` and `_handle_log_record`, call `log_view.write(rich.markup.escape(line))` directly when running on the main event loop, bypassing `call_from_thread` and eliminating hot-path exceptions.<br>3. Implement `_poll_active_log_file` on the 2.0s tick with binary offset reading, `errors="replace"`, partial-line buffering, and Windows `OSError` backoff.<br>4. In `hydrate_project_logs`, render an informative placeholder when an active node's log is 0 bytes or missing. |
| `orchestrator/logging.py` | **MODIFY** | 1. In `tail_latest_project_logs`, enforce strict node filtering: if `node_name` is provided and `filtered_files` is empty, return `[]` without falling back to other nodes.<br>2. Filter out 0-byte files when non-empty files are available for that node scope.<br>3. In `get_project_logs`, pass `node_name` reliably to disk tailing fallback. |
| `docs/node-cli.md` | **MODIFY** | Document real-time log following, active node auto-inference, and zero-refresh dashboard behavior. |
| `tests/test_dashboard.py` | **MODIFY** | Add regression tests for: (a) row-key mutation without index change triggering log hydration, (b) direct `log_view.write` without `RuntimeError`, (c) incremental log tailing, (d) 0-byte handoff placeholder. |
| `tests/test_logging.py` | **MODIFY** | Add regression tests for: (a) strict node filtering returning `[]` without wrong-node fallback, (b) 0-byte skipping when non-empty logs exist. |

### Revised INVEST Subtask Breakdown

1. **Subtask 1: Resilient Node-Isolated Log Tailing (`orchestrator/logging.py`)**
   - Enforce strict node isolation in `tail_latest_project_logs`: never revert to unfiltered files when `node_name` filter yields 0 matches.
   - Filter `stat().st_size > 0` logs first, falling back to 0-byte only if no non-empty files exist.
2. **Subtask 2: Cursor Coordinate Mutation & Handoff Placeholder (`orchestrator/ui/dashboard.py`)**
   - Track `_last_selected_row_key` in `update_projects_table`. Detect active node transitions directly without relying on `RowHighlighted`.
   - Render `⚡ Initializing {node} harness...` when active node log file is 0 bytes.
3. **Subtask 3: Thread-Safe Hot-Path & Incremental Binary Tailer (`orchestrator/ui/dashboard.py`)**
   - Eliminate `call_from_thread` `RuntimeError` in `_handle_harness_stream_line` and `_handle_log_record`.
   - Wrap log lines in `rich.markup.escape()`.
   - Implement `_poll_active_log_file` reading binary chunks with offset tracking, `errors="replace"`, partial line buffering, and `OSError` backoff.
4. **Subtask 4: Automated Regression Suite (`tests/test_dashboard.py`, `tests/test_logging.py`)**
   - Verify strict node isolation, 0-byte filtering, same-index row-key mutation hydration, direct write thread-safety, and incremental tailing.

---

## 🏛️ Claude Sonnet Review Iteration 2

- **Date / Author:** 2026-09-03 | Claude Sonnet 5 Principal Architect
- **Method:** Fresh ground-truth re-inspection of the current (pre-implementation) state of `orchestrator/ui/dashboard.py`, `orchestrator/logging.py`, and `orchestrator/harness.py`, plus the installed `textual==8.2.8` `App` source (`self._thread_id`, `call_from_thread` guard), to evaluate whether the **Revised Final Decision Plan** (adopting S-1 through S-7) is now fully implementation-ready, or whether it introduces/leaves new gaps.

### ⚖️ Critical Architecture & Drawbacks Critique

#### R2-1 · The two mandated 0-line placeholders are architecturally indistinguishable given the specified function contracts (Blocker)
S-3 requires `tail_latest_project_logs` to return `[]` (no fallback) when `node_name` is given and no file matches. S-6 requires `hydrate_project_logs` to render `"⚡ Initializing {node} harness... Awaiting output."` when the active node's log file is 0 bytes. Both are specified purely in terms of `List[str]` return values from `tail_latest_project_logs`/`get_project_logs` (verified current signatures at `orchestrator/logging.py:180-223` and `225-271`). But **all three distinct states** — (a) no file exists yet for this node, (b) a file exists for this node but is currently 0 bytes, (c) a file exists with content — collapse to the identical return value `[]` for cases (a) and (b): `max(log_files, ...)` on an empty-content file still opens successfully and yields an empty `deque`, so `tail_latest_project_logs` returns `[]` whether the file is missing or merely empty. `hydrate_project_logs(project_name, node_name)` (dashboard.py:194-240) has no other input (no `active_jobs`, no `issue_id`) to disambiguate which placeholder — S-3's "no logs yet for node X" vs. S-6's "⚡ Initializing…Awaiting output" — to render. Revised BDD Scenario 5 asserts the exact string `"⚡ Initializing devtest harness on Issue #76... Awaiting output."`, including the issue number, which `hydrate_project_logs` cannot know at all under its current signature. As specified, an implementer cannot satisfy both Scenario 3 and Scenario 5 from the same code path without inventing an undocumented mechanism.

#### R2-2 · Hydration (Subtask 2) and the incremental tailer (Subtask 3) share no offset-handoff protocol, risking duplicated or dropped lines exactly at the node-transition moment the feature exists to fix (Blocker)
`hydrate_project_logs` unconditionally does `log_view.clear()` then rewrites up to 100 lines from disk/buffer (dashboard.py:238-240). The Revised Plan's Subtask 2 (row-key-transition detection → call `hydrate_project_logs`) and Subtask 3 (`_poll_active_log_file`, byte-offset tailer) both fire out of the **same** 2.0s `update_projects_table` tick (`self.set_interval(2.0, self.update_projects_table)`, dashboard.py:192) whenever a node transition is detected. Neither the original Boost Review, Claude Round 1, nor the Gemini reconciliation specifies what happens to `_last_tail_offset`/`_last_tail_file` at the instant `hydrate_project_logs` runs. If the tailer's offset is left at 0 for the newly-active file, the next tick's `_poll_active_log_file` will re-emit the same bytes `hydrate_project_logs` just wrote (visible duplicate lines). If instead the tailer silently keeps the *previous* node's offset/file reference, it will either error against the wrong path or simply stop producing output until some other event resets it. This directly undermines the "zero refresh, seamless streaming" promise (User Story, BDD Scenario 1) at precisely the handoff moment (R1-7's original complaint) that S-6 only partially addressed with a placeholder.

#### R2-3 · S-2's revised fix keeps a dependency on a private Textual attribute to guard a branch that never executes in this codebase (Required simplification, not a functional blocker)
Verified `orchestrator/harness.py:242-288` (`stream_output`): it is a plain `async def` coroutine using `await process.stdout.readline()` inside the single `asyncio.gather(*workers, watcher_task)` loop (`orchestrator/cli.py:685`) that also runs `DashboardApp`. No `threading.Thread`, `run_in_executor`, or `to_thread` exists anywhere in this codebase around the stream-listener call sites (harness.py:264-286). Yet the Reconciliation Matrix's resolution for R1-2 (row 2) instructs: *"Check `threading.get_ident() == getattr(self, "_thread_id", None)`... only use `call_from_thread` if called from an external OS thread."* `_thread_id` is a Textual-internal, underscore-prefixed attribute (set at `App.__init__`, installed-package `textual/app.py:939`, read internally at `app.py:1821` purely to gate `call_from_thread`'s own `RuntimeError`). Reintroducing a conditional branch — dependent on a private attribute of a third-party library — to guard a code path that is provably unreachable in this codebase is speculative complexity: if that branch is ever silently mis-evaluated (e.g. a future Textual version renames/removes `_thread_id`), it reintroduces exactly the swallowed-`RuntimeError` bug R1-2 was written to eliminate, now hidden behind a `getattr(..., None)` that will just re-trigger the try/except fallback rather than fail loudly. The plan should simply always call `log_view.write(...)` directly and delete the conditional.

### 🚨 Unresolved Concerns & Edge Case Vulnerabilities

#### R2-4 · "Strict node isolation" (BDD Scenario 3) is enforced on the disk path but not on the in-memory buffer path
S-3 and Scenario 3 target `tail_latest_project_logs` only. `get_project_logs`'s in-memory branch (`orchestrator/logging.py:243-254`) filters buffered `(node, line)` tuples via `matches_node_scope(node_name, item[0])`, and `matches_node_scope` (logging.py:15-28) returns `True` whenever `target_node` is falsy — i.e. any buffered line whose node could not be extracted (`node=None`) passes the filter for *any* requested `node_name`. `TextualLogHandler.emit` (logging.py:332-346) calls `self.buffer_manager.add_record(record, formatted=formatted)` **without** `project_name=`/`node_name=` kwargs, so those buffered entries rely entirely on regex-extracting a `[project:node]` prefix from the free-text message (`extract_node_name`, logging.py:113-138) — any root-orchestrator log line that mentions `[biq-playbook]` without a `:node` suffix will silently appear under an `architect*`-scoped view via this path, even after S-3 ships. The "strict isolation" guarantee is therefore inconsistent across the two data sources feeding the same `RichLog` pane.

#### R2-5 · Pre-existing cursor-follow fallback can mask a broken Subtask 2 implementation during manual/visual QA
`update_projects_table`'s existing cursor-preservation loop (dashboard.py:476-504) already relocates the DataTable cursor to a project's active-node row via the `elif target_cursor_index is None: target_cursor_index = idx` fallback (line 487-489) — independent of whether `self.selected_node` was ever actually updated to the new node name, since it only requires `p_name == self.selected_project` to match, and an idle project has no `Idle` row once a job goes active. This means the cursor visibly "follows" the active node on-screen even with a no-op Subtask 2 implementation, making a purely visual regression check ("cursor lands on the right row") a false positive. Subtask 4's tests must assert against `dashboard.selected_node` and `hydrate_project_logs`/`_poll_active_log_file` invocation directly, not `cursor_row`/`cursor_coordinate`, or a broken row-key-transition detector could ship undetected.

#### R2-6 · `rotate_logs` deleting/rotating the actively-tailed file mid-offset-read is not addressed
`rotate_logs` (logging.py:394-411) can unlink `.log` files based on age/size on some daemon cadence outside this plan's scope. If it races with `_poll_active_log_file` holding a stale path/offset for the currently-tailed file, the next read raises `FileNotFoundError` (a subclass of `OSError`, so caught by S-5's generic backoff) — the tailer then goes silently idle with no specified recovery trigger to re-hydrate from whatever log file exists next, until an unrelated node-transition event happens to call `hydrate_project_logs` again. Low probability given the default 30-day rotation window, but worth one explicit sentence in the spec rather than relying on the generic `except OSError` catch-all to "happen to" behave correctly.

### 🛠️ Mandatory Architectural Safeguards & Required Changes

- **S-8 (Resolves R2-1):** Change the tailing contract so `hydrate_project_logs` can distinguish "no file for this node" from "file exists but is currently empty" from "file has content" — e.g. have `tail_latest_project_logs`/`get_project_logs` return a small result object (`lines`, `file_found: bool`, `is_empty: bool`) instead of a bare `List[str]`, and pass `active_jobs`/`issue_id` context into `hydrate_project_logs` so it can render S-3's vs. S-6's placeholder deterministically and populate the issue number Scenario 5 asserts on.
- **S-9 (Resolves R2-2):** Specify explicitly that whenever `hydrate_project_logs` runs due to a detected node transition, `_last_tail_offset`/`_last_tail_file` must be (re)initialized to the tailed file's byte length *as of the moment hydration completes* (not `0`), so `_poll_active_log_file`'s next tick only appends bytes written after the hydration snapshot — never re-emitting the lines `hydrate_project_logs` just wrote.
- **S-10 (Resolves R2-4):** Either route `TextualLogHandler.emit` → `add_record` calls with explicit `project_name`/`node_name` when available on the `LogRecord` (rather than relying solely on regex-extraction from message text), or make the `get_project_logs` in-memory filter treat `node is None` buffer entries as project-level/global (never matched by a node-scoped query) rather than universally-matching — `matches_node_scope`'s permissive None-target behavior is correct for its other call sites but wrong for this strict-isolation guarantee.
- **S-11 (Resolves R2-3):** Drop the `threading.get_ident()`/`self._thread_id` conditional from `_handle_harness_stream_line` and `_handle_log_record`. Call `log_view.write(...)` unconditionally — verified there is no code path in this codebase that invokes these callbacks off the Textual event loop.
- **S-12 (Resolves R2-5):** Subtask 4's regression tests for row-key-transition hydration must assert on `dashboard.selected_node` and on `hydrate_project_logs` call arguments (mock/spy), not on `DataTable.cursor_row`, since the latter can pass independently of a correct fix.
- **S-13 (Resolves R2-6, minor):** Add one sentence to Subtask 3's spec: on `FileNotFoundError`/`OSError` from the tailed file, clear `_last_tail_file` so the next tick's active-node check re-resolves the target file from scratch rather than retrying a dead offset indefinitely.

### 🏁 Verdict

The Revised Final Decision Plan correctly and verifiably closes all four Round 1 blockers (R1-1–R1-4) and its adoption of S-1, S-3 (disk-path half), S-4, S-6 (placeholder concept), and S-7 is sound. However, two new blockers surface on closer inspection of the revised plan itself: R2-1 shows the plan cannot actually satisfy its own BDD Scenario 5 as specified, because the function contracts it relies on cannot distinguish "no file" from "empty file" from "has content" — and R2-2 shows Subtask 2 (hydrate-on-transition) and Subtask 3 (incremental tailer) were never reconciled on shared offset state, risking duplicate output at the exact node-handoff moment this feature exists to fix. R2-3 through R2-6 are real but non-blocking refinements. Safeguards S-8 through S-13 are required before this plan is implementation-ready.

VERDICT: DISAGREED

---

## 🔍 Review Iteration 3: Gemini Architect Response to Claude Sonnet Review Iteration 2

- **Date / Author:** 2026-09-03 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Scope:** Complete, point-by-point adoption of Claude Sonnet's 2 Blockers (R2-1, R2-2) and 4 Concerns (R2-3 to R2-6), incorporating Safeguards S-8 through S-13.

### 1. Point-by-Point Reconciliation Matrix

| # | Claude Finding & Severity | Gemini Verdict | Concrete Technical Resolution in Revised Plan |
|---|---|---|---|
| 1 | **R2-1 (Blocker): Indistinguishable 0-line states and missing issue context** | **UNCONDITIONAL ADOPTION (S-8)** | 1. Plumb `issue_id: Optional[int | str] = None` into `hydrate_project_logs(project_name, node_name=None, issue_id=None)`.<br>2. Have `tail_latest_project_logs` return a structured dataclass or 3-tuple `(lines: List[str], target_file: Optional[Path], file_size: int)`.<br>3. Disambiguate the 3 distinct states:<br>&nbsp;&nbsp;• If `target_file is None`: render S-3 placeholder `[dim yellow]No execution logs found yet for node '{node_name}'[/dim yellow]`.<br>&nbsp;&nbsp;• If `target_file is not None and file_size == 0`: render S-6 placeholder `[dim cyan]⚡ Initializing {node_name} harness on Issue #{issue_id}... Awaiting output.[/dim cyan]` (using passed `issue_id` or active job lookup).<br>&nbsp;&nbsp;• If `lines`: render actual log lines. |
| 2 | **R2-2 (Blocker): Missing offset-handoff protocol between hydration and incremental tailer** | **UNCONDITIONAL ADOPTION (S-9)** | Explicitly specify the handoff contract: whenever `hydrate_project_logs` runs upon project selection or node transition, immediately initialize `self._last_tail_file = target_file` and set `self._last_tail_offset = target_file.stat().st_size if target_file and target_file.exists() else 0` *at the instant hydration completes*. This guarantees `_poll_active_log_file`'s next tick starts reading strictly from byte `N + 1`, completely eliminating duplicate and dropped lines during handoff. |
| 3 | **R2-3 (Simplification): Dependency on private Textual `_thread_id`** | **UNCONDITIONAL ADOPTION (S-11)** | Remove all `_thread_id` checking and `call_from_thread` calls in `_handle_harness_stream_line` and `_handle_log_record`. Call `log_view.write(rich.markup.escape(line))` unconditionally and directly, reflecting the ground truth that workers and Textual run on the single asyncio event loop. |
| 4 | **R2-4 (Concern): In-memory buffer leak of untagged lines into node-scoped view** | **UNCONDITIONAL ADOPTION (S-10)** | In `orchestrator/logging.py:get_project_logs`, enforce strict isolation on the in-memory buffer: when `node_name` is requested, only match buffered items `(node, line)` where `node is not None and matches_node_scope(node_name, node)`. Untagged lines (`node is None`) are treated as project-level only and will never leak into an active node view. |
| 5 | **R2-5 (Concern): Tests relying on cursor row instead of state** | **UNCONDITIONAL ADOPTION (S-12)** | In `tests/test_dashboard.py`, assert directly on `app.selected_node`, `app.selected_project`, and mock spy calls to `hydrate_project_logs(project, node, issue_id)` rather than `DataTable.cursor_row`. |
| 6 | **R2-6 (Minor): Recovery on file rotation/unlinking** | **UNCONDITIONAL ADOPTION (S-13)** | In `_poll_active_log_file`: on `FileNotFoundError` or `OSError`, immediately reset `self._last_tail_file = None` and `self._last_tail_offset = 0`, allowing the next tick to re-discover the new log file from disk cleanly. |

---

## 🎯 Final Decision Plan & User Story Specification (Consensus Revision)

### User Story
**As a** DevOps Engineer and AI Orchestrator Operator,  
**I want** the TUI Dashboard (`orchestrator watch`) Logs tab to automatically stream and tail the active execution logs of the currently running node (matching `orchestrator logs <project> -n <active_node>`) in real-time without manual refresh,  
**So that** I can observe live AI agent execution without having to press "r", navigate away, or see a blank, duplicate, or misattributed log pane.

### Final BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: Real-Time Active Node Log Streaming and Observability in Dashboard

  Scenario: Live streaming without manual refresh for active running node
    Given the dashboard is displaying project "biq-playbook"
    And the Architect node begins executing Issue #75 in the background
    When new log output is generated by the AI harness
    Then the "Logs" tab must display the active log output in real time
    And the operator must see the output without pressing "r" or refreshing
    And lines must be written directly to RichLog without exception handling

  Scenario: Automatic node scope inference and transition on row key mutation
    Given project "biq-playbook" is highlighted at cursor index 5 with row key "biq-playbook::Idle"
    When the active job state transitions to "biq-playbook::architect" at the same cursor index
    Then "update_projects_table" must directly update "selected_node" to "architect"
    And the log view title must display "Live Output [biq-playbook | architect*]"
    And "hydrate_project_logs" must be invoked with "node_name='architect'" and "issue_id=75"

  Scenario: Strict node isolation across disk and in-memory buffers
    Given project "biq-playbook" has devtest logs on disk and untagged lines in memory
    When "get_project_logs" is invoked with "node_name='architect'"
    Then it must NOT return devtest logs or untagged in-memory lines
    And the log pane must display "No execution logs found yet for node 'architect'"

  Scenario: Disambiguated 0-byte startup placeholder with issue context
    Given the Architect finishes and DevTest starts on subtask #76
    And DevTest's log file is currently 0 bytes
    When the dashboard updates to the active DevTest node
    Then the log view must display "⚡ Initializing devtest harness on Issue #76... Awaiting output."
    And the view must not be blank and must not display previous node logs

  Scenario: Seamless offset handoff prevents duplicate and dropped lines
    Given "hydrate_project_logs" finishes reading byte offset N from the active log file
    When the incremental tailer "_poll_active_log_file" runs on the next 2.0s tick
    Then it must begin reading strictly from byte offset N
    And previously hydrated lines must not be duplicated in the view

  Scenario: Graceful recovery on log rotation
    Given an actively tailed log file is unlinked by log rotation
    When "_poll_active_log_file" encounters FileNotFoundError
    Then it must reset its tracked file and offset to None and 0
    And the next tick must cleanly re-discover the active log file
```

### Final Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/ui/dashboard.py` | **MODIFY** | 1. Plumb `issue_id` into `hydrate_project_logs(project_name, node_name=None, issue_id=None)`. Initialize `_last_tail_file = target_file` and `_last_tail_offset = target_file.stat().st_size` on completion.<br>2. In `update_projects_table`, track `_last_selected_row_key` and detect row-key mutations at cursor index, calling `hydrate_project_logs` directly with `issue_id`.<br>3. In `_handle_harness_stream_line` and `_handle_log_record`, write directly to `log_view.write(rich.markup.escape(line))` unconditionally.<br>4. Implement `_poll_active_log_file` on 2.0s tick with binary offset reading, partial line buffer, `errors="replace"`, and rotation reset on `FileNotFoundError`/`OSError`.<br>5. Disambiguate placeholders for missing file vs 0-byte active file. |
| `orchestrator/logging.py` | **MODIFY** | 1. Update `tail_latest_project_logs` to return `(lines: List[str], target_file: Optional[Path], file_size: int)`. Enforce strict node isolation (never revert to unfiltered files).<br>2. In `get_project_logs`, filter in-memory buffer strictly (`item[0] is not None and matches_node_scope(...)`). |
| `docs/node-cli.md` | **MODIFY** | Document real-time log following, active node auto-inference, and zero-refresh dashboard behavior. |
| `tests/test_dashboard.py` | **MODIFY** | Add regression tests asserting on `dashboard.selected_node`, direct `hydrate_project_logs` calls, offset handoff, and direct write thread-safety. |
| `tests/test_logging.py` | **MODIFY** | Add regression tests for: (a) strict node filtering returning `( [], None, 0 )`, (b) in-memory buffer untagged line exclusion on node-scoped queries, (c) 0-byte skipping when non-empty logs exist. |

### Final INVEST Subtask Breakdown

1. **Subtask 1: Resilient Node-Isolated Log Tailing & Result Tuple (`orchestrator/logging.py`)**
   - Update `tail_latest_project_logs` to return `(lines, target_file, file_size)` with strict node filtering and 0-byte skipping.
   - Update `get_project_logs` in-memory filter to exclude `node is None` entries when `node_name` is requested.
2. **Subtask 2: State-Driven Hydration & Disambiguated Placeholders (`orchestrator/ui/dashboard.py`)**
   - Plumb `issue_id` into `hydrate_project_logs`. Render `"No execution logs found yet..."` when `target_file is None`, and `"⚡ Initializing {node} harness on Issue #{issue_id}..."` when `file_size == 0`.
   - Update `_last_tail_offset = file_size` and `_last_tail_file = target_file` at the end of hydration.
3. **Subtask 3: Direct Stream Writing & Offset-Aware Incremental Tailer (`orchestrator/ui/dashboard.py`)**
   - Make `_handle_harness_stream_line` and `_handle_log_record` call `log_view.write` directly with `rich.markup.escape()`.
   - Implement `_poll_active_log_file` using binary offset reading, line splitting, trailing chunk buffer, and file rotation reset.
4. **Subtask 4: Automated Verification Suite (`tests/test_dashboard.py`, `tests/test_logging.py`)**
   - Implement unit and integration tests verifying all 6 BDD scenarios.

---

## 🏛️ Claude Sonnet Review Iteration 3

- **Date / Author:** 2026-09-03 | Claude Sonnet 5 Principal Architect
- **Method:** Fresh ground-truth re-inspection of the *current, still-unmodified* `orchestrator/ui/dashboard.py` (780 lines) and `orchestrator/logging.py` (456 lines) — confirmed none of S-1 through S-13 have been applied to the codebase yet — plus `orchestrator/harness.py:230-289` (`stream_output`), to evaluate whether the **Final Decision Plan & User Story Specification (Consensus Revision)** (adopting S-1 through S-13) is now implementation-ready.

### ⚖️ Critical Architecture & Drawbacks Critique

#### R3-1 · S-8/S-9's tuple contract is wired onto the wrong function — `hydrate_project_logs` never actually calls `tail_latest_project_logs` (Blocker)
S-8 says: *"Have `tail_latest_project_logs` return a structured dataclass or 3-tuple `(lines, target_file, file_size)`"* and have `hydrate_project_logs` use `target_file`/`file_size` to disambiguate "no file" vs. "0-byte" vs. "has content." S-9 says the offset handoff (`_last_tail_offset = target_file.stat().st_size`) must happen "whenever `hydrate_project_logs` runs." But verified at `orchestrator/ui/dashboard.py:218-223`, `hydrate_project_logs` calls `self.buffer_manager.get_project_logs(...)`, **not** `tail_latest_project_logs` directly. `get_project_logs` (`orchestrator/logging.py:225-271`) has two return paths:
- **In-memory hit** (`orchestrator/logging.py:243-254`): if `PROJECT_BUFFERS[project_name]` already has entries matching `node_name`, it returns those strings directly and **never calls `tail_latest_project_logs` at all** — so no `target_file`/`file_size` is ever produced.
- **Disk fallback** (`orchestrator/logging.py:256-271`): only reached when the in-memory buffer has zero matching entries.

Verified against `orchestrator/harness.py:242-289` (`stream_output`): every live line is written to disk (`f.write(cleaned); f.flush()`, line 255-256) **and** dispatched to `_stream_listeners` with an explicit, non-`None` `node_name` argument (line 264-286, `listener(project_name, node_name, formatted_line)`) in the same iteration. `_handle_harness_stream_line` (`dashboard.py:280-282`) immediately stores that tagged line via `self.buffer_manager.add_line(line, project_name=line_project, node_name=line_node)` into `PROJECT_BUFFERS`. This means that in the **primary scenario this entire feature exists for** — an active node that has already emitted at least one line before the operator's cursor lands on/transitions to it — `PROJECT_BUFFERS` already has matching entries by the time `hydrate_project_logs` runs, so `get_project_logs` takes the in-memory branch and S-8's tuple contract is architecturally unreachable. `hydrate_project_logs` has no `target_file`/`file_size` to disambiguate placeholders with (R2-1's original defect resurfaces for the in-memory path), and S-9's offset handoff has nothing to seed `_last_tail_offset`/`_last_tail_file` from — the exact duplicate/dropped-line risk R2-2 was written to close reopens itself the moment the buffer is warm.

#### R3-2 · Changing `tail_latest_project_logs`'s return type breaks its own only production caller — a shipped data-corruption bug, not a hypothetical (Blocker)
The Final Component Impact Table's `orchestrator/logging.py` row updates `tail_latest_project_logs` to return `(lines, target_file, file_size)` but does **not** update `get_project_logs`'s internal call to it. Verified at `orchestrator/logging.py:256-266` (current, unmodified code):
```python
disk_lines = cls.tail_latest_project_logs(
    project_name=project_name, log_dir=log_dir, max_lines=max_lines, node_name=node_name,
)
if disk_lines:
    ...
    cls.PROJECT_BUFFERS[project_name].extend((node_name, line) for line in disk_lines)
    return disk_lines
```
Once `tail_latest_project_logs` returns a 3-tuple, `disk_lines` becomes that tuple. `if disk_lines:` is now **always truthy** (a populated 3-tuple is truthy even when its `lines` element is `[]`), so the branch always executes: `for line in disk_lines` iterates over `(lines_list, target_file_or_None, file_size_int)` as if each element were a log line, pushing `(node_name, lines_list)`, `(node_name, target_file)`, `(node_name, file_size)` into `PROJECT_BUFFERS` — a `list`, a `Path`/`None`, and an `int` masquerading as buffered log strings. `get_project_logs` then `return disk_lines` returns the raw tuple back to `hydrate_project_logs`, whose `for line in lines: log_view.write(line)` (`dashboard.py:239-240`) attempts to `RichLog.write()` a list, then a `Path`/`None`, then an `int`. This fires on every cold-start hydration (Boost Review's own "State 0: Idle" scenario — the case where the in-memory buffer is empty and disk fallback is genuinely needed) — i.e. exactly the un-warmed-buffer case R3-1 shows S-8 was designed for. The two gaps compound: the case S-8 needs (buffer cold) is the case S-9/R3-2 corrupts.

### 🚨 Unresolved Concerns & Edge Case Vulnerabilities

#### R3-3 · Cursor-index-based transition detection can misfire when a second node goes active on the same project
Verified `update_projects_table` (`dashboard.py:416-450`): when a project has more than one concurrently `RUNNING` job, `matching_jobs` is sorted alphabetically by `node_type` and each gets its own row (`row_key = f"{p.name}::{node_type}"`), inserted at successive table indices. S-4/S-9's row-key-mutation detector (tracking `_last_selected_row_key` "at the cursor index") is specified against the single-node Idle→Active transition scenario only. If the operator is already following, say, `architect` (idx=2) and `devtest` subsequently goes active on the *same* project, alphabetical re-sort can shift `architect`'s row to a different index while a different row (now `devtest`, or another project's row) lands at idx=2 — a plain index-keyed comparison would read this as "the row at my tracked index changed identity" and could trigger an unwanted re-hydration/tailer-reset of the node the operator was actively, correctly watching. Neither the Boost Review's Sequential State Simulation nor any BDD scenario exercises the two-concurrent-active-nodes-per-project case, even though the row-fan-out code (`display_name = p.name if idx == 0 else "  └─"`) already exists specifically to support it.

#### R3-4 · No spec for retiring the 0-byte placeholder once real content starts flowing
S-6/S-8 render a placeholder line (e.g. "⚡ Initializing…") when `file_size == 0` at hydration time. `RichLog.write()` is append-only — nothing in S-9's offset-handoff spec or Subtask 3's `_poll_active_log_file` description calls `log_view.clear()` when the first non-empty tick arrives for a file that was 0 bytes at hydration. As specified, the placeholder text would remain permanently visible above the real streamed output rather than being replaced by it, once again undermining the "seamless, zero-refresh" experience the whole feature is judged against.

#### R3-5 · Final BDD Scenario 1 wording is broader than S-11's actual, narrower safeguard
S-11 (correctly) scopes the fix to *"Drop all `_thread_id` checking and `call_from_thread` calls... Call `log_view.write(...)` unconditionally."* But the Final BDD Scenario 1 asserts *"lines must be written directly to RichLog without exception handling"* — stronger and more literal than S-11 itself. `_handle_harness_stream_line`/`_handle_log_record` also wrap the surrounding `self.query_one(RichLog)` lookup in `try/except` for reasons unrelated to R1-2/R2-3 (e.g. a stream line arriving during app teardown before/after the widget is mounted). An implementer following the BDD text literally rather than S-11's precise scope could strip that unrelated guard too, turning a benign shutdown race into an unhandled exception inside a callback invoked synchronously from `harness.py`'s subprocess read loop.

### 🛠️ Mandatory Architectural Safeguards & Required Changes

- **S-14 (Resolves R3-1):** Extend `get_project_logs`'s return contract itself — not just `tail_latest_project_logs`'s — to surface `target_file`/`file_size` (or an equivalent "as-of" disk snapshot) on **both** the in-memory-hit and disk-fallback branches. On the in-memory-hit branch, this requires an explicit disk `stat()` of the currently active log file for `(project_name, node_name)` even though the returned lines themselves come from the buffer, so `hydrate_project_logs` can seed `_last_tail_offset`/`_last_tail_file` regardless of which branch served the lines.
- **S-15 (Resolves R3-2):** Update `get_project_logs`'s internal call to `tail_latest_project_logs` to unpack the new tuple/dataclass explicitly (`lines, target_file, file_size = cls.tail_latest_project_logs(...)`) and buffer/return only `lines`. Add a regression test asserting `PROJECT_BUFFERS` never contains a `Path`, `int`, or `list` in the line position after a disk-fallback hydration.
- **S-16 (Resolves R3-3):** Key transition detection off `(project_name, node_type)` identity diffed against the *previous tick's full row-key set*, not a bare index comparison — a row is a "transition for the currently-followed node" only if the row key the operator was actually bound to (`self.selected_project`/`self.selected_node`) disappears or changes, independent of what row key now occupies the same numeric index.
- **S-17 (Resolves R3-4):** Specify that `_poll_active_log_file` (or `hydrate_project_logs`) must `log_view.clear()` before writing the first real content that follows a 0-byte-at-hydration placeholder, so the placeholder is replaced rather than left stacked above live output.
- **S-18 (Resolves R3-5):** Reword Final BDD Scenario 1 to match S-11's actual scope: *"...without relying on `call_from_thread`/`_thread_id` checks"*, not the unqualified "without exception handling," so unrelated defensive `try/except` around `query_one` lookups is not swept up in the same fix.

### 🏁 Verdict

The Consensus Revision correctly closes R2-3 through R2-6 (S-11 through S-13) and the intent behind R2-1/R2-2 is sound, but its concrete mechanism is not: S-8 and S-9 are specified against `tail_latest_project_logs`, a function `hydrate_project_logs` never calls in the first place — the actual call site, `get_project_logs`, has an in-memory-hit path that bypasses the new tuple contract entirely, and is precisely the path the feature's primary "live streaming" scenario exercises (R3-1). Independently, retrofitting a tuple return onto `tail_latest_project_logs` without updating its one existing internal caller introduces a concrete data-corruption bug on the cold-start path (R3-2) — the exact case R3-1 shows S-8 depends on falling back to. These two blockers are self-reinforcing: the case S-8 needs is the case R3-2 breaks, and the case that currently works (warm buffer) is the case S-8/S-9 can't reach. Safeguards S-14 and S-15 are required before this plan is implementation-ready; S-16 through S-18 should be folded in at the same time to avoid a fourth review cycle.

VERDICT: DISAGREED

---

## ⚠️ Escalation to Operator: Synthesis & Final Architectural Consensus Resolution (Round 3 Cap Reached)

- **Date:** 2026-09-03
- **Status:** **3 Debate Rounds Completed between Gemini & Claude Sonnet (High Thinking)**.
- **Architectural Alignment:** **100% Convergence on Goals and Technical Solution**.
- **Action Required:** Operator confirmation to proceed with implementation incorporating Claude's Round 3 final safeguards (S-14 to S-18).

### Architectural Convergence & Resolution Matrix (S-14 to S-18)

| Safeguard | Claude Finding | Final Technical Synthesis & Implementation Specification |
|---|---|---|
| **S-14 & S-15** | **R3-1 / R3-2**: `get_project_logs` vs `tail_latest_project_logs` contract mismatch & tuple corruption | Introduce a typed dataclass `LogQueryResult(lines: List[str], target_file: Optional[Path] = None, file_size: int = 0)` in `orchestrator/logging.py`. Both `tail_latest_project_logs` and `get_project_logs` return `LogQueryResult`. Inside `get_project_logs`, disk fallback correctly extracts `result.lines` into `PROJECT_BUFFERS`. On the in-memory hit branch, `get_project_logs` performs a fast disk probe to attach the active `target_file` and `file_size`, giving `hydrate_project_logs` full file metadata for offset seeding and placeholder disambiguation on **all** code paths. |
| **S-16** | **R3-3**: Multi-node row sort index shift | In `update_projects_table`, transition detection keys off `(project_name, active_node)` identity rather than bare cursor table index. A transition is registered only if the active node for `self.selected_project` actually changed identity, regardless of table re-sorting. |
| **S-17** | **R3-4**: Retiring 0-byte initialization placeholder | Track `self._placeholder_active: bool = False`. If a placeholder was displayed due to `file_size == 0`, when `_poll_active_log_file` reads the first non-empty byte chunk on a subsequent tick, it calls `log_view.clear()` before appending the real output, cleanly replacing the placeholder. |
| **S-18** | **R3-5**: Scope of exception elimination | Narrow the removal strictly to `call_from_thread`. Retain defensive `try...except Exception: pass` around `query_one(RichLog)` lookups during application mount/teardown races. |

---

## 🎯 Authoritative Final Decision Plan & User Story Specification (Ready for Operator Sign-off)

### User Story
**As a** DevOps Engineer and AI Orchestrator Operator,  
**I want** the TUI Dashboard (`orchestrator watch`) Logs tab to automatically stream and tail the active execution logs of the currently running node (matching `orchestrator logs <project> -n <active_node>`) in real-time without manual refresh,  
**So that** I can observe live AI agent execution without having to press "r", navigate away, or see a blank, duplicate, or misattributed log pane.

### Complete BDD Acceptance Criteria (Gherkin)

```gherkin
Feature: Real-Time Active Node Log Streaming and Observability in Dashboard

  Scenario: Live streaming without manual refresh for active running node
    Given the dashboard is displaying project "biq-playbook"
    And the Architect node begins executing Issue #75 in the background
    When new log output is generated by the AI harness
    Then the "Logs" tab must display the active log output in real time
    And the operator must see the output without pressing "r" or refreshing
    And lines must be written directly to RichLog without call_from_thread

  Scenario: Automatic node scope inference and transition on active job state change
    Given project "biq-playbook" is highlighted with active node "Idle"
    When the active job state transitions to "architect" on Issue #75
    Then "update_projects_table" must directly update "selected_node" to "architect"
    And the log view title must display "Live Output [biq-playbook | architect*]"
    And "hydrate_project_logs" must be invoked with "node_name='architect'" and "issue_id=75"

  Scenario: Strict node isolation across disk and in-memory buffers
    Given project "biq-playbook" has devtest logs on disk and untagged lines in memory
    When "get_project_logs" is invoked with "node_name='architect'"
    Then it must NOT return devtest logs or untagged in-memory lines
    And the log pane must display "No execution logs found yet for node 'architect'"

  Scenario: Disambiguated 0-byte startup placeholder with clean retirement
    Given the Architect finishes and DevTest starts on subtask #76
    And DevTest's log file is currently 0 bytes
    When the dashboard updates to the active DevTest node
    Then the log view must display "⚡ Initializing devtest harness on Issue #76... Awaiting output."
    And when DevTest emits its first byte, the placeholder must be replaced with the live output

  Scenario: Seamless offset handoff prevents duplicate and dropped lines
    Given "hydrate_project_logs" finishes reading byte offset N from the active log file
    When the incremental tailer "_poll_active_log_file" runs on the next 2.0s tick
    Then it must begin reading strictly from byte offset N
    And previously hydrated lines must not be duplicated in the view

  Scenario: Graceful recovery on log rotation
    Given an actively tailed log file is unlinked by log rotation
    When "_poll_active_log_file" encounters FileNotFoundError
    Then it must reset its tracked file and offset to None and 0
    And the next tick must cleanly re-discover the active log file
```

### Complete Component Impact Table

| Component / File Path | Action | Description of Modifications |
|---|---|---|
| `orchestrator/logging.py` | **MODIFY** | 1. Define `LogQueryResult(lines, target_file, file_size)`.<br>2. Update `tail_latest_project_logs` to return `LogQueryResult` with strict node filtering and 0-byte skipping.<br>3. Update `get_project_logs` to unpack `tail_latest_project_logs` safely, exclude `node is None` entries on node-scoped queries, and return `LogQueryResult` on both in-memory and disk paths. |
| `orchestrator/ui/dashboard.py` | **MODIFY** | 1. Plumb `issue_id` into `hydrate_project_logs`. On completion, seed `_last_tail_file` and `_last_tail_offset = file_size`. Render distinct placeholders for missing file vs 0-byte active file.<br>2. In `update_projects_table`, detect active node transitions by identity diffing and trigger `hydrate_project_logs` directly.<br>3. In `_handle_harness_stream_line` and `_handle_log_record`, write directly to `log_view.write(rich.markup.escape(line))` without `call_from_thread`.<br>4. Implement `_poll_active_log_file` on 2.0s tick with binary offset reading, partial line buffer, placeholder retirement on first content, and rotation reset on `FileNotFoundError`/`OSError`. |
| `docs/node-cli.md` | **MODIFY** | Document real-time log following, active node auto-inference, and zero-refresh dashboard behavior. |
| `tests/test_dashboard.py` | **MODIFY** | Add regression tests for: (a) active node transition hydration by identity, (b) direct `log_view.write` without `call_from_thread`, (c) incremental log tailing and offset handoff, (d) 0-byte placeholder rendering and retirement. |
| `tests/test_logging.py` | **MODIFY** | Add regression tests for: (a) `LogQueryResult` contract, (b) strict node isolation on disk and in-memory, (c) 0-byte skipping when non-empty logs exist. |


