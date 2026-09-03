# 📋 Implementation Plan & Refinement Lifecycle: Deprecate Legacy Local Pipeline from crosstrainingapp to Harmonize with Centralized Orchestrator

## 📝 Initial Draft Proposal

### Background & Objective
In `crosstrainingapp` (`AntaresAndBharani/crosstrainingapp`), legacy per-project PowerShell pipeline scripts (`scripts/local-pipeline/*.ps1`), obsolete test suites (`scripts/tests/ArchitectWorkflow.*.Tests.ps1`), and prompt templates (`.claude/tasks/`, `.antigravity/tasks/`) remain from an early prototype before the centralized Python orchestrator in `graph-engineering` was created.

As proven by `biq-app` (`BasketIQ/biq-app`):
1. Modern application repositories are 100% clean and do **not** maintain local orchestration scripts or custom label taxonomy.
2. All graph lifecycle execution (`architect`, `devtest`, `reviewer`, `supervisor`, `bau`) is driven centrally by `graph-engineering`.
3. All Windows Scheduled Tasks for the local pipeline (`CTA-*`, `DT-*`) have already been permanently disabled.
4. Keeping deprecated local scripts in `crosstrainingapp` actively pollutes context for AI agents, causing label confusion (e.g. `type:user-story` / `type:subtask` vs `needs-triage` / `architect-processed` / `queued`).

### Core Actions
1. **Remove Deprecated Pipeline Scripts**:
   - Delete `crosstrainingapp/scripts/local-pipeline/` (`run-architect.ps1`, `run-backlog-triage.ps1`, `run-pr-review.ps1`, `run-three-amigos-and-dev-test.ps1`).
2. **Remove Deprecated Pipeline Tests & Docs**:
   - Delete `crosstrainingapp/scripts/tests/ArchitectWorkflow.Schema.Tests.ps1`, `ArchitectWorkflow.Static.Tests.ps1`, `ArchitectWorkflow.SubIssues.Tests.ps1`, and `test-architect-workflow.sh`.
   - Delete `crosstrainingapp/docs/ARCHITECT-WORKFLOW-VERIFICATION.md`.
   - Delete obsolete task templates in `crosstrainingapp/.claude/tasks/` and `crosstrainingapp/.antigravity/tasks/`.
3. **Harmonize Test Runner**:
   - Verify that `scripts/tests/Invoke-ScriptTests.ps1` executes 100% green without the deleted files.
4. **Changelog & Documentation**:
   - Record deprecation under `## [Unreleased]` in `crosstrainingapp/CHANGELOG.md`.

---

## 🔍 Review Iteration 1: 3-Amigos Critical Architectural Review

- **Date / Author:** 2026-09-02 | Antigravity AI Architect
- **Target Repositories:** `crosstrainingapp`, `graph-engineering`
- **Reference Standard:** `biq-app` (`BasketIQ/biq-app`)

### 1. Point-by-Point Deprecation & Clean-Up Matrix

| # | Item / File Path | Role & Current State | Verdict | Rationale & Safety Verification |
|---|---|---|---|---|
| 1 | `crosstrainingapp/scripts/local-pipeline/` | Legacy per-repo runner scripts (`run-architect.ps1`, etc.). | **DELETE** | Windows scheduled tasks `CTA-*` are permanently disabled. Central orchestrator handles all pipelines externally. Zero dependencies from app code or CI. |
| 2 | `crosstrainingapp/scripts/tests/ArchitectWorkflow.*.Tests.ps1` | Pester tests asserting legacy local pipeline schema. | **DELETE** | They only test `run-architect.ps1`. Deleting them leaves the test suite cleaner and prevents false regressions. |
| 3 | `crosstrainingapp/docs/ARCHITECT-WORKFLOW-VERIFICATION.md` | Outdated documentation referencing `type:user-story` and local pipeline cutover. | **DELETE** | Superseded by centralized specifications in `ws-setups/graph-engineering/docs/`. |
| 4 | `crosstrainingapp/.claude/tasks/` & `.antigravity/tasks/` | Prompts crafted specifically for local `.ps1` wrappers. | **DELETE** | Prompts are now dynamically constructed by `graph-engineering/orchestrator/nodes/*.py`. |
| 5 | `crosstrainingapp/scripts/tests/Invoke-ScriptTests.ps1` | General script test runner. | **PRESERVE** | Must remain to run remaining active utility tests (`AdbEmulatorHelper`, `GitHubArtifactHelper`, `PostE2EEvidence`, `PrComment`, `SummarizeUnitTests`). |
| 6 | Active CLI and E2E helper scripts (`scripts/*.ps1`) | Core dev utilities used during PR builds and E2E testing. | **PRESERVE** | Essential project infrastructure; unaffected by pipeline deprecation. |

