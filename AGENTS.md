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
2. **Headless Claude Opus Execution:** Invoke Claude CLI (`--model opus --effort high --dangerously-skip-permissions -p`) or run `scripts/cross_review.py`.
3. **Hard 3-Round Cap:** Gemini and Claude debate for a maximum of 3 iterations (`## 🔍 Review Iteration N` and `## 🏛️ Claude Opus Review Iteration N`).
4. **Early Exit on Consensus:** If Claude issues `VERDICT: AGREED`, mark the plan as approved and present the consensus plan to the operator.
5. **Operator Escalation Gate (No Agreement after Round 3):** If after 3 rounds disagreement remains, halt execution immediately, append `## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies`, and surface a structured Dispute Matrix to the user highlighting the contested points, risks, and trade-offs for final human decision.




