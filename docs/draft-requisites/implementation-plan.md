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

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the Architect node to throttle execution based on dynamic story locks and a configurable lookahead ceiling (`lookahead_backoff_seconds`), while executing in isolated Git worktrees,  
**So that** pipeline queues execute in deterministic FIFO order with zero Git index collisions and zero wasted LLM tokens during active development.

### ⚙️ System Architecture & Data Flow
```
[CLI: orchestrator run / watch]
         │
  ┌──────┴──────┐
  ▼             ▼
[Project A]   [Project B]  (Concurrent non-blocking asyncio tasks)
  │
  ├─▶ [Architect Coroutine] (Producer)
  │     ├─ 1. Check `project_states.is_paused` ──▶ If True: SKIP (0 Tokens)
  │     ├─ 2. Check Lookahead Cap: `count_planned_stories >= max_planned_stories`
  │     │       └─ If Cap Reached: SKIP (0 Tokens, log [DEBUG] "Lookahead limit reached")
  │     ├─ 3. Check Idle Backoff: `now - last_sweep < lookahead_backoff_seconds` (when backlog empty)
  │     ├─ 4. Decompose Story in `.graph/worktrees/architect_<proj>` (`orchestrator/worktree.py`)
  │     ├─ 5. Set Subtask 1 ──▶ `ready-for-dev`, Subtasks 2..N ──▶ `queued`
  │     └─ 6. Update Parent Story Checklist (`- [ ] #<id>`)
  │
  └─▶ [DevTest Coroutine] (Consumer)
        ├─ 1. Check `project_states.is_paused` ──▶ If True: SKIP (0 Tokens)
        ├─ 2. Resolve Active Story Lock (`StateManager.get_next_devtest_task`)
        ├─ 3. Pre-execution guard: If `state in (CLOSED, MERGED)` ──▶ Auto-clean & SKIP (0 Tokens)
        ├─ 4. Implement in `.graph/worktrees/devtest_<proj>` (`orchestrator/worktree.py`)
        ├─ 5. Open PR, monitor CI checks, squash-merge, and clean worktree
        ├─ 6. Check off parent checklist (`- [x] #<id>`)
        ├─ 7. Promote lowest open queued sibling to `ready-for-dev`
        └─ 8. If 100% subtasks closed ──▶ Auto-close Parent Story & Unblock Lookahead
```

### ✅ Formal BDD Acceptance Criteria

#### Scenario 1: Dynamic Story Lock & Architect Lookahead Throttling
```gherkin
Given "max_planned_stories" is set to 1
And SQLite contains active Story #90 currently in progress with child subtasks
When the Architect node executes its polling cycle
Then it must query "count_planned_stories" from StateManager
And immediately return SKIPPED with 0 LLM tokens consumed
And emit a debug log: "[DEBUG] [project|architect] Lookahead capacity reached (1/1 stories). Dormancy active."
```

#### Scenario 2: Configurable Lookahead Idle Backoff
```gherkin
Given "lookahead_backoff_seconds" is set to 1200 (20 minutes)
And an active project has 0 issues requiring triage and 0 stories in progress
When the Architect completes an idle backlog sweep
Then it must record the sweep timestamp
And all subsequent Architect checks within the 1200s window must skip polling with 0 LLM tokens consumed.
```

#### Scenario 3: Standardized Worktree Provisioning and Cleanup
```gherkin
Given the orchestrator executes Architect and DevTest concurrently
When provisioning workspace directories
Then it must exclusively call "orchestrator/worktree.py:prepare_worktree"
And upon task completion or PR merge, it must call "worktree.clean_worktree"
And zero index lock collisions shall occur between nodes.
```

#### Scenario 4: Autonomous Lookahead Unlocking on Story Completion
```gherkin
Given the Architect is dormant due to locked Story #90
When DevTest merges the final subtask and auto-closes Story #90
Then "count_planned_stories" drops below "max_planned_stories"
And on the next polling cycle, the Architect automatically unblocks to triage the next backlog issue.
```

### 🛠️ Component-by-Component Impact Table

| Component | Target File | Modifications |
|---|---|---|
| **Configuration** | `orchestrator/config.py`, `templates/config.example.yaml` | Add `lookahead_backoff_seconds: int = 1200` to `NodeConfig` with backward-compatible defaults. |
| **Database & Locking** | `orchestrator/db.py` | Expose `get_active_story_lock` (aliasing `get_active_locked_story_id`), preserve CTE `ActiveStory` query in `get_next_devtest_task`. |
| **Architect Node** | `orchestrator/nodes/architect.py` | Check `count_planned_stories >= max_planned_stories` and `lookahead_backoff_seconds` timestamp gate; execute strictly in `prepare_worktree`. |
| **DevTest Node** | `orchestrator/nodes/devtest.py` | Execute strictly in `prepare_worktree`, sanitize on merge, auto-promote queued siblings, and close completed stories. |
| **Worktree Layer** | `orchestrator/worktree.py` | Ensure robust detached worktree mounting, sanitization, and startup pruning. |
| **Test Suite** | `tests/test_sequential_pipeline.py`, `tests/test_worktrees.py` | Full unit and BDD integration test coverage across all 4 scenarios. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(config, architect)`)**: `lookahead_backoff_seconds` config field and Architect lookahead capacity gating (`count_planned_stories >= max_planned_stories`).
- **Subtask 2 (`feat(worktree, devtest)`)**: Worktree lifecycle encapsulation in `orchestrator/worktree.py`, post-merge sanitization, and sequential subtask promotion.
- **Subtask 3 (`test(sequential_pipeline)`)**: BDD integration test suite for lookahead gating, idle backoff timing, and worktree concurrency isolation.
