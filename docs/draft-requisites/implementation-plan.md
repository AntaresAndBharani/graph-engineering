### Phase 1: Functional & DX (Developer Experience) Review

#### 1. Workflow Analysis & Token Conservation Journey

The current Architect node polling loop causes massive token consumption by repeatedly executing codebase queries and governance checks on fast cycles (every 5 seconds to 5 minutes).

To eliminate token waste, we introduce a **Single-Story Batch & Project Dormancy Protocol**:

1. **Single-Story Dequeue:** When triggered for a project, the Architect claims strictly **one** story tagged with `status:ready-for-architecture` (or `needs-triage`).


2. **Decomposition & Hand-off:** The Architect analyzes the story, spawns subtask issues with `type:subtask`, links them in the parent issue body, sets Subtask 1 to `status:ready-for-dev`, and tags the parent story as `status:in-progress`.


3. **Immediate Dormancy:** The Architect for that project immediately transitions to `DORMANT` mode. It does **not** evaluate or poll any other story while the current story is in flight.


4. **Re-arming on Story Completion:** Only when DevTest finishes and closes the parent story does the Architect re-arm to evaluate the next story.


5. **Backlog Idle Backoff (20-Minute Cadence):** If a project has zero stories waiting for architecture, the poller backs off to a low-frequency 20-minute check (`idle_poll_interval_min: 20`) instead of continuous scanning.



```
                [Project Poller Cycle]
                          │
         ┌────────────────┴────────────────┐
         ▼                                 ▼
[Active Story in Progress?]       [No Active Story?]
         │                                 │
  [YES: Dormant]                  [Query GitHub API]
  • Architect skips cycle         • Has `ready-for-arch`?
  • 0 Token Consumption                   ├── YES: Claim 1 Story -> Decompose -> Go Dormant
  • DevTest runs subtasks                 └── NO:  Sleep for 20 minutes (0 LLM Tokens)

```

---

#### 2. Project-Scoped Process Control CLI UX

Operators need explicit commands in `orchestrator/cli.py` to control projects independently without killing the entire daemon:

* `graph-orchestrator project start <project_name>`: Unpauses/starts processing for a specific project.
* `graph-orchestrator project stop <project_name>`: Gracefully pauses task dispatching for a specific project without interrupting other running projects.


* `graph-orchestrator project status [project_name]`: Prints the runtime state, active node, lock status, and poll timers.



```text
$ graph-orchestrator project stop crosstrainingapp
[INFO] Pausing project 'crosstrainingapp'...
[SUCCESS] Project 'crosstrainingapp' is now PAUSED. Active node execution will finish cleanly.

$ graph-orchestrator project status
Project Name        State     Active Node   Architect Status   DevTest Status   Next Sweep
crosstrainingapp    PAUSED    Idle          DORMANT            IDLE             --:--:--
graph-engineering   ACTIVE    devtest       DORMANT (Story #90) #93 (Pytest)    in 18m 42s

```

---

#### 3. Edge Cases & Resilience Strategy

* **Cold-Start with Zero Workload:** If a project starts with zero open issues, the Architect must not execute an LLM harness run. It records a timestamp in `orchestrator/db.py` and schedules the next remote scan 20 minutes out.


* **Mid-Story Project Pause (`project stop`):** If an operator stops a project while DevTest is executing, the running harness process completes its current git push/PR cycle, but subsequent subtasks are blocked from starting until `project start` is issued.


* **Stale Dormancy Recovery:** If DevTest crashes or is blocked, the Architect remains dormant to prevent queue corruption. Operators can issue `graph-orchestrator project reset-dormancy <project_name>` or resolve the blocked subtask.
* **Database State Migration:** Concurrency state (`is_paused`, `architect_dormant`) must be stored in `orchestrator/db.py` under SQLite WAL mode to guarantee thread-safe reads and writes between the CLI commands and the background daemon.



---

#### 4. Acceptance Criteria (BDD Format)

```gherkin
Scenario: Architect processes single story and enters dormancy
  Given project "graph-engineering" is active with 0 active stories in progress
  And there are 3 issues with label "status:ready-for-architecture" (#101, #102, #103)
  When the Architect cycle executes for "graph-engineering"
  Then it must claim only the lowest ID story (#101)
  And it must decompose #101 into linked subtasks with Subtask 1 in "status:ready-for-dev"
  And it must mark parent #101 as "status:in-progress"
  And the Architect status for "graph-engineering" must transition to "DORMANT"
  And stories #102 and #103 must remain untouched with 0 tokens consumed.

Scenario: Idle project enforces 20-minute scan cadence
  Given project "crosstrainingapp" has 0 issues in "status:ready-for-architecture"
  When the poller completes its issue check
  Then it must record "last_architect_sweep" in SQLite
  And subsequent poller ticks must skip GitHub API and LLM harness calls until 20 minutes have elapsed.

Scenario: DevTest completion re-arms Architect dormancy
  Given project "graph-engineering" has Architect in state "DORMANT" for Story #101
  When DevTest squash-merges the final subtask and marks Story #101 "status:completed"
  Then the database must reset "architect_dormant = 0"
  And the Architect is re-armed to evaluate the next story on its next sweep.

Scenario: Project CLI controls manage runtime execution state
  Given the orchestrator daemon is running multiple projects
  When the operator executes "graph-orchestrator project stop crosstrainingapp"
  Then SQLite must record "is_paused = 1" for "crosstrainingapp"
  And the worker loop for "crosstrainingapp" must suspend dispatching tasks
  And the worker loop for "graph-engineering" must continue uninterrupted.

```

