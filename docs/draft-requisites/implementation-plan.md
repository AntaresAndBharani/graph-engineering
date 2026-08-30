The current `graph-engineering` workflow does not support strict parent-to-subtask linking or deterministic sequential execution. Currently, the Architect creates loose markdown breakdown comments on the parent issue rather than spawning linked subtask issues, and the Poller treats all issues with `status:ready-for-dev` as independent parallel tasks without preserving ID ordering or parent completion gates.

---

### Gap Analysis: Current vs. Proposed SDLC Lifecycle

| SDLC Phase | Current Implementation | Proposed Target Workflow | Status / Gap |
| --- | --- | --- | --- |
| **Issue Ingestion** | Issue created with `status:ready-for-architecture`.

 | Issue created with `status:ready-for-architecture` and labeled `type:story` or `type:epic`.

 | **Aligned** (Needs type tag) |
| **Architect Breakdown** | Posts an ADR/plan as a comment on the parent issue.

 | Spawns individual GitHub sub-issues (`type:subtask`), embeds parent references (`Parent: #<id>`), and links them in the parent body.

 | **GAP: Missing sub-issue creation & linking** |
| **Execution Gating** | All sub-items immediately get `status:ready-for-dev`.

 | Only **Subtask 1 (lowest ID)** gets `status:ready-for-dev`; Subtasks $2 \dots N$ get `status:queued`. Parent gets `status:in-progress`.

 | **GAP: Missing sequential queue label** |
| **DevTest Execution** | DevTest claims any `status:ready-for-dev` issue in arbitrary order.

 | DevTest claims the lowest active subtask ID, develops, tests, opens PR, verifies CI, and merges.

 | **GAP: Missing ID-sorted selection** |
| **Subtask Advance** | Standalone completion; no downstream awareness.

 | On PR merge, DevTest closes subtask, updates parent checklist, and promotes the next subtask (`status:queued` $\to$ `status:ready-for-dev`). | **GAP: Missing sequential unlock trigger** |
| **Parent Closure & Loop** | Parent issue must be manually closed. | When all child subtasks are `closed`, DevTest marks the Parent Story `status:completed` and closes it, unblocking the Architect to take the next Story.

 | **GAP: Missing parent completion gate** |

---

### Phase 1: Functional & DX (Developer Experience) Review

#### 1. Workflow Analysis & Multi-Agent Node Flow

```
[User / PO] ──────────────────────────┐
  │ Creates Issue #50                 │
  │ (status:ready-for-architecture)   │
  ▼                                   │
[Architect Node]                      ▼
  ├─ 1. Analyzes Issue #50 & Codebase Governance
  ├─ 2. Creates Subtasks:
  │      • Issue #51 (Subtask 1) -> Tag: `status:ready-for-dev`
  │      • Issue #52 (Subtask 2) -> Tag: `status:queued`
  │      • Issue #53 (Subtask 3) -> Tag: `status:queued`
  ├─ 3. Updates Parent #50 body with linked tasklist:
  │      `- [ ] #51`, `- [ ] #52`, `- [ ] #53`
  └─ 4. Updates Parent #50 status -> `status:in-progress`
            │
            ▼
[DevTest Node: Sequential Execution Loop]
  │
  ├─▶ Step A: Claims Subtask #51 (lowest ID with `status:ready-for-dev`)
  │     • Develops & runs local tests
  │     • Pushes branch `feature/issue-51` & opens PR #54 (`Fixes #51`)
  │     • Polls GitHub Actions CI until passing
  │     • Squash-merges PR #54 & closes Issue #51
  │     • Updates Parent #50 body: `- [x] #51`
  │     • Unlocks Subtask #52: `status:queued` -> `status:ready-for-dev`
  │
  ├─▶ Step B: Claims Subtask #52 -> Develops -> PR -> CI -> Merges -> Unlocks #53
  │
  └─▶ Step C: Claims Subtask #53 (Final Subtask) -> Merges -> Closes #53
            │
            ▼
