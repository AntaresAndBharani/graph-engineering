---
name: user-story-refining
description: >-
  Critical 3-Amigos architectural refinement workflow for draft user stories, requirements, and implementation plans. Supports standard refinement and Boost Mode (--boost / /boost) for 360-degree multi-perspective deep analysis, sequential thinking state simulation, and adversarial red-teaming. Maintains a persistent evolutionary audit trail in the implementation plan file (pass --plan <path>; defaults to docs/draft-requisites/implementation-plan.md) tracking the initial plan, agent reviews, operator reviews, and the final decision plan. Thoroughly inspects technical implementation, identifies anti-patterns, demands robust BDD acceptance criteria and INVEST decomposition, and withholds approval until the design is pristine, resilient, and well-documented. Trigger with /refine-story [--plan <path>], /user-story-refining, /refine-story --boost, or /boost.
---

# User Story Refining Workflow (/refine-story, /user-story-refining, --boost)

Use this workflow whenever the user issues `/refine-story`, `/user-story-refining`, `/refine-story --boost`, `/boost`, or asks to critically review and refine a draft user story, requirement, or implementation plan.

## 🤖 Recommended Model
- **Model:** **Gemini 3.8 Flash (High)** (`gemini-3.8-flash-high`)
- **Profile:** Deep analytical reasoning for 3-Amigos critical review, Boost swarm evaluation, edge-case vulnerability detection, and pristine BDD Gherkin synthesis.

## 🎯 Purpose
Serves as an uncompromising, hyper-critical 3-Amigos and Architectural Review Gate. It maintains a persistent, multi-iteration audit trail inside `<PLAN>`—capturing the initial plan, successive agent reviews, operator reviews, multi-perspective boost analyses, and the consolidated **Final Decision Plan** from which GitHub Epic stories are produced.

---

