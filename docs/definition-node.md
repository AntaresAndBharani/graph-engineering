# Definition Node (Architect)

The only node currently in scope. Turns a requirement into a set of SMART
GitHub sub-issues, ready for the (not-yet-built) Three Amigos readiness gate.
Has two entry points now (2026-08-22 revision) — see below.

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

Mechanically: the PO creates a GitHub issue labeled `draft`, works it up with
Gemini/Antigravity, and when ready relabels it `ready-for-architect`. That
label is the trigger for Architect's headless entry point below. This isn't
a GitHub Actions trigger *to* the PO — there's no automation upstream of
this, it's just a visible backlog convention instead of an ad hoc prompt
each time.

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
2. **Headless entry** (new) — triggered by the `ready-for-architect` label,
   via `anthropics/claude-code-action` with `CLAUDE_CODE_OAUTH_TOKEN`, no
   human present. Assumes the PO already did Requirement Refinement upstream
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

1. **No conflicts** — refine + decompose, create the issues, label the
   parent/epic `ready-for-review`. Normal path, hands off to Three Amigos.
2. **Minor technical adjustment** — Architect makes it directly (e.g.
   tightens an acceptance criterion, notes a specific file/module to touch).
   No escalation; this is what "light refinement" means in practice.
3. **Real conflict or business call** — something the draft didn't account
   for that only the PO can decide (e.g. "this conflicts with how X
   currently works — replace it or coexist?"). Architect does **not**
   guess. It relabels the issue back to `needs-po-input` (not the generic
   `draft` — there's real content already, just one decision needed),
   comments with the specific conflict, and stops. The PO resolves it
   (in Antigravity/Gemini or otherwise) and re-tags `ready-for-architect`
   to re-enter.

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

- One optional **parent/epic issue** capturing the original requirement,
  when the requirement doesn't fit in a single issue.
- One or more **sub-issues**, linked to the parent via GitHub's native
  sub-issue relationship (or a task-list checkbox `- [ ] #123` in the parent
  body if native sub-issues aren't available in the target repo).
- Declare cross-issue dependencies explicitly in the body (`Depends on #NNN`)
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

Once built, the Three Amigos node (Gemini) will not chat freely with
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
   `needs-po-input` mechanism described above, rather than guessing or
   bouncing the question back to Three Amigos unanswered.

This exchange (Three Amigos ↔ Architect specifically, tier 1) is capped at 3
rounds, same circuit breaker as the rest of the pipeline. The Architect ↔ PO
tier (when it escalates further) has no cap, per "Headless technical
refinement" above.

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
gh issue create --title "<title>" --body "<body + acceptance criteria>" --label "<labels>"
```

For a parent/epic issue, create it first, capture its number, then reference
it in each sub-issue body (or use `gh issue create --json` output plus the
repo's native sub-issue linking if available).

## Label taxonomy this node uses

```
draft               PO is drafting with Gemini/Antigravity — not yet Architect's concern
ready-for-architect  PO considers it ready — triggers the headless entry point
needs-po-input       Architect escalated a conflict — PO must resolve, then re-tag ready-for-architect
ready-for-review     Architect finished — hands off to Three Amigos (not built yet)
```

## Out of scope (do not build yet)

Three Amigos validation, the Dev & Test loop, PR review, and merge/backlog
automation are all designed at a high level in the top-level README but are
**not implemented**. Don't wire up any of this — including the headless
entry point described above, or the `gh issue create` calls being scripted
into an unattended workflow — until asked. This doc describes the full
contract Architect commits to; it doesn't mean the headless trigger or the
Three Amigos side are being built now.