[DevTest: Parent Story Closure]
  ├─ 1. Detects all subtasks for Parent #50 are closed
  ├─ 2. Labels Parent #50 `status:completed` and closes Issue #50
  └─ 3. Emits event: Pipeline idle -> Architect claims next Story (#60)

```

* **Friction Points Identified:**
* **Race Conditions in Polling:** If the Architect creates multiple subtasks simultaneously, the Poller in `orchestrator/poller.py` could dispatch DevTest workers across subtasks in parallel unless subsequent subtasks are explicitly labeled `status:queued`.


* **Dangling / Orphaned Subtasks:** If a parent story is canceled or fails midway, downstream subtasks must not remain open in a zombie `status:queued` state.
* **Sequential Sorting Assurance:** In addition to label transitions, DevTest must explicitly query candidate tasks ordered by `issue_number ASC` to guarantee that lower ID subtasks always take precedence.



#### 2. Label Taxonomy & State Rules

| Label Name | Category | Applied To | Purpose / Trigger |
| --- | --- | --- | --- |
| `type:story` / `type:epic` | Entity Type | Parent Issues | Designates a top-level feature or user story. |
| `type:subtask` | Entity Type | Child Issues | Designates an atomic subtask created by the Architect.

 |
| `status:ready-for-architecture` | Pipeline State | Parent Issues | Signal for Architect node to claim the story.

 |
| `status:in-progress` | Pipeline State | Parent Issues | Set while child subtasks are being actively developed.

 |
| `status:ready-for-dev` | Pipeline State | Active Subtask | Signal for DevTest node to claim and develop this specific issue.

 |
| `status:queued` | Pipeline State | Pending Subtasks | Holding state for dependent subtasks waiting for predecessor completion. |
| `status:completed` | Terminal State | Parent & Subtask | Applied when implementation is verified and merged.

 |
| `status:blocked` | Exception State | Parent & Subtask | Applied if CI fails repeatedly or merge conflicts cannot be resolved. |

#### 3. Edge Cases & Resilience Strategy

* **Subtask Development Failure:** If DevTest encounters unresolvable CI failures or merge conflicts on Subtask $N$, it marks Subtask $N$ as `status:blocked`, marks the Parent Story as `status:blocked`, and halts the sequence without unlocking Subtask $N+1$.
* **Parent Issue Body Mutation Drift:** When updating the parent checklist (`- [x] #51`), DevTest reads the latest issue body directly from GitHub to prevent overwriting manual human comments or concurrent updates.
* **Database State Isolation:** All subtask-to-parent relationships must be cached in `sdlc_items` within `orchestrator/db.py` under SQLite WAL mode to support instant TUI rendering without hitting GitHub API limits.


* **Single Active Story Constraint:** DevTest only advances the current active story's subtasks until completion before the Architect is allowed to pick up the next `status:ready-for-architecture` issue.



#### 4. Acceptance Criteria (BDD Format)

```gherkin
Scenario: Architect breaks down parent story into linked subtasks
  Given an open issue #50 with label "status:ready-for-architecture" and "type:story"
  When the Architect node executes the decomposition routine
  Then it must create subtask issues (#51, #52, #53) labeled with "type:subtask"
  And subtask #51 (lowest ID) must receive label "status:ready-for-dev"
  And subtasks #52 and #53 must receive label "status:queued"
  And the body of parent #50 must be updated with markdown tasklist references to #51, #52, and #53
  And parent #50 must be transitioned to label "status:in-progress".

Scenario: DevTest completes subtask and unlocks the next sequential subtask
  Given active subtask #51 with label "status:ready-for-dev" linked to parent #50
  And pending subtask #52 with label "status:queued" linked to parent #50
  When DevTest opens PR #54, verifies passing CI, and squash-merges PR #54
  Then subtask #51 must be closed with label "status:completed"
  And parent #50 tasklist must mark "- [x] #51"
  And subtask #52 must have label "status:queued" removed and "status:ready-for-dev" applied.

Scenario: DevTest detects final subtask completion and closes parent story
  Given subtask #53 is the final open subtask for parent #50
  When DevTest successfully develops, verifies CI, and merges the PR for #53
  Then subtask #53 must be closed
  And DevTest must verify that 100% of subtasks for #50 are closed
  And parent #50 must receive label "status:completed" and be closed
  And the poller must unblock the Architect node to claim the next ready story.

```

#### 5. CLI UX Guidelines

Structured logging via `orchestrator/logging.py` must track the parent-child sequential progression:

* `[INFO] [node:architect] Story #50 decomposed into 3 subtasks: #51 (Active), #52 (Queued), #53 (Queued).`
* `[INFO] [node:devtest] Subtask #51 merged (PR #54). Unlocking next sequential subtask: #52.`
* `[INFO] [node:devtest] Final subtask #53 completed. Parent Story #50 completed and closed.`
* `[INFO] [poller] Story #50 finished. Routing next story (#60) to Architect.`

---

### Phase 2: Architectural & Implementation Plan

#### 1. Codebase Impact & Component Updates

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/config.py`<br> | **Modify** | Add `labels` taxonomy schema (`story_label`, `subtask_label`, `queued_label`) to configuration models.

 |
| `templates/config.example.yaml`<br> | **Modify** | Define default label mappings and sequential execution settings.

 |
| `orchestrator/db.py`<br> | **Modify** | Extend `sdlc_items` table to store `parent_issue_id`, `sequence_order`, and `subtask_status`.

 |
| `orchestrator/nodes/architect.py`<br> | **Modify** | Implement `create_subtasks_and_link()`, setting the first child to `ready-for-dev` and remaining to `queued`.

 |
| `orchestrator/nodes/devtest.py`<br> | **Modify** | Add `unlock_next_subtask()` and `evaluate_parent_completion()` methods post-merge.

 |
| `orchestrator/poller.py`<br> | **Modify** | Ensure DevTest only polls `status:ready-for-dev` ordered by `issue_number ASC`, and Architect pauses while an active Story is `in-progress`.

 |
| `tests/test_nodes.py`<br> | **Modify** | Add unit tests covering subtask spawning, parent linking, sequential unlocking, and parent closure.

 |

---

#### 2. Database Schema: Parent-Child SDLC Tracking (`orchestrator/db.py`)



```sql
CREATE TABLE IF NOT EXISTS sdlc_items (
    project_name TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    parent_issue_id INTEGER,
    item_type TEXT CHECK(item_type IN ('STORY', 'SUBTASK')) NOT NULL,
    sequence_order INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    pipeline_status TEXT NOT NULL,
    pr_number INTEGER,
    pr_status TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_name, issue_number)
);

