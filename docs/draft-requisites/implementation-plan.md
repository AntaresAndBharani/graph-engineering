# 📋 Implementation Plan & Refinement Lifecycle: Architect Label Taxonomy Governance

---

## 📝 Initial Draft Proposal
*The Architect node's workflow is now strictly bounded by a rigid label taxonomy:*
1. **Trigger:** `orchestrator/poller.py` identifies a parent user story in GitHub containing the exact label `stream out`.
2. **Execution:** `orchestrator/nodes/architect.py` processes the story and generates subtasks.
3. **Output:** The Architect assigns the label `queued` to all newly created subtasks and transitions the parent story's label to `architect-processed`.
4. **Constraint Enforcement:** No other labels are permitted to be read or written by the Architect node.

---

## 🔍 Review Iteration 1: Agent Architectural Critical Review & Deadlock Analysis
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Point-by-Point Verdict Matrix:**

| Proposed Item | Verdict | Critical Architectural Analysis & Nuance |
|---|:---:|---|
| **1. Trigger label `stream out` for Architect triage** | ⚠️ **MODIFY (Make Configurable with Default Fallback)** | Hardcoding `stream out` and forbidding other trigger labels breaks backward compatibility for existing repos using `needs-triage` or `status:ready-for-architecture`. Instead, allow `label_trigger: "stream out"` via `NodeConfig` while keeping flexible schema defaults. |
| **2. Output label `queued` for ALL newly created subtasks** | ⚠️ **MODIFY (Autonomous Subtask 1 Activation or Cold-Start Unlocking)** | **CRITICAL DEADLOCK HAZARD:** If the Architect labels ALL subtasks as `queued` on an idle project without setting Subtask 1 to `ready-for-dev`, DevTest will find 0 active tasks and the pipeline will freeze in perpetual idle. To safely support all-queued creation, `StateManager.get_next_devtest_task` or DevTest must automatically activate Subtask 1 of the oldest active story. |
| **3. Parent story transition to `architect-processed`** | ✅ **APPROVE** | Standardizes parent story completion state to `architect-processed` upon successful decomposition. |
| **4. Strict prohibition of all other labels on the Architect node** | ❌ **REJECT (Scoping Violation)** | The Architect node executes 3 distinct governance pillars: (1) Living Architecture Plane sync, (2) Architectural PR Code Review (`needs-architect-review` -> `architect-approved`/`needs-refactor`), and (3) Story Triage. Forbidding all other labels disables Pillar 2 PR reviews. The taxonomy constraints must apply specifically to the **Triage & Decomposition Pillar**. |
| **5. Rejecting `config.yaml` on unauthorized label definitions** | ⚠️ **MODIFY** | `NodeConfig` contains various operational fields (`review_trigger`, `queued_label`, `branch_prefix`, etc.). Strict validation should enforce known taxonomy schema fields without breaking extensible YAML structures. |

---

## 🛡️ Edge Cases & Deadlock Prevention Strategy

1. **Cold-Start Pipeline Activation:**
   * When all subtasks are created as `queued` under an `architect-processed` story:
   * DevTest's deterministic CTE query (`StateManager.get_next_devtest_task`) must resolve Subtask 1 from the oldest `architect-processed` story and automatically promote it from `queued` to `ready-for-dev` upon pickup, ensuring execution never deadlocks.
2. **Multi-Word Label Formatting (`stream out`):**
   * GitHub CLI commands with spaces must always be wrapped in quotes (`--label "stream out"`, `--remove-label "stream out"`) to prevent shell argument fragmentation.
3. **Idempotency on Partial Failure:**
   * If decomposition fails halfway through creating subtasks, the parent story retains `stream out`, but existing subtasks are linked via `sync_parent_subtask_links` on the retry to avoid subtask duplication.

---

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the Architect node's triage pillar to ingest stories labeled `stream out` (or configured trigger), generate all subtasks with the `queued` initial state, and mark the parent story `architect-processed`, with automatic first-subtask activation in DevTest,  
**So that** story decomposition follows a clean, standardized label lifecycle without manual intervention or pipeline deadlocks.

