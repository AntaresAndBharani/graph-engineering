# Definition Node (Architect)

**Implemented and live-testing (2026-08-23)** —
[`architect.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/architect.yml)
in `crosstrainingapp`, merged via
[PR #54](https://github.com/AntaresAndBharani/crosstrainingapp/pull/54).
See the top-level README's "Implementation lessons from live testing" for
real bugs/prerequisites found so far. This doc is still the design
reference — update it if live testing surfaces a real design change, not
just a code fix.

Turns a requirement into a set of SMART GitHub sub-issues, ready for the
Three Amigos readiness gate (also now implemented — see
`docs/three-amigos-node.md`). Has two entry points (2026-08-22 revision) —
see below.

**Label names below are aligned to the actual target implementation repo
(`AntaresAndBharani/crosstrainingapp`, 2026-08-23), not generic placeholders.**
That repo already has `type:user-story` / `type:subtask` issue types and a
`status:definition` / `status:ready` / `status:in-progress` lifecycle, plus
production-grade `user-story.yml` / `subtask.yml` issue templates — this
design reuses and extends those rather than inventing a parallel taxonomy.
If this pipeline is ever pointed at a different repo, map onto whatever that
repo's existing conventions are the same way, rather than reintroducing these
exact names by default.

- **Model:** Claude Opus (highest reasoning tier available).
- **Output:** GitHub issues created via `gh issue create`, following the
  schema below.

## PO drafting, upstream of this node (not part of this node, but its usual input)

The requirement doesn't have to start as a blank prompt to Architect. The
Product Owner (the user) can draft the User Story first with **Gemini or
Antigravity** — using a subscription that's otherwise idle relative to this
pipeline, since the automated Gemini nodes run on the AI Studio free tier,
not that subscription. Not the `gemini` CLI interactively — that consumer
login path was discontinued 2026-06-18; use Gemini's chat interface or
Antigravity directly.

Mechanically: the PO creates a `type:user-story` issue (using the existing
`user-story.yml` template — it already gets `status:definition` by default),
works it up with Gemini/Antigravity, and when ready relabels it
`status:ready-for-architect`. That label is the trigger for Architect's
headless entry point below. This isn't a GitHub Actions trigger *to* the
PO — there's no automation upstream of this, it's just a visible backlog
convention instead of an ad hoc prompt each time.

## Two entry points, one node

Anthropic's own multi-agent guidance says add complexity only when a step
demonstrably needs it (a different model, a different exit condition,
independent checkpointing). A "Product Owner" node was considered and
rejected for that reason — but the node does now have two distinct ways
work can arrive, because they have genuinely different exit conditions:

1. **Interactive entry** (original design) — the human brings a raw,
   unrefined idea directly to an interactive Claude Code session. Architect
   runs both phases live: **Requirement Refinement** (open-ended,
   conversational — asks clarifying questions, pushes back on ambiguity,
   negotiates scope with the human directly) gated behind an explicit human
   "go", then **SMART Decomposition**. Full detail in "Prompt templates"
   below. Use this when there's no PO/Antigravity draft yet and you want to
   shape the idea with Claude from scratch.
2. **Headless entry** (new) — triggered by the `status:ready-for-architect`
   label, via `anthropics/claude-code-action` with `CLAUDE_CODE_OAUTH_TOKEN`,
   no human present. Assumes the PO already did Requirement Refinement upstream
   (with Gemini/Antigravity). Architect's job here is **not** re-refinement
   from scratch — it's **light technical refinement grounded in the actual
   codebase** (this run needs a real checkout, not just the issue text) plus
   SMART Decomposition. See "Headless technical refinement" below for what
   "light" means and when it escalates instead of proceeding.

State carries which mode produced an issue as `architect_mode: INTERACTIVE |
HEADLESS`, and `requirement_status: REFINING | DEFINED` as before — nothing
downstream (issue creation) runs while it's `REFINING`, which is now only
ever true mid-interactive-session, never in the headless path.

## Headless technical refinement & conflict escalation

The headless entry point isn't pure mechanical decomposition — Architect
reads the repo and can catch things a PO-level draft (written without
codebase context) couldn't have: existing patterns, integration points,
edge cases the current architecture already has opinions about. Three
possible outcomes per run, not two:

1. **No conflicts** — refine + decompose, create the `type:subtask` issues
   (using the `subtask.yml` template's fields — entry-points, acceptance
   criteria, verification commands map directly onto what Architect already
   produces), each labeled `status:review` — **not** the template's own
   default of `status:ready`, since Three Amigos hasn't seen them yet.
   Normal path, hands off to Three Amigos.
2. **Minor technical adjustment** — Architect makes it directly (e.g.
   tightens an acceptance criterion, notes a specific file/module to touch).
   No escalation; this is what "light refinement" means in practice.
3. **Real conflict or business call** — something the draft didn't account
   for that only the PO can decide (e.g. "this conflicts with how X
   currently works — replace it or coexist?"). Architect does **not**
   guess. It relabels the issue (the `type:user-story`, or a `type:subtask`
   if the conflict surfaced during clarification-answering — see "Answering
   Three Amigos clarification requests" below) to `status:needs-po-input`,
   comments with the specific conflict, and stops. The PO resolves it (in
   Antigravity/Gemini or otherwise) and relabels back to
   `status:ready-for-architect` to re-enter — on a `type:user-story` this
   means "redo the refinement/decomposition pass"; on a `type:subtask` it
   means "incorporate my answer into this specific subtask," a lighter
   re-entry than a full redo. Same label, different scope of work,
   depending on which issue type it's applied to.

**No iteration cap on this specific loop.** Every other agent-to-agent loop
in this design (Three Amigos ↔ Architect, PR Reviewer ↔ Dev) is capped at 3
rounds because it could otherwise run away autonomously. This loop can't —
it always terminates in a human (the PO) decision, so there's nothing to
runaway-protect against. Don't apply the same cap here by habit.

## SMART criteria applied to issues

- **Specific** — one clear outcome per issue; no "and also" scope creep.
- **Measurable** — acceptance criteria are concrete and checkable, not vague
  ("handles errors" is not acceptable; "returns 4xx with a typed error body
  when X" is).
- **Achievable** — scoped to roughly one PR. If it isn't, split it.
- **Relevant** — traceable back to the parent requirement; flag anything that
  looks like scope creep instead of quietly including it.
- **Time-bound-ish** — bounded by scope, not a calendar date: an issue should
  have an obvious "done" state, not open-ended follow-on work folded in.

## Issue structure

- The **`type:user-story` issue** (created upstream by the PO, per "PO
  drafting" above) is the parent — Architect doesn't create this, it reads
  it.
- One or more **`type:subtask` issues**, each linked back via the
  `subtask.yml` template's `parent-story` field, and referenced from the
  user story's own `subtasks` checklist field.
- Declare cross-subtask dependencies via the template's `blocked-by` field
  so the Dev node — later — knows what order to work in.

## Output schema

```json
{
  "architect_mode": "INTERACTIVE | HEADLESS",
  "requirement_status": "REFINING | DEFINED",
  "outcome": "PROCEED | PO_ESCALATION",
  "po_escalation": { "issue": "string", "conflict": "string" },
  "epic": {
    "title": "string | null",
    "body": "string | null"
  },
  "issues": [
    {
      "title": "string",
      "body": "string — context, technical approach, links to related code/docs",
      "acceptance_criteria": ["string", "..."],
      "labels": ["string", "..."],
      "depends_on": ["string — title or temp-id of another issue in this batch"]
    }
  ]
}
```

`outcome`/`po_escalation` only apply to the headless entry point — the
interactive path doesn't need them, since a real conflict just becomes part
of the live conversation instead of a structured escalation.

`acceptance_criteria` entries feed directly into the Three Amigos gate's
`qa_analysis.bdd_scenarios` check (see the pipeline design in the top-level
README) — write them so each one can become a Given/When/Then test.

## Answering Three Amigos clarification requests (two-tier)

The Three Amigos node (Gemini) does not chat freely with
Architect — per the agent-communication principles in the top-level README,
inter-agent exchanges stay structured and bounded, not open-ended. When Three
Amigos can't resolve a doubt itself, it routes back here with specific,
targeted questions instead of a generic "revise this":

```json
{
  "clarification_questions": [
    { "issue": "string — which issue title/temp-id", "field": "string — which field is ambiguous", "question": "string" }
  ]
}
```

This is now a **two-tier escalation**, not a straight line to the human:

1. **Architect (headless) tries first.** If it can answer from the issue's
   context and its own repo knowledge (a technical detail, not a business
   call), it does — updates the relevant issue field(s), returns the same
   `issues` schema, and does **not** re-run Requirement Refinement for a
   targeted clarification.
2. **If Architect itself can't resolve it** — the question turns out to be a
   business call, not a technical one — it escalates further using the same
   `status:needs-po-input` mechanism described above, rather than guessing or
   bouncing the question back to Three Amigos unanswered.

Full loop, tier 2 case (2026-08-23 — traced through completely, not just
described):

```
type:subtask, status:review
        v  Three Amigos: NEEDS_CLARIFICATION
status:needs-clarification   (+ clarification_questions posted)
        v  triggers headless Architect (tier 1)
   Architect resolves it from repo knowledge?
        |-- yes --> status:review  (back to Three Amigos, re-check)
        |-- no  --> status:needs-po-input  (+ comment: exactly what needs deciding)
                        v  PO answers in a comment, relabels
                    status:ready-for-architect
                        v  triggers headless Architect again — incorporate the
                           PO's answer into this subtask, not re-decompose it
                    status:review  (back to Three Amigos, re-check)
```

This exchange (Three Amigos ↔ Architect specifically, tier 1) is capped at 3
rounds, same circuit breaker as the rest of the pipeline. The Architect ↔ PO
tier (when it escalates further) has no cap, per "Headless technical
refinement" above. **Once the PO actually answers, the tier-1 iteration
count resets for that thread** — the cap exists to stop autonomous looping,
not to penalize a case where a human already stepped in and resolved it.

## Prompt templates

### Requirement Refinement (Phase 1, interactive entry only)

```
You are acting as the Architect for an autonomous SDLC pipeline, in
Requirement Refinement mode. Your job right now is NOT to write issues — it's
to make sure the requirement is actually well-defined before decomposition
starts.

==============================
RAW REQUIREMENT:
==============================
{{RAW_REQUIREMENT}}
==============================

Ask clarifying questions about anything ambiguous: scope boundaries, target
users, non-functional constraints, edge cases, what's explicitly out of
scope. Push back on requirements that seem too broad for a single body of
work. Do not propose issues yet.

When the human confirms the requirement is ready, respond with
{"requirement_status": "DEFINED"} and move to decomposition.
```

### SMART Decomposition (Phase 2, interactive entry — requirement already refined live)

```
You are acting as the Architect for an autonomous SDLC pipeline. The
requirement below has been confirmed as defined. Decompose it into SMART
GitHub sub-issues.

==============================
DEFINED REQUIREMENT:
==============================
{{REFINED_REQUIREMENT}}
==============================

RULES:
- Each issue must be independently shippable (roughly one PR of work).
- Acceptance criteria must be concrete and testable by an automated test
  runner — no vague criteria.
- Flag anything that looks like scope creep instead of silently including it
  — put it in a separate issue or note it as out of scope.
- Declare dependencies between issues explicitly.

OUTPUT FORMAT:
Output ONLY valid JSON matching the schema in "Output schema" above
(architect_mode: INTERACTIVE, outcome: PROCEED always — conflicts surface in
the live conversation instead of po_escalation).
```

### Technical Refinement + Decomposition (headless entry — PO already drafted)

```
You are acting as the Architect for an autonomous SDLC pipeline, running
headless. A Product Owner has already drafted this User Story with
Gemini/Antigravity — your job is NOT to re-run requirement refinement from
scratch. It's to:

1. Read the repository to understand existing patterns, integration points,
   and any architectural constraints relevant to this US.
2. Refine technical details the PO-level draft couldn't have known, and make
   minor adjustments directly where they're clearly technical, not business,
   calls.
3. If you find a real conflict or a decision only the Product Owner can make
   — do not guess or proceed. Set outcome to PO_ESCALATION with a specific
   po_escalation.conflict describing exactly what needs a PO decision.
4. Otherwise, decompose into SMART GitHub sub-issues per the usual criteria.

==============================
PO-DRAFTED USER STORY (issue #{{ISSUE_NUMBER}}):
==============================
{{ISSUE_BODY}}
==============================

OUTPUT FORMAT:
Output ONLY valid JSON matching the schema in "Output schema" above, with
architect_mode: HEADLESS.
```

## Creating the issues

```bash
gh issue create --title "[Subtask]: <title>" \
  --body "<body, structured to match subtask.yml's fields — parent-story, target-repo, task-description, entry-points, acceptance-criteria, verification, size, complexity, blocked-by>" \
  --label "type:subtask,status:review"
```

Fill in the `subtask.yml` template's fields directly (parent-story,
target-repo, entry-points, acceptance-criteria, verification, size,
complexity, blocked-by) rather than inventing a different body shape — that
template already covers everything Architect needs to produce.

**Link as a real GitHub sub-issue too (2026-08-25).** The `parent-story`
body field is a human-readable reference, not a structural link — every
node downstream that needs to walk the story↔subtask relationship (Three
Amigos, Merge & Backlog, Architect's own restructure-mode discovery) used
to regex-match that text across every open `type:subtask` issue. Replaced
with GitHub's real Sub-issues relationship: immediately after `gh issue
create` for a new subtask, call `POST /repos/{o}/{r}/issues/{story}/sub_issues`
with the new subtask's integer database `id` (**not** the GraphQL node id
`gh issue view --json id` returns — confirmed live, that shape 422s).
The parent-story text field stays in the body too, as a harmless
human-readable fallback — this is additive, not a replacement for it.

## Label taxonomy this node uses

Reuses `crosstrainingapp`'s existing `type:user-story` / `type:subtask` /
`status:definition` labels; adds the rest. Two labels are dual-purpose —
same name, meaning depends on which issue type it's applied to:

```
status:definition        (existing) PO is drafting — not yet Architect's concern
status:ready-for-architect  on type:user-story: PO considers the draft ready, run full refine+decompose
                             on type:subtask: PO answered a needs-po-input escalation, incorporate it
status:needs-po-input    Architect escalated a conflict — PO must resolve, then relabel status:ready-for-architect
status:review            Architect created/updated this subtask — hands off to Three Amigos
```

Full detail on the `type:user-story` / `type:subtask` templates being reused
(and why subtasks get `status:review` instead of the template's own default
`status:ready`) is in the top-level README's "Label taxonomy" section.

## Out of scope (do not build yet)

Both entry points described above (interactive and headless) **are
implemented** — see the "Implemented and live-testing" note at the top of
this doc. What's still out of scope: the Dev & Test loop, PR review, and
merge/backlog automation, all designed at a high level in the top-level
README but **not implemented**. Don't wire those up until asked.