CREATE INDEX IF NOT EXISTS idx_sdlc_parent ON sdlc_items(project_name, parent_issue_id);

```

---

### Step-by-Step Implementation Checklist

* **Step 1: Configuration & Label Taxonomy (`orchestrator/config.py` & `templates/config.example.yaml`)**

* Add configuration keys under `orchestrator.labels`:
```yaml
labels:
  story: "type:story"
  subtask: "type:subtask"
  ready_for_arch: "status:ready-for-architecture"
  in_progress: "status:in-progress"
  ready_for_dev: "status:ready-for-dev"
  queued: "status:queued"
  completed: "status:completed"
  blocked: "status:blocked"

```




* **Step 2: SQLite Schema Migration (`orchestrator/db.py`)**

* Add `parent_issue_id` and `sequence_order` columns to `sdlc_items`.


* Add helper methods:
* `get_active_story(project_name) -> int | None`
* `get_pending_subtasks(parent_id) -> list[dict]`
* `get_next_queued_subtask(parent_id) -> dict | None`




* **Step 3: Architect Subtask Creation & Linking (`orchestrator/nodes/architect.py`)**

* Refactor Architect execution logic:
1. Parse decomposition JSON from LLM harness into structured subtasks: `list[SubtaskSpec]`.
2. For each subtask, invoke GitHub API (`gh.create_issue`) with title, body (referencing `Parent: #<parent_id>`), and label `type:subtask`.
3. Label the **first subtask (index 0)** with `status:ready-for-dev`.
4. Label **all remaining subtasks (index $1 \dots N$)** with `status:queued`.
5. Update parent issue body with a Markdown checklist containing references to all created subtask numbers.
6. Transition parent issue from `status:ready-for-architecture` to `status:in-progress`.
7. Populate `sdlc_items` in `orchestrator/db.py` with the complete parent-child hierarchy.






* **Step 4: DevTest Sequential Execution & Parent Completion (`orchestrator/nodes/devtest.py`)**

* In DevTest post-merge routine:
1. Close the current subtask issue with label `status:completed`.
2. Read parent issue body, replace `- [ ] #<subtask_id>` with `- [x] #<subtask_id>`, and update parent body on GitHub.
3. Query GitHub/SQLite for remaining subtasks belonging to the same parent.
4. **If remaining `status:queued` subtasks exist:**
* Find the subtask with the lowest issue number.
* Remove `status:queued` and apply `status:ready-for-dev`.


5. **If all subtasks for the parent are closed:**
* Remove `status:in-progress` from the parent issue.
* Apply `status:completed` and close the parent issue.
* Emit pipeline idle signal so Architect claims the next story.






* **Step 5: Poller Scheduling Logic (`orchestrator/poller.py`)**

* Update `poll_devtest_tasks()` to sort candidate issues by `issue.number ASC` to enforce deterministic execution ordering.


* Update `poll_architect_tasks()` to ensure Architect does not claim a new story if an existing story has active `in-progress` subtasks.




* **Step 6: Testing & CI Verification (`tests/test_nodes.py`)**

* Write `test_architect_creates_and_links_subtasks`: Mock LLM response; assert parent body receives checklist, child 1 gets `ready-for-dev`, child 2 gets `queued`.
* Write `test_devtest_advances_sequential_subtask`: Mock PR merge on child 1; assert child 2 transitions from `queued` to `ready-for-dev`.
* Write `test_devtest_closes_parent_on_final_subtask`: Mock PR merge on final child; assert parent is closed with `status:completed`.
* Run `pytest -v tests/` and verify all tests pass.





