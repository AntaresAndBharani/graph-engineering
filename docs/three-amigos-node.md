# Three Amigos Node (Readiness Gate)

**Not implemented — definition only**, per the current-scope note in the
top-level README. Documented so the full pipeline can be reviewed together.

- **Model:** Gemini 3.7 Flash, **High thinking effort** (changed 2026-08-22
  from Gemini 3.1 Pro — Pro was pulled from Google AI Studio's free tier in
  April 2026 and is now paid-only; see the top-level README's "Claude &
  Gemini auth / free-tier status" for the full trade-off). A readiness gate
  is still a critical quality checkpoint, so plain/default-effort Flash was
  rejected for the same reason as before — it tends to rubber-stamp
  well-formatted but logically flawed issues. High effort is meant to close
  most of that gap while staying in the free-tier-eligible Flash family;
  this hasn't been validated against real issues yet, and the 3-round
  iteration cap + human escalation exist partly as a backstop if it
  rubber-stamps something Pro would have caught.
- **Trigger:** a `type:subtask` issue labeled `status:review` by Architect
  (either entry point, or after resolving a clarification — see
  `docs/definition-node.md`). Label names aligned to the actual target repo
  (`AntaresAndBharani/crosstrainingapp`) — see `docs/definition-node.md`'s
  note on this.
- **Output:** a readiness verdict (JSON, for routing) **and** a human-readable
  comment on the issue (see "Posting a readable summary" below) — the JSON
  alone is not the deliverable.

## Evaluation framework

Three independent perspectives, evaluated together:

1. **Product** — is the business intent and outcome clear? Is there scope
   creep or unbounded edge cases that should be split into another issue?
2. **Developer** — are the technical touchpoints, dependencies, and failure
   modes (network, auth, state) addressed?
3. **QA** — are acceptance criteria deterministic and testable? Are negative
   paths and boundary conditions explicit? Formulate Given/When/Then
   scenarios from them.

## Output schema

```json
{
  "product_analysis": { "scope_verdict": "CLEAR | NEEDS_SPLIT | AMBIGUOUS", "notes": "string" },
  "developer_analysis": { "technical_risks": ["string"], "missing_technical_details": ["string"] },
  "qa_analysis": { "is_testable": true, "bdd_scenarios": ["Given ... When ... Then ..."], "unhandled_edge_cases": ["string"] },
  "verdict": "READY | NEEDS_REVISION | NEEDS_CLARIFICATION",
  "clarification_questions": [
    { "issue": "string — issue title/temp-id", "field": "string — which field is ambiguous", "question": "string" }
  ],
  "architect_feedback": "string — only for NEEDS_REVISION: consolidated actionable instructions"
}
```

Two distinct failure paths, not one — this is the refinement from the
2026-08-22 design review:

- **`NEEDS_REVISION`** — the issue itself is incomplete or wrong (missing
  acceptance criteria, unbounded scope, wrong approach). Routes back to the
  Architect's **SMART Decomposition** phase for a full rework of that issue.
- **`NEEDS_CLARIFICATION`** — the issue is basically sound but has one or a
  few specific ambiguous points. Routes back to the Architect with the
  `clarification_questions` array — a targeted exchange, not a rework. This
  is a structured hand-off, not a live chat channel: no back-and-forth
  conversation, just question in → answer out, matching the
  "no open-ended agent-to-agent dialogue" rule in the top-level README. The
  Architect's contract for answering these is documented in
  `docs/definition-node.md` under "Answering Three Amigos clarification
  requests" — **two-tier as of 2026-08-22**: Architect (headless) tries to
  answer from its own repo knowledge first, and only escalates further to
  the PO (`status:needs-po-input`) if the question turns out to be a business
  call it can't make on its own. Three Amigos doesn't need to know which
  tier resolved it — it just gets `status:review` again, eventually, and
  re-checks.

## Posting a readable summary (2026-08-22 — not optional)

The `verdict` JSON drives routing; it is not what the PO reads. Since `READY`
now lands on the PO approval gate (see "Routing" below), the PO is reading
the actual GitHub issue to decide whether to say go — so before or when
applying `status:awaiting-approval`, Three Amigos must also post a comment
synthesizing `product_analysis`, `developer_analysis`, and `qa_analysis`
into plain language: what was checked, what the BDD scenarios are, anything
worth the PO's attention even though it didn't block readiness. Same for
`NEEDS_REVISION` / `NEEDS_CLARIFICATION` — the feedback/questions should be
visible as a comment on the issue, not only passed as JSON to Architect,
so there's a readable audit trail of why the issue bounced.

## Routing

```
READY               -> status:awaiting-approval -> PO relabels status:ready (reusing the EXISTING
                        label crosstrainingapp's Antigravity Developer/Tester setup already
                        picks up) -> Dev & Test, once the PO says go
NEEDS_REVISION       -> status:needs-revision -> Architect (SMART Decomposition)
NEEDS_CLARIFICATION  -> status:needs-clarification -> Architect (headless, may further
                        escalate to status:needs-po-input) -> status:review -> Three Amigos (re-check)
```

`READY` no longer routes straight into Dev & Test (2026-08-22 revision) — it
lands on an explicit human checkpoint instead. The point of this pipeline's
front half is to hand the PO a fully-vetted, ready-to-build issue and wait
for an actual go-ahead before any implementation work starts, not to
auto-continue just because the readiness gate passed. `status:ready` is
deliberately the *existing* label, not a new one — it already means "ready
to be picked up" in this repo, and the PO applying it is what already
triggers the interactive Antigravity Developer/Tester workflow. No new
label needed for that handoff.

The two loop-back paths (`NEEDS_REVISION`, `NEEDS_CLARIFICATION` at the
Three-Amigos-↔-Architect tier specifically) share one `iteration_count` in
state, capped at 3. If still not `READY` after 3 rounds, escalate to the PO
instead of looping further or letting the issue through by default. This cap
does **not** apply to the Architect ↔ PO tier of clarification escalation
(`status:needs-po-input`) — see `docs/definition-node.md` for the full
traced-through loop; that tier always terminates in a human decision so it
doesn't need a runaway-protection cap, and once the PO answers, the tier-1
count resets for that thread rather than carrying a grudge from before the
human stepped in.

## Prompt template

```
You are acting as an autonomous "Three Amigos" review panel (Product Owner,
Software Developer, QA Engineer). Your mission is to rigorously evaluate the
following GitHub issue specification against the Definition of Ready.

==============================
ISSUE UNDER REVIEW:
==============================
Title: {{ISSUE_TITLE}}
Description / Specs:
{{ISSUE_BODY}}

Acceptance Criteria:
{{ISSUE_CRITERIA}}
==============================

EVALUATION FRAMEWORK — evaluate step-by-step from all three perspectives
(Product, Developer, QA — see above).

DECISION RULE:
- If the issue is fundamentally incomplete or misscoped: verdict =
  NEEDS_REVISION, with consolidated architect_feedback.
- If the issue is sound but one or a few specific points are ambiguous:
  verdict = NEEDS_CLARIFICATION, with targeted clarification_questions —
  do NOT ask the Architect to redo the whole issue for a narrow doubt.
- If everything checks out: verdict = READY.

OUTPUT FORMAT:
Output ONLY valid JSON matching the schema in docs/three-amigos-node.md.
```

## Interfaces this node depends on

- Consumes the Architect's issue JSON schema (`docs/definition-node.md`).
- Its `NEEDS_CLARIFICATION` output must match the shape the Architect already
  commits to reading (`docs/definition-node.md` → "Answering Three Amigos
  clarification requests").
- Its `READY` output (specifically the generated `bdd_scenarios`) becomes
  extra context for the Dev & Test node — attach it to the issue/branch
  context rather than re-deriving test scenarios there.
