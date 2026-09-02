# Workspace Guidelines & Agent Protocols

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



