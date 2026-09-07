---
name: agy-architect-review
description: >-
  Collaborative cross-architectural debate and consensus review between Claude and Gemini Architect (gemini-3.8-flash-high) using the Antigravity (agy) CLI. Iterates up to a maximum of 3 rounds exclusively via docs/draft-requisites/implementation-plan.md, unearthing technical drawbacks, architectural hazards, and edge cases. If consensus is reached, the plan is marked approved. If after 3 rounds disagreement remains, execution halts and surfaces the exact points of contention to the operator. Trigger with /agy-architect-review, /agy-review, or /gemini-architect-review.
---

# Antigravity (Gemini Flash High) Architect Cross-Review Workflow (/agy-architect-review)

Use this workflow whenever the user explicitly issues `/agy-architect-review`, `/agy-review`, `/gemini-architect-review`, or asks for an architectural cross-review of an implementation plan using the Antigravity (`agy`) CLI powered by **`gemini-3.8-flash-high`**.

---

## 🎯 Purpose & Core Value

This skill orchestrates a rigorous, bi-directional architectural dialogue mediated between the authoring agent and **Gemini Architect (`gemini-3.8-flash-high`)** via the `agy` CLI to pressure-test, critique, and align on an implementation plan.

Key invariants:
1. **Single Medium of Truth:** All communication happens **exclusively** through `docs/draft-requisites/implementation-plan.md` in the target project workspace. Neither agent communicates through ephemeral chat buffers; all feedback is permanently audited.
2. **Autonomous Headless Antigravity Execution & Session Continuity:** Gemini Architect is invoked via `agy` CLI with `--model gemini-3.8-flash-high --dangerously-skip-permissions -p`. Across iterative debate rounds (Rounds 2 & 3), the review runner resumes the session using `-c`, preserving previous conversational memory and token cache rather than re-indexing from scratch on each round.
3. **Gemini Model Invariant:** In `gemini-3.8-flash-high`, reasoning effort is built into the model identity itself, so `--effort` is omitted.
4. **Hard 3-Round Cap:** The debate cannot exceed 3 rounds.
5. **Guaranteed Operator Escalation:** If after 3 rounds agreement is not achieved (`VERDICT: AGREED`), execution halts immediately and the skill surfaces a structured dispute matrix of unresolved points directly to the human operator.

---

## 🔄 Lifecycle & State Machine

```mermaid
stateDiagram-v2
    [*] --> CheckPlan: Trigger /agy-architect-review
    CheckPlan --> InitialReview: docs/draft-requisites/implementation-plan.md exists
    InitialReview --> AppendInitial: Authoring agent evaluates codebase & appends Iteration N
    AppendInitial --> InvokeGemini: Invoke Gemini Architect via agy CLI
    InvokeGemini --> GeminiCritique: Gemini inspects codebase & appends Iteration N
    GeminiCritique --> CheckVerdict: Read Gemini Verdict
    
    CheckVerdict --> ConsensusApproved: VERDICT is AGREED
    ConsensusApproved --> [*]: Final Decision Plan approved & ready
    
    CheckVerdict --> CheckRounds: VERDICT is DISAGREED
    CheckRounds --> InitialReview: Round < 3 (Authoring agent addresses concerns)
    CheckRounds --> EscalateOperator: Round == 3 (Cap reached)
    EscalateOperator --> [*]: Surface unresolved points matrix to Operator
```

---

## 🛠️ Step-by-Step Execution Protocol

### Step 1: Target Plan Verification
Ensure the target project has an existing implementation plan:
```powershell
<project_root>/docs/draft-requisites/implementation-plan.md
```
If the file does not exist, prompt the user or run `/refine-story` first to establish the initial proposal.

### Step 2: Pre-Review / Counter-Proposal (Round N)
Before or between Gemini invocations:
1. Inspect the live codebase (`grep_search`, `view_file`) to verify ground truth.
2. Append the architectural perspective to `docs/draft-requisites/implementation-plan.md`:
   ```markdown
   ## 🔍 Review Iteration N (Author Perspective)
   ### 1. Ground Truth Codebase Inspection
   ### 2. Architectural Trade-offs & Proposals
   ### 3. Edge Cases & Resilience Strategy
   ```

### Step 3: Headless Antigravity (Gemini 3.8 Flash High) Execution
Invoke Gemini Architect non-interactively using the provided helper script or direct CLI command.

