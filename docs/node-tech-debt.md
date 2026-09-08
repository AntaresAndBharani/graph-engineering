# Technical Debt & Quality Audit Node (`node-tech-debt`)

> [!NOTE]
> **Status: Optional / Autonomous Quiescence-Gated Node** (`enabled: false` by default).  
> In the streamlined 2-node parallel topology, the Technical Debt Node operates as an autonomous quality auditor that triggers exclusively when the repository is fully quiescent (all active story locks released, no open SDLC items or pipeline labels, and zero pending pull requests). It can be enabled in `config.yaml` (`tech_debt.enabled: true` or `nodes.tech_debt.enabled: true`) or invoked on demand via `orchestrator run --project <name> --node tech_debt --force`.

**Module**: [`orchestrator/nodes/tech_debt.py`](file:///C:/Users/rogal/workspaces/graph-engineering/orchestrator/nodes/tech_debt.py)

The **Technical Debt Node** acts as Node 5 in the autonomous pipeline—a fail-closed architectural auditor that analyzes the repository on branch `main` for critical architectural debt, testing gaps, maintainability hazards, and dead code, dispatching validated high-impact issues directly into the SDLC backlog.

---

## 🏛️ Operational Flow

```mermaid
flowchart TD
    Start["Trigger Cycle (Orchestrator Cycle / On-Demand)"] --> Quiescence{"1. Strict Quiescence Gate\n(is_project_fully_quiescent)"}
    
    Quiescence -->|Dev Active / Locks / Open PRs / CLI Error| IdleQuiescent["Exit: Development active. Idle (0 tokens)"]
    Quiescence -->|Quiescent (True)| ResolveSHA["2. Resolve Upstream origin/main SHA"]
    
    ResolveSHA --> SHAFilter{"3. Commit SHA Match?\n(last_audited_sha == current_sha & not force)"}
    SHAFilter -->|Already Audited| IdleSHA["Exit: Commit already audited. Idle (0 tokens)"]
    SHAFilter -->|New Commit SHA| CooldownCheck{"4. Cooldown Elapsed?\n(elapsed >= interval_seconds & not force)"}
    
    CooldownCheck -->|In Cooldown| IdleCooldown["Exit: In cooldown (<N>m remaining). Idle (0 tokens)"]
    CooldownCheck -->|Cooldown Elapsed / Force| Worktree["5. Ensure Ephemeral Worktree\n(checkout_detached_upstream origin/main)"]
    
    Worktree --> Harness["6. Execute Claude Sonnet (effort: low)\nStrict Negative Constraints Prompt"]
    Harness --> Parse["7. Parse Structured Recommendation\n(=== TECH DEBT RECOMMENDATION ===)"]
    
    Parse -->|=== NO TECH DEBT FOUND ===| RecordClean["Record 'CLEAN' in SQLite tech_debt_audits\n0 Issues Created"]
    Parse -->|High-Impact Debt Identified| DispatchIssue["8. Create GitHub Issue labeled 'story,needs-triage'\ngh issue create"]
    
    DispatchIssue --> RecordStory["Record 'STORY_CREATED' with issue_number in tech_debt_audits"]
    RecordClean --> Cleanup["9. Cleanup Ephemeral Worktree & Release Lock"]
    RecordStory --> Cleanup
```

---

## 🔑 Operational Capabilities & Invariants

### 1. Strict Fail-Closed Quiescence Gate
- Quiescence is evaluated deterministically via `is_project_fully_quiescent(project, state_manager)`:
  1. **Active Story Locks**: Halts if any active running job or story lock is held in the SQLite `active_jobs` blackboard for the project repo.
  2. **Active SDLC Items**: Halts if any SDLC item in `sdlc_items` is in `ACTIVE_SDLC_STATES` (`OPEN`, `PLANNED`, `ACTIVE`, `IN_PROGRESS`, `QUEUED`, `REVIEW`, `DEV_IMPLEMENTED`).
  3. **Pipeline Active Labels**: Halts if any item holds active pipeline labels (`architect-approved`, `needs-architect-review`, `ready-for-dev`, `dev-implemented`, `architect-processed`, `needs-triage`, `queued`, `in-progress`, `under-review`).
  4. **Open Pull Requests**: Queries `gh pr list --repo <repo> --state open --limit 5`. If any open PR exists, or if GitHub CLI times out or errors, the check fails closed, completely preventing interference with active development while consuming **0 LLM tokens**.

### 2. Decoupled Upstream Commit SHA Guard & Cooldown Throttling
- Resolves upstream commit SHA via `get_origin_main_sha` (`git fetch origin main` and `git rev-parse origin/main`).
- **Zero-Token Commit Skip**: If `current_sha == last_audit.commit_sha` and `--force` is not set, immediately logs `"Commit <sha> already audited for tech-debt. Idle (0 tokens)."` and exits.
- **Cooldown Interval**: Enforces a configurable cooldown period (`interval_seconds: 14400`, default 4 hours). If a new commit is detected but the cooldown has not elapsed, exits with 0 tokens.

### 3. Ephemeral Worktree & Detached HEAD Isolation
- Operates inside an isolated git worktree under `.graph/worktrees/<project>/tech_debt` managed by `WorktreeManager`.
- Synchronizes with upstream using `checkout_detached_upstream(wt_path, branch="main", timeout=30.0)` in detached HEAD mode to eliminate branch collision errors (`fatal: 'main' is already checked out`).
- Always releases worktrees and locks safely inside `finally` blocks.

### 4. Structured Synthesis & Idempotent Issue Dispatch
- Dispatches prompt to **Claude Sonnet (`claude-sonnet-5`, effort: low)** with strict negative constraints prohibiting cosmetic refactoring, docstring cleanups, or busywork.
- If no critical debt exists, Claude emits `=== NO TECH DEBT FOUND ===`. The orchestrator updates SQLite blackboard `tech_debt_audits` with status `CLEAN` and creates **0 GitHub issues**.
- If high-impact debt is identified, Claude outputs a structured block (`Title`, `Severity`, `Component`, `Description`, `Acceptance Criteria` Gherkin, `Remediation Plan`).
- The orchestrator validates the schema, formats the story issue, and executes `gh issue create --repo <repo> --label story,needs-triage`.
- Records the created issue number in SQLite `tech_debt_audits` with status `STORY_CREATED`.

---

## ⚙️ Configuration Schema

Configured in `~/.config/orchestrator/config.yaml` or project configurations:

```yaml
settings:
  tech_debt_interval_seconds: 14400   # 4 hours default cooldown

projects:
  - name: "crosstrainingapp"
    repo: "AntaresAndBharani/crosstrainingapp"
    local_path: "~/workspaces/crosstrainingapp"
    tech_debt:
      enabled: false                  # Optional / Disabled by default
      harness: "claude"
      model: "sonnet"
      effort: "low"
      interval_seconds: 14400         # 4 hours
    nodes:
      tech_debt:
        enabled: false
        harness: "claude"
        model: "sonnet"
        effort: "low"
        interval_seconds: 14400
```
