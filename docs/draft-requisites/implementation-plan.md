# 📋 Implementation Plan & Refinement Lifecycle: Architect Label Taxonomy Governance

---

## 📝 Initial Draft Proposal
*The Architect node's workflow is strictly bounded by a rigid label taxonomy:*
1. **Trigger:** `orchestrator/poller.py` identifies a parent user story in GitHub containing the configured trigger label (e.g. `stream out` or `needs-triage`).
2. **Execution:** `orchestrator/nodes/architect.py` processes the story and generates subtasks.
3. **Output:** The Architect assigns the label `queued` to all newly created subtasks and transitions the parent story's label to `architect-processed`.
4. **Constraint Enforcement:** No other labels are permitted to be read or written by the Architect node.

---

## 🔍 Review Iteration 1: Agent Architectural Critical Review & Deadlock Analysis
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Verdict Matrix:**

| Proposed Item | Verdict | Critical Architectural Analysis & Nuance |
|---|:---:|---|
| **1. Trigger label `stream out` for Architect triage** | ⚠️ **MODIFY** | Sourced directly from `config.yaml` (`label_trigger`) with default fallback to `needs-triage`. |
| **2. Output label `queued` for ALL newly created subtasks** | ⚠️ **MODIFY** | To prevent pipeline deadlocks when all subtasks are initialized to `queued`, DevTest autonomously promotes Subtask 1 of the active story from `queued` to `ready-for-dev` upon pickup. |
| **3. Parent story transition to `architect-processed`** | ✅ **APPROVE** | Standardizes parent story completion state to `architect-processed` (or configured `processed_label`) upon decomposition. |
| **4. Strict prohibition of all other labels on Architect node** | ⚠️ **MODIFY** | Scoped strictly to the 1-pass triage & decomposition lifecycle. |
| **5. Strict configuration binding from `config.yaml`** | ✅ **APPROVE** | All labels (`label_trigger`, `processed_label`, `queued_label`) must be read dynamically from `config.yaml` with clean Pydantic defaults. |

---

## 💬 Review Iteration 2: Operator Clarification on Streamlined Architect Lifecycle
- **Date / Author:** 2026-09-01 | Operator
- **Operator Directives & Architectural Clarification:**
  - In the streamlined 2-node parallel topology, the Architect's sole purpose is **1-pass Triage & Decomposition** (`needs-triage` / `stream out` $	o$ `architect-processed` / `architect done`).
  - There is no circular PR review loop for the Architect node; DevTest handles implementation, CI verification, and auto-merge directly.
  - All label definitions must be driven dynamically by the project's `config.yaml` (`label_trigger`, `processed_label`, `queued_label`).
  - Once triaged and labeled `queued` (with parent labeled `architect-processed`), the Architect's job on that issue is 100% complete with no additional label mutations.

---

## 🔍 Review Iteration 3: Agent Convergence & Direct 1-Pass Architecture Blueprint
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Technical Validation & Synthesis:**
  1. **Config-Driven Taxonomy:**
     - `NodeConfig.label_trigger`: Sourced from `config.yaml` (e.g., `"needs-triage"` or `"stream out"`).
     - `NodeConfig.processed_label`: Sourced from `config.yaml` (e.g., `"architect-processed"`).
     - `NodeConfig.queued_label`: Sourced from `config.yaml` (e.g., `"queued"`).
  2. **1-Pass Decomposition Guarantee:**
     - Architect prompt generates all subtasks with `queued_label`, embeds the markdown checklist into the parent body, removes `label_trigger`, adds `processed_label`, and immediately exits.
  3. **DevTest Autonomous First-Subtask Unlocking:**
     - DevTest's deterministic CTE query (`get_next_devtest_task`) identifies the lowest open subtask in the oldest `architect-processed` story and activates it (`queued` $	o$ `ready-for-dev`) on execution, ensuring continuous, deadlock-free flow.

---

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the Architect node to execute a strict, 1-pass triage lifecycle that reads its trigger label from `config.yaml`, creates all subtasks with the `queued` label, and transitions the parent story to `architect-processed`, with automatic subtask activation in DevTest,  
**So that** story decomposition is clean, deterministic, and fully configurable without redundant review loops or pipeline stalls.

