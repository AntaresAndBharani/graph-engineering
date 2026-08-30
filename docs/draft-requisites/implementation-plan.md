The root cause of the empty log pane when selecting a subtask is a misaligned event delegation in the Textual framework, combined with the lack of a persistent project-scoped log buffer.

### Phase 1: Functional & DX (Developer Experience) Review

**Workflow Analysis & Multi-Pane TUI Journey**

* **The Bug:** Textual data tables emit `DataTable.RowSelected` and `DataTable.RowHighlighted` events upon interaction. Currently, clicking subtask `#455` in the SDLC widget either triggers a destructive `rich_log.clear()` or bubbles up an event that fails to map to a valid log stream.
* **The Fix:** The `RichLog` pane must be completely decoupled from the *subtask* selection. Its state should be exclusively bound to the *project* selection (e.g., `crosstrainingapp`). Clicking any row in the SDLC widget must be a UI-isolated, non-destructive action.

**Edge Cases & Resilience Strategy**

* **Cold-Start Hydration:** When a project is selected but the orchestrator was just restarted, the in-memory log buffer is empty. The system must implement a disk-tail fallback to read the last 100 lines of `~/.config/orchestrator/logs/<project_name>/*.log`.
* **Event Bubbling:** Textual events bubble up the DOM. If the SDLC widget's highlight event lacks an explicit handler with `event.stop()`, it may trigger the parent container's refresh logic, causing UI flicker.
* **Dual Stream Ingestion:** Logs originate from two sources: root Python loggers (`orchestrator.*`) and active agent subprocess stdout (via `harness.py`). Both streams must be explicitly tagged with the `project_name` and routed to the same project-scoped memory buffer.

**Acceptance Criteria (BDD Format)**

```gherkin
Scenario: Subtask navigation does not destructively clear active logs
  Given the TUI dashboard is active and project "crosstrainingapp" is selected
  And the "Logs" pane is streaming execution output for the "devtest" node
  When the user clicks or navigates through subtasks (e.g., #455) in the SDLC table
  Then the "Logs" pane must NOT clear its contents
  And it must continuously append the active node's live execution without interruption.

Scenario: Idempotent Log Hydration on Project Selection
  Given the user switches focus from "graph-engineering" to "crosstrainingapp"
  When the project selection event fires
  Then the dashboard must retrieve "crosstrainingapp"'s scoped log buffer
  And if the memory buffer is empty, it must tail the latest disk log for that project
  And populate the "RichLog" pane.

```

**CLI UX Guidelines**

* **Log Tagging:** Standardize output in `orchestrator/logging.py` to always prefix the project and node: `[crosstrainingapp|devtest] Running pytest for #455...`.
* **Visual Scope Indicator:** Update the `RichLog` border or tab title dynamically to indicate the active scope: `Logs [Scope: crosstrainingapp]` to confirm to the operator exactly which node's logs they are reading.

---

### Phase 2: Architectural & Implementation Plan

**Codebase Impact & Component Updates**

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/logging.py` | **Modify** | Implement `ProjectLogBufferManager` using a `collections.deque(maxlen=500)` per project and a disk-tailing fallback helper. |
| `orchestrator/harness.py` | **Modify** | Update `AsyncHarnessAdapter._stream_listeners` signature to pass `(project_name, line)` to route subprocess stdout accurately. |
| `orchestrator/ui/dashboard.py` | **Modify** | Bind `RichLog` to the `ProjectLogBufferManager`. Add explicit event suppression for `#sdlc_widget` interactions. |

**Technical Constraints & Safeguards**

* **Memory Bounding:** Do not store unbounded logs in memory. Use `defaultdict(lambda: deque(maxlen=500))` to prevent memory leaks during long-running daemon sessions.
* **Thread-Safe TUI Updates:** Logs emitted from async worker loops must be passed to the Textual UI thread using `app.call_from_thread(rich_log.write, formatted_message)`.
* **Strict Selectors:** Ensure event decorators target the exact DOM ID. Use `@on(DataTable.RowHighlighted, "#sdlc_widget")` to trap the SDLC table events and call `event.stop()`.

**Execution Steps**

