# 📋 Implementation Plan & Refinement Lifecycle: Deterministic Dual-Level Orchestration

---

## 📝 Initial Draft Proposal
*Deterministic orchestration guarantees that every decision that can be resolved via local state, Git metadata, or database logic consumes 0 LLM tokens, reserving AI harness execution strictly for creative code synthesis and architectural decomposition.*

### Deterministic vs. LLM Execution Boundary Matrix (Initial Proposal)

| Stage / Decision Gate | Execution Type | Mechanism / File | Token Cost |
| --- | --- | --- | --- |
| **Workload Polling & Ingestion** | **100% Deterministic** | `orchestrator/poller.py` via `gh` CLI | **0 Tokens** |
| **Anti-Flap Content Hashing** | **100% Deterministic** | SHA-256 (`title + body`) in `orchestrator/db.py` | **0 Tokens** |
| **Story Lock & Subtask Sequencing** | **100% Deterministic** | SQLite CTE query (`ORDER BY sequence_order ASC`) | **0 Tokens** |
| **Architect Dormancy & Throttling** | **100% Deterministic** | SQLite `project_runtime_state` (20m idle backoff) | **0 Tokens** |
| **Quota Pre-Flight Runway Gate** | **100% Deterministic** | `token_usage_events` rolling window in `orchestrator/db.py` | **0 Tokens** |
| **PR Status & CI Verification** | **100% Deterministic** | `statusCheckRollup` bulk query in `orchestrator/poller.py` | **0 Tokens** |
| **Subtask Promotion & Story Close** | **100% Deterministic** | Label mutation & checklist regex in `devtest.py` | **0 Tokens** |
| **Architectural Decomposition** | **LLM-Driven** | Claude / Gemini CLI harness in `orchestrator/harness.py` | Variable |
| **Code Implementation & Local Tests** | **LLM-Driven** | Gemini / Claude CLI harness in `orchestrator/harness.py` | Variable |

---

## 🔍 Review Iteration 1: Agent Architectural Critical Review
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Verdict Matrix:**

| Proposed Item | Verdict | Critical Analysis |
|---|:---:|---|
| Create `orchestrator/git_utils.py` | ❌ **REJECT** | `orchestrator/worktree.py` already exists and implements `WorktreeManager`, `prepare_worktree`, and `prune_worktrees`. Creating `git_utils.py` causes duplicate logic. |
| New `project_runtime_state` table with static `architect_dormant` | ❌ **REJECT** | Storing static boolean flags in a separate table introduces split-brain risk and duplicates `project_states`. Dormancy should be dynamically resolved via SQLite CTE from `sdlc_items`. |
| Hard-coded 20-min sleep (`idle_poll_interval_min: 20`) | ⚠️ **MODIFY** | Hardcoded synchronous sleeps block event loops and break configurable polling. Replace with `lookahead_backoff_seconds: int = 1200` in `NodeConfig` evaluated asynchronously against sweep timestamps. |
| Duplicate CLI commands (`project start/stop/status`) | ⚠️ **MODIFY** | Reuse existing CLI architecture (`orchestrator pause <proj>`, `orchestrator resume <proj>`, `orchestrator list`). |

---

## 💬 Review Iteration 2: Operator Feedback & Multi-Agent Flow Refinement
- **Date / Author:** 2026-09-01 | Operator
- **Operator Notes & Directives:**
  - Emphasized token-preserving dormancy and deterministic story locking.
  - Requested decoupling Architect dormancy from static flags: dynamic inference from `sdlc_items` active locks.
  - Mandated that worktrees strictly use `orchestrator/worktree.py` with detached HEAD (`--detach origin/main`) to eliminate Git lock collisions.
  - Specified structured terminal logging with `[DEBUG]` for recurring idle skips and `[INFO]` for state transitions.

---

