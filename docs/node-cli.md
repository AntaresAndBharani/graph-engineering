# CLI & Terminal Dashboard Specification (`docs/node-cli.md`)

**Package**: `orchestrator.cli` / `orchestrator.ui`  
**Command Entry Point**: `orchestrator`  
**Architecture Layer**: Presentation & UI Adapters (Layer 4)

---

## 🏛️ Overview

The `graph-orchestrator` CLI serves as the developer control plane and daemon runner for the multi-agent software engineering graph. It provides single-pass commands, multi-project status inspection, PO-proxy supervision, and a continuous background watch daemon equipped with a modern **Textual Terminal User Interface (TUI)** observability dashboard.

---

## 🖥️ Textual TUI Observability Dashboard

When running `orchestrator watch` in an interactive terminal, the daemon launches a read-only, non-blocking TUI powered by `textual` and `rich`.

```text
┌──────────────────────────────────────── Graph Orchestrator ────────────────────────────────────────┐
│ Config: ~/.orchestrator/config.yaml | Last Reload: 14:52:10 (CLI IPC) | Daemon PID: 18532 [SUCCESS]│
├────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Project Name    Repository                    Active Node   Status   Last Updated   Locks/Anom.  Agent Model     │
│ alpha           AntaresAndBharani/alpha       DevTest       Active   14:52:10       Issue #27    claude-sonnet-5 │
│ crosstraining   AntaresAndBharani/crosstrain  Idle          Active   14:52:10       None         —               │
│ zebra           AntaresAndBharani/zebra       Idle          Paused   14:52:10       None         —               │
├──────────────────────────────────────────────────┬─────────────────────────────────────────────────┤
│ Active SDLC Items (alpha)                        │ [ Logs ]  Quota Limits  Alerts (24h)            │
│ ID    PR Status    Title                   Status│ 14:52:08 [INFO] Daemon started                  │
│ #27   #32 [green]  feat(ui): dashboard     ready │ 14:52:10 [INFO] [alpha] DevTest: Starting TDD   │
└──────────────────────────────────────────────────┴───── Q: Quit  R: Refresh  Space: Auto-Scroll ┘
```

### Key UI Capabilities:
1. **ConfigStatusBanner Widget (Top Pane)**:
   - Positioned directly above `#projects_table` with `height: 3`.
   - Displays the canonical resolved config file path, last reload local timestamp, trigger source (e.g. `CLI IPC` vs `Daemon Startup`), and reload status (`SUCCESS`/`FAILED`).
   - Prevents layout overflow by styling `#projects_table` with `height: 1fr`.
   - Dynamically rehydrated upon reload via asynchronous `DashboardApp._rebind_config`.
2. **Alphabetically Sorted Projects DataTable (Top Pane)**:
   - Displays real-time status across all configured repositories with an explicit 7th column: `[Project Name | Repository | Active Node | Status | Last Updated | Locks/Anomalies | Agent Model]`.
   - Projects are automatically sorted in alphabetical order.
   - Refreshed asynchronously every 2 seconds via `self.set_interval(2.0, ...)` without blocking the UI event loop.
   - Dedicated 7th column **Agent Model** renders pure harness-agnostic specifications via `format_node_agent_spec(model, effort)` (e.g. `claude-sonnet-5 (medium)` or `gemini-3.8-flash-high`), rendering `—` for idle or unassigned project rows.