### ⚙️ System Architecture & Data Flow
```
[GitHub Issue: labeled with configured `label_trigger` (e.g. 'stream out' / 'needs-triage')]
         │
  ▼ (Poller Ingestion)
[Architect Node: 1-Pass Triage & Decomposition]
  ├─ 1. Ingest Story with label: `label_trigger`
  ├─ 2. Generate Subtasks 1..N: all labeled `queued_label` (default 'queued')
  ├─ 3. Embed Parent Checklist: "- [ ] #<subtask_id>"
  └─ 4. Update Parent Story: remove `label_trigger`, add `processed_label` (default 'architect-processed')
         │
  ▼ (DevTest Autonomous Activation & E2E Merge)
[DevTest Node: Consumer]
  ├─ 1. Resolve Active/Oldest Story Lock via StateManager
  ├─ 2. Auto-promote Subtask 1: `queued` ──▶ `ready-for-dev`
  ├─ 3. Implement in isolated worktree, verify CI 100% Green, squash-merge
  ├─ 4. Unlock next sequential subtask (`queued` ──▶ `ready-for-dev`)
  └─ 5. On 100% subtask completion: mark parent "dev-implemented" and close
```

### ✅ Formal BDD Acceptance Criteria

#### Scenario 1: Config-Driven 1-Pass Story Decomposition
```gherkin
Given a project configured with "label_trigger: stream out", "processed_label: architect-processed", and "queued_label: queued"
And a GitHub parent issue #60 is labeled "stream out"
When the Architect node executes its triage cycle
Then it must create all child subtasks labeled strictly as "queued"
And it must remove "stream out" and add "architect-processed" to issue #60
And it must embed the subtasks checklist into the body of issue #60.
```

#### Scenario 2: Autonomous DevTest Activation of Queued Subtasks
```gherkin
Given parent issue #60 is labeled "architect-processed" with open subtasks #61 and #62 labeled "queued"
When DevTest resolves the project workload
Then it must select Subtask #61 as the lowest sequential task
And automatically promote Subtask #61 from "queued" to "ready-for-dev"
And proceed with implementation without waiting for external triggers.
```

#### Scenario 3: Strict Config Loading from `config.yaml`
```gherkin
Given a user defines custom label mappings in "~/.config/orchestrator/config.yaml"
When the orchestrator loads the configuration
Then the Architect node must dynamically bind "label_trigger", "processed_label", and "queued_label" from the configuration
And execute triage operations exclusively using the configured labels.
```

#### Scenario 4: Zero Token Idle When No Issues Match Trigger Label
```gherkin
Given no issues in the repository have the configured "label_trigger"
When the Architect node executes its triage cycle
Then it must record the idle sweep timestamp in SQLite
And exit immediately with 0 LLM tokens consumed.
```

### 🛠️ Component-by-Component Impact Table

| Component | Target File | Modifications |
|---|---|---|
| **Configuration** | `orchestrator/config.py`, `templates/config.example.yaml` | Ensure `label_trigger`, `processed_label`, and `queued_label` in `NodeConfig` load dynamically with clean defaults. |
| **Architect Node** | `orchestrator/nodes/architect.py` | Update `build_triage_prompt` and `_triage_story` to create all subtasks with `queued_label` and parent with `processed_label` from config. |
| **DevTest Node** | `orchestrator/nodes/devtest.py` | Ensure autonomous pickup and activation of Subtask 1 from `queued` to `ready-for-dev` on `architect-processed` stories. |
| **Test Suite** | `tests/test_architect_governance.py`, `tests/test_sequential_pipeline.py` | Comprehensive BDD integration test coverage verifying config-driven 1-pass triage and DevTest activation. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(architect, config)`)**: Config-driven 1-pass triage decomposition producing all-`queued` subtasks and `architect-processed` parent.
- **Subtask 2 (`feat(devtest)`)**: Autonomous promotion of Subtask 1 from `queued` to `ready-for-dev` upon DevTest story pickup.
- **Subtask 3 (`test(architect_governance)`)**: BDD test suite verifying config loading, all-queued creation, and zero-token idle sweeps.