---

## 🔍 Review Iteration 2: Critical Multi-Repository Comparative Analysis & Drawback Evaluation

- **Date / Author:** 2026-09-02 | Antigravity AI Architect
- **Benchmark Scope:** `crosstrainingapp` vs. `biq-app` vs. `biq-app-native` vs. `biq-playbook`

### 1. Multi-Repository Forensic Architecture Comparison

| Repository | `scripts/local-pipeline/`? | Legacy Task Prompts (`.claude/tasks/`)? | Scheduled Tasks Active? | Orchestrator Config (`config.yaml`) | Status & Taxonomy |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **`biq-app`** | ❌ None (`False`) | ❌ None | None | ✅ Active (needs-triage -> dev-implemented) | **Clean Application Repo** (zero local pipeline debt) |
| **`biq-app-native`** | ❌ None (`False`) | ❌ None | None | ✅ Active (needs-triage -> dev-implemented) | **Clean Application Repo** (zero local pipeline debt) |
| **`biq-playbook`** | ❌ None (`False`) | ❌ None | None | ✅ Active (needs-triage -> dev-implemented) | **Clean Application Repo** (only domain UX agents) |
| **`crosstrainingapp`** | ⚠️ **Present** (`True`) | ⚠️ **Present** (`True`) | ❌ All `CTA-*` Disabled | ✅ Active (needs-triage -> dev-implemented) | **Hybrid / Polluted** (contains dead prototype scripts) |

### 2. Critical Evaluation of Drawbacks & Concerns

#### Drawback 1: Loss of Standalone PowerShell Execution
- **Concern:** If an operator wants to manually run `run-architect.ps1` from a local PowerShell console without launching the Python orchestrator daemon.
- **Critical Verdict: MITIGATED / NON-ISSUE.**
  `graph-engineering` provides a first-class CLI runner:
  `python -m orchestrator.cli run --project crosstrainingapp --node architect`
  This executes the exact same task in 1 command, with full state locking, worktree isolation, and zero chance of label divergence.

#### Drawback 2: Impact on Android Gradle Build or CI/CD
- **Concern:** Could removing `scripts/local-pipeline/` break `./gradlew assembleDebug`, `testDebugUnitTest`, or GitHub Actions workflows?
- **Critical Verdict: ZERO IMPACT.**
  Inspection of `build.gradle.kts`, `app/build.gradle.kts`, and `.github/workflows/*.yml` confirmed **0 references** to `scripts/local-pipeline`. CI builds run Gradle tasks and Maestro E2E tests, which live in `.maestro/` and `app/`.

#### Drawback 3: Impact on Script Test Suite (`Invoke-ScriptTests.ps1`)
- **Concern:** Will deleting files cause test failures in `pwsh -File ./scripts/tests/Invoke-ScriptTests.ps1`?
- **Critical Verdict: MUST DELETE OBSOLETE TESTS CONCURRENTLY.**
  `scripts/tests/ArchitectWorkflow.*.Tests.ps1` specifically test `run-architect.ps1`. If `run-architect.ps1` is deleted without deleting its test files, `Invoke-ScriptTests.ps1` will fail with file-not-found errors. Therefore, deleting `ArchitectWorkflow.*.Tests.ps1` simultaneously is mandatory to keep `Invoke-ScriptTests.ps1` 100% green.