---

#### 5. CLI UX Guidelines

* **Terminal Logging (`orchestrator/logging.py`):** Standardize messages to reflect the dormancy state and idle timers:


* `[INFO] [crosstrainingapp|architect] Story #101 decomposed. Entering DORMANCY until Story #101 completes.`

* `[DEBUG] [crosstrainingapp|poller] Architect idle. Next sweep scheduled in 19m 50s.`

* `[INFO] [cli] Project 'crosstrainingapp' execution resumed.`



* **TUI Dashboard:** Display the dormancy indicator in `ProjectStatusTable` under the Active Node column: `devtest (Arch: Dormant)`.



---

### Phase 2: Architectural & Implementation Plan

#### 1. Codebase Impact & Component Updates

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/config.py`<br> | **Modify** | Add `architect_idle_poll_interval_min: int = 20` to `ProjectConfig` / `NodeConfig`. |
| `templates/config.example.yaml`<br> | **Modify** | Expose `architect_idle_poll_interval_min: 20` under `nodes.architect` configuration. |
| `orchestrator/db.py`<br> | **Modify** | Add `project_runtime_state` table tracking `is_paused`, `architect_dormant`, and `last_architect_sweep`. Add helper getters/setters. |
| `orchestrator/nodes/architect.py`<br> | **Modify** | Enforce single-story processing (`limit=1`), set dormancy flag upon subtask hand-off, and exit early if dormant. |
| `orchestrator/nodes/devtest.py`<br> | **Modify** | In `_merge_and_close()`, reset `architect_dormant = 0` when the active parent story is closed.

 |
| `orchestrator/poller.py`<br> | **Modify** | Check `is_paused` and 20-minute `last_architect_sweep` timestamp before querying GitHub or triggering Architect.

 |
| `orchestrator/cli.py`<br> | **Modify** | Implement `project` command group with `start`, `stop`, and `status` subcommands. |
| `tests/test_cli.py`<br> | **Modify** | Add tests for `project start/stop/status` subcommands. |
| `tests/test_nodes.py`<br> | **Modify** | Add unit tests asserting Architect single-story execution and dormancy toggling.

 |

---

#### 2. Database Schema: Project Runtime State (`orchestrator/db.py`)



```sql
CREATE TABLE IF NOT EXISTS project_runtime_state (
    project_name TEXT PRIMARY KEY,
    is_paused INTEGER DEFAULT 0,
    architect_dormant INTEGER DEFAULT 0,
    active_story_id INTEGER DEFAULT NULL,
    last_architect_sweep TIMESTAMP DEFAULT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

```

---

#### 3. Step-by-Step Implementation Checklist

* **Step 1: Configuration & Schema (`orchestrator/config.py` & `templates/config.example.yaml`)**

* Add `idle_poll_interval_min: int = 20` to `NodeConfig` for the Architect node.


* Update `templates/config.example.yaml` with the default 20-minute idle cadence.




* **Step 2: State Tracking Engine (`orchestrator/db.py`)**

* Initialize `project_runtime_state` table in `init_db()`.


* Implement async state helpers:
* `async def is_project_paused(project_name: str) -> bool`
* `async def set_project_paused(project_name: str, paused: bool) -> None`
* `async def is_architect_dormant(project_name: str) -> bool`
* `async def set_architect_dormancy(project_name: str, dormant: bool, story_id: Optional[int]) -> None`
* `async def should_run_architect_sweep(project_name: str, interval_min: int) -> bool`
* `async def record_architect_sweep(project_name: str) -> None`




* **Step 3: Architect Single-Story & Dormancy Enforcement (`orchestrator/nodes/architect.py`)**

* In `ArchitectNode.execute()`:
1. Check `await db.is_architect_dormant(project.name)` or if `await db.get_active_story_count(project.name) > 0`. If true, log debug notice and return `NodeResult.SKIPPED` (0 LLM tokens).
2. Claim strictly **one** issue (`limit=1`) matching `status:ready-for-architecture`.


3. Decompose into subtasks, set Subtask 1 to `status:ready-for-dev`, and tag parent story as `status:in-progress`.


4. Call `await db.set_architect_dormancy(project.name, dormant=True, story_id=parent_id)`.


5. Update `last_architect_sweep` timestamp.






* **Step 4: DevTest Story Completion & Re-arming (`orchestrator/nodes/devtest.py`)**

* In `DevTestNode._merge_and_close()` post-merge routine:
1. Close current subtask.


2. Check if all sibling subtasks for the parent story are completed.


3. If parent story is completed, close parent story with `status:completed`.


4. Call `await db.set_architect_dormancy(project.name, dormant=False, story_id=None)` to re-arm the Architect for the next cycle.






* **Step 5: Poller Idle Cadence Gating (`orchestrator/poller.py`)**

* In `poll_project_workload()`:
1. Check `if await db.is_project_paused(project.name): return empty_workload`.


2. Check `if not await db.should_run_architect_sweep(project.name, config.architect.idle_poll_interval_min):` skip Architect issue lookups.






* **Step 6: CLI Subcommands Implementation (`orchestrator/cli.py`)**

* Implement Click/Typer subcommands:
```python
@cli.group()
def project():
    """Manage project-specific orchestration state."""
    pass

@project.command("start")
@click.argument("project_name")
def start_project(project_name: str):
    # set is_paused = 0 in db
    pass

@project.command("stop")
@click.argument("project_name")
def stop_project(project_name: str):
    # set is_paused = 1 in db
    pass

@project.command("status")
@click.argument("project_name", required=False)
def project_status(project_name: Optional[str]):
    # query project_runtime_state and render rich table
    pass

```




* **Step 7: Automated Tests & Verification (`tests/`)**

* `tests/test_cli.py`: Test `graph-orchestrator project start/stop/status` commands.


* `tests/test_nodes.py`: Test Architect runs 1 story $\to$ enters dormancy $\to$ DevTest completes story $\to$ Architect re-arms.


* `tests/test_poller.py`: Test 20-minute idle throttling skips polling checks.


* Run `pytest -v tests/` to confirm 100% test pass rate.





---

# 📋 EPIC: Architect Token Conservation & Granular Project Lifecycle CLI

## 📖 Context & Scope

The Architect node currently consumes excessive LLM tokens by continuously evaluating the codebase and issue queues on fast polling loops.

This EPIC introduces:

1. **Single-Story Batching & Project Dormancy:** The Architect processes only one story at a time, decomposes it into sequential subtasks, and immediately enters dormancy while DevTest develops and closes the story.


2. **20-Minute Idle Polling Cadence:** When no stories require architecture, the background scanner checks for new stories only once every 20 minutes.


3. **Project CLI Controls:** Dedicated CLI commands (`project start`, `project stop`, `project status`) to pause and resume individual projects on demand without stopping the orchestrator daemon.



## 🧑‍💻 User Story

**As a** Graph Engineering Platform Operator,

**I want** the Architect node to process one story at a time, enter dormancy during subtask development, and poll on a 20-minute idle cadence, while providing CLI controls to start and stop individual projects,

**So that** LLM token consumption is minimized and project execution can be controlled granularly.

---

## ✅ Acceptance Criteria (BDD Format)

### AC 1: Single-Story Decomposition & Architect Dormancy

* **Given** an active project with multiple open stories labeled `status:ready-for-architecture`,


* **When** the Architect node executes its scheduled cycle,


* **Then** it must claim only 1 story (lowest ID),


* **And** create linked subtasks and set the parent story to `status:in-progress`,


* **And** transition the project's Architect state to `DORMANT` in SQLite, skipping all subsequent cycles with 0 token consumption until the active story completes.



### AC 2: 20-Minute Idle Backlog Scanning

* **Given** an active project with 0 stories in `status:ready-for-architecture` and no active story in progress,


* **When** the poller evaluates the project workload,


* **Then** it must defer the next Architect evaluation for 20 minutes (`idle_poll_interval_min: 20`),


* **And** make zero LLM harness calls during the backoff window.



### AC 3: Automatic Re-arming on Story Completion

* **Given** a project with an Architect in state `DORMANT` for an active story,


* **When** DevTest squash-merges the final subtask and marks the parent story `status:completed`,


* **Then** the database must clear the dormancy flag (`architect_dormant = 0`),


* **And** the Architect must re-arm to claim the next available story on its next sweep.



### AC 4: Project-Specific CLI Lifecycle Commands

* **Given** the orchestrator daemon is actively running,


* **When** the operator runs `graph-orchestrator project stop <project_name>`,


* **Then** the targeted project state must transition to `PAUSED` in SQLite,


* **And** background task dispatching for that project must halt immediately while allowing other projects to continue executing.


* **When** the operator runs `graph-orchestrator project start <project_name>`,


* **Then** the targeted project state must transition back to `ACTIVE` and resume polling.