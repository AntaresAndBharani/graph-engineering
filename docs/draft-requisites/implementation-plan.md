### Phase 1: Functional & DX (Developer Experience) Review

#### 1. Workflow Analysis & Node Lifecycle Management

The transition to a streamlined pipeline (`Architect` $\to$ `DevTest`) requires a formal mechanism to disable inactive nodes (`supervisor`, `reviewer`, `bau`) without deleting their implementations from `orchestrator/nodes/`. The orchestrator runtime must dynamically inspect the `enabled` boolean state for every node in `orchestrator/config.py`.

```
                    [GitHub Issues / Webhook Poller]
                                   │
                 ┌─────────────────┴─────────────────┐
                 ▼                                   ▼
      [Target: Reviewer/BAU/Sup]           [Target: Architect/DevTest]
                 │                                   │
      [Check: node.enabled == False]       [Check: node.enabled == True]
                 │                                   │
                 ▼                                   ▼
        [Skip Polling & Log:                [Dispatch Native Async
         "Node <name> is disabled"]          Worktree Execution]
```

* **Friction Points Identified:**
  * **Hardcoded Polling Queries:** In `orchestrator/poller.py`, status queries currently scan for `status:needs-review` or `status:needs-po-review` even if those nodes are deprecated. This generates unnecessary GitHub API calls.
  * **Label Schema Divergence:** Discrepancies exist between legacy labels (`status:needs-review`, `status:post-merge-review`) and active labels (`status:ready-for-architecture`, `status:planned`, `status:ready-for-dev`, `status:in-progress`, `status:completed`, `status:blocked`).
  * **Documentation Staleness:** Documentation files in `docs/node-reviewer.md`, `docs/node-supervisor.md`, `docs/node-bau.md`, and `.graph/architecture.md` depict active 4-hop routing loops rather than documenting them as optional or disabled components.

#### 2. Edge Cases & Resilience Strategy

* **Disabled Node Issue Ingestion:** If an issue in GitHub carries a legacy label (e.g., `status:needs-review`), the poller must ignore it or emit a single debug log entry rather than throwing an unhandled `KeyError` or dispatching an unconfigured worker.
* **Config Toggle Drift:** If a node is set to `enabled: false` in `templates/config.example.yaml`, the CLI daemon in `orchestrator/cli.py` must bypass creating worktrees or memory buffers for that node.
* **Hot-Reloading Node Toggles:** When `orchestrator/reloader.py` detects a configuration change enabling/disabling a node, it must reload active tasks without restarting the entire async event loop.
* **Database State Isolation:** Inactive node names in `sdlc_items.active_node` must be marked as `IDLE (Disabled)` in `orchestrator/db.py` to prevent the TUI dashboard from expecting live streams.

#### 3. Acceptance Criteria (BDD Format)

```gherkin
Scenario: Node disabled via configuration skips polling and dispatch
  Given the configuration sets "nodes.reviewer.enabled = false"
  And an open issue in GitHub is labeled "status:needs-review"
  When the poller cycle runs in "orchestrator/poller.py"
  Then the poller must not query or dispatch workers for the reviewer node
  And the reviewer worktree directory must not be created.

Scenario: Inactive node execution raises explicit disabled error
  Given the "ReviewerNode" is instantiated with "enabled = false"
  When the orchestrator attempts to invoke "ReviewerNode.execute()"
  Then it must raise "NodeDisabledError" or return "NodeResult.DISABLED"
  And record "[WARN] Attempted execution on disabled node: reviewer" in logs.

Scenario: Label taxonomy cleanup harmonizes active and legacy states
  Given a project configuration defining the active label schema
  When the daemon verifies GitHub label requirements
  Then only active labels ("status:ready-for-architecture", "status:planned", "status:ready-for-dev", "status:in-progress", "status:completed", "status:blocked") must be provisioned
  And deprecated reviewer labels must be omitted from auto-creation routines.
```

#### 4. CLI UX & Terminal Feedback Guidelines

* **Startup Configuration Audit:** `orchestrator/cli.py` prints a clean status matrix of active versus dormant nodes on startup:

```text
[INFO] Node Status Registry:
       • architect:  ENABLED  (Harness: claude, Worktree: Isolated)
       • devtest:    ENABLED  (Harness: gemini, Worktree: Isolated)
       • supervisor: DISABLED (Bypassed)
       • reviewer:   DISABLED (Bypassed)
       • bau:        DISABLED (Bypassed)
```

* **Standardized Log Namespace:** Enforce strict prefixing via `orchestrator/logging.py`:
  * `[DEBUG] [poller] Skipping disabled node listener: reviewer`
  * `[INFO] [graph-engineering|architect] Story #95 decomposed. Tagged: status:planned`

---

### Phase 2: Architectural & Implementation Plan

#### 1. Codebase Impact & Component Updates

| File Path | Action | Description / Responsibility |
| --- | --- | --- |
| `orchestrator/config.py` | **Modify** | Add `is_node_enabled(node_name)` helper to `ProjectConfig`. Add `LabelTaxonomyConfig` to unify labels. |
| `templates/config.example.yaml` | **Modify** | Set `reviewer.enabled: false`, `supervisor.enabled: false`, and `bau.enabled: false`. Update label mappings. |
| `orchestrator/nodes/reviewer.py` | **Modify** | Enforce `enabled` guard without deleting implementation. |
| `orchestrator/nodes/supervisor.py` | **Modify** | Enforce `enabled` guard without deleting implementation. |
| `orchestrator/nodes/bau.py` | **Modify** | Enforce `enabled` guard without deleting implementation. |
| `orchestrator/poller.py` | **Modify** | Filter dispatch loops against `project.is_node_enabled(node_name)` before polling GitHub. |
| `orchestrator/cli.py` | **Modify** | Display active/disabled status matrix in startup logs. |
| `docs/node-reviewer.md` | **Modify** | Mark header with `> **Status:** Deprecated / Disabled by default`. |
| `docs/node-supervisor.md` | **Modify** | Mark header with `> **Status:** Optional / Disabled by default`. |
| `docs/node-bau.md` | **Modify** | Mark header with `> **Status:** Optional / Disabled by default`. |
| `.graph/architecture.md` | **Modify** | Update SDLC pipeline diagrams to reflect the 2-node parallel flow (`Architect` $\to$ `DevTest`). |
| `README.md` | **Modify** | Update active workflow instructions, label guides, and configuration flags. |
| `tests/test_config.py` | **Modify** | Add tests for node enablement toggling and label taxonomy parsing. |
| `tests/test_nodes.py` | **Modify** | Add tests verifying disabled nodes return early without network or disk operations. |

---

#### 2. Label Taxonomy Harmonization

| Category | Label String | Target Entity | Lifecycle Role |
| --- | --- | --- | --- |
| **Ingestion** | `status:ready-for-architecture` | Parent Story / Issue | Trigger for Architect node decomposition. |
| **Planning** | `status:planned` | Parent Story | Decomposed story with linked queued subtasks awaiting execution. |
| **Active Story** | `status:in-progress` | Parent Story | Story currently claimed and undergoing subtask execution. |
| **Active Subtask** | `status:ready-for-dev` | Subtask Issue | Target for DevTest code implementation and testing. |
| **Queued Subtask** | `status:queued` | Subtask Issue | Dependent subtask waiting for predecessor completion. |
| **Terminal** | `status:completed` | Story & Subtask | PR squash-merged and verified. |
| **Failure / Lock** | `status:blocked` | Story & Subtask | Unresolvable merge conflict or CI check exhaustion. |
| **Deprecated** | `status:needs-review` | *None* | Disabled with Reviewer node. |
| **Deprecated** | `status:post-merge-review` | *None* | Disabled with Reviewer node. |

---

#### 3. Step-by-Step Implementation Checklist

* **Step 1: Configuration & Schema Layer (`orchestrator/config.py` & `templates/config.example.yaml`)**
  * Update `ProjectConfig` to include `is_node_enabled(node_name: str) -> bool`.
  * Set `reviewer.enabled: false`, `supervisor.enabled: false`, and `bau.enabled: false` in `templates/config.example.yaml`.
  * Standardize `LabelTaxonomyConfig` with fields for `ready_for_arch`, `planned`, `in_progress`, `ready_for_dev`, `queued`, `completed`, and `blocked`.

* **Step 2: Node Guards & Poller Filtering (`orchestrator/nodes/` & `orchestrator/poller.py`)**
  * In `orchestrator/poller.py` (`fetch_project_workload`), check `project.is_node_enabled(node_type)` before executing GitHub API queries for that node's labels.
  * In `orchestrator/cli.py` (`run_project_cycle`), bypass calling disabled node routines.

* **Step 3: Startup CLI Auditing (`orchestrator/cli.py`)**
  * In `orchestrator/cli.py`, add a startup Node Status Registry table printing the active/dormant status of all nodes.

* **Step 4: Documentation Synchronization**
  * In `.graph/architecture.md`, replace 4-hop diagrams with the streamlined 2-node parallel flow (`Architect` Producer $\to$ `DevTest` Consumer).
  * In `docs/node-reviewer.md`, `docs/node-supervisor.md`, and `docs/node-bau.md`, add deprecation/disabled callout headers explaining how to enable them if needed.
  * In `docs/node-architect.md` and `docs/node-devtest.md`, document the worktree isolation, story locking, and automated PR squash-merge operations.
  * In `README.md`, update quickstart commands, label taxonomy tables, and YAML configuration examples.

* **Step 5: Automated Testing & Verification (`tests/`)**
  * In `tests/test_config.py`, verify `enabled: false` deserializes correctly from YAML.
  * In `tests/test_poller.py` and `tests/test_cli.py`, test that disabled node queues and routines are bypassed.
  * Run `pytest -v tests/` to confirm complete test pass rate across the test suite.

---

## 🔗 GitHub Reference
- **GitHub Issue:** [Issue #125](https://github.com/AntaresAndBharani/graph-engineering/issues/125)
- **Label:** `needs-triage`

## 🔨 Subtasks
- [ ] feat(config, poller): disable dormant nodes by default and gate poller workload queries on node.enabled
- [ ] feat(cli): startup Node Status Registry table and project cycle dispatch bypass for disabled nodes
- [ ] docs: harmonize architecture docs, README, and node guides with 2-node parallel topology
- [ ] test(config, cli, poller): unit tests for node enablement gating, poller bypassing, and label taxonomy
