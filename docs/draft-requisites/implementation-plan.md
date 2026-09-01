# 📋 Implementation Plan & Refinement Lifecycle: DevTest GitHub Label Governance

---

## 📝 Initial Draft Proposal
*This architectural review addresses the transition to a strictly configuration-driven, dynamically triggered `devtest` node:*
1. **Dynamic Ingestion:** `orchestrator/poller.py` parses configured GitHub label strings (`label_trigger`, `queued_label`, `label_output`) and queries GitHub for the configured labels.
2. **Stateless Execution:** `orchestrator/nodes/devtest.py` dynamically evaluates issue payloads and performs state transitions using `node_cfg` properties rather than hardcoded string literals.
3. **Constraint Enforcement:** Ensure all GitHub workflow label transitions are driven strictly by the project's `config.yaml` while keeping node operational properties (`harness`, `model`, `effort`, `branch_prefix`, etc.) standard and intact.

---

## 🔍 Review Iteration 1: Initial Architectural Review
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Verdict Matrix:**

| Proposed Item | Verdict | Critical Architectural Analysis & Nuance |
|---|:---:|---|
| **1. Dynamic `devtest` GitHub label configuration from `config.yaml`** | ✅ **APPROVE** | Aligns `devtest` with the dynamic label architecture established for `architect` (`label_trigger`, `queued_label`, `label_output`). |
| **2. Restricting `NodeConfig` schema via `extra="forbid"`** | ❌ **REJECT** | Unnecessary constraint that risks interfering with core node operational properties (`harness`, `model`, `effort`, `branch_prefix`, `auto_merge_approved`, `enabled`). |
| **3. Parameterizing all hardcoded string literals in `devtest.py`** | ✅ **APPROVE** | Replaces static `"ready-for-dev"`, `"queued"`, and `"dev-implemented"` literals throughout `devtest.py` with `node_cfg` properties. |
| **4. Parameterizing Poller workload queries in `poller.py`** | ✅ **APPROVE** | Ensures `orchestrator/poller.py` dynamically queries the configured `label_trigger` when fetching workloads for `devtest`. |

---

## 💬 Review Iteration 2: Operator Clarification on GitHub Labels vs Node Properties
- **Date / Author:** 2026-09-01 | Operator
- **Operator Directives & Architectural Clarification:**
  - When configuring labels, we are strictly configuring **GitHub workflow labels** (`label_trigger`, `label_output`, `queued_label`).
  - Node operational properties (`harness`, `model`, `effort`, `branch_prefix`, `auto_merge_approved`, `enabled`) are essential engine properties that remain permanent on `NodeConfig`.
  - We do not apply artificial `extra='forbid'` constraints on node configuration models. Instead, we simply declare and respect the allowed GitHub workflow labels in `config.yaml` and consume them dynamically across the node lifecycle.

---

## 🔍 Review Iteration 3: Agent Convergence & Clean GitHub Label Mapping
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Technical Blueprint & Synthesis:**
  1. **Clean Label Properties in `NodeConfig`:**
     - `label_trigger: Optional[str] = "ready-for-dev"` (Active trigger)
     - `label_output: Optional[str] = "dev-implemented"` (Completion label)
     - `queued_label: Optional[str] = "queued"` (Queued subtask label)
  2. **End-to-End DevTest Parameterization (`orchestrator/nodes/devtest.py`):**
     - Phase 1: Remediate PRs with `needs-refactor`.
     - Phase 2: Autonomous E2E merge for Green PRs with `node_cfg.label_output`.
     - Phase 3: Issue pickup and activation from `node_cfg.queued_label` ──▶ `node_cfg.label_trigger`.
     - Sequential Advance: Unlock next queued subtask (`node_cfg.queued_label` ──▶ `node_cfg.label_trigger`) upon PR squash-merge.
  3. **Dynamic Workload Polling (`orchestrator/poller.py`):**
     - `fetch_project_workload` dynamically uses `devtest_cfg.label_trigger` for issue queries.
  4. **Multi-Environment Config Synchronization:**
     - Synchronize `templates/config.example.yaml`, `%USERPROFILE%/.orchestrator/config.yaml`, and `%USERPROFILE%/.config/orchestrator/config.yaml`.

