# Definition Node (Architect)

The only node currently in scope. Turns a raw requirement into a set of SMART
GitHub sub-issues, ready for the (not-yet-built) Three Amigos readiness gate.

- **Model:** Claude Opus (highest reasoning tier available).
- **Trigger:** a new feature/requirement from the user, in any repo.
- **Output:** GitHub issues created via `gh issue create`, following the
  schema below.

## Two phases, one node

The Architect isn't just a decomposer — it's the human's point of contact for
*redefining* the requirement, not only splitting it. That's still one node,
not two: same actor (Claude Opus), same conversation, same human. Anthropic's
own multi-agent guidance is explicit that complexity should only be added
when a step demonstrably needs it (a different model, a different exit
condition, independent checkpointing) — none of that applies here, so a
"Product Owner" node was considered and rejected. Instead, the node has two
explicit phases with a hard gate between them:

1. **Requirement Refinement** — open-ended, conversational, human-in-the-loop.
   The Architect asks clarifying questions, pushes back on ambiguity or scope,
   and negotiates the requirement with the human directly (this is a
   human↔agent conversation, not an agent↔agent one — Anthropic's guidance
   against open-ended back-and-forth is specifically about *inter-agent*
   chat; a human checkpoint is the recommended pattern, not the anti-pattern).
2. **SMART Decomposition** — begins only once the human explicitly confirms
   the requirement is defined. This transition must be an explicit signal
   (the human says "go" / "that's right, proceed"), not the model silently
   deciding it has asked enough questions.

State carries this as `requirement_status: "REFINING" | "DEFINED"`. Nothing
downstream (issue creation) runs while it's `"REFINING"`.

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

The Architect step should produce this JSON internally before calling `gh`,
so downstream steps (Three Amigos, once built) can consume it directly:

```json
{
  "requirement_status": "REFINING | DEFINED",
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

`acceptance_criteria` entries feed directly into the Three Amigos gate's
`qa_analysis.bdd_scenarios` check (see the pipeline design in the top-level
README) — write them so each one can become a Given/When/Then test.

## Answering Three Amigos clarification requests

Once built, the Three Amigos node (Gemini) will not chat freely with the
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

The Architect answers each question directly (updating the relevant issue
field), returns the same `issues` schema above, and does **not** re-run the
full Requirement Refinement phase for a targeted clarification — that phase
is for the human, not for resolving a Three Amigos doubt. This exchange is
capped at 3 rounds (same circuit breaker as the rest of the pipeline); if
unresolved after that, it escalates to the human rather than looping.

## Prompt templates

### Requirement Refinement (Phase 1)

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

### SMART Decomposition (Phase 2)

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
Output ONLY valid JSON matching this schema:
{
  "requirement_status": "DEFINED",
  "epic": { "title": "string | null", "body": "string | null" },
  "issues": [
    {
      "title": "string",
      "body": "string",
      "acceptance_criteria": ["string"],
      "labels": ["string"],
      "depends_on": ["string"]
    }
  ]
}
```

## Creating the issues

```bash
gh issue create --title "<title>" --body "<body + acceptance criteria>" --label "<labels>"
```

For a parent/epic issue, create it first, capture its number, then reference
it in each sub-issue body (or use `gh issue create --json` output plus the
repo's native sub-issue linking if available).

## Out of scope (do not build yet)

Three Amigos validation, the Dev & Test loop, PR review, and merge/backlog
automation are all designed at a high level in the top-level README but are
**not implemented**. Don't wire them up — including the `gh issue create`
calls above being scripted into an unattended loop — until asked. The
"Answering Three Amigos clarification requests" section above documents the
*contract* this node will need to honor later; it doesn't mean the Three
Amigos side is being built now.
