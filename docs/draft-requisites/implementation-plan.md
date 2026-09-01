# 📋 Implementation Plan & Refinement Lifecycle: Deterministic User-Triggered Config Reload

---

## 📝 Initial Draft Proposal
*This architectural review addresses the transition from automated file-watching hot-reload to a deterministic, user-triggered configuration reload workflow:*
1. **Remove Automatic File Watching:** Remove `SourceWatcher` automatic filesystem mtime polling from the background worker loop to eliminate race conditions during partial file edits.
2. **Deterministic IPC Reload:** The operator executes `graph-orchestrator config reload` (or `orchestrator reload`), setting an IPC reload flag in the SQLite state database (`daemon_control`).
3. **Safe In-Memory Swap:** The daemon's worker loop checks the reload flag, parses and validates the updated `config.yaml`, atomically swaps the active `GlobalConfig` reference, and clears the flag.
4. **Resilient Error Containment:** If the updated configuration contains syntax errors or invalid schemas, the daemon logs a descriptive error and continues executing with the previous valid configuration without crashing.

---

## 🔍 Review Iteration 1: Agent Architectural Assessment & IPC Harmonization
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Point-by-Point Verdict Matrix:**

| Proposed Item | Verdict | Critical Architectural Analysis & Nuance |
|---|:---:|---|
| **1. Eradicate automatic filesystem mtime polling (`SourceWatcher`)** | ✅ **APPROVE** | Background file-watching on every loop cycle causes non-deterministic behavior and crashes during in-flight file edits. Removing `SourceWatcher` ensures daemon state changes only on explicit operator command. |
| **2. Use SQLite as the cross-process IPC reload broker** | ✅ **APPROVE (Leverage Existing `daemon_control`)** | The codebase already has `daemon_control` table with `request_reload()`, `is_reload_requested()`, and `clear_reload_request()` in `orchestrator/db.py`. No redundant `daemon_state` table needed. |
| **3. CLI command interface: `orchestrator config reload`** | ✅ **APPROVE** | Introduce a `config` sub-command group with `reload` (`orchestrator config reload`), while maintaining top-level `orchestrator reload` for backward compatibility. |
| **4. Resilient error containment on invalid YAML / schema** | ✅ **APPROVE** | If `load_config()` fails with `ValidationError` or YAML syntax error during hot-reload, catch the exception, log `[ERROR] [daemon] Config reload failed... Retaining previous state.`, and keep running with the active `GlobalConfig`. |
| **5. Atomic config swap in worker loops** | ✅ **APPROVE** | Reassign `config` reference and update matching `project` instances atomically before the next cycle pass. |

---

## 🛡️ Edge Cases & Resilience Strategy

1. **Crash-Proof Daemon Resilience:**
   * Wrap reload evaluation in a guarded `try/except Exception` block. If invalid YAML or unparseable fields are encountered, log the exact error and retain the previously validated `GlobalConfig` without crashing the daemon or worker coroutines.
2. **Offline CLI Invocations:**
   * If the operator runs `orchestrator config reload` while the daemon is offline, the CLI records the flag in SQLite and reports success. When the daemon subsequently starts, it parses the configuration freshly and clears any stale flags during startup initialization.
3. **Multi-Project Concurrency Safety:**
   * When multiple project worker loops are running, each worker checks the flag; once reloaded, the flag is cleared idempotently, and all workers safely update their respective `ProjectConfig` instances.
4. **Zero Residual File-Watch Overhead:**
   * Eradicate `SourceWatcher` class and obsolete tests, removing file system polling overhead and disk I/O on every tick.

---

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the orchestrator daemon to reload configuration strictly upon executing `orchestrator config reload` (or `orchestrator reload`) instead of automatic filesystem watching,  
**So that** configuration updates are deterministic, safe from partial file writes, and resilient against syntax errors without daemon restarts.

### ⚙️ System Architecture & IPC Flow
```
[Operator: config.yaml edited]
         │
         ▼
[CLI: orchestrator config reload]
         │
         ├──▶ [SQLite: daemon_control SET reload_requested = '1']
         │
         ▼
[Daemon Worker Loop (Next Tick)]
         │
         ├──▶ Check: is_reload_requested() == True
         ├──▶ Try: load_config(config_path)
         │       ├─ Success ──▶ Atomically swap config & project instances
         │       └─ Failure ──▶ Log error & retain previous valid configuration
         │
         └──▶ StateManager.clear_reload_request() (SET reload_requested = '0')
```

### ✅ Formal BDD Acceptance Criteria

#### Scenario 1: Deterministic Reload on Explicit Operator Command
```gherkin
Given a running orchestrator daemon with active configuration
When the operator modifies config.yaml and saves without running a CLI command
Then the daemon must NOT reload and must continue executing with original settings
When the operator executes "orchestrator config reload"
Then the daemon must detect the reload signal on its next cycle tick
And atomically swap to the updated configuration.
```

#### Scenario 2: Resilient Error Recovery on Invalid Config Syntax
```gherkin
Given a running orchestrator daemon with valid active configuration
When the operator saves invalid YAML or schema errors in config.yaml
And executes "orchestrator config reload"
Then the daemon must log a descriptive validation error
And continue running with the previous valid configuration without terminating.
```

#### Scenario 3: Backward-Compatible CLI Command Aliases
```gherkin
Given the orchestrator CLI interface
When the operator executes either "orchestrator config reload" or "orchestrator reload"
Then both commands must register the reload request in SQLite
And output confirmation to the terminal.
```

### 🛠️ Component-by-Component Impact Table

| Component | Target File | Modifications |
|---|---|---|
| **CLI & Commands** | `orchestrator/cli.py` | Add `config_app` Typer sub-command with `reload` command; retain top-level `reload` alias; remove `SourceWatcher` from `_project_worker_loop` and daemon startup logs. |
| **Reloader Engine** | `orchestrator/reloader.py` | Remove `SourceWatcher` class; preserve `hot_reload_runtime(config_path)` function for safe module and config reloading. |
| **State Database** | `orchestrator/db.py` | Leverage existing `request_reload()`, `is_reload_requested()`, and `clear_reload_request()` on `daemon_control`. |
| **Test Suite** | `tests/test_reloader.py`, `tests/test_cli.py` | Update tests to verify deterministic CLI reload and resilient validation error containment. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(cli, reloader)`)**: Remove `SourceWatcher` file-watching from `_project_worker_loop` and `orchestrator/reloader.py`; add `config reload` CLI sub-command.
- **Subtask 2 (`feat(resilience)`)**: Implement guarded config swap in `_project_worker_loop` retaining previous `GlobalConfig` on validation error.
- **Subtask 3 (`test(reload)`)**: Update unit and BDD tests verifying manual reload, invalid config error recovery, and CLI command execution.
