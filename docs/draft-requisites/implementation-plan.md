# 📋 Implementation Plan & Refinement Lifecycle: DevTest Dynamic Label Taxonomy Governance

---

## 📝 Initial Draft Proposal
*This architectural review addresses the transition to a strictly configuration-driven, dynamically triggered `devtest` node:*
1. **Startup Check:** `orchestrator/config.py` validates that the `devtest` node config defines dynamic label triggers (`label_trigger: ready-for-dev`, `queued_label: queued`, `label_output: dev-implemented`).
2. **Dynamic Polling:** `orchestrator/poller.py` parses these configured strings and queries GitHub targeting the configured labels.
3. **Stateless Execution:** `orchestrator/nodes/devtest.py` dynamically evaluates issue payloads and performs state transitions using `node_cfg` properties rather than hardcoded string literals.
4. **Constraint Enforcement:** Support full configurability from `config.yaml` while preserving model consistency across all SDLC nodes.

---

## 🔍 Review Iteration 1: Agent Architectural Critical Review & Schema Harmonization
- **Date / Author:** 2026-09-01 | Agent / Architect
- **Point-by-Point Verdict Matrix:**

| Proposed Item | Verdict | Critical Architectural Analysis & Nuance |
|---|:---:|---|
| **1. Dynamic `devtest` label configuration from `config.yaml`** | ✅ **APPROVE** | Aligns `devtest` with the dynamic label architecture already established for `architect` (`label_trigger`, `queued_label`, `label_output`). |
| **2. Creating an isolated `DevTestLabelConfig` with `extra="forbid"`** | ❌ **REJECT (Anti-Pattern & Breaking Change)** | Forbidding extra fields on a dedicated sub-model breaks `NodeConfig` properties like `harness`, `model`, `effort`, `branch_prefix`, `auto_merge_approved`, and `enabled`. Instead, keep `NodeConfig` unified across all nodes with clean defaults. |
| **3. Parameterizing all hardcoded string literals in `devtest.py`** | ✅ **APPROVE** | Replaces static `"ready-for-dev"`, `"queued"`, and `"dev-implemented"` literals throughout `orchestrator/nodes/devtest.py` with `node_cfg.label_trigger`, `node_cfg.queued_label`, and `node_cfg.label_output` (with dual-format prefix resilience). |
| **4. Parameterizing Poller workload queries in `poller.py`** | ✅ **APPROVE** | Ensures `orchestrator/poller.py` dynamically queries the configured `label_trigger` and `queued_label` when fetching workloads for `devtest`. |
| **5. Dual-label conflict priority (Active trumps Queued)** | ✅ **APPROVE** | If an issue contains both `label_trigger` and `queued_label`, DevTest treats it as active, purges the stale queued label, and executes. |

---

## 🛡️ Edge Cases & Resilience Strategy

1. **Unified Schema Consistency:**
   * Standardize `NodeConfig` across both `architect` and `devtest`:
     * `label_trigger`: Active trigger (`"ready-for-dev"` for DevTest, `"needs-triage"` for Architect).
     * `queued_label`: Inactive/queued state (`"queued"`).
     * `label_output`: Completed/transition state (`"dev-implemented"` for DevTest, `"ready-for-dev"` for Architect).
2. **Dual-Format Workflow Taxonomy:**
   * Support both shorthand (`"ready-for-dev"`, `"queued"`, `"dev-implemented"`) and prefixed format (`"status:ready-for-dev"`, `"status:queued"`, `"status:dev-implemented"`) via existing case-insensitive normalization.
3. **Graceful Fallbacks & Default Resilience:**
   * If a user config omits `label_trigger` or `queued_label`, Pydantic defaults automatically supply `"ready-for-dev"`, `"queued"`, and `"dev-implemented"`, preventing startup crashes while allowing full YAML customization.
4. **Multi-Environment Config Synchronization:**
   * Ensure `templates/config.example.yaml`, `%USERPROFILE%/.orchestrator/config.yaml`, and `%USERPROFILE%/.config/orchestrator/config.yaml` are 100% synchronized.

---

## 🎯 Final Decision Plan & User Story Specification

### 🧑‍💻 User Story
**As a** Graph Engineering Platform Operator,  
**I want** the `devtest` node and poller to dynamically ingest and evaluate its active trigger (`label_trigger`), queued trigger (`queued_label`), and completion label (`label_output`) directly from `config.yaml`,  
**So that** repository workflow labels can be customized per project without hardcoded string dependencies or pipeline regressions.

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
Then it must query GitHub issues matching the configured label_trigger and queued_label
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
| **Configuration** | `orchestrator/config.py`, `templates/config.example.yaml` | Ensure `NodeConfig` default values and documentation for `label_trigger`, `queued_label`, and `label_output` are unified. |
| **Poller Engine** | `orchestrator/poller.py` | Parameterize `fetch_project_workload` to use project-specific `devtest` label configurations. |
| **DevTest Node** | `orchestrator/nodes/devtest.py` | Replace all static literal label strings in Phase 1, Phase 2, Phase 3, and subtask unlocking with `node_cfg` properties. |
| **Live Configs** | `~/.orchestrator/config.yaml`, `~/.config/orchestrator/config.yaml` | Synchronize live configuration files with documented label schemas. |
| **Test Suite** | `tests/test_nodes.py`, `tests/test_sequential_pipeline.py` | Add BDD test coverage for custom DevTest label triggers and dynamic subtask progression. |

### 🧱 INVEST Subtask Decomposition
- **Subtask 1 (`feat(devtest, config)`)**: Parameterize all DevTest node label references (`label_trigger`, `queued_label`, `label_output`) to dynamically consume `NodeConfig`.
- **Subtask 2 (`feat(poller)`)**: Parameterize DevTest poller workload queries to dynamically ingest configured labels.
- **Subtask 3 (`test(devtest_labels)`)**: BDD test suite verifying custom DevTest label triggers, subtask activation, and dual-label conflict resolution.