#### Drawback 4: Active E2E and CI Helpers
- **Concern:** Are there any scripts in `scripts/` that must NOT be touched?
- **Critical Verdict: SAFEGUARD ACTIVE SCRIPTS.**
  `AdbEmulatorHelper.ps1`, `GitHubArtifactHelper.ps1`, `PostE2EEvidence.ps1`, `PrComment.ps1`, and `SummarizeUnitTests.ps1` are actively used by DevTest and CI for automated APK testing. They reside in `scripts/` (root) and `scripts/tests/`, NOT in `scripts/local-pipeline/`. They must remain strictly untouched.

### 3. Final Synthesis: Is this the real change that we have to do in `crosstrainingapp`?
**Yes.** `crosstrainingapp` is currently the **only repository out of four** that still retains the prototype PowerShell pipeline scripts. Removing them is the only way to achieve architectural consistency across the entire ecosystem.

---

## 🎯 Final Decision Plan & User Story Specification

### 📖 User Story
**As a** Graph Engineering Platform Operator,  
**I want** all legacy local-pipeline scripts, obsolete workflow tests, and redundant prompt templates removed from `crosstrainingapp`,  
**So that** `crosstrainingapp` matches the clean application architecture of `biq-app`, eliminating dead code, label confusion, and context pollution for autonomous agents.

---

### 🏗️ Architecture Alignment: Before vs After

```mermaid
flowchart TD
    subgraph Legacy Crosstrainingapp Prototype (DEPRECATED)
        A1["CTA-* Scheduled Tasks (Disabled)"] -.-> A2["scripts/local-pipeline/*.ps1"]
        A2 -.-> A3[".claude/tasks/*.md"]
        A2 -.-> A4["type:user-story / type:subtask"]
    end

    subgraph Target Harmonized Architecture (Identical to biq-app)
        O1["Central Python Orchestrator (graph-engineering)"] -->|Remote gh CLI / Worktree| App["crosstrainingapp (Clean App Code)"]
        O1 -->|Labels: needs-triage -> architect-processed -> queued -> ready-for-dev| GH["GitHub API & Issues"]
        App --> CI["GitHub Actions CI/CD (.github/workflows)"]
        App --> AppTests["Gradle & Script Utility Tests (Invoke-ScriptTests.ps1)"]
    end
```

---

### ✅ Acceptance Criteria (Gherkin BDD Format)

```gherkin
Feature: Deprecate Legacy Local Pipeline and Align crosstrainingapp Architecture

  Scenario: Legacy local-pipeline scripts directory is completely removed
    Given the repository "crosstrainingapp"
    When inspecting the "scripts/" directory
    Then the directory "scripts/local-pipeline/" must not exist.

  Scenario: Obsolete architect workflow tests and shell scripts are removed
    Given the repository "crosstrainingapp"
    When inspecting "scripts/tests/"
    Then "ArchitectWorkflow.Schema.Tests.ps1" must not exist
    And "ArchitectWorkflow.Static.Tests.ps1" must not exist
    And "ArchitectWorkflow.SubIssues.Tests.ps1" must not exist
    And "test-architect-workflow.sh" must not exist.

  Scenario: Remaining script utility test suite passes 100%
    Given the repository "crosstrainingapp"
    When executing "pwsh -NoProfile -File ./scripts/tests/Invoke-ScriptTests.ps1"
    Then the exit code must be 0
    And all remaining utility test suites (AdbEmulatorHelper, GitHubArtifactHelper, PostE2EEvidence, PrComment, SummarizeUnitTests) must pass.

  Scenario: Obsolete prompt templates and documentation are purged
    Given the repository "crosstrainingapp"
    When inspecting ".claude/tasks/" and ".antigravity/tasks/"
    Then neither directory must exist
    And "docs/ARCHITECT-WORKFLOW-VERIFICATION.md" must not exist.

  Scenario: Changelog documents the deprecation and architectural harmonization
    Given "CHANGELOG.md" in "crosstrainingapp"
    When inspected under "## [Unreleased]"
    Then it must record the removal of legacy local-pipeline scripts and test suites.
```