## 🔍 Review Iteration 3: Agent Multi-Window & Lookahead Convergence Review
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Verdict & Convergence Analysis:**
  1. **Lookahead Gating Invariant:** Replaced binary single-story lock with `count_planned_stories >= max_planned_stories`. Supports both 1-story strict lock (`max_planned_stories = 1`) and lookahead pipelining (`max_planned_stories = 2`).
  2. **WAL Concurrency Safety:** Confirmed `orchestrator/db.py` uses WAL mode (`PRAGMA journal_mode=WAL;`), rendering `PRAGMA read_uncommitted` unnecessary and avoiding dirty reads.
  3. **Method Reuse:** Reused `StateManager.get_active_locked_story_id(project_name)` (aliased to `get_active_story_lock`) instead of creating redundant SQL queries.

---

## 💬 Review Iteration 4: Operator Feedback & Resilient Lifecycle Refinements
- **Date / Author:** 2026-09-01 | Operator
- **Operator Directives & Critical Enhancements:**
  - **Early Pause Evaluation:** Move `is_paused` check to the top of `run_project_cycle` before node dispatch.
  - **Restart-Resilient Idle Backoff:** Persist `last_idle_sweep_at` in SQLite (`project_states` table) so the 20-minute throttle survives daemon restarts.
  - **Terminal States & Blocked Subtask Quarantine:** Ensure subtask blockers prevent premature promotion of subsequent siblings and keep parent in-progress without deadlocking lookahead.
  - **Non-Destructive Worktree Stashing:** Before cleaning worktrees with uncommitted changes, execute `git stash push -u` to safeguard untracked code artifacts from accidental deletion.

---

## 🔍 Review Iteration 5: Agent Architectural Verification & Production Blueprint
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Technical Validation & Ground Truth Synthesis:**
  1. **Database Schema Evolution:** Add non-destructive column migration in `orchestrator/db.py`:
     `ALTER TABLE project_states ADD COLUMN last_idle_sweep_at REAL DEFAULT NULL;`
  2. **Worktree Stash Protection:** Update `orchestrator/worktree.py:clean_worktree()` to check `git status --porcelain` and stash untracked files (`git stash push -u -m "Orchestrator pre-clean recovery"`) before resetting.
  3. **Early Exit in CLI Dispatch:** Confirmed `run_project_cycle()` already evaluates `is_project_paused()` on line 181 before polling or node coroutines.
  4. **Blocked Quarantine Invariant:** Confirmed `StateManager.get_next_devtest_task()` and `reconcile_completed_stories()` strictly hold the story lock and quarantine blocked subtasks.

---

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the Architect node to throttle execution based on dynamic story locks, persistent restart-resilient idle backoffs (`last_idle_sweep_at`), and isolated worktrees with stash protection,  
**So that** multi-story pipelines execute in strict deterministic FIFO order with zero Git lock collisions, zero accidental code loss, and minimal LLM token consumption.

### ⚙️ System Architecture & Data Flow
```
[CLI: orchestrator run / watch]
         │
  ├─▶ Check `project_states.is_paused` ──▶ If True: Early Exit (0 Tokens)
  │
  ├─▶ [Architect Coroutine] (Producer)
  │     ├─ 1. Check Lookahead Cap: `count_planned_stories >= max_planned_stories`
  │     │       └─ If Cap Reached: SKIP (0 Tokens, log [DEBUG] "Lookahead limit reached")
  │     ├─ 2. Check Persistent Idle Backoff: `now - project_states.last_idle_sweep_at < lookahead_backoff_seconds`
  │     │       └─ If in Backoff: SKIP (0 Tokens, log [DEBUG] "Idle backoff active")
  │     ├─ 3. Decompose Story in `.graph/worktrees/architect_<proj>` (`orchestrator/worktree.py`)
  │     ├─ 4. Set Subtask 1 ──▶ `ready-for-dev`, Subtasks 2..N ──▶ `queued`
  │     ├─ 5. Update Parent Story Checklist (`- [ ] #<id>`)
  │     └─ 6. If backlog empty: Update `project_states.last_idle_sweep_at = now()`
  │
  └─▶ [DevTest Coroutine] (Consumer)
        ├─ 1. Resolve Active Story Lock via SQLite CTE (`StateManager.get_next_devtest_task`)
        ├─ 2. Pre-execution guard: If `state in (CLOSED, MERGED)` ──▶ Auto-clean & SKIP (0 Tokens)
        ├─ 3. Implement in `.graph/worktrees/devtest_<proj>` (`orchestrator/worktree.py`)
        ├─ 4. Open PR, monitor CI checks, squash-merge
        ├─ 5. Worktree Hygiene: Stash untracked artifacts before clean reset (`clean_worktree`)
        ├─ 6. Check off parent checklist (`- [x] #<id>`)
        ├─ 7. Promote lowest open queued sibling to `ready-for-dev` (quarantine if blocked)
        └─ 8. If 100% subtasks closed ──▶ Auto-close Parent Story & Unblock Lookahead
