# Workspace Guidelines & Agent Protocols

## 🤖 Default Agent Model
Across all tasks, skills (`/user-story-refining`, `/quick-fix`), and node executions:
- **Default Model:** **Gemini 3.8 Flash (High)** (`gemini-3.8-flash-high`)
- Provides highest speed, deep architectural reasoning, and robust tool-calling accuracy.

## Direct Fix Shortcut (`/quick-fix`)
When the user prefixes their instruction with `/quick-fix` or explicitly requests a direct fix on `main`:
1. **Bypass the standard multi-step lifecycle** (no User Story decomposition, no feature branch, no remote PR gate).
2. **Implement directly on branch `main`**.
3. **Execute local test suite** (`pytest -v`) to confirm 100% passing tests.
4. **Update `CHANGELOG.md`** under `## [Unreleased]`.
5. **Commit and Push directly to `origin/main`** using `Set-GhToken-Antares.ps1`.

## User Story Refining Protocol (`/refine-story`, `/user-story-refining`, `--boost`)
When the user prefixes their instruction with `/refine-story`, `/user-story-refining`, `/refine-story --boost`, `/boost`, or asks to refine/review a draft specification:
1. **Maintain Living Audit Trail in `docs/draft-requisites/implementation-plan.md`:** Never overwrite previous iterations. Preserve the initial plan, append each new review iteration (`## 🔍 Review Iteration N`), incorporate operator feedback iterations (`## 💬 Review Iteration N`), incorporate multi-perspective boost evaluations (`## 🚀 Boost Review Iteration N`), and maintain the consolidated `## 🎯 Final Decision Plan & User Story Specification` at the bottom.
2. **Inspect Ground Truth Codebase:** View live schemas, models, and classes in `orchestrator/` before evaluating.
3. **Point-by-Point Critical Verdict Matrix:** Scrutinize every proposal point for data duplication, class redundancy, backward compatibility, and anti-patterns.
4. **Boost Mode Deep Evaluation (When `--boost` or `/boost` is Triggered):**
   - **360° Multi-Perspective Swarm:** Execute 4 deep analytical lenses (Architecture & Schema Integrity, Adversarial QA & Edge Cases, Security & Non-Interactive Subprocess Governance, Product & INVEST BDD).
   - **Sequential State Simulation:** Trace state machine transitions across nominal flows, 3-retry transient budgets, and terminal failure quarantines.
   - **Adversarial Red-Team Critique:** Uncover top failure vectors and mandate concrete architectural safeguards.
5. **Analyze Edge Cases & Resilience:** Evaluate zero-division on idle, cold-start states, UTC normalization, and non-blocking UI execution.
6. **Produce Pristine User Story in Final Decision Plan:** Generate full Gherkin BDD Acceptance Criteria (`Given/When/Then`), architecture diagrams, component impact table, and INVEST subtask breakdown.
7. **Strict Approval Gate:** Withhold approval and do not proceed to implementation or issue creation until the plan is 100% sound, verified, and explicitly approved by the user. Once approved, the Final Decision Plan serves as the source of truth for creating the GitHub Epic story.

## Cross-Architectural Review Protocol (`/cross-review`, `/claude-review`, `/claude-architect-review`)
When the user prefixes their instruction with `/cross-review`, `/claude-review`, or asks to cross-examine an implementation plan between Gemini and Claude:
1. **Single Communication Medium:** All exchanges happen strictly via `docs/draft-requisites/implementation-plan.md`. Never use temporary buffers.
2. **Headless Claude Sonnet Execution:** Invoke Claude CLI (`--model sonnet --effort medium --dangerously-skip-permissions -p`) or run `scripts/cross_review.py`.
3. **Hard 3-Round Cap:** Gemini and Claude debate for a maximum of 3 iterations (`## 🔍 Review Iteration N` and `## 🏛️ Claude Sonnet Review Iteration N`).
4. **Early Exit on Consensus:** If Claude issues `VERDICT: AGREED`, mark the plan as approved and present the consensus plan to the operator.
5. **Operator Escalation Gate (No Agreement after Round 3):** If after 3 rounds disagreement remains, halt execution immediately, append `## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies`, and surface a structured Dispute Matrix to the user highlighting the contested points, risks, and trade-offs for final human decision.