---

### 📦 Component Impact Table

| Repository | Path | Action | Description |
| :--- | :--- | :---: | :--- |
| `crosstrainingapp` | `scripts/local-pipeline/` | **DELETE** | Remove all 4 legacy runner `.ps1` scripts. |
| `crosstrainingapp` | `scripts/tests/ArchitectWorkflow.*.Tests.ps1` | **DELETE** | Remove 3 obsolete Pester test files. |
| `crosstrainingapp` | `scripts/tests/test-architect-workflow.sh` | **DELETE** | Remove obsolete bash test script. |
| `crosstrainingapp` | `docs/ARCHITECT-WORKFLOW-VERIFICATION.md` | **DELETE** | Remove outdated cutover documentation. |
| `crosstrainingapp` | `.claude/tasks/` & `.antigravity/tasks/` | **DELETE** | Remove obsolete prompt template directories. |
| `crosstrainingapp` | `CHANGELOG.md` | **MODIFY** | Record clean-up under `## [Unreleased]`. |

---

### 📋 INVEST Subtask Breakdown

1. **Subtask 1 (Purge Local Pipeline Scripts & Tasks):** Delete `crosstrainingapp/scripts/local-pipeline/` and `.claude/tasks/`, `.antigravity/tasks/`.
2. **Subtask 2 (Purge Obsolete Tests & Verification Docs):** Delete `ArchitectWorkflow.*.Tests.ps1`, `test-architect-workflow.sh`, and `docs/ARCHITECT-WORKFLOW-VERIFICATION.md`.
3. **Subtask 3 (Test Verification):** Run `Invoke-ScriptTests.ps1` and `./gradlew.bat testDebugUnitTest` to verify 100% green pass rate with zero regressions.
4. **Subtask 4 (Changelog & Git Delivery):** Update `CHANGELOG.md`, create feature branch `feat/deprecate-legacy-local-pipeline`, commit, push, open PR, verify remote CI, and merge.

---

## 🔍 Review Iteration 3: Three Amigos Critical Review — Evidence-Based Re-Verification

- **Date / Reviewer:** 2026-09-03 | Three Amigos (Business / Dev / QA)
- **Scope reviewed:** The full plan as of Iteration 2, re-tested against the live
  working copies of `crosstrainingapp` (`~/workspaces/ws-gym/crosstrainingapp`,
  branch `feat/issue-473`, `23cb787`) and `biq-app`
  (`~/workspaces/basket/mobile/biq-app`), the live GitHub workflow states, and
  `~/.orchestrator/config.yaml`.
- **Verdict: REWORK REQUIRED.** The deletion list is accurate and safe as far as
  it goes, but the plan removes only the PowerShell half of the legacy pipeline
  while leaving its GitHub Actions twin — including one *still-active* workflow
  — in the repository. As written it cannot deliver its own stated objective.

### Findings