```

### ✅ Formal BDD Acceptance Criteria

#### Scenario 1: Persistent Idle Backoff Survives Daemon Restarts
```gherkin
Given "lookahead_backoff_seconds" is set to 1200 (20 minutes)
And the Architect completes an idle sweep and records the timestamp into SQLite "project_states.last_idle_sweep_at"
When the orchestrator daemon restarts 5 minutes later
Then the Architect must read "last_idle_sweep_at" from SQLite
And skip polling with 0 LLM tokens consumed for the remaining 15 minutes of the window
And emit a debug log: "[DEBUG] [project|architect] Idle backoff active. Next sweep in 15m 00s."
```

#### Scenario 2: Subtask Blockage Quarantines Story and Halts Sibling Promotion
```gherkin
Given active Story #90 has Subtask #91 (blocked/failed) and Subtask #92 (queued)
When DevTest processes the project workload
Then it must NOT promote Subtask #92 to "ready-for-dev"
And Story #90 must remain locked in progress
And no other user stories shall be dispatched (0 LLM tokens consumed).
```

#### Scenario 3: Non-Destructive Worktree Hygiene with Stash Protection
```gherkin
Given DevTest executes an implementation task in its isolated worktree
And the worktree contains uncommitted/untracked files upon task completion
When "clean_worktree" is invoked during post-merge teardown
Then it must execute "git stash push -u" to preserve untracked files
Before resetting the worktree to clean HEAD state.
```

#### Scenario 4: Autonomous Lookahead Unlocking on Story Completion
```gherkin
Given the Architect is dormant due to locked Story #90 at lookahead capacity (1/1)
When DevTest merges the final subtask and auto-closes Story #90
Then "count_planned_stories" drops from 1 to 0
And on the next polling cycle, the Architect automatically unblocks to triage the next backlog issue.
```

### 🛠️ Component-by-Component Impact Table

| Component | Target File | Modifications |
|---|---|---|
| **Configuration** | `orchestrator/config.py`, `templates/config.example.yaml` | Add `lookahead_backoff_seconds: int = 1200` to `NodeConfig` with backward-compatible defaults. |
| **Database & Schema** | `orchestrator/db.py` | Add `last_idle_sweep_at REAL` column to `project_states`. Implement `update_idle_sweep_timestamp` and `get_last_idle_sweep_timestamp`. |
| **Worktree Layer** | `orchestrator/worktree.py` | Update `clean_worktree()` to check `git status --porcelain` and perform `git stash push -u` before reset. |
| **Architect Node** | `orchestrator/nodes/architect.py` | Query `last_idle_sweep_at` from SQLite; update timestamp upon empty sweep; enforce `count_planned_stories >= max_planned_stories`. |
| **DevTest Node** | `orchestrator/nodes/devtest.py` | Enforce blocked-subtask quarantine, worktree stash protection on cleanup, and 100% completion story closure. |
| **Test Suite** | `tests/test_sequential_pipeline.py`, `tests/test_worktrees.py` | Comprehensive BDD integration test coverage across all 4 scenarios. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(config, db)`)**: `last_idle_sweep_at` persistence in `project_states` and `lookahead_backoff_seconds` config schema.
- **Subtask 2 (`feat(worktree)`)**: Worktree stash protection and safe sanitization in `orchestrator/worktree.py`.
- **Subtask 3 (`feat(architect, devtest)`)**: Persistent idle backoff gate in `architect.py` and blocked quarantine logic in `devtest.py`.
- **Subtask 4 (`test(sequential_pipeline, worktree)`)**: BDD integration test suite verifying restart survival, stash protection, and lookahead unlock.
