---
name: user-story-refining
description: >-
  Critical 3-Amigos architectural refinement workflow for draft user stories, requirements, and implementation plans. Supports standard refinement and Boost Mode (--boost / /boost) for 360-degree multi-perspective deep analysis, sequential thinking state simulation, and adversarial red-teaming. Maintains a persistent evolutionary audit trail in docs/draft-requisites/implementation-plan.md tracking the initial plan, agent reviews, operator reviews, and the final decision plan. Thoroughly inspects technical implementation, identifies anti-patterns, demands robust BDD acceptance criteria and INVEST decomposition, and withholds approval until the design is pristine, resilient, and well-documented. Trigger with /refine-story, /user-story-refining, /refine-story --boost, or /boost.
---

# User Story Refining Workflow (/refine-story, /user-story-refining, --boost)

Use this workflow whenever the user issues `/refine-story`, `/user-story-refining`, `/refine-story --boost`, `/boost`, or asks to critically review and refine a draft user story, requirement, or implementation plan.

## 🎯 Purpose
Serves as an uncompromising, hyper-critical 3-Amigos and Architectural Review Gate. It maintains a persistent, multi-iteration audit trail inside `docs/draft-requisites/implementation-plan.md`—capturing the initial plan, successive agent reviews, operator reviews, multi-perspective boost analyses, and the consolidated **Final Decision Plan** from which GitHub Epic stories are produced.

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
   - Never overwrite or erase prior review iterations in `docs/draft-requisites/implementation-plan.md`.
   - Append each review as a distinct `## 🔍 Review Iteration N` or `## 🚀 Boost Review Iteration N` section and keep the `## 🎯 Final Decision Plan` updated at the bottom as the single source of truth for GitHub issue creation.

2. **Uncompromising Critical Scrutiny:**
   - Never rubber-stamp or provide passive approval.
   - Be relentlessly skeptical: challenge assumptions, unearth hidden complexity, detect redundant tables/classes, and identify potential regressions.

3. **Ground Truth Codebase Verification:**
   - **Never evaluate in a vacuum.** Always inspect the live codebase (`grep_search`, `view_file`) to check existing SQLite schemas, Pydantic models, and method signatures before agreeing to any proposed changes.

4. **No Premature Approval Gate:**
   - **Strict Rule:** **DO NOT** declare approval or suggest moving to implementation unless every technical detail, edge case, schema migration, and backward-compatibility concern is completely resolved and thoroughly documented.

---

## 📂 Living Document Standard: `docs/draft-requisites/implementation-plan.md`

Every refined user story must be tracked in `docs/draft-requisites/implementation-plan.md` following this structure:

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
1. Locate and view `docs/draft-requisites/implementation-plan.md` (or the draft prompt/issue).
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
3. Append a new section `## 🔍 Review Iteration N` or `## 🚀 Boost Review Iteration N` to `docs/draft-requisites/implementation-plan.md`.

### Step 3: Synthesize or Update the Final Decision Plan
1. Update `## 🎯 Final Decision Plan & User Story Specification` at the bottom of `docs/draft-requisites/implementation-plan.md`.
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

### Step 5: Issue Creation (Post-Approval)
Once the user explicitly approves:
- Create the GitHub Epic Issue using the exact content from `## 🎯 Final Decision Plan & User Story Specification`.
- Apply the `needs-triage` label for Architect node pickup.