| # | Severity | Perspective | Finding | Evidence | Recommended action |
|---|---|---|---|---|---|
| 1 | **Blocker** | Business | Plan deprecates only `scripts/local-pipeline/`. The GitHub Actions twin of the same prototype (`architect.yml`, `dev-test.yml`, `three-amigos.yml`, `pr-review.yml`, `merge.yml`) is untouched, so "matches the clean architecture of `biq-app`" is not achieved. | `crosstrainingapp/.github/workflows/` holds 8 workflows, 5 of them orchestration; `biq-app/.github/workflows/` holds 5, all app CI/deploy, zero orchestration. | Either extend scope to the 5 orchestration workflows, or restate the objective as a partial, phase-1 clean-up and name the follow-up. |
| 2 | **Blocker** | Dev | `Merge & Backlog` (`merge.yml`) is **`active`** on the remote, not disabled. It runs on the legacy `review:approved` / `type:subtask` taxonomy — the exact label confusion cited as this plan's motivation. | `gh workflow list --all` → `Merge & Backlog  active  340912648` (Architect / Dev & Test / PR Review / Three Amigos are `disabled_manually`). | Disable or delete `merge.yml` as part of this change. Note it now co-exists with `devtest.auto_merge_approved: true` in the orchestrator config — two merge authorities. |
| 3 | Major | Dev | `.github/workflows/prompts/` duplicates the prompt corpus being deleted. Removing `.claude/tasks/` and `.antigravity/tasks/` leaves an identical 7-file prompt set in the repo, so the context pollution the plan targets survives. | `.github/workflows/prompts/`: `architect-answer-clarifications.md`, `architect-decompose.md`, `architect-restructure.md`, `dev-test-fixup.md`, `dev-test-implement.md`, `pr-review.md`, `three-amigos.md`. | Add `.github/workflows/prompts/` to the deletion set (paired with finding 1). |
| 4 | Major | QA | Drawback 3's stated mechanism is wrong. The runner **auto-discovers** tests by glob; it never enumerates script paths, so deleting `run-architect.ps1` alone cannot produce "file-not-found errors" *from the runner*. | `scripts/tests/Invoke-ScriptTests.ps1:18` — `Get-ChildItem -Path $TestDir -Filter "*.Tests.ps1"`. | Conclusion (delete concurrently) stands; correct the reasoning. Before deleting, confirm the `ArchitectWorkflow.*` tests actually *fail* on a missing script rather than passing vacuously — if they pass vacuously, they were never load-bearing, which is itself worth recording. |
| 5 | Major | QA | The Gherkin acceptance criteria under-specify the suite: they name 5 remaining suites, but `scripts/tests/` contains **14** test files. A green run of the 5 named suites is not evidence of a green suite. | Unlisted: `CrossTrainingAppCli`, `InvokeScriptTests.Selftest`, `LocalTestingDocumentation.Static`, `ReleaseWorkflow.Static`, `SummarizeUnitTests.Markdown`, `SummarizeUnitTests.Static`. | Replace the enumerated list with the runner's own contract: exit code 0 **and** `FailCount = 0` across all discovered files, plus an assertion that the discovered-file count dropped by exactly 3. |
| 6 | Major | Business | Recency conflict, unacknowledged. The three `ArchitectWorkflow.*.Tests.ps1` suites this plan deletes were added **in the current unreleased cycle**. Deleting them days later without stating what changed reads as churn and will not survive PO review. | `CHANGELOG.md:36,80,81` — added under `[Unreleased]` via #407–#411. | Add one paragraph stating the decision that superseded #407–#411 and its date, and reflect the reversal in the `[Unreleased]` entry rather than silently adding a removal line beneath the addition. |
| 7 | Major | Dev | No rollback or base-branch statement. Every action is an irreversible deletion, and the working copy is currently on `feat/issue-473`, not `main`. Subtask 4 does not say what `feat/deprecate-legacy-local-pipeline` branches from. | `git rev-parse --abbrev-ref HEAD` → `feat/issue-473`. | Specify: branch from a clean `origin/main`; recovery path is `git revert` of a single squashed deletion commit — which requires all deletions to land in *one* commit, not four subtasks' worth. |
| 8 | Minor | Dev | `.gitignore` keeps a rule for a directory that will no longer exist. | `.gitignore:45` — `logs/local-pipeline/`. | Remove the line in Subtask 1. |
| 9 | Minor | Dev | Adjacent, pre-existing: the orchestrator declares context files for this project that do not exist, so two of three context injections are silently empty. | `~/.orchestrator/config.yaml:169-172` lists `.graph/testing-standards.md` and `.graph/git-workflow.md`; `crosstrainingapp/.graph/` contains only `architecture.md` (same gap in `biq-app`). | Out of scope for this plan, but file a separate issue — it undercuts the "harmonized context" rationale. |
| 10 | Non-issue | Dev | Iteration 2's Drawback 1 mitigation **confirmed**: the orchestrator CLI genuinely exposes the replacement entry point, and `crosstrainingapp` is a registered, enabled project. | `orchestrator/cli.py:91-108` defines `run` with `--project/-p` and `--node/-n`; `~/.orchestrator/config.yaml:165-167`. | None. |
| 11 | Non-issue | Dev | Deletion targets all exist and have no inbound references from app code, Gradle, or CI. `docs/ARCHITECT-WORKFLOW-VERIFICATION.md` is referenced only by itself. | Repo-wide grep for `local-pipeline\|ARCHITECT-WORKFLOW-VERIFICATION\|ArchitectWorkflow\|test-architect-workflow` returns matches only inside the files being deleted, plus `CHANGELOG.md`, `.gitignore:45`, and a comment at `.github/workflows/three-amigos.yml:187`. | None. |