3. **Multi-Pane Bottom Split (50/50 Horizontal Layout)**:
   - **Bottom-Left (`SDLCProgressWidget`)**: Renders active SDLC items (Issues, Subtasks, PRs) for the selected project directly from SQLite with prioritized column layout `["ID", "PR Status", "Title", "Status/Label"]`:
     - **Column 0 (`ID`)**: Renders `#<issue_number>` with `[LOCKED]` visual badge (e.g. `#27 [LOCKED]`) when a parent story is locked by an active node, or clean `#<issue_number>` for unlocked items. Renders `-` for empty states.
     - **Column 1 (`PR Status`)**: Prioritized ahead of Title to eliminate horizontal scrolling. Renders linked PR numbers and color-coded CI statuses (`#32 [green]PASS[/green]`, `#32 [yellow]RUNNING[/yellow]`, `#32 [red]FAIL[/red]`, `#32 [blue]MERGED[/blue]`, `#32`, or `-`).
     - **Column 2 (`Title`)**: Renders hierarchical tree prefixes (`  ├─ `, `  └─ `) for child subtasks, capped at width 45 with ellipsis (`...`) overflow to preserve visibility of PR badges and lock states. Displays `No active SDLC items` in empty states.
     - **Column 3 (`Status/Label`)**: Renders clean, sanitized comma-separated label strings (`ready-for-dev`, `queued`, `architect-processed`) or item state. Raw Python dictionaries (`{'name': ...}`) are completely eliminated via SQLite sanitization migration.
   - **Bottom-Right (`TabbedContent`)**: Hosts switchable tabs between the filtered daemon log stream (`RichLog`), quota limits (`HarnessQuotaWidget`), and recent 24h anomaly/retry events (`AnomalyAlertsWidget`).
4. **Reactive Project Selection**:
   - Highlighting any project row in the top DataTable (via Up/Down arrow keys or mouse) triggers `@on(DataTable.RowHighlighted)`, immediately and reactively re-querying SQLite and updating both the SDLC progress pane and the 24h anomaly alerts tab.
5. **Project-Scoped Log Hydration & Bounded Stream (`ProjectLogBufferManager` & `RichLog`)**:
   - Captures core root `orchestrator` daemon events via `TextualLogHandler` and live agent subprocess outputs.
   - Backed by `ProjectLogBufferManager` maintaining a global bounded buffer (`GLOBAL_LOG_BUFFER: deque(maxlen=1000)`) and project-scoped buffers (`PROJECT_BUFFERS: dict[str, deque[tuple[Optional[str], str]](maxlen=500)]`) storing `(node_name, line)` tuples to support independent node filtering and prevent memory growth or UI freezes.
   - Automatically drops verbose per-node agent harness traces (which are isolated in per-node log files under `~/.config/orchestrator/logs/`).
   - **Idempotent Project-Scoped Log Hydration & Node Filtering**: When the operator switches project selection in the top table, `DashboardApp` clears the `RichLog` pane and populates it with the selected project's scoped buffer, while live incoming logs from other background projects accumulate safely in their respective buffers without polluting the active view. `get_project_logs` supports node filtering (`node_name="devtest"`) to retrieve logs tagged specifically for that node in original order.
   - **Per-Node Cold-Start Disk Log Fallback**: When in-memory deques are empty upon daemon restart or cold start, `ProjectLogBufferManager.tail_latest_project_logs` uses pure-Python recursive disk tailing (`pathlib.Path.rglob`) to load the last 100 lines from the latest execution log file under `~/.config/orchestrator/logs/<project>/<node>/*.log` (scoped directly to the requested node directory), providing immediate historical context.
   - **Persistent Append-Only Stream**: Historical log entries persist indefinitely across 2.0s table refresh ticks without calling `clear()`.
   - **Interactive Auto-Scroll & Clear Controls**: Pressing `Space` toggles auto-scrolling (`[Auto-Scroll: ON/OFF]`) allowing the operator to freeze scroll position to inspect historical traces, while `Ctrl+L` manually clears the buffer on demand.