## Antigravity Architect Review Protocol (`/agy-architect-review`, `/agy-review`, `/gemini-architect-review`)
When the user prefixes their instruction with `/agy-architect-review`, `/agy-review`, or asks for an architectural cross-review via Antigravity (`agy`):
1. **Single Communication Medium:** All exchanges happen strictly via `docs/draft-requisites/implementation-plan.md`. Never use temporary buffers.
2. **Tri-Party Review Council:**
   - **Author Agent:** Formulates the implementation proposal and synthesizes revisions in response to council critiques.
   - **Gemini Architect (`gemini-3.8-flash-high`):** Headless execution via `agy` CLI (`--model gemini-3.8-flash-high --dangerously-skip-permissions -p`, using `-c` for Rounds 2 & 3). Scrutinizes technical architecture, concurrency, pipe safety, DB schema integrity, and performance.
   - **Claude QA Guardian (`sonnet`, `effort: low`):** Headless execution via `claude` CLI (`--model sonnet --effort low --dangerously-skip-permissions -p`). Acts as the **Requirements & UX/UI Guardian**—enforces 100% fidelity to the operator's original prompt and constraints (anti-drift), audits UX/UI ergonomics (or functional correctness if backend only), and verifies Gherkin BDD testability.
3. **Hard 3-Round Cap:** Council debates for a maximum of 3 iterations (`## 🔍 Review Iteration N`, `## 🏛️ Gemini Architect Review Iteration N`, and `## 🧪 Claude QA Review Iteration N`).
4. **Dual Consensus Gate:** Approval requires **both** Gemini Architect AND Claude QA to issue `VERDICT: AGREED`. If either party objects, the plan is not approved and the author must address their feedback.
5. **Operator Escalation Gate (No Agreement after Round 3):** If after 3 rounds disagreement remains, halt execution, append `## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies`, and present a consolidated Dispute Matrix to the operator.

## Upstream Functional Slicing & Direct DevTest Assignment Protocol
When pre-refining requirements using `/refine-story` or `/agy-architect-review`:
1. **Zero-Token Runtime Architect Bypass:** Pre-refined stories bypass runtime Architect Node triage (`needs-triage`) completely, saving 100% of runtime triage/decomposition LLM tokens.
2. **Pattern Selection:**
   - **Pattern A (Standalone Task, $\le 300$ LOC, $\le 4$ files):** Create a single issue with label `ready-for-dev` (no parent, no child). DevTest executes directly via Fallback 1 and closes the issue upon merge.
   - **Pattern B (Decomposed Feature, $> 300$ LOC):**
     - Create a Parent Feature Issue labeled `architect-processed` with a `- [ ] #<child_id>` checklist in the body.
     - Post an immediate comment on the Parent issue linking all child issue numbers (`Child issues: #101, #102`).
     - Create Child Slice 1 labeled `ready-for-dev` with `Parent: #<parent_id>` in the body.
     - Create Child Slices 2..N labeled `queued` with `Parent: #<parent_id>` in the body.
     - DevTest executes Slice 1, advances the parent checklist, promotes Slice 2 to `ready-for-dev`, and marks the parent `dev-implemented` upon completion.
3. **Strict Sizing Boundary:** Every functional slice must deliver a vertical capability and touch $\le 4$ files with $\le 300$ estimated LOC diff. Slices exceeding this boundary must be sub-sliced before issue provisioning.
4. **Single Active Feature Invariant:** Provision only **one active feature parent story** (`architect-processed`) per project at a time. Backlog feature stories remain held without `architect-processed` until the active feature merges and closes.