### Concerns & drawbacks

#### Concern A: The plan solves half a problem and claims the whole one
The motivating defect is stated as *context pollution and label confusion for AI
agents*. An agent reading `crosstrainingapp` after this plan lands still finds
five orchestration workflows describing a `type:user-story` → `status:review` →
`review:approved` lifecycle, and seven prompt files instructing it to operate
that lifecycle. The PowerShell wrappers were never the only source of the
confusion — they were the half that happened to be easiest to spot.
**Verdict: the objective as written is unachievable within the current scope.**

#### Concern B: "Everything is disabled" was asserted, not verified end-to-end
Iteration 1 justified deletion on "Windows scheduled tasks `CTA-*` are
permanently disabled". That is a statement about *one* of three executors this
repo has historically had. The GitHub-side executor was not checked, and one
of its workflows is live. The general failure mode — reasoning about
deprecation from one executor's state — is worth fixing in the method, not just
in this document.
**Verdict: re-run the disabled-check across all three executors before merging.**

#### Concern C: Deleting tests is being treated as bookkeeping
Findings 4 and 6 combine badly: three test suites added in the current cycle are
being deleted on the assumption they would otherwise fail, and nobody has run
them against the post-deletion tree to confirm that. If they in fact pass
vacuously, the correct record is "these tests never asserted anything about a
live system", which changes how much confidence #407–#411 should have bought.
**Verdict: run the suite once with the scripts deleted and the tests retained,
capture the output, and cite it in the PR.**

### Open questions for the author

1. Is the deprecation of the GitHub Actions orchestration workflows in scope
   here, a deliberate phase 2, or an intentional decision to keep them? The
   answer changes findings 1, 2 and 3 and the story's acceptance criteria.
2. What decision superseded #407–#411, and on what date?
3. Should `merge.yml` be disabled or deleted — and does anything still depend on
   its parent-story-closing behaviour, or has the orchestrator absorbed that?

### Unverified claims

- **"All Windows Scheduled Tasks for the local pipeline (`CTA-*`, `DT-*`) have
  already been permanently disabled."** Not re-verified in this pass; only the
  GitHub-side executor state was checked (`gh workflow list --all`). Confirm with
  `Get-ScheduledTask -TaskName 'CTA-*','DT-*'` before merging.
- **`biq-app-native` and `biq-playbook` rows in Iteration 2's comparison table.**
  Neither repository was located in the local workspace during this pass; only
  `biq-app` was inspected directly. The table's claims for the other two remain
  single-sourced from Iteration 2.
