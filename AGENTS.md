# Workspace Guidelines & Agent Protocols

## Direct Fix Shortcut (`/quick-fix`)
When the user prefixes their instruction with `/quick-fix` or explicitly requests a direct fix on `main`:
1. **Bypass the standard multi-step lifecycle** (no User Story decomposition, no feature branch, no remote PR gate).
2. **Implement directly on branch `main`**.
3. **Execute local test suite** (`pytest -v`) to confirm 100% passing tests.
4. **Update `CHANGELOG.md`** under `## [Unreleased]`.
5. **Commit and Push directly to `origin/main`** using `Set-GhToken-Antares.ps1`.

## User Story Refining Protocol (`/refine-story`, `/user-story-refining`)
When the user prefixes their instruction with `/refine-story`, `/user-story-refining`, or asks to refine/review a draft specification:
1. **Inspect Ground Truth Codebase:** View live schemas, models, and classes in `orchestrator/` before evaluating.
2. **Point-by-Point Critical Verdict Matrix:** Scrutinize every proposal point for data duplication, class redundancy, backward compatibility, and anti-patterns.
3. **Analyze Edge Cases & Resilience:** Evaluate zero-division on idle, cold-start states, UTC normalization, and non-blocking UI execution.
4. **Produce Pristine User Story:** Generate full Gherkin BDD Acceptance Criteria (`Given/When/Then`), architecture diagrams, and INVEST subtask breakdown.
5. **Strict Approval Gate:** Withhold approval and do not proceed to implementation or issue creation until the plan is 100% sound, verified, and explicitly approved by the user.