#### Option A: Via Python Helper Script (Recommended)
```powershell
python <skill_path>/scripts/agy_cross_review.py --model gemini-3.8-flash-high --max-rounds 3
```
The helper script automatically:
- Resolves the local `docs/draft-requisites/implementation-plan.md`.
- Formulates the architectural critique prompt.
- Executes `agy -p ... --model gemini-3.8-flash-high --dangerously-skip-permissions` (resuming via `-c` for Rounds 2 & 3).
- Parses Gemini's appended section and verdict.
- Emits structured JSON summary.

#### Option B: Direct Shell Invocation
```powershell
$prompt = @"
You are the Principal Architect conducting Round N of an unsparing, hyper-critical Architectural Review of 'docs/draft-requisites/implementation-plan.md'.
1. Read the plan and inspect the live codebase using your Read/Grep/Bash tools.
2. Scrutinize all drawbacks, architectural hazards, performance bottlenecks, schema locking, and backward-compatibility risks.
3. Append a new section to 'docs/draft-requisites/implementation-plan.md' titled:
## 🏛️ Gemini Architect Review Iteration N
Must conclude with either:
VERDICT: AGREED (only if 100% sound with zero unresolved drawbacks) or VERDICT: DISAGREED.
4. Output a concise 3-5 bullet point summary to stdout.
"@

# Round 1:
agy -p $prompt --model gemini-3.8-flash-high --dangerously-skip-permissions

# Rounds 2 & 3:
agy -c -p $prompt --model gemini-3.8-flash-high --dangerously-skip-permissions
```

### Step 4: Verdict Analysis & Convergence Check
Read the updated `docs/draft-requisites/implementation-plan.md`.

#### Case 1: Consensus Reached (`VERDICT: AGREED`)
- Update `## 🎯 Final Decision Plan & User Story Specification` incorporating all refined insights and agreed safeguards.
- Mark status: **APPROVED BY ARCHITECT CONSENSUS**.
- Report completion and present the approved Final Decision Plan to the user.

#### Case 2: Disagreement & Round < 3
- Analyze objections under `## 🏛️ Gemini Architect Review Iteration N`.
- Identify whether concessions, architectural refactoring, or code-grounded explanations are needed.
- Increment round count ($N \to N+1$).
- Append `## 🔍 Review Iteration N+1 (Response to Gemini Architect)` addressing every objection.
- Return to **Step 3** to re-invoke Gemini Architect for Round $N+1$.

#### Case 3: Cap Reached (Round == 3 with Disagreement)
- **DO NOT INVOKE GEMINI AGAIN.**
- Append escalation marker in `docs/draft-requisites/implementation-plan.md`:
  ```markdown
  ## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies (Round 3 Cap Reached)
  ```
- Extract the exact points of divergence and present an **Operator Dispute Matrix** directly in the chat.

---

## 🚨 Operator Escalation Format (When Round 3 Has No Consensus)

When Round 3 finishes without consensus, output the following structured briefing directly to the operator:

```markdown
### ⚠️ Cross-Review Round 3 Escalation: Unresolved Architectural Disagreements

The authoring agent and Gemini Architect (gemini-3.8-flash-high) have completed 3 iterative debate rounds via `implementation-plan.md` without reaching 100% consensus. As per protocol, execution has halted to request your architectural decision.

#### 📊 Points of Contention Matrix
| Contested Item | Author Stance & Rationale | Gemini Architect Stance & Rationale | Risk / Trade-Off |
| :--- | :--- | :--- | :--- |
| **1. [Topic A]** | ... | ... | ... |
| **2. [Topic B]** | ... | ... | ... |

#### 🎯 Action Required from Operator
Please select how you wish to proceed:
- **Option 1:** Adopt the Author proposal for [Topic A] and [Topic B].
- **Option 2:** Adopt Gemini Architect's proposal for [Topic A] and [Topic B].
- **Option 3:** Provide specific compromise or custom guidance.
```

---

## 📋 Document Section Naming Standards

In `docs/draft-requisites/implementation-plan.md`, sections MUST strictly use these headers:
- Initial plan: `## 📋 Initial Implementation Proposal`
- Author reviews: `## 🔍 Review Iteration N (Author Perspective)`
- Gemini Architect reviews: `## 🏛️ Gemini Architect Review Iteration N`
- Final decision: `## 🎯 Final Decision Plan & User Story Specification`
- Escalation: `## ⚠️ Escalation to Operator: Unresolved Architectural Discrepancies`