1. **Buffer Management:** In `orchestrator/logging.py`, build `ProjectLogBufferManager`. Implement `.append(project_name, line)` and `.get_logs(project_name)`. Include a fallback to `tail -n 100` on the project's disk log directory if the memory deque is empty.
2. **Harness Routing:** In `orchestrator/harness.py`, modify the subprocess stream broadcaster to inject the `project_name` into every emitted log line listener callback.
3. **Event Suppression:** In `orchestrator/ui/dashboard.py`, add an explicit handler for `#sdlc_widget` to consume clicks without triggering log clears:
```python
@on(DataTable.RowSelected, "#sdlc_widget")
@on(DataTable.RowHighlighted, "#sdlc_widget")
def on_sdlc_interaction(self, event: DataTable.RowHighlighted) -> None:
    event.stop()  # Isolate event; prevent log wiping

```


4. **Log Hydration:** In `orchestrator/ui/dashboard.py`, update the `#projects_table` selection handler. When `active_project` changes, call `rich_log.clear()`, fetch the buffer via `ProjectLogBufferManager.get_logs(new_project)`, and write the lines to the UI.

---

# 📋 EPIC: Project-Scoped Reactive Log Tailing & Non-Destructive Navigation

## 📖 Context & Scope

In the `orchestrator watch` TUI dashboard, interacting with the SDLC subtask table inadvertently interrupts or clears the `RichLog` pane. Operators need the log pane to persistently tail the active node's execution (e.g., `devtest`) for the selected project, regardless of where they click in the subtask hierarchy.

This requires implementing a thread-safe `ProjectLogBufferManager`, routing both Python loggers and async subprocess streams into project-scoped queues, and explicitly isolating Textual UI events to prevent destructive rendering.

## 🧑‍💻 User Story

**As a** Graph Engineering Platform Operator,

**I want** the TUI log pane to continuously tail the active node's output for my selected project and ignore clicks within the SDLC subtask table,

**So that** I can monitor live execution output persistently without the logs wiping or resetting when I inspect different user stories.

## ✅ Acceptance Criteria (BDD Format)

### AC 1: Non-Destructive SDLC Subtask Navigation

* **Given** the dashboard is actively monitoring the `crosstrainingapp` project,
* **And** the `RichLog` pane contains live execution logs for node `devtest`,
* **When** the user clicks or highlights subtask `#455` (or any row) in `#sdlc_widget`,
* **Then** the `RichLog` pane must NOT execute a `.clear()` operation,
* **And** the background live stream must continue appending to the pane seamlessly.

### AC 2: Idempotent Project-Scoped Hydration

* **Given** the user selects a new project row in the top `#projects_table`,
* **When** the selection event fires,
* **Then** the UI must retrieve the scoped log buffer strictly for that project,
* **And** clear the `RichLog` pane and populate it with the retrieved historical buffer.

### AC 3: Cold-Start Disk Fallback

* **Given** a project is selected but the orchestrator daemon was recently restarted (in-memory deque is empty),
* **When** the UI requests the project logs,
* **Then** the log manager must fallback to tailing the last 100 lines from the project's log directory on disk,
* **And** display them in the `RichLog` pane to provide immediate historical context.

### AC 4: Complete Stream Capture

* **Given** an agent node executes an external LLM CLI harness,
* **When** the subprocess emits stdout/stderr streams,
* **Then** the `AsyncHarnessAdapter` must explicitly tag those streams with the `project_name`,
* **And** route them into the corresponding project's memory buffer so they appear in the TUI.

---

## 🔗 GitHub Reference
- **GitHub Issue:** [Issue #90](https://github.com/AntaresAndBharani/graph-engineering/issues/90)
- **Label:** `needs-triage`

## 🔨 Subtasks
- [ ] feat(logging): ProjectLogBufferManager with in-memory deque and cross-platform disk tailing
- [ ] feat(harness): inject project_name into AsyncHarnessAdapter stream listener callbacks
- [ ] feat(ui): project-scoped log hydration and #sdlc_widget event isolation in DashboardApp
- [ ] test(ui, logging): comprehensive BDD integration tests for project-scoped log streaming