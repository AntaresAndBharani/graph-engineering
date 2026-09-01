---
name: user-story-refining
description: >-
  Critical 3-Amigos architectural refinement workflow for draft user stories, requirements, and implementation plans. Maintains a persistent evolutionary audit trail in docs/draft-requisites/implementation-plan.md tracking the initial plan, agent reviews, operator reviews, and the final decision plan. Thoroughly inspects technical implementation, identifies anti-patterns, demands robust BDD acceptance criteria and INVEST decomposition, and withholds approval until the design is pristine, resilient, and well-documented. Trigger with /refine-story or /user-story-refining.
---

# User Story Refining Workflow (/refine-story, /user-story-refining)

Use this workflow whenever the user issues /refine-story, /user-story-refining, or asks to critically review and refine a draft user story, requirement, or implementation plan.

## 🎯 Purpose
Serves as an uncompromising, hyper-critical 3-Amigos and Architectural Review Gate. It maintains a persistent, multi-iteration audit trail inside docs/draft-requisites/implementation-plan.md—capturing the initial plan, successive agent reviews, operator reviews, and the consolidated **Final Decision Plan** from which GitHub Epic stories are produced.

---

## 🛡️ Core Principles & Golden Rules

1. **Persistent Evolutionary Audit Trail:**
   - Never overwrite or erase prior review iterations in docs/draft-requisites/implementation-plan.md.
   - Append each review as a distinct ## 🔍 Review Iteration N section and keep the ## 🎯 Final Decision Plan updated at the bottom as the single source of truth for GitHub issue creation.

2. **Uncompromising Critical Scrutiny:**
   - Never rubber-stamp or provide passive approval.
   - Be relentlessly skeptical: challenge assumptions, unearth hidden complexity, detect redundant tables/classes, and identify potential regressions.

3. **Ground Truth Codebase Verification:**
   - **Never evaluate in a vacuum.** Always inspect the live codebase (grep_search, iew_file) to check existing SQLite schemas, Pydantic models, and method signatures before agreeing to any proposed changes.

4. **No Premature Approval Gate:**
   - **Strict Rule:** **DO NOT** declare approval or suggest moving to implementation unless every technical detail, edge case, schema migration, and backward-compatibility concern is completely resolved and thoroughly documented.

---

## 📂 Living Document Standard: docs/draft-requisites/implementation-plan.md

Every refined user story must be tracked in docs/draft-requisites/implementation-plan.md following this structure:

`markdown
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

## 💬 Review Iteration 2: Operator / Stakeholder Feedback
- **Date / Author:** [YYYY-MM-DD | Operator]
- [Feedback, adjustments, new constraints, and clarifications added by the user]

---

## 🔍 Review Iteration N: Subsequent Refinement
- **Date / Author:** [YYYY-MM-DD | Agent / Operator]
- [Subsequent evaluations resolving open questions]

---

## 🎯 Final Decision Plan & User Story Specification
[The consolidated, approved source of truth for GitHub Issue creation]
- **User Story:** As a... I want... So that...
- **Architecture & Data Flow:** Diagram of data lifecycle and components.
- **BDD Acceptance Criteria:** Minimum 4 Given/When/Then Gherkin scenarios.
- **Component Impact Table:** Exact file paths and modifications.
- **INVEST Subtask Breakdown:** Granular, independently testable subtasks.
`

---

## 📋 Execution Procedure

### Step 1: Ingestion & Ground Truth Research
1. Locate and view docs/draft-requisites/implementation-plan.md (or the draft prompt/issue).
2. Cross-reference proposed tables, models, and classes against existing files in orchestrator/ to detect:
   - **Data Duplication:** Existing tables or ledger structures that already capture the requested telemetry.
   - **Class Duplication:** Existing managers or engines that should be extended rather than duplicated.
   - **Breaking Schema Changes:** Proposed config fields that break legacy configurations.

### Step 2: Formulate Critical Review Iteration
1. Evaluate each proposal point against architectural invariants, performance, backward compatibility, and resilience.
2. Determine the iteration index (e.g., Iteration 1, Iteration 2, Iteration 3).
3. Append a new section ## 🔍 Review Iteration N: [Title] to docs/draft-requisites/implementation-plan.md with:
   - **Point-by-Point Verdict Matrix** (APPROVE / MODIFY / REJECT).
   - **Critical Weak Points & Anti-Patterns**.
   - **Resilience & Edge Case Solutions**.

### Step 3: Synthesize or Update the Final Decision Plan
1. Update ## 🎯 Final Decision Plan & User Story Specification at the bottom of docs/draft-requisites/implementation-plan.md.
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
- Create the GitHub Epic Issue using the exact content from ## 🎯 Final Decision Plan & User Story Specification.
- Apply the 
eeds-triage label for Architect node pickup.
