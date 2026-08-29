Here is a highly detailed, comprehensive **Epic and Requirements Document** tailored specifically to be fed into an AI-assisted IDE (like Cursor, Windsurf, Copilot, or whichever "Anti-Gravity IDE" you are using). It provides all the necessary context, exact file mappings, Acceptance Criteria, and testing parameters based on your repository dump.

You can paste this directly into your IDE's prompt/context window to begin generating the code safely.

---

# 📋 EPIC: "Anti-Gravity" Architecture — Decoupled Artifact Blackboard

## 📖 Context & Problem Statement

Currently, the `graph-engineering` CLI orchestrator has a flaw where the **Reviewer** node naively evaluates Pull Requests. When an open PR has merge conflicts, GitHub's API returns `mergeable: false`, which the node misinterprets as a completed merge, improperly tagging it for `post-merge-review`.

To fix this without creating a rigid, tightly-coupled state machine, we are implementing an **"Anti-Gravity" Artifact Blackboard**.

* **GitHub Labels** remain the strictly decoupled *Router* (triggering nodes via `orchestrator/poller.py`).
* **SQLite Database** (`orchestrator/db.py`) becomes the *Blackboard* (allowing a node to leave context/artifacts for the next node).

## 🧑‍💻 User Story

**As the** Graph Engineering System Orchestrator,
**I want** the Reviewer node to correctly evaluate PRs and save its review context in a local database blackboard,
**So that** conflicted PRs are no longer falsely tagged as merged, and the DevTest node can intelligently resolve git conflicts without forcing redundant code reviews.

---

## ✅ Acceptance Criteria (BDD Format)

### AC 1: The Artifact Blackboard (Database Layer)

* **Given** the orchestrator initializes the local database,
* **When** `orchestrator/db.py` executes its schema setup,
* **Then** a `pr_artifacts` table must be created to store contextual comments from agents.
* **And** it must provide an idempotent `upsert_pr_artifact` method and a `get_pr_artifact` method.

### AC 2: True Post-Merge Fix (Reviewer Node)

* **Given** a Pull Request is evaluated by the Reviewer node,
* **When** the PR is completely merged (`state == "closed"` AND `merged == True`),
* **Then** the node tags it with `status:post-merge-review` and proceeds with post-merge validation.

### AC 3: Async Deferral (Reviewer Node)

* **Given** a Pull Request is evaluated by the Reviewer node,
* **When** GitHub is still calculating mergeability (`state == "open"` AND `mergeable == None`),
* **Then** the node must defer execution cleanly (e.g., return `NodeResult.DEFERRED`) and **not** guess the state or apply any labels.

### AC 4: Merge Conflict Evaluation & Context (Reviewer Node)

* **Given** an open Pull Request has merge conflicts (`mergeable == False` OR `mergeable_state == "dirty"`),
* **When** the Reviewer node evaluates it, it must still perform the code artifact review.
* **Then** if the code review passes, it must write a row to the DB Blackboard with status `APPROVED_WITH_CONFLICT` and an explanatory comment.
* **And** it must apply the `status:merge-conflict` label on GitHub to route the issue to DevTest.

### AC 5: Context-Aware Conflict Resolution (DevTest Node)

* **Given** the DevTest node picks up a task routed via `status:merge-conflict`,
* **When** it begins processing, it queries the DB Blackboard for the PR number.
* **Then** if the database reports `APPROVED_WITH_CONFLICT`, DevTest **only** resolves the git merge conflict and pushes the fix (skipping logic rewrites).
* **And** upon a successful push, it bypasses further reviews by removing `status:merge-conflict` and applying the `status:ready-to-merge` label.

---

## 🛠️ Technical Implementation Spec (For the AI IDE)

Target the following files in the `graph-engineering` repository structure to fulfill the Acceptance Criteria:

### 1. `orchestrator/db.py`

* **Action:** Add table creation to the SQLite initialization phase:
```sql
CREATE TABLE IF NOT EXISTS pr_artifacts (
    pr_number INTEGER PRIMARY KEY,
    node_name TEXT NOT NULL,
    status TEXT NOT NULL,
    comment TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

```


* **Action:** Implement `upsert_pr_artifact(self, pr_number: int, node_name: str, status: str, comment: str)`. Use `INSERT OR REPLACE INTO` to prevent Primary Key constraint errors.
* **Action:** Implement `get_pr_artifact(self, pr_number: int) -> dict | None` to fetch the dictionary payload.

### 2. `orchestrator/nodes/reviewer.py`

* **Action:** Refactor the main PR state evaluation logic to strictly enforce AC 2, AC 3, and AC 4.
* **Logic Flow:**
1. `if pr.state == "closed" and pr.merged is True:` -> True post-merge.
2. `elif pr.state == "open" and pr.mergeable is None:` -> Return Deferred.
3. `elif pr.state == "open" and (pr.mergeable is False or getattr(pr, 'mergeable_state', '') in ["dirty", "conflict"]):` -> Run the code review. If it passes, invoke `db.upsert_pr_artifact(...)` with status `"APPROVED_WITH_CONFLICT"` and set label `status:merge-conflict`.



### 3. `orchestrator/nodes/devtest.py`

* **Action:** At the top of the workflow handling `status:merge-conflict`, fetch context via `db.get_pr_artifact(pr.number)`.
* **Action:** Add branching logic. If `artifact["status"] == "APPROVED_WITH_CONFLICT"`, execute git conflict resolution, push, and immediately tag GitHub with `status:ready-to-merge` (removing the conflict label).

### 4. `orchestrator/cli.py` & `orchestrator/logging.py`

* **Action:** Expose the Blackboard to engineers via the CLI by adding a command like `show-artifact <pr_number>` in `cli.py` which fetches and prints the database row.
* **Action:** Ensure transparent logging in `logging.py` when nodes read/write to the Blackboard so engineers can see DB routing logic in the terminal.

---

## 🧪 Testing & CI Requirements (`.github/workflows/ci.yml`)

Ensure these unit tests are written to pass the CI pipeline:

* **`tests/test_db.py`:**
* Add a test verifying `upsert_pr_artifact` correctly overwrites an older status for the same `pr_number` without throwing a `sqlite3.IntegrityError`.


* **`tests/test_nodes.py` (Reviewer Tests):**
* Mock a PR with `state="closed"` and `merged=False`. Ensure the Reviewer takes *no* action (prevents false post-merges).
* Mock a PR with `mergeable=False`. Ensure the Reviewer calls `db.upsert_pr_artifact` and applies `status:merge-conflict`.


* **`tests/test_nodes.py` (DevTest Tests):**
* Mock the DB returning `APPROVED_WITH_CONFLICT`. Ensure DevTest applies `status:ready-to-merge` after resolving, verifying it bypasses the secondary review cycle.



## 📚 Documentation Updates

* Update `docs/node-reviewer.md` and `docs/node-devtest.md` to document this new Blackboard state-sharing behavior.