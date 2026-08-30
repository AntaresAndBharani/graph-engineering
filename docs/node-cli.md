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
│ Project Name    Repository                    Active Node   Status   Last Updated   Locks/Anomalies│
│ alpha           AntaresAndBharani/alpha       DevTest       Active   14:52:10       Issue #27      │
│ crosstraining   AntaresAndBharani/crosstrain  Idle          Active   14:52:10       None           │
│ zebra           AntaresAndBharani/zebra       Idle          Paused   14:52:10       None           │
├──────────────────────────────────────────────────┬─────────────────────────────────────────────────┤
│ Active SDLC Items (alpha)                        │ [ Logs ]  Alerts (24h)                          │
│ ID    Title                   Status   PR        │ 14:52:08 [INFO] Daemon started                  │
│ #27   feat(ui): dashboard     dev      #32       │ 14:52:10 [INFO] [alpha] DevTest: Starting TDD   │
└──────────────────────────────────────────────────┴───── Q: Quit  R: Refresh  Space: Toggle Auto-Scroll  Ctrl+L: Clear Logs ┘
```

### Key UI Capabilities:
1. **Alphabetically Sorted Projects DataTable (Top Pane)**:
   - Displays real-time status across all configured repositories: `[Project Name | Repository | Active Node | Status | Last Updated | Locks/Anomalies]`.
   - Projects are automatically sorted in alphabetical order.
   - Refreshed asynchronously every 2 seconds via `self.set_interval(2.0, ...)` without blocking the UI event loop.
2. **Multi-Pane Bottom Split (50/50 Horizontal Layout)**:
   - **Bottom-Left (`SDLCProgressWidget`)**: Renders active SDLC items (Issues, Subtasks, PRs) for the selected project directly from SQLite.
   - **Bottom-Right (`TabbedContent`)**: Hosts switchable tabs between the filtered daemon log stream (`RichLog`), quota limits (`HarnessQuotaWidget`), and recent 24h anomaly/retry events (`AnomalyAlertsWidget`).
3. **Reactive Project Selection**:
   - Highlighting any project row in the top DataTable (via Up/Down arrow keys or mouse) triggers `@on(DataTable.RowHighlighted)`, immediately and reactively re-querying SQLite and updating both the SDLC progress pane and the 24h anomaly alerts tab.
4. **Filtered & Append-Only Bounded Log Stream (`RichLog`)**:
   - Captures core root `orchestrator` daemon events via `TextualLogHandler` and live agent subprocess outputs.
   - Backed by a bounded `collections.deque(maxlen=1000)` buffer to prevent memory growth or UI freezes.
   - Automatically drops verbose per-node agent harness traces (which are isolated in per-node log files under `~/.config/orchestrator/logs/`).
   - **Persistent Append-Only Stream**: Historical log entries persist indefinitely across 2.0s table refresh ticks without calling `clear()`.
   - **Interactive Auto-Scroll & Clear Controls**: Pressing `Space` toggles auto-scrolling (`[Auto-Scroll: ON/OFF]`) allowing the operator to freeze scroll position to inspect historical traces, while `Ctrl+L` manually clears the buffer on demand.
5. **Native Dual-Level Async Concurrency**:
   - Runs natively inside the existing Python `asyncio` event loop using `app.run_async()`.
   - **Level 1 (Inter-Project Concurrency)**: Per-project worker loops execute concurrently in parallel worker tasks via `asyncio.gather()` with zero cross-thread SQLite collisions.
   - **Level 2 (Intra-Project Concurrency)**: Within each project cycle (`run_project_cycle`), Architect (producer) and DevTest (consumer) execute concurrently via `asyncio.gather(architect_cycle, devtest_cycle)` in isolated Git worktrees (`.graph/worktrees/`) with failure isolation, falling back gracefully to serial execution on `local_path` when worktrees are disabled.
6. **Graceful Teardown & Resource Cleanup**:
   - Pressing `Q` or sending `SIGINT` (`Ctrl+C`) triggers graceful shutdown.
   - Automatically unmounts Textual, cancels worker tasks, terminates all active harness subprocesses via `AsyncHarnessAdapter.terminate_all_active()`, unregisters the daemon PID from SQLite `state.db`, and restores terminal raw mode cleanly.

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

---

### `orchestrator run`
Executes an immediate single evaluation pass across registered projects.

```bash
orchestrator run [-p PROJECT] [-n NODE] [-c CONFIG]
```

---

### `orchestrator list`
Displays a formatted Rich table of all registered repositories, assigned harnesses, and status.

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
```

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

### `orchestrator reload`
Signals the running daemon to dynamically hot-reload configuration and in-memory Python modules without restarting the process.

```bash
orchestrator reload
```

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

