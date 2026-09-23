# 📋 Implementation Plan & Refinement: Resilient TUI Terminal Display Renegotiation & Docking Re-Sync

## 📝 Initial Draft Proposal

### Background & Operational Incident
When an operator connects or disconnects a laptop to/from a docking station or multi-monitor workstation, Windows initiates display renegotiation (display adapter handoff, DPI scaling shift, monitor disconnect/reconnect, console window buffer resize). 
During this transition, the interactive Textual TUI dashboard (`orchestrator watch`) displayed a completely frozen UI:
1. **Clock Frozen:** The clock in the Header stopped ticking.
2. **Table & Output Stalled:** `Last Updated` stopped advancing, active log streams stopped rendering, and the terminal appeared non-responsive.
3. **Subprocess Continued Running Unaffected:** In the background, active worker loops and AI harness subprocesses (`agy --model gemini-3.8-flash-low...`) continued executing without interruption.

### Root Cause Analysis (Ground Truth Audit)
1. **Conhost / Windows Terminal Buffer Size Events:**
   During docking transitions, Windows Terminal emits rapid `WINDOW_BUFFER_SIZE_EVENT` sequences. If dimensions momentarily collapse to 0x0 or exceed bounds during display adapter re-enumeration, or if an unhandled exception occurs during layout calculation or stream dispatch, Textual's event loop halts or screen refresh ceases.
2. **Direct `console.print()` Contention with Application Mode:**
   Background tasks and stream listeners in `_project_worker_loop` and `AsyncHarnessAdapter` invoke `console.print()` or `_console.print()` directly to `sys.stdout`. In Windows Console, writing raw text to stdout while Textual's `WindowsDriver` and `WriterThread` have enabled `ENABLE_VIRTUAL_TERMINAL_PROCESSING` and Alt Screen mode corrupts terminal escape state and risks deadlocking the 30-item bounded `WriterThread._queue`.
3. **Absence of a Watchdog Heartbeat & Recovery Keybinding:**
   The dashboard has no dedicated heartbeat task or display renegotiation handler (`on_resize` / `re-sync`) to force-refresh layout and clear stale console buffer states after a docking event.

---

## 🔍 Review Iteration 1: 3-Amigos Critical Architectural & Resilience Review

- **Date / Author:** 2026-09-14 | Antigravity AI Architect
- **Target Repository:** `AntaresAndBharani/graph-engineering`
- **Architectural Scope:** `orchestrator/ui/dashboard.py`, `orchestrator/cli.py`, `orchestrator/harness.py`, `orchestrator/ui/widgets.py`

### 1. Point-by-Point Verdict Matrix

| # | Proposal Element | Target Component | Verdict | Technical Rationale & Architectural Rule |
|---|---|---|---|---|
| 1 | **TUI App Watchdog Heartbeat & Liveness Recovery** | `orchestrator/ui/dashboard.py` | **APPROVE** | Implement a lightweight background asyncio heartbeat watchdog (every 3s) that monitors UI event loop responsiveness. If a frame render has stalled or display renegotiation occurred, it forces `self.refresh(repaint=True, layout=True)`. |
| 2 | **Explicit Terminal Display Renegotiation (`on_resize`) Handler** | `orchestrator/ui/dashboard.py` | **APPROVE** | Add `@on(events.Resize)` in `DashboardApp`. Sanitizes incoming dimensions, debounces rapid resize storms, forces layout re-calculation, and invalidates/refreshes DataTable and RichLog viewports gracefully. |
| 3 | **Interactive Emergency Redraw Keybinding (`Ctrl+R` / `F5`)** | `orchestrator/ui/dashboard.py` | **APPROVE** | Add dedicated keybindings (`f5` / `ctrl+r`) for "Redraw / Re-sync Display" that clears terminal escape state, resynchronizes console dimensions, and repaints the screen without restarting the daemon. |
| 4 | **Stdout Stream Isolation during TUI Application Mode** | `orchestrator/cli.py`, `orchestrator/harness.py` | **APPROVE** | In `watch` mode, route background worker prints strictly through the Textual log handler or buffer manager rather than raw `console.print()` to `sys.stdout`. Prevents terminal mode collisions and WriterThread queue lockups. |
| 5 | **Clamped Layout Bounds on Zero / Transient Geometry** | `orchestrator/ui/widgets.py`, `orchestrator/ui/dashboard.py` | **APPROVE** | Ensure all responsive widgets (`DataTable`, `ConfigStatusBanner`, `SDLCProgressWidget`, `RichLog`) gracefully handle edge-case terminal dimensions (`width < 20`, `height < 10`, `0x0`) without throwing layout arithmetic exceptions. |

---

## 🎯 Final Decision Plan & User Story Specification

### User Story: Resilient TUI Terminal Display Renegotiation & Docking Re-Sync
**As an** engineering operator using `orchestrator watch` on a multi-monitor workstation or laptop with a docking station,  
**I want** the TUI dashboard to smoothly survive docking, undocking, and display resolution shifts without freezing or becoming unresponsive,  
**So that** I have continuous, uninterrupted real-time observability of autonomous engineering pipelines across all repositories.

### Gherkin BDD Acceptance Criteria

```gherkin
Feature: Resilient TUI Display Renegotiation & Docking Re-Sync

  Scenario: Dashboard smoothly handles display renegotiation upon docking
    Given the Textual dashboard is actively running in application mode
    When the terminal window undergoes rapid resize events or momentary 0x0 geometry during docking
    Then the application does not raise an unhandled exception or crash
    And the layout automatically recalculates and repaints cleanly within 2 seconds
    And the clock and Last Updated timestamps continue advancing normally.

  Scenario: Operator manually triggers display re-sync via keybinding
    Given the dashboard display has experienced visual artifacts or stalled rendering
    When the operator presses "f5" or "ctrl+r"
    Then the dashboard executes an immediate full layout and repaint refresh
    And all tables and log panes are re-synchronized to the current terminal dimensions.

  Scenario: Background worker execution does not corrupt TUI terminal in watch mode
    Given "orchestrator watch" is actively managing running background worker loops
    When worker loops log status or execute child AI harnesses
    Then output is routed through the in-memory buffer and Textual widgets without writing uncoordinated ANSI escapes directly to sys.stdout.
```

### Component Impact Table

| Component / Target | Action | Description |
| :--- | :---: | :--- |
| `orchestrator/ui/dashboard.py` | **MODIFY** | Add `on_resize` handler, watchdog liveness heartbeat, and F5 / Ctrl+R redraw keybinding. |
| `orchestrator/cli.py` | **MODIFY** | Silence raw `console.print` in `_project_worker_loop` when TUI application mode is active. |
| `orchestrator/harness.py` | **MODIFY** | Guard `_console.print` streaming when running in TUI mode to route exclusively via stream listeners. |
| `tests/test_dashboard_resilience.py` | **NEW TEST** | Add unit and integration tests covering rapid resize storms, zero geometry, and F5 redraw action. |

### INVEST Subtask Breakdown
1. **Subtask 1 (TUI Resize & Redraw Architecture):** Add `on_resize` handler, `f5`/`ctrl+r` redraw action, and watchdog liveness heartbeat in `DashboardApp`.
2. **Subtask 2 (Console Stream Safety):** Suppress raw stdout `console.print` from worker loops and harness adapter when TUI is running.
3. **Subtask 3 (Automated Resilience Testing):** Implement `tests/test_dashboard_resilience.py` testing rapid resize storms and redraw recovery.