### ⚙️ System Architecture & Data Flow
```
[GitHub Issue: labeled 'stream out']
         │
  ▼ (Poller Ingestion)
[Architect Node: Pillar 3 Triage]
  ├─ 1. Ingest Story with label: "stream out"
  ├─ 2. Generate Subtasks 1..N: all labeled "queued"
  ├─ 3. Update Parent Body Checklist: "- [ ] #<subtask_id>"
  └─ 4. Update Parent Story: remove "stream out", add "architect-processed"
         │
  ▼ (DevTest Autonomous Activation & Execution)
[DevTest Node: Consumer]
  ├─ 1. Query StateManager for active/oldest story lock
  ├─ 2. Auto-promote Subtask 1: "queued" ──▶ "ready-for-dev"
  ├─ 3. Implement in isolated worktree, verify CI, squash-merge
  ├─ 4. Promote next queued subtask ("queued" ──▶ "ready-for-dev")
  └─ 5. On 100% subtask completion: mark parent "dev-implemented" and close
```

### ✅ Formal BDD Acceptance Criteria

#### Scenario 1: Architect Decomposes Story into All-Queued Subtasks
```gherkin
Given a GitHub issue #50 is labeled "stream out"
When the Architect node executes its triage cycle
Then it must create all child subtasks labeled strictly as "queued"
And it must update parent issue #50 by removing "stream out" and adding "architect-processed"
And it must embed the subtasks checklist ("- [ ] #<id>") into the parent body.
```

#### Scenario 2: DevTest Autonomous Activation of Queued Subtasks
```gherkin
Given parent issue #50 is labeled "architect-processed" with open subtasks #51 and #52 labeled "queued"
When DevTest queries the StateManager for the next actionable task
Then it must identify Subtask #51 as the lowest sequential task
And automatically promote Subtask #51 from "queued" to "ready-for-dev"
And begin implementation without stalling the pipeline.
```

#### Scenario 3: Architectural PR Review Pillar Integrity Preserved
```gherkin
Given a Pull Request is submitted with label "needs-architect-review"
When the Architect node executes its PR review cycle (Pillar 2)
Then it must evaluate the PR diff against ".graph/architecture.md"
And apply "architect-approved" or "needs-refactor" without interference from triage label constraints.
```

#### Scenario 4: Backward-Compatible Config Taxonomy Validation
```gherkin
Given a configuration specifying "label_trigger: stream out", "processed_label: architect-processed", and "queued_label: queued"
When the orchestrator validates the configuration on startup
Then it must accept the taxonomy and bind the labels to the Architect node.
```

### 🛠️ Component-by-Component Impact Table

| Component | Target File | Modifications |
|---|---|---|
| **Configuration** | `orchestrator/config.py`, `templates/config.example.yaml` | Support `stream out` as configurable trigger label; maintain robust taxonomy mapping. |
| **Architect Node** | `orchestrator/nodes/architect.py` | Ensure triage decomposition creates all subtasks with `queued` label and updates parent to `architect-processed` while preserving Pillar 2 PR review labels. |
| **DevTest Node** | `orchestrator/nodes/devtest.py` | Ensure autonomous promotion of first queued subtask in `architect-processed` stories. |
| **Database & CTE** | `orchestrator/db.py` | Ensure `StateManager.get_next_devtest_task` recognizes `stream out` and `architect-processed` story states. |
| **Test Suite** | `tests/test_architect_governance.py`, `tests/test_sequential_pipeline.py` | BDD test coverage for all-queued decomposition, autonomous activation, and taxonomy validation. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(architect, config)`)**: Standardize Architect decomposition to generate `queued` subtasks and transition parent to `architect-processed`.
- **Subtask 2 (`feat(devtest)`)**: Autonomous promotion of Subtask 1 from `queued` to `ready-for-dev` on `architect-processed` story pickup.
- **Subtask 3 (`test(architect_governance)`)**: BDD test suite verifying `stream out` ingestion, all-queued creation, and PR review preservation.
