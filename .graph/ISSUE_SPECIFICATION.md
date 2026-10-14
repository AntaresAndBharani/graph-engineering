## 🎯 Final Decision Plan & User Story Specification

### User Story: Dual-Persona DevTest Negotiation Loop Upgrade
**As a** system engineer and platform operator,
**I want** the `devtest` node to run an iterative Developer ↔ Reviewer negotiation loop bounded by `max_rounds` with PR-comment turn markers,
**So that** PRs are independently verified against issue specifications prior to auto-merging without manual intervention or label proliferation.

---

### 1. Architectural Summary & Scope Boundaries
This feature upgrades the `devtest` node (`orchestrator/nodes/devtest.py`) from a single-pass CLI execution into an iterative dual-persona (Developer ↔ Reviewer) negotiation loop with a bounded round cap (`max_rounds`, default 3) and a deterministic contingency resolution exit.
- **Strict backward compatibility**: Disabled by default (`reviewer.enabled: false`). Existing single-pass behavior is byte-for-byte preserved.
- **Zero label proliferation**: Turn coordination and negotiation states live exclusively within PR comments via structured marker headers (`<!-- DEVTEST_TURN: ... -->`).
- **Resumption & restart robustness**: PR comments serve as the single source of truth across process restarts.
- **Strict merge gating**: All PR merges are exclusively routed through `_verify_and_auto_merge_pr` (handling CI gating, `sdlc_items` SQLite synchronization, parent feature checklist progression, and worktree cleanup). Raw merges by persona CLI are prohibited.

---

### 2. Configuration Schema (`orchestrator/config.py` & `templates/config.example.yaml`)

1. Define `DevTestReviewerConfig` inheriting from `OrchestratorBaseModel`:
```python
class DevTestReviewerConfig(OrchestratorBaseModel):
    enabled: bool = False
    harness: Optional[str] = None  # Falls back to devtest.harness
    model: Optional[str] = None    # Falls back to devtest.model
    effort: Optional[str] = None   # Falls back to devtest.effort
    max_rounds: int = Field(default=3, ge=1)
```
2. Attach `reviewer` to `NodeConfig`:
```python
class NodeConfig(OrchestratorBaseModel):
    ...
    reviewer: DevTestReviewerConfig = Field(default_factory=DevTestReviewerConfig)

    @field_validator("reviewer", mode="before")
    @classmethod
    def _coerce_none_reviewer(cls, v: Any) -> Any:
        return DevTestReviewerConfig() if v is None else v
```
3. Call `DevTestReviewerConfig.model_rebuild()` in the existing rebuild block (`config.py:384-390`).
4. Implement helper `resolve_reviewer_config(node_cfg: NodeConfig) -> DevTestReviewerConfig` to cleanly inherit `harness`, `model`, and `effort` from the parent `node_cfg` when unset.
5. Update `templates/config.example.yaml` with the `reviewer:` block documentation under `nodes.devtest`.

---

### 3. PR Comment State Machine & Dispatcher Contract

#### 3.1 Marker Syntax & Generation
Every negotiation comment posted by either persona contains:
`<!-- DEVTEST_TURN: persona=DEVELOPER|REVIEWER round=1..N status=READY_FOR_REVIEW|CHANGES_REQUESTED|APPROVED|CONTINGENCY_PLAN|CONTINGENCY_COMPLETED -->`
- The orchestrator parses the Reviewer / Developer CLI output and programmatically injects/validates the marker header to prevent malformed LLM responses.
- `determine_next_action(pr_comments, max_rounds=3)` scans comment bodies from newest to oldest for the last valid `DEVTEST_TURN` marker, filtering out non-marker bot/user comments.