---

# 📋 EPIC: Deterministic Sequential SDLC Pipeline (Architect Breakdown $\to$ DevTest Chain $\to$ Parent Closure)

## 📖 Context & Scope

We are refactoring the end-to-end execution lifecycle in `graph-engineering` to ensure that User Stories are decomposed into explicitly linked subtasks and executed strictly in sequence.

* **Architect Node:** Takes a parent story (`type:story`), generates atomic subtask issues (`type:subtask`), links them in the parent body, and activates only the first subtask (`status:ready-for-dev`) while holding others in `status:queued`.


* **DevTest Node:** Executes subtasks one at a time (lowest ID first), develops, tests, opens PR, verifies CI, and merges. Upon merging, it unlocks the next subtask.


* **Parent Completion:** When the final subtask merges, DevTest marks the parent story `status:completed` and closes it, unblocking the Architect to claim the next story.



## 🧑‍💻 User Story

**As the** Graph Engineering System Operator,
**I want** the Architect node to decompose stories into linked, queued subtasks and the DevTest node to execute them sequentially before closing the parent story,
**So that** complex user stories are delivered in a strictly ordered, autonomous, and verifiable progression with zero concurrency race conditions.

---

## ✅ Acceptance Criteria (BDD Format)

### AC 1: Subtask Decomposition & Parent Checklist Linking

* **Given** an open issue with label `status:ready-for-architecture` and `type:story`,
* **When** the Architect node processes the story,


* **Then** it creates individual GitHub issues for each subtask with label `type:subtask`.
* **And** the lowest ID subtask receives label `status:ready-for-dev`.


* **And** all subsequent subtasks receive label `status:queued`.
* **And** the parent issue body is updated with Markdown tasklist links to each subtask number.
* **And** the parent issue label transitions to `status:in-progress`.



### AC 2: Deterministic Sequential Subtask Promotion

* **Given** DevTest completes the implementation, CI verification, and PR squash-merge for subtask $N$,
* **When** the post-merge handler executes,


* **Then** subtask $N$ is closed with label `status:completed`.
* **And** the parent issue checklist is updated to check off `- [x] #<N>`.
* **And** the next queued subtask $N+1$ (lowest ID with `status:queued`) is promoted to `status:ready-for-dev`.

### AC 3: Autonomous Parent Story Closure

* **Given** the final subtask for a parent story is merged and closed,
* **When** DevTest evaluates parent story completion,


* **Then** it verifies that 100% of linked subtasks are closed.
* **And** it removes `status:in-progress`, applies `status:completed`, and closes the parent issue.

### AC 4: Pipeline Story Scheduling Gate

* **Given** an existing parent story has subtasks in progress or queued,
* **When** the Poller runs its cycle,


* **Then** the Architect node is prohibited from claiming new `status:ready-for-architecture` stories until the active story is closed.

---

## 🛠️ Technical Implementation Spec (For the Antigravity IDE)

Target the following files in `graph-engineering`:

### 1. `orchestrator/config.py` & `templates/config.example.yaml`

* Add label mappings:
```python
class LabelConfig(BaseModel):
    story: str = "type:story"
    subtask: str = "type:subtask"
    ready_for_arch: str = "status:ready-for-architecture"
    in_progress: str = "status:in-progress"
    ready_for_dev: str = "status:ready-for-dev"
    queued: str = "status:queued"
    completed: str = "status:completed"
    blocked: str = "status:blocked"

```



### 2. `orchestrator/db.py`

* Update `sdlc_items` table with `parent_issue_id` and `sequence_order` columns.


* Add `get_next_queued_subtask(parent_id: int)` and `get_active_story(project_name: str)`.

### 3. `orchestrator/nodes/architect.py`

* In `execute()`, parse subtask specifications, create subtask GitHub issues, label the first with `status:ready-for-dev` and the rest with `status:queued`, update the parent body checklist, and apply `status:in-progress` to the parent.



### 4. `orchestrator/nodes/devtest.py`

* In `_merge_and_close()`, update parent body checklist `- [x] #<id>`, find the next subtask with `status:queued`, and promote it to `status:ready-for-dev`. If no subtasks remain, label parent `status:completed` and close it.



### 5. `orchestrator/poller.py`

* Enforce `issue_number ASC` sorting when querying `status:ready-for-dev` tasks.


* Block Architect dispatch if `db.get_active_story()` indicates an unfinished story in progress.

### 6. `tests/test_nodes.py`

* Add unit tests asserting subtask creation, parent body tasklist formatting, sequential subtask unlocking, and parent closure. Ensure 100% test pass rate across `pytest -v tests/`.