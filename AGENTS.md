# Workspace Guidelines & Agent Protocols

## 🤖 Default Agent Model
Across all tasks, skills (`/user-story-refining`, `/quick-fix`), and node executions:
- **Default Model:** **Gemini 3.8 Flash (High)** (`gemini-3.8-flash-high`)
- Provides highest speed, deep architectural reasoning, and robust tool-calling accuracy.

## 🌐 Global Skills: Single Source of Truth
The shared SDLC skills (`agy-architect-review`, `architect-council-review`, `claude-architect-review`, `provision-story`, `quick-fix`, `user-story-refining`) live **only** in `graph-engineering/skills/`. They are exposed to every project through mirrored directories in `$HOME\.gemini\config\plugins\swarm-dev-core\skills` (recreate / sync with `scripts/link-global-skills.ps1`). Note: Storing them under `skills/` instead of `.agents/skills/` prevents duplicate discovery inside `graph-engineering` while real mirrored directories in the global plugin make them available across all projects.
- **Never copy these skills (or `.agents/skills`) into another project repo.** A copy silently forks the skill and stops receiving fixes.
- Invoke skill helper scripts through the global path (e.g. `python "$HOME\.gemini\config\plugins\swarm-dev-core\skills\agy-architect-review\scripts\agy_cross_review.py"`) from the target project's root, never via a project-relative `.agents/skills/...` path.
- Project-specific skills (e.g. `darwin-trader`'s `trading-committee`) stay in their own repo's `.agents/skills/`.

## Direct Fix Shortcut (`/quick-fix`)
When the user prefixes their instruction with `/quick-fix` or explicitly requests a direct fix on `main`:
1. **Bypass the standard multi-step lifecycle** (no User Story decomposition, no feature branch, no remote PR gate).
2. **Implement directly on branch `main`**.
3. **Execute local test suite** (`pytest -v`) to confirm 100% passing tests.
4. **Update `CHANGELOG.md`** under `## [Unreleased]`.
5. **Commit and Push directly to `origin/main`** using `Set-GhToken-Antares.ps1`.

## User Story Refining Protocol (`/refine-story`, `/user-story-refining`, `--boost`)
When the user prefixes their instruction with `/refine-story`, `/user-story-refining`, `/refine-story --boost`, `/boost`, or asks to refine/review a draft specification:
1. **Maintain Living Audit Trail in the plan file:** The plan file is whatever file the operator names for the run (`--plan`, `--path`, `--file`, or a path in the request text) and is refined **in place**; a raw requirement file is converted into the plan itself. The default `docs/draft-requisites/implementation-plan.md` is used only when no file is named and must never be touched while another plan file is in use. It is passed as `--plan <path>` (e.g. `/refine-story --plan docs/specs/feature.md`), defaulting to `docs/draft-requisites/implementation-plan.md`; use it for every read, append, archive, and provisioning step (`story provision --file <path>`). Never overwrite previous iterations. Preserve the initial plan, append each new 3-Amigos review iteration (`## 🔍 3-Amigos Review Iteration N`), Functional Architect review iterations (`## 🏛️ Functional Architect Review Iteration M`), incorporate operator feedback iterations (`## 💬 Review Iteration N`), incorporate multi-perspective boost evaluations (`## 🚀 Boost Review Iteration N`), and maintain exactly **one** consolidated `## 🎯 Final Decision Plan & User Story Specification` at the bottom — replace it in place when revising (it is the only mutable section) instead of appending another full copy.
   - **One active plan per live file:** Before starting a new feature plan, archive the finished one with `python "$HOME\.gemini\config\plugins\swarm-dev-core\skills\agy-architect-review\scripts\agy_cross_review.py" --plan "<path>" --archive` (moves it verbatim to an `archive/` folder next to the plan file). The audit trail lives in the archive; the live file must only hold the active plan so reviewers do not re-read finished features.
2. **Inspect Ground Truth Codebase:** View live schemas, models, and classes in `orchestrator/` before evaluating.
3. **Point-by-Point Critical Verdict Matrix:** Scrutinize every proposal point for data duplication, class redundancy, backward compatibility, and anti-patterns.
4. **Boost Mode Deep Evaluation (When `--boost` or `/boost` is Triggered):**
   - **360° Multi-Perspective Swarm:** Execute 4 deep analytical lenses (Architecture & Schema Integrity, Adversarial QA & Edge Cases, Security & Non-Interactive Subprocess Governance, Product & INVEST BDD).
   - **Sequential State Simulation:** Trace state machine transitions across nominal flows, 3-retry transient budgets, and terminal failure quarantines.
   - **Adversarial Red-Team Critique:** Uncover top failure vectors and mandate concrete architectural safeguards.
5. **Analyze Edge Cases & Resilience:** Evaluate zero-division on idle, cold-start states, UTC normalization, and non-blocking UI execution.
6. **Tier 1: 3-Amigos Review Cycle (Max 3 Iterations):** The 3-Amigos (Product, Software Architect, QA Guardian) refine technical feasibility, schemas, and BDD scenarios until agreement is achieved or Round 3 is completed.
7. **Tier 2: Functional Architect Validation Gate (Max 3 FA Iterations):**
   - The converged plan is evaluated by the **Functional Architect** to verify: *Does the current solution actually fix the root problem or not?*
   - If changes are required, the Functional Architect proposes concrete modifications and sends the plan back to the 3-Amigos for a fresh 3-iteration cycle.
   - If after 3 Functional Architect review iterations agreement is still not reached, the Functional Architect exercises executive authority and **unilaterally provides the definitive solution** in the Final Decision Plan.
8. **Produce Pristine User Story in Final Decision Plan:** Generate full Gherkin BDD Acceptance Criteria (`Given/When/Then`), architecture diagrams, component impact table, resolution type (Consensus vs FA Executive Determination), and INVEST subtask breakdown.
9. **Strict Approval Gate:** Withhold approval and do not proceed to implementation or issue creation until the plan is 100% sound, verified, and explicitly approved by the user. Once approved, the Final Decision Plan serves as the source of truth for creating the GitHub Epic story.

## Cross-Architectural Review Protocol (`/cross-review`, `/claude-review`, `/claude-architect-review`)
When the user prefixes their instruction with `/cross-review`, `/claude-review`, or asks to cross-examine an implementation plan between Gemini and Claude:
1. **Single Communication Medium:** All exchanges happen strictly via `docs/draft-requisites/implementation-plan.md`. Never use temporary buffers.
2. **Headless Claude Sonnet Execution:** Invoke Claude CLI (`--model sonnet --effort medium --dangerously-skip-permissions -p`) or run `python "$HOME\.gemini\config\plugins\swarm-dev-core\skills\claude-architect-review\scripts\cross_review.py"`.
3. **Hard 3-Round Cap:** Gemini and Claude debate for a maximum of 3 iterations (`## 🔍 Review Iteration N` and `## 🏛️ Claude Sonnet Review Iteration N`).
4. **Early Exit on Consensus:** If Claude issues `VERDICT: AGREED`, mark the plan as approved and present the consensus plan to the operator.
5. **Operator Escalation Gate (No Agreement after Round 3):** If after 3 rounds disagreement remains, halt execution immediately, append `## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies`, and surface a structured Dispute Matrix to the user highlighting the contested points, risks, and trade-offs for final human decision.

## Antigravity Architect Review Protocol (`/agy-architect-review`, `/agy-review`, `/gemini-architect-review`)
When the user prefixes their instruction with `/agy-architect-review` (optionally with `--plan <path>`), `/agy-review`, or asks for an architectural cross-review via Antigravity (`agy`):
1. **Single Communication Medium:** All exchanges happen strictly via the plan file: whatever file the operator names (`--plan`, `--path`, `--file`, or a path in the request text), defaulting to `docs/draft-requisites/implementation-plan.md` only when none is named. Pass it to the helper script as `--plan` and check the JSON `plan_path`; never touch the default file while another plan file is in use, and never run `--archive` during a review. Never use temporary buffers. Completed plans are archived to an `archive/` folder next to the plan file so the live file only holds the active plan.
2. **Two-Tier Review Council Architecture:**
   - **Tier 1: Tri-Party Working Council:**
     - **Author Agent:** Formulates the implementation proposal and synthesizes revisions in response to council critiques.
     - **System Architect (Gemini 3.8 Flash High, `gemini-3.8-flash-high`):** Headless execution via `agy` CLI (`-p --model gemini-3.8-flash-high --dangerously-skip-permissions`, never `-c`). Scrutinizes technical architecture, concurrency, pipe safety, DB schema integrity, and performance.
     - **QA Guardian (Gemini 3.8 Flash Medium, `gemini-3.8-flash-medium`):** Headless execution via `agy` CLI (`-p --model gemini-3.8-flash-medium --dangerously-skip-permissions`, never `-c`). Acts as the **Requirements & UX/UI Guardian**—enforces 100% fidelity to the operator's original prompt and constraints (anti-drift), audits UX/UI ergonomics (or functional correctness if backend only), and verifies Gherkin BDD testability.
   - **Tier 2: Chief Functional Architect Validation Gate:**
     - **Chief Functional Architect (Claude Opus 5.5, `claude-opus-5-5`, `effort: high`; `max` on the final executive iteration):** Headless execution via `claude` CLI (`-p --model claude-opus-5-5 --effort high --tools "Read,Grep,Glob" --strict-mcp-config --output-format json --no-session-persistence --dangerously-skip-permissions`, prompt through stdin; the final executive iteration uses `--effort max` and adds `Edit`). Scrutinizes the proposal in the *most critical way possible*:
       1. Verifies whether the solution genuinely fixes the initial problem and root causes.
       2. Verifies whether the design strictly follows industry standards and best practices.
3. **Token Discipline:** Every round is a fresh session (never resume with `-c`/`-r`; the plan file already carries the history). Reviewers read only the line ranges the helper script gives them (original proposal, current Final Decision Plan, latest author iteration, their own previous review) and inspect only the codebase files the plan names. claude reviewers get those sections inlined in the prompt and return their review as the reply (the script appends it), with read-only code tools, no MCP servers, no subagents, a timeout, and per-run metrics (`chief_fa_metrics`) in the JSON result.
4. **Cadence & Feedback Loop:** Council debates for up to 3 iterations per cycle (`## 🔍 Review Iteration N`, `## 🏛️ Architect Review Iteration N`, and `## 🧪 QA Review Iteration N`). Every 3 iterations of the council (or upon dual council consensus), the proposal passes to the Chief Functional Architect.
5. **Dual Consensus Gate & Chief FA Review:** Reviewers tag each objection `[BLOCKING]` or `[NON-BLOCKING]`. When the council reaches dual consensus (`VERDICT: AGREED` from both Architect and QA), or completes 3 council rounds, the Chief Functional Architect evaluates the plan. If changes are required, the Chief Functional Architect details actionable amendments and returns the plan to the council for a fresh 3-iteration cycle.
6. **Executive Determination Gate (3-Iteration Chief FA Cap):** The cycle repeats for a maximum of 3 Chief Functional Architect iterations. If after 3 Chief FA iterations no agreement has been reached, the Chief Functional Architect exercises executive authority and **unilaterally decides and provides the final binding solution** in the Final Decision Plan (`[Executive Resolution: Dictated by Chief Functional Architect]`).

## Architect Council Review Protocol (`/architect-council-review`, `/council-review`, `/council`)
When the user prefixes their instruction with `/architect-council-review` (optionally with `--plan <path>` / `--diff-range <range>`), `/council-review` or `/council`, or asks for a fast triage / gating review by the Architect Council:
1. **Council:** **Functional Architect** (`claude` CLI, `claude-opus-5-5`, `--effort high`: problem fidelity, user story completeness, Given-When-Then precision, scope creep). **Technical Lead** (`agy` CLI, `claude-opus-5-5-medium`: feasibility, patterns, minimal change set). **Quality Assurance** (`agy` CLI, `gemini-3.8-flash-high`: regression surface, backwards compatibility, test matrix, contract stability). Models and efforts come from the global config `$HOME\.gemini\config\plugins\swarm-dev-core\config\architect-council-review.json` (created once by `scripts/link-global-skills.ps1` and never overwritten). agy model IDs carry the effort in the name, and agy never gets `--effort`.
2. **Bounded rounds:** Round 1 runs the three members in parallel (Independent Review). Any discrepancy triggers Round 2 (Cross-Rebuttal). Unanimity after either round appends `## ✅ Council Consensus`. Run it with `python "$HOME\.gemini\config\plugins\swarm-dev-core\skills\architect-council-review\scripts\council_review.py" --plan "<PLAN>"` or `orchestrator council --plan "<PLAN>"`.
3. **Deadlock gate:** Disagreement after Round 2 appends `## ⚠️ Council Deadlock Escalation Report` (exit code 2). Show it to the operator verbatim and stop. Only the operator decides: `orchestrator council --continue N` (N more rounds) or `orchestrator council --proceed provision-story` (accept the compromise and hand off to `/provision-story`).
4. **Single medium:** The plan file carries every round, decision and the round counter. Reviewers never edit it. After consensus or `--proceed`, the agent compiles the one `## 🎯 Final Decision Plan & User Story Specification` before provisioning.

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