6. **Real-Time Active Node Log Streaming & Zero-Refresh Observability**:
   - **Zero-Refresh Streaming Behavior**: When an active AI execution node runs in the background, live log lines emitted by the AI harness stream into the "Logs" tab in real-time without requiring the operator to press `R` or refresh manually.
   - **Automatic Node Scope Inference & Identity Transition**: When background workers acquire SQLite task locks (e.g. `architect` locking Issue #75, or `devtest` locking Issue #76), `update_projects_table` detects active job state changes via `(project_name, active_node)` identity diffing on the 2.0s table refresh loop. The active node and issue ID (`selected_node`, `selected_issue_id`) are updated automatically without cursor movement or manual selection, updating the log pane border title to `Live Output [<project> | <node>*]` and triggering project log hydration.
   - **Disambiguated Placeholders & Clean Retirement**:
     - *Missing Execution Logs*: When a node is selected or actively running but no execution log file exists yet, the log view renders: `No execution logs found yet for node '{node_name}'`.
     - *0-Byte Active Startup File*: When a newly spawned harness initializes and creates a 0-byte log file on disk, the view renders: `⚡ Initializing {node_name} harness on Issue #{issue_id}... Awaiting output.`.
     - *Clean Retirement*: Upon receiving the first live output byte (via harness stream callback or incremental disk polling), the placeholder cleanly retires and is replaced by live execution output.
   - **Seamless Offset Handoff Protocol**: On initial log hydration of an active log file, `DashboardApp` captures `_last_tail_file = target_file` and `_last_tail_offset = file_size`. During 2.0s incremental polling (`_poll_active_log_file`), reads start strictly from byte `_last_tail_offset`, completely preventing duplicate lines or dropped output.
   - **Direct Main-Loop Writing**: Streaming lines are written directly to `log_view.write(rich.markup.escape(line))` when executing on the main asyncio thread, eliminating `call_from_thread` `RuntimeError` thread exceptions while retaining thread-safe dispatch when called from worker threads.
   - **Strict Node Scope Isolation**: Enforces strict isolation across disk and in-memory buffers; querying logs for `architect` will never return `devtest` logs or untagged in-memory lines, nor fall back to unfiltered project files.
   - **Graceful Log Rotation Recovery**: If an actively tailed log file is unlinked or rotated (e.g. via `rotate_logs`), or truncated in-place, `_poll_active_log_file` catches `FileNotFoundError` or size truncation, resets tracked file and offset to `None` and `0`, and cleanly re-discovers the active log file on the subsequent tick.
7. **Native Dual-Level Async Concurrency**:
   - Runs natively inside the existing Python `asyncio` event loop using `app.run_async()`.
   - **Level 1 (Inter-Project Concurrency)**: Per-project worker loops execute concurrently in parallel worker tasks via `asyncio.gather()` with zero cross-thread SQLite collisions.
   - **Level 2 (Intra-Project Concurrency)**: Within each project cycle (`run_project_cycle`), Architect (producer) and DevTest (consumer) execute concurrently via `asyncio.gather(architect_cycle, devtest_cycle)` in isolated Git worktrees (`.graph/worktrees/`) with failure isolation, falling back gracefully to serial execution on `local_path` when worktrees are disabled.
8. **Graceful Teardown & Resource Cleanup**:
   - Pressing `Q` or sending `SIGINT` (`Ctrl+C`) triggers graceful shutdown.
   - Automatically unmounts Textual, cancels worker tasks, terminates all active harness subprocesses via `AsyncHarnessAdapter.terminate_all_active()`, unregisters the daemon PID from SQLite `state.db`, and restores terminal raw mode cleanly.

---

### 🔴 Real-Time Active Node Log Streaming & Observability Specification

The dashboard provides zero-refresh, real-time observability into autonomous AI agents executing across registered repositories.

#### Architectural Safeguards (Consensus Architecture S-1 to S-18)
1. **Typed Query Result (`LogQueryResult`)**: Both `tail_latest_project_logs` and `get_project_logs` in `orchestrator.logging` return a typed `LogQueryResult(lines: List[str], target_file: Optional[Path], file_size: int)` container supporting backwards-compatible tuple and iterable unpacking.
2. **Identity Diffing State Transition**: During 2.0s table refresh ticks (`update_projects_table`), the dashboard compares current `(selected_project, selected_node)` identity against the active job status extracted from SQLite locks. When a node transitions (e.g. `Idle -> architect` on Issue #75, or `architect -> devtest` on Issue #76), the dashboard updates `selected_node`, `selected_issue_id`, and `border_title` dynamically.
3. **Disambiguated Status Placeholders**:
   - `No execution logs found yet for node '{node_name}'`: Displayed when no disk logs or in-memory lines match the requested node scope.
   - `⚡ Initializing {node_name} harness on Issue #{issue_id}... Awaiting output.`: Displayed when a 0-byte log file is detected on disk during harness startup.
   - The placeholder cleanly retires as soon as the first byte is emitted by the harness.
4. **Seamless Offset Handoff Protocol**:
   - `hydrate_project_logs` seeds `_last_tail_file` with the active log file path and `_last_tail_offset` with the current byte length.
   - On each subsequent 2.0s tick, `_poll_active_log_file` checks `target_file.stat().st_size`. If new bytes have arrived, it seeks directly to `_last_tail_offset` and reads only the delta, updating `_last_tail_offset = target_file.stat().st_size`. This completely prevents duplicate lines.
5. **Direct Main-Loop Writing & Escape**:
   - When output is received on the main event loop thread (`threading.get_ident() == self._thread_id`), lines are written directly via `log_view.write(rich.markup.escape(line))`, avoiding `call_from_thread`. When received from background worker threads, `call_from_thread` is invoked safely.
6. **Graceful Log Rotation Recovery**:
   - If an actively tailed file is unlinked or rotated (e.g. by `rotate_logs`), `_poll_active_log_file` catches `FileNotFoundError`, resets `_last_tail_file = None` and `_last_tail_offset = 0`, and re-discovers the active file on the next tick.
   - If a file is truncated in-place (`current_size < _last_tail_offset`), it resets the offset to 0 and re-tails cleanly.

---

## 🛠️ CLI Commands & Options

### `orchestrator watch`
Starts the continuous background polling daemon.

```bash
orchestrator watch [OPTIONS]
```

**Options**:
- `-i, --interval INTEGER`: Polling interval in seconds (overrides `poll_interval_seconds` in `config.yaml`).
- `-c, --config PATH`: Path to custom `config.yaml` file.
- `--dashboard / --no-dashboard`: Enable or disable the interactive Textual TUI dashboard (default: `--dashboard`).
- `--headless`: Run in headless mode (alias for `--no-dashboard`).

**Headless Fallback & CI/CD Auto-Detection**:
- When standard output is not an interactive terminal (`sys.stdout.isatty() is False`), or when `--no-dashboard` / `--headless` is specified, the Textual TUI does not initialize.
- Headless execution runs the standard asynchronous polling loop emitting formatted logs directly to `stdout`.
- The presentation UI module `orchestrator.ui.dashboard` is loaded lazily and is never imported in headless mode.

**Instant Non-Blocking Startup & Background Label Synchronization**:
- Decouples repository workflow label synchronization from daemon startup by executing `sync_all_projects_labels` as an unawaited background task (`asyncio.create_task`).
- Enables the Textual TUI dashboard to launch and become interactive within 1.0 second without blocking on remote GitHub API calls.
- Protects worker cycles via a per-project `asyncio.Event` synchronization barrier (with a 60-second timeout fallback), ensuring repository labels are provisioned before each project worker executes its first cycle.

---

### `orchestrator run`
Executes an immediate single evaluation pass across registered projects. At startup, renders the formatted **Autonomous Node Status Registry** table displaying each node (`architect`, `devtest`, `reviewer`, `supervisor`, `bau`), its repository, `ENABLED`/`DISABLED` status, assigned harness, concurrency mode, and pure harness-agnostic `Agent Model` across configured projects before executing the dispatch pass.

```bash
orchestrator run [-p PROJECT] [-n NODE] [-c CONFIG]
```

---

### `orchestrator start`
Executes a dedicated project node lifecycle until the queue is completely drained. Reuses `_project_worker_loop` with `exit_when_idle=True`, queue drain through pending PR CI checks, distinct scriptable exit codes (`0` drained, `1` stop requested, `2` error or max passes exceeded), global stop validation, and dedicated `lifecycle_pid` tracking.

```bash
orchestrator start <project_name> [-n devtest] [-c CONFIG] [-i INTERVAL] [--max-passes 50]
```

**Options**:
- `project_name` (Argument): Target registered project name.
- `-n, --node TEXT`: Target node lifecycle (`devtest` or omitted for full lifecycle).
- `-c, --config PATH`: Path to custom `config.yaml` file.
- `-i, --interval INTEGER`: Polling interval in seconds between passes.
- `--max-passes INTEGER`: Maximum passes to execute before aborting (default: 50).

**Exit Codes**:
- `0`: Queue completely drained (all actionable tasks implemented and PR CI checks verified/merged).
- `1`: Stop requested via IPC (`orchestrator stop`) or global safe stop signal active.
- `2`: Configuration error, unhandled fatal error, or maximum passes exceeded without draining queue.

**Lifecycle Execution & Queue Drain Architecture**:
- **Reused Worker Loop**: Executes `_project_worker_loop` with `exit_when_idle=True`, immediately following up active work with a 1.0s debounce pause without waiting for the full polling interval.
- **Queue Drain Predicate (`is_project_queue_drained`)**: Protects against premature lifecycle exits by verifying that:
  1. No feature-branch PRs are awaiting CI checks (`RUNNING`, `PENDING`, `PASS`) or auto-merge.
  2. No PRs tagged `needs-refactor` are awaiting remediation.
  3. No actionable tasks remain in SQLite or GitHub queues.
- **Dedicated Lifecycle PID Tracking**: Registers the running process under `lifecycle_pid` in SQLite `daemon_control` without overwriting main daemon PID or control flags. Force stop (`orchestrator stop --force`) terminates active lifecycle runners alongside daemon workers.
- **Global Safe Stop Validation**: Immediately refuses to start if a global stop request is active.

#### Deterministic Lowest-ID Subtask Dispatch Invariant
To prevent out-of-order execution, race conditions, or skipped prerequisites across active development pipelines, `graph-orchestrator` enforces deterministic lowest-ID dispatch across all evaluation paths:
- **Strict Ascending Order (`issue_number ASC`)**: When multiple subtasks are available, DevTest strictly selects the subtask with the **lowest ID (`issue_number ASC`)**, regardless of whether its current label is `ready-for-dev` or `queued`. A lower-numbered `queued` subtask is never skipped in favor of a higher-numbered `ready-for-dev` subtask.
- **Automatic Promotion on Pickup**: When an active parent story's child subtasks are evaluated in `StateManager.get_next_devtest_task`, open child items are ordered strictly by `issue_number ASC`. If the selected candidate is labeled `queued`, DevTest automatically promotes it to `ready-for-dev` via GitHub CLI (`gh issue edit <id> --add-label ready-for-dev --remove-label queued`) upon execution.
- **Fallback 1 Query Window & Skip-and-Continue**: When no active User Story is locked, `get_next_devtest_task` evaluates unlinked standalone tasks using a widened query window (`LIMIT 10`) searching for `(ready-for-dev OR queued)` items. It iterates through the window skipping blocked (`blocked`, `status:blocked`) or in-progress candidates, dispatching the lowest available candidate to prevent pipeline stalls.
- **Safe Sequential Advancement**: When a subtask's PR is merged, `_advance_sequential_subtask` evaluates remaining open child subtasks, strictly excluding closed (`CLOSED`, `MERGED`, `DONE`), implemented (`dev-implemented`), in-progress (`in-progress`), blocked (`blocked`), orchestration-failed (`orchestration-failed`), and the just-merged subtask ID, promoting `min(number)` to unlock the next sequential step.
- **Phase 3 Duplicate PR Prevention**: Before dispatching an LLM coding harness, DevTest inspects `sdlc_items.linked_pr` and queries open PRs for `feat/issue-<id>`. If an open PR already exists, it transitions immediately to awaiting Phase 2 CI verification rather than spawning a duplicate implementation harness.

---

### `orchestrator list`
Displays a formatted Rich table of all registered repositories, assigned harnesses, status, and dedicated 7th column `Agent Model` (`—` for idle or unassigned rows).

```bash
orchestrator list [-c CONFIG]
```

---

### `orchestrator doctor`
Performs system health inspection across dependencies (`git`, `gh`, `claude`, `agy`, `devin`), database connectivity, and repository paths.

```bash
orchestrator doctor
```

---

### `orchestrator init` / `orchestrator labels`
Initializes SQLite database and provisions/synchronizes all managed workflow labels on GitHub.

```bash
orchestrator init [-p PROJECT] [-c CONFIG]
orchestrator labels [-p PROJECT] [-c CONFIG]
orchestrator labels sync [-p PROJECT] [-c CONFIG] [--no-purge]
```

**Smart Single-Pass Label Synchronization & Purge Guard**:
- **Single-Pass Inspection**: Queries `gh label list --json name,color,description --limit 200` in a single API round-trip per repository.
- **Color Normalization**: Normalizes hex colors via `color.lstrip('#').casefold()` to prevent false-positive drift between uppercase and lowercase codes.
- **One-Shot Purge Guard**: Permanently skips obsolete label deletion passes once `legacy_purge_done:{repo}` is recorded in SQLite `daemon_control`.
- **Targeted Creation**: Issues `gh label create --force` only when a managed label is missing or its color/description differs, reporting `True` for verified-already-correct labels with zero redundant calls.
- **Keyword-Only State Parameter**: Declares `*, state_manager: Optional[StateManager] = None` keyword-only to guarantee compilation safety across existing callers.

---

### `orchestrator pause` / `orchestrator resume`
Dynamically pauses or resumes polling and node execution for a specific project.

```bash
orchestrator pause <project_name>
orchestrator resume <project_name>
```

---

### `orchestrator stop`
Signals a safe graceful stop to the running background daemon without killing active jobs ungracefully.

```bash
orchestrator stop
```

---

### `orchestrator config reload` / `orchestrator reload`
Signals the running daemon to dynamically hot-reload configuration and in-memory Python modules without restarting the process.

```bash
orchestrator config reload [-c CONFIG]
orchestrator reload [-c CONFIG]
```

**Centralized Reload Architecture & Synchronous Acknowledgement**:
- **Single-Owner Reload Watcher**: A dedicated 1.0s `_daemon_reload_watcher` task in `orchestrator/cli.py` is the sole consumer of `reload_requested` signals, completely eliminating worker reload race conditions.
- **Shared Mutable `ConfigHolder`**: Reloaded configuration is published atomically to active project worker loops and UI components via `ConfigHolder` (`orchestrator/reloader.py`).
- **Synchronous CLI Acknowledgement**: When an active daemon PID is detected (`psutil.pid_exists`), `orchestrator config reload` polls SQLite `daemon_control` for up to 2.0s until `last_reload_at_epoch > pre_epoch`, displaying confirmed reload timestamp, PID, and active project count.
- **Short-Circuit Signal Queuing**: If no active daemon is running (or the PID is dead), the CLI queues the reload signal immediately without waiting 2.0s.
- **Reactive 4-Holder Rebind**: On reload, the watcher invokes `DashboardApp._rebind_config`, asynchronously updating:
  1. `DashboardApp.config` (`self.config`)
  2. `QuotaManager.config`
  3. `QuotaManager.quota_settings`
  4. `HarnessQuotaWidget` (immediately triggering `update_quotas` to render updated limits in the Quota tab)
  5. `ConfigStatusBanner` (rendering resolved path, local timestamp, and trigger source).

---

### `orchestrator supervisor`
Commands for PO-proxy Supervisor evaluation, inspection, and blackboard tracking.

```bash
orchestrator supervisor evaluate <issue_number> -p <project_name> [--dry-run]
orchestrator supervisor status -p <project_name>
```

---

## 🔁 Transient Upstream Error Retry Engine

The `AsyncHarnessAdapter` integrates an in-memory automatic retry engine with exponential backoff and randomized jitter to handle transient upstream API errors (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED, 502/504 Bad Gateway/Timeout, connection resets).

### Configuration (`HarnessRetryConfig`)
```yaml
harnesses:
  claude:
    binary: "claude"
    args: ["-p", "{prompt}", "--dangerously-skip-permissions"]
    retry:
      max_retries: 3
      initial_delay_seconds: 5.0
      backoff_factor: 2.0
      max_delay_seconds: 60.0
      retryable_patterns:
        - "503"
        - "UNAVAILABLE"
        - "429"
        - "RESOURCE_EXHAUSTED"
        - "502"
        - "504"
        - "rate limit"
        - "quota exceeded"
        - "connection reset"
        - "server disconnected"
        - "fetch failed"
```

### Exponential Backoff with Jitter Formula
$$\text{delay} = \min(\text{max\_delay}, \text{initial\_delay} \times \text{backoff\_factor}^{\text{attempt}}) \times (0.8 + 0.4 \times \text{random}())$$