---

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the `devtest` node and poller to dynamically ingest and evaluate its active trigger (`label_trigger`), queued trigger (`queued_label`), and completion label (`label_output`) directly from `config.yaml`,  
**So that** repository GitHub workflow labels can be configured per project without hardcoded string literals or engine drift.

### ⚙️ System Architecture & Data Flow
```
[config.yaml: devtest.label_trigger / queued_label / label_output]
         │
         ├──▶ [orchestrator/poller.py] ──▶ Dynamic GitHub API queries for DevTest workload
         │
         └──▶ [orchestrator/nodes/devtest.py]
                ├─ Phase 1: Remediate 'needs-refactor' PRs
                ├─ Phase 2: Autonomous E2E merge for Green PRs with `node_cfg.label_output`
                ├─ Phase 3: Dispatch & activate subtasks using `node_cfg.label_trigger` / `queued_label`
                └─ Sequential Advance: Unlock next subtask (`queued_label` ──▶ `label_trigger`)
```

### ✅ Formal BDD Acceptance Criteria

#### Scenario 1: Config-Driven DevTest Workload Polling
```gherkin
Given a project configured with devtest "label_trigger: ready-for-dev" and "queued_label: queued"
When orchestrator/poller.py executes fetch_project_workload
Then it must query GitHub issues matching the configured label_trigger
And synchronize them into SQLite SDLC memory.
```

#### Scenario 2: Dynamic Subtask Unlocking on Sequential Progression
```gherkin
Given a project configured with custom labels "label_trigger: in-development" and "queued_label: backlog-queued"
And parent story #70 has completed child subtask #71
When DevTest advances the parent story and unlocks the next queued subtask #72
Then it must remove "backlog-queued" and add "in-development" to subtask #72 via GitHub CLI
And update the SQLite SDLC state to match the configured labels.
```

#### Scenario 3: Dual-Label Conflict Resolution
```gherkin
Given an issue is labeled with both "ready-for-dev" and "queued"
When DevTest evaluates the issue in Phase 3
Then it must treat the issue as active (ready-for-dev)
And remove the stale "queued" label via GitHub CLI.
```

#### Scenario 4: Backward-Compatible Default Taxonomy
```gherkin
Given a minimal config.yaml that does not explicitly declare devtest label keys
When the orchestrator loads the configuration
Then devtest must default to "label_trigger: ready-for-dev", "queued_label: queued", and "label_output: dev-implemented"
And execute without raising validation errors.
```

### 🛠️ Component-by-Component Impact Table

| Component | Target File | Modifications |
|---|---|---|
| **Configuration** | `orchestrator/config.py`, `templates/config.example.yaml` | Ensure `NodeConfig` defaults (`label_trigger`, `queued_label`, `label_output`) are documented and unified. |
| **Poller Engine** | `orchestrator/poller.py` | Parameterize `fetch_project_workload` to use project-specific `devtest` label configurations. |
| **DevTest Node** | `orchestrator/nodes/devtest.py` | Replace all static literal label strings in Phase 1, Phase 2, Phase 3, and subtask unlocking with `node_cfg` properties. |
| **Live Configs** | `~/.orchestrator/config.yaml`, `~/.config/orchestrator/config.yaml` | Synchronize live configuration files with documented label schemas. |
| **Test Suite** | `tests/test_nodes.py`, `tests/test_sequential_pipeline.py` | Add BDD test coverage for custom DevTest label triggers and dynamic subtask progression. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(devtest, config)`)**: Parameterize all DevTest node label references (`label_trigger`, `queued_label`, `label_output`) to dynamically consume `NodeConfig`.
- **Subtask 2 (`feat(poller)`)**: Parameterize DevTest poller workload queries to dynamically ingest configured labels.
- **Subtask 3 (`test(devtest_labels)`)**: BDD test suite verifying custom DevTest label triggers, subtask activation, and dual-label conflict resolution.