## 📄 Plan File Argument (`--plan <path>`)
Usage: `/refine-story [--plan <path>] [--boost]` (also `/user-story-refining`, `/boost`). The flags can be combined in any order; `--path` and `--file` are accepted as aliases of `--plan`.
- **`<PLAN>`** in this document means the plan file for the current run: the file the operator named for this run (see *Resolving `<PLAN>`* below; absolute, or relative to the target project's root), or `docs/draft-requisites/implementation-plan.md` when no path is given.
- If `<PLAN>` does not exist yet, create it (including parent folders) and start it with the `# 📋 Implementation Plan` heading from the Living Document Standard below.
- Completed plans are archived to an `archive/` folder **next to `<PLAN>`**.

### Resolving `<PLAN>` (mandatory, before any other step)
1. **Any file the operator names for this run IS `<PLAN>`** — whether passed as `--plan <path>`, `--path <path>`, `--file <path>`, or mentioned in the request text (e.g. "review the requirement in `docs/.../impl_x.md`"). That file is both the input and the output: refine it **in place**.
2. Use the default `docs/draft-requisites/implementation-plan.md` **only when the operator names no file at all**.
3. **Hard rule:** when `<PLAN>` is not the default file, never create, write, append to, or archive `docs/draft-requisites/implementation-plan.md` (or any other plan file). Every helper-script call gets `--plan "<PLAN>"` with exactly that path.
4. **Raw requirement files:** if `<PLAN>` has no `# 📋 Implementation Plan` heading (e.g. it only holds the operator's free-text request), convert it in place: add `# 📋 Implementation Plan: <feature title>` at the top, keep the operator's original text verbatim under `## 📝 Initial Draft Proposal`, then append review iterations and the Final Decision Plan to the same file. Only archive `<PLAN>` when it already holds a *finished* plan and the operator starts a new feature in that same file.
5. State the resolved `<PLAN>` path at the start of your first reply, and confirm the exact file you wrote at the end.

---

## 🚀 Modes of Operation

### 1. Standard Mode (`/refine-story`, `/user-story-refining`)
Executes the rigorous 3-Amigos review: inspecting live codebase ground truth, generating point-by-point verdict matrices, detecting anti-patterns, resolving edge cases, and formulating BDD acceptance criteria with INVEST subtask decomposition.

### 2. Boost Mode (`/refine-story --boost`, `/user-story-refining --boost`, `/boost`)
Activates a deep **360° Multi-Perspective & Adversarial Analysis Swarm**:
- **Lens 1: Architectural & Data Integrity:** Scrutinizes database schemas, CTE query performance, SQLite WAL lock contention, hot-reload safety, and backward compatibility.
- **Lens 2: Adversarial QA & Resilience:** Probes race conditions, zero-token cold-starts, rate-limit replenishment edge cases, timeout hangs, and failure-cascade containment.
- **Lens 3: Security & Governance:** Evaluates non-interactive subprocess security (`GH_PROMPT_DISABLED="1"`), token isolation, and least-privilege worktree sandboxing.
- **Lens 4: Product & INVEST Decomposition:** Enforces strict single-responsibility subtasks with full Given/When/Then Gherkin acceptance criteria.
- **Sequential Thinking State Simulation:** Methodically traces state transitions across normal execution, 3-retry transient failure, and terminal failure quarantine.
- **Adversarial Red-Team Critique:** Identifies the top failure modes of the proposal and mandates concrete structural safeguards.

---

## 🛡️ Core Principles & Golden Rules

1. **Persistent Evolutionary Audit Trail:**
   - Never overwrite or erase prior review iterations in `<PLAN>`.
   - Append each review as a distinct `## 🔍 Review Iteration N` or `## 🚀 Boost Review Iteration N` section and keep the `## 🎯 Final Decision Plan` updated at the bottom as the single source of truth for GitHub issue creation.

2. **Uncompromising Critical Scrutiny:**
   - Never rubber-stamp or provide passive approval.
   - Be relentlessly skeptical: challenge assumptions, unearth hidden complexity, detect redundant tables/classes, and identify potential regressions.

3. **Ground Truth Codebase Verification:**
   - **Never evaluate in a vacuum.** Always inspect the live codebase (`grep_search`, `view_file`) to check existing SQLite schemas, Pydantic models, and method signatures before agreeing to any proposed changes.

4. **No Premature Approval Gate:**
   - **Strict Rule:** **DO NOT** declare approval or suggest moving to implementation unless every technical detail, edge case, schema migration, and backward-compatibility concern is completely resolved and thoroughly documented.

---

## 📂 Living Document Standard: `<PLAN>`

Every refined user story must be tracked in `<PLAN>` following this structure:

```markdown
# 📋 Implementation Plan & Refinement Lifecycle: [Topic / Feature]

## 📝 Initial Draft Proposal
[The original, raw proposal or requisite from the operator/stakeholder]

---

## 🔍 Review Iteration 1: Agent Critical Architectural Review
- **Date / Author:** [YYYY-MM-DD | Agent / Architect]
- **Verdict Matrix:** Table of proposed items with APPROVE / MODIFY / REJECT verdicts and technical rationale.
- **Identified Weak Points & Anti-Patterns:** Specific data duplications, class redundancies, or schema flaws.
- **Edge Cases & Resilience Invariants:** Cold starts, zero-division, concurrency, and UTC normalization.

---

## 🚀 Boost Review Iteration [N]: 360° Multi-Perspective Deep Analysis (When in Boost Mode)
- **Date / Author:** [YYYY-MM-DD | Boost Swarm Architect]
- **Architecture & Data Integrity Lens:** [Schema, CTE efficiency, WAL locks, hot-reload safety]
- **Adversarial QA & Resilience Lens:** [Cold-start, timeout, 3-retry budget, zero-token gating]
- **Security & Subprocess Lens:** [Non-interactive flags, environment isolation, worktree safety]
- **Sequential Thinking State Simulation:** [Step-by-step state transition trace]
- **Adversarial Red-Team Critique:** [Top failure vectors and safeguards]

---

## 💬 Review Iteration [N]: Operator / Stakeholder Feedback
- **Date / Author:** [YYYY-MM-DD | Operator]
- [Feedback, adjustments, new constraints, and clarifications added by the user]

---

## 🎯 Final Decision Plan & User Story Specification
[The consolidated, approved source of truth for GitHub Issue creation]
- **User Story:** As a... I want... So that...
- **Architecture & Data Flow:** Diagram of data lifecycle and components.
- **BDD Acceptance Criteria:** Minimum 4 Given/When/Then Gherkin scenarios.
- **Component Impact Table:** Exact file paths and modifications.
- **INVEST Subtask Breakdown:** Granular, independently testable subtasks.
```

---

## 📋 Execution Procedure

### Step 1: Ingestion & Ground Truth Research
1. Resolve `<PLAN>` and view it (or the draft prompt/issue). If a finished plan is still in it, archive it first with `python "$HOME\.gemini\config\plugins\swarm-dev-core\skills\agy-architect-review\scripts\agy_cross_review.py" --plan "<PLAN>" --archive`.
2. Cross-reference proposed tables, models, and classes against existing files in `orchestrator/` to detect:
   - **Data Duplication:** Existing tables or ledger structures that already capture the requested telemetry.
   - **Class Duplication:** Existing managers or engines that should be extended rather than duplicated.
   - **Breaking Schema Changes:** Proposed config fields that break legacy configurations.

### Step 2: Formulate Critical Review Iteration (Standard or Boost)
1. Evaluate each proposal point against architectural invariants, performance, backward compatibility, and resilience.
2. If **Boost Mode** is active:
   - Execute the **4 Analytical Lenses** (Architecture, Adversarial QA, Security, INVEST).
   - Trace sequential state transitions and simulate edge-case failure loops.
   - Formulate the **Adversarial Red-Team Critique**.
3. Append a new section `## 🔍 Review Iteration N` or `## 🚀 Boost Review Iteration N` to `<PLAN>`.

### Step 3: Synthesize or Update the Final Decision Plan
1. Update `## 🎯 Final Decision Plan & User Story Specification` at the bottom of `<PLAN>`.
2. Ensure it contains:
   - User Story (*As a... I want... So that...*).
   - System Architecture & Data Flow sequence.
   - Full Gherkin BDD Acceptance Criteria (Given / When / Then).
   - Component-by-Component Impact Table.
   - INVEST-compliant subtask breakdown.

### Step 4: Present Review & Enforce Approval Gate
1. Present the critical review and updated Final Decision Plan in your chat response.
2. Highlight any unresolved trade-offs or decisions requiring operator input.
3. **STOP and request explicit operator approval.** Do NOT create GitHub issues, branches, or code modifications until the user explicitly confirms approval of the Final Decision Plan.

### Step 5: Issue Creation (Post-Approval & Direct DevTest Provisioning)
Once the user explicitly approves:
1. **Deterministic CLI Provisioning (`/provision-story`):**
   - Execute deterministic provisioning using the companion skill `/provision-story` or directly via CLI:
     ```powershell
     python -m orchestrator.cli story provision <project_name> --file "<PLAN>"
     ```
   - Pre-flight dry run is available via `--dry-run`.
   - The CLI parses `<PLAN>` (always pass `--file "<PLAN>"`; without it the CLI falls back to the default path), verifies the Single Active Feature Invariant, creates issues via `gh` CLI, posts the triple-redundancy comment, and synchronizes SQLite `state.db` with 0 runtime LLM tokens.
2. **Zero-Token Runtime Architect Bypass:** Pre-refined requirements do NOT use `needs-triage` (saving 100% of runtime Architect LLM tokens).
3. **Provisioning Pattern Selection:**
   - **Pattern A (Standalone Task, $\le 300$ LOC, $\le 4$ files):** Single issue labeled `ready-for-dev` directly (no parent, no child). DevTest executes directly via Fallback 1.
   - **Pattern B (Decomposed Feature Story, $> 300$ LOC):**
     - Parent Feature Issue labeled `architect-processed` with markdown checklist `- [ ] #<child_id>` in the body.
     - Immediate comment on Parent issue listing all child issue numbers (`Child issues: #101, #102`) for search-lag defense.
     - Child Slice 1 labeled `ready-for-dev` with `Parent: #<parent_id>` in the body.
     - Child Slices 2..N labeled `queued` with `Parent: #<parent_id>` in the body.
     - DevTest executes Slice 1, advances the parent checklist via native `_advance_parent_and_unlock_next_subtask`, unlocks Slice 2, and closes the parent upon completion.
4. **Single Active Feature Invariant:** Provision only **one active feature parent story** (`architect-processed`) per project at a time. The CLI fails closed if an active story lock is detected. Backlog feature stories remain deferred or unprovisioned without `architect-processed` until the active feature merges.

