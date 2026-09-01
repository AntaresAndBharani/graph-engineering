---
name: user-story-refining
description: >-
  Critical 3-Amigos architectural refinement workflow for draft user stories, requirements, and implementation plans. Thoroughly inspects technical implementation, identifies architectural anti-patterns, data duplication, and weak points, demands robust BDD acceptance criteria and INVEST decomposition, and withholds approval until the design is pristine, resilient, and well-documented. Trigger with /refine-story or /user-story-refining.
---

# User Story Refining Workflow (/refine-story, /user-story-refining)

Use this workflow whenever the user issues /refine-story, /user-story-refining, or asks to critically review and refine a draft user story, requirement, or implementation plan.

## 🎯 Purpose
Serves as an uncompromising, hyper-critical 3-Amigos and Architectural Review Gate. It validates draft specifications against the existing codebase, intercepts architectural anti-patterns and data duplication, analyzes failure modes and edge cases, and transforms raw requirements into pristine, INVEST-decomposed User Stories with formal BDD acceptance criteria.

---

## 🛡️ Core Principles & Golden Rules

1. **Uncompromising Critical Scrutiny:**
   - Never rubber-stamp or provide passive approval.
   - Be relentlessly skeptical: challenge assumptions, unearth hidden complexity, detect redundant tables/classes, and identify potential regressions.

2. **Ground Truth Codebase Verification:**
   - **Never evaluate in a vacuum.** Always inspect the live codebase (grep_search, iew_file) to check existing SQLite schemas, Pydantic models, and method signatures before agreeing to any proposed changes.

3. **No Premature Approval Gate:**
   - **Strict Rule:** **DO NOT** declare approval or suggest moving to implementation unless every technical detail, edge case, schema migration, and backward-compatibility concern is completely resolved and thoroughly documented.

---

## 📋 Execution Procedure

### Step 1: Ingestion & Ground Truth Research
1. Locate and view the draft specification (e.g., docs/draft-requisites/implementation-plan.md, GitHub issue description, or user prompt).
2. Cross-reference proposed tables, models, and classes against existing files in orchestrator/ to detect:
   - **Data Duplication:** Existing tables or ledger structures that already capture the requested telemetry.
   - **Class Duplication:** Existing managers or engines that should be extended rather than duplicated.
   - **Breaking Schema Changes:** Proposed config fields that break legacy configurations.

### Step 2: Point-by-Point Critical Assessment
Construct a structured Verdict Matrix evaluating each element of the proposal:

| Proposed Item | Verdict (APPROVE / MODIFY / REJECT) | Critical Architectural Analysis & Justification |
|---|:---:|---|
| **Configuration Schema** | ... | Analyze backward compatibility, defaults, and flexibility across providers. |
| **Database & Persistence** | ... | Analyze schema normalization, SQLite WAL contention, index coverage, and single-source-of-truth. |
| **Core Engine / Business Logic** | ... | Analyze concurrency, zero-division hazards, race conditions, and error recovery. |
| **TUI / Presentation Layer** | ... | Analyze non-blocking event loop execution, in-place keyed diffing, and UX ergonomics. |

### Step 3: Edge Case & Resilience Analysis
Explicitly identify and solve edge cases:
- **Cold Starts:** How does the feature behave on an empty database or fresh environment?
- **Zero / Extreme Values:** Are calculations guarded against ZeroDivisionError, NoneType, or negative durations?
- **Platform Invariants:** Are timestamps strictly UTC-normalized? Are paths cross-platform (Path.expanduser())?
- **Concurrency & UI Freezes:** Are all database queries and external calls non-blocking to the Textual 2.0s refresh loop?

### Step 4: Pristine User Story & Implementation Specification
Produce a polished, production-ready specification containing:
1. **Epic / User Story Formulation:**
   - Clear *As a... I want... So that...* framing.
2. **System Architecture & Data Flow:**
   - Clean ASCII or Mermaid sequence diagram illustrating data lifecycle from source to UI/storage.
3. **Formal BDD Acceptance Criteria (Given / When / Then):**
   - Minimum 4 comprehensive Gherkin scenarios covering primary workflows, countdown/threshold math, edge cases, and alert logging.
4. **Component-by-Component Impact Table:**
   - Explicit file paths and exact modifications required across layers.
5. **INVEST-Compliant Subtask Decomposition:**
   - Granular, independently testable subtasks ready for creation on GitHub.

### Step 5: Gate Enforcement & User Approval
- Highlight open questions, technical trade-offs, and critical decisions.
- Stop and request explicit user confirmation before creating GitHub issues, branches, or code changes.