#### 3.2 Exhaustive Transition Table
| Last Marker / State | Next Persona | Action |
|---|---|---|
| No PR exists yet | `DEVELOPER` | Round 1 initial implementation. Open PR and post `persona=DEVELOPER round=1 status=READY_FOR_REVIEW`. |
| PR exists with no valid marker | `DEVELOPER` | Treat as Round 1 recovery: verify PR branch and post `persona=DEVELOPER round=1 status=READY_FOR_REVIEW`. |
| `persona=DEVELOPER`, `status=READY_FOR_REVIEW` (`round=R`) | `REVIEWER` | Evaluate PR diff against issue. If `R < max_rounds`, post `APPROVED` or `CHANGES_REQUESTED`. If `R >= max_rounds`, post `APPROVED` or `CONTINGENCY_PLAN`. |
| `persona=REVIEWER`, `status=CHANGES_REQUESTED` (`round=R < max_rounds`) | `DEVELOPER` | Address reviewer feedback in worktree, push commit to PR branch, and post `persona=DEVELOPER round=R+1 status=READY_FOR_REVIEW`. |
| `persona=REVIEWER`, `status=CONTINGENCY_PLAN` (`round=max_rounds`) | `DEVELOPER` | Apply reviewer contingency directives, ensure local tests pass, push commit, and post `persona=DEVELOPER round=max_rounds status=CONTINGENCY_COMPLETED`. |
| `persona=REVIEWER`, `status=APPROVED` | `ORCHESTRATOR` | Hand off to `_verify_and_auto_merge_pr` for CI status check, merge, parent subtask progression, and slot release. |
| `persona=DEVELOPER`, `status=CONTINGENCY_COMPLETED` | `ORCHESTRATOR` | Hand off to `_verify_and_auto_merge_pr` (idempotent merge gate). If CI fails, route to `needs-refactor`. |
| PR already merged or closed | `NONE` | No-op terminal state. |
| Invalid / unparseable verdict after bounded retry | `NONE` | Leave PR open, log error, fail safe without merging. |

---

### 4. Lifecycle & Integration Guards in `orchestrator/nodes/devtest.py`

1. **Phase 2 Non-Terminal Auto-Merge Guard (`devtest.py:1048-1080`)**:
   - In dual-persona mode (`node_cfg.reviewer.enabled is True`), Phase 2 only auto-merges an open PR if its latest valid marker is `APPROVED` or `CONTINGENCY_COMPLETED`. Active negotiation PRs (`READY_FOR_REVIEW`, `CHANGES_REQUESTED`, `CONTINGENCY_PLAN`) are skipped.
2. **Phase 3 Duplicate-PR Guard & Dispatcher Routing (`devtest.py:1201-1238`)**:
   - Instead of aborting with `"Waiting for Phase 2 CI verification"`, an existing open PR with non-terminal negotiation marker is routed directly to `determine_next_action` to run the next required turn.
3. **Phase 3 Destructive Git Reset Guard (`devtest.py:1339-1345`)**:
   - Resumed Developer turns fetch and check out `origin/<branch_name>` instead of `origin/main` with `git reset --hard origin/main`, preserving all prior rounds' commits.
4. **Daemon Tick Scheduling & Quotas**:
   - Each persona turn executes within a single daemon tick with its own slot acquisition, `lock_ttl` (`devtest.py:1313`), and `check_dispatch_quota` verified against the resolved persona harness.

---

### 5. Acceptance Criteria (Gherkin BDD)

#### Scenario 1: Legacy Compatibility (Reviewer Disabled)
- **Given** `nodes.devtest.reviewer.enabled` is `false` or omitted in `config.yaml`,
- **When** `devtest` runs on a `ready-for-dev` issue,
- **Then** the Developer CLI is invoked in a single pass, opens the PR, and exits;
- **And** the orchestrator merges the PR via `_verify_and_auto_merge_pr` only after CI is green, byte-for-byte unchanged from legacy behavior.

#### Scenario 2: Agreement Reached Within Max Rounds
- **Given** `nodes.devtest.reviewer.enabled` is `true` and `max_rounds` is 3,
- **When** Developer opens PR with `DEVELOPER/READY_FOR_REVIEW round=1`,
- **And** Reviewer posts `REVIEWER/CHANGES_REQUESTED round=1`,
- **And** Developer pushes fixes with `DEVELOPER/READY_FOR_REVIEW round=2`,
- **And** Reviewer posts `REVIEWER/APPROVED round=2`,
- **Then** the negotiation terminates early at Round 2, Round 3 is not executed,
- **And** the orchestrator auto-merges the PR via `_verify_and_auto_merge_pr` once CI passes.

#### Scenario 3: Bounded Rounds Exhaustion & Contingency Resolution
- **Given** `nodes.devtest.reviewer.enabled` is `true` and `max_rounds` is 3,
- **When** Developer posts `READY_FOR_REVIEW` for Round 1, Round 2, and Round 3,
- **And** Reviewer requests changes in Rounds 1 and 2,
- **And** on Round 3 Reviewer posts `status=CONTINGENCY_PLAN` with pragmatic alignment directives,
- **Then** Developer executes the contingency plan, pushes commits, and posts `DEVELOPER/CONTINGENCY_COMPLETED round=3`,
- **And** the orchestrator auto-merges the PR via `_verify_and_auto_merge_pr` upon passing CI.

#### Scenario 4: Phase 2 Auto-Merge Skip on Active Non-Terminal Negotiation
- **Given** dual mode is enabled and an open PR has green CI (`PASS`),
- **When** the latest PR marker is `DEVELOPER/READY_FOR_REVIEW round=1`,
- **Then** Phase 2 does NOT auto-merge the PR, allowing the Reviewer persona to evaluate it.

#### Scenario 5: Mid-Loop Restart Resumption
- **Given** an open PR exists with latest marker `DEVELOPER/READY_FOR_REVIEW round=2` after a daemon restart,
- **When** `devtest` runs,
- **Then** the Phase 3 duplicate PR guard routes to the dispatcher without error,
- **And** the Reviewer persona is dispatched for Round 2 without resetting the branch to `origin/main` or opening a duplicate PR.

#### Scenario 6: Post-Contingency CI Failure Routing
- **Given** latest marker is `DEVELOPER/CONTINGENCY_COMPLETED`,
- **When** `_verify_and_auto_merge_pr` checks CI and detects a failure (`FAIL`),
- **Then** the PR is not merged and the issue is routed to `needs-refactor`.

#### Scenario 7: Full SDLC Side-Effect Execution on Merge
- **Given** a dual-mode PR reaches `APPROVED` or `CONTINGENCY_COMPLETED` with passing CI,
- **When** `_verify_and_auto_merge_pr` executes,
- **Then** `sdlc_items` state is set to `MERGED`, the worktree is cleaned up, the parent feature checklist is advanced, and the next queued child slice is unlocked.

---

### 📦 Task Breakdown (Pattern A: Standalone Task)

* **[ ] Task 1 (Config Schema & Validation):** Define `DevTestReviewerConfig(OrchestratorBaseModel)` with `max_rounds: int = Field(default=3, ge=1)`, attach to `NodeConfig` with null coercion validator, add `model_rebuild()`, helper `resolve_reviewer_config()`, and update `templates/config.example.yaml`.
* **[ ] Task 2 (Pure Turn Dispatcher & Parser):** Implement `determine_next_action(pr_comments, max_rounds=3)` and marker parser in `orchestrator/nodes/devtest.py` covering all transition table rows, comment filtering, and 0-marker recovery.
* **[ ] Task 3 (Lifecycle Guards & Turn Execution):** Update `run_devtest_node` Phase 2 non-terminal PR skip, Phase 3 duplicate PR routing to dispatcher, `origin/<branch>` non-destructive checkout, and persona turn invocation per daemon tick.
* **[ ] Task 4 (Verification & Test Suite):** Add comprehensive unit and integration tests in `tests/test_devtest_dual_persona.py` validating all 7 Gherkin scenarios, state transitions, and backwards compatibility.