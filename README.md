# Graph Engineering — Agentic SDLC

Design and configuration for an autonomous software-development-lifecycle (SDLC)
pipeline, modeled as a directed state graph. Source discussion:
`Graph Engineering in AI Development.pdf` (Gemini conversation, 2026-08-22).

## Pipeline

```
[Requirement] -> 1. Architect -> 2. Three Amigos -> 3. Dev & Test -> 4. PR Review -> 5. Merge & Backlog
```

| # | Node | Role | Model tier |
|---|------|------|------------|
| 1 | Architect (**Definition**) | Refine the requirement with the human, then decompose into SMART GitHub sub-issues | Claude Opus |
| 2 | Three Amigos | Product/Dev/QA readiness gate before coding starts; asks Architect targeted clarification questions when blocked | Gemini (Pro) |
| 3 | Dev & Test | Implement the issue, run local tests, open the PR | Gemini (Pro) |
| 4 | PR Review | Review diff vs. acceptance criteria; exchanges PR comments with Dev until Claude judges it merge-ready | Claude Opus |
| 5 | Merge & Backlog | `gh pr merge` + `gh issue create` for follow-ups | Deterministic ($0) |

Split: **Claude handles definition and review** (nodes 1 & 4, high cost-of-error
points, and the two places a human or another agent needs a decision *from*
Claude). **Gemini handles development and testing** (nodes 2 & 3, high-iteration
work). Node 5 is plain `gh` CLI, no model involved.

## Current scope

> **Only node 1 (Architect / Definition) is implemented / being built right
> now.** Nodes 2–5 are now fully *documented* below for review, but still
> **not implemented or configured** — don't wire up automation, scripts, or
> CLI loops for them until asked.

Per-node specs:

- [`docs/definition-node.md`](docs/definition-node.md) — Architect (**in progress**): Requirement Refinement / SMART Decomposition phases, issue schema, prompt templates.
- [`docs/three-amigos-node.md`](docs/three-amigos-node.md) — Three Amigos (defined, not built): readiness gate, `NEEDS_REVISION` vs `NEEDS_CLARIFICATION` routing.
- [`docs/dev-test-node.md`](docs/dev-test-node.md) — Dev & Test (defined, not built): implementation loop, handling PR Reviewer feedback.
- [`docs/pr-review-node.md`](docs/pr-review-node.md) — PR Review (defined, not built): review schema, blocking vs. follow-up split, merge authority.
- [`docs/merge-node.md`](docs/merge-node.md) — Merge & Backlog (defined, not built): deterministic merge + backlog issue creation.

## Inter-agent communication principles

These apply across all five nodes, including the ones not built yet, and are
drawn from Anthropic's published guidance on agent design
([Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents),
[How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)):

- **Add a node only when it demonstrably needs one** — a different model, a
  different exit condition, or independent checkpointing. A different
  *phase* of the same actor's work (e.g. Architect refining a requirement
  before decomposing it) is a sub-flow within a node, not a new node.
- **No open-ended chat between agents.** Human↔agent conversation
  (Architect refining a requirement with the user) is fine and recommended
  as a checkpoint. Agent↔agent exchanges (Three Amigos ↔ Architect,
  PR Reviewer ↔ Dev) must stay structured: specific questions, specific
  fields, bounded rounds — never a freeform back-and-forth.
- **Prefer shared, persistent artifacts over passing message history.** A
  GitHub PR's comment thread is exactly this kind of artifact — durable,
  human-visible, and already checkpointed by GitHub itself. Nodes should
  read/write it directly rather than duplicating the conversation inside the
  orchestrator's own state.
- **Every agent-to-agent loop needs an iteration cap and a human escalation
  path** at the cap — never merge, approve, or silently proceed just because
  retries ran out.
- **Authority for a verdict stays with the node responsible for it.** e.g.
  `review_status` / merge-readiness is set only by the PR Reviewer (Claude);
  the Dev node can push commits and reply in the thread, but never marks its
  own work approved.

## Interaction design for nodes 2 & 4 (documented now, not built)

Captured here so the decision isn't lost before these nodes are implemented:

- **Three Amigos → Architect:** when Three Amigos (Gemini) can't resolve a
  doubt itself, it does not get a live chat channel to Claude. It emits
  targeted `clarification_questions` (issue, field, question) as part of its
  structured output; Architect answers just those fields and returns updated
  issue JSON. See "Answering Three Amigos clarification requests" in
  `docs/definition-node.md` for the schema Architect already commits to.
  Capped at 3 rounds, then escalates to the human.
- **PR Reviewer ↔ Dev:** the exchange happens as real GitHub PR comments
  (`gh pr review --comment` / `gh pr comment`), not an internal message log —
  that thread *is* the state. Dev pushes fixes and can reply in-thread, but
  only the PR Reviewer node (Claude) sets `review_status: APPROVED`. Capped
  the same way, with human escalation at the cap instead of an unbounded
  review/fix loop.

## Shared state schema (consolidated)

Every node reads/writes a subset of this. Listed here once so the full
picture is reviewable without cross-referencing all five docs:

```
raw_requirement          string
requirement_status       REFINING | DEFINED                (Architect)
github_issue_ids         [int]
current_issue_id         int | null
clarification_questions  [{issue, field, question}]         (Three Amigos -> Architect)
branch_name               string
pr_number                 int | null
pr_diff                   string
test_output                string
error_count                int
review_status              APPROVED | CHANGES_REQUESTED | null   (PR Reviewer sets this — no one else)
review_feedback             string
followup_tasks              [string]
iteration_count             int                              (shared circuit breaker, cap 3)
```

## Implementation substrate (decided, not yet built)

Nodes 2–5 are event-triggered GitHub Actions workflows, not scheduled/cron
jobs and not a standalone polling script — GitHub already emits the exact
events this graph's edges correspond to, so there's no need to build or host
a poller:

| Node | Trigger event |
|---|---|
| Three Amigos | `issues: [labeled]` — label set to `ready-for-review` once Architect finishes decomposition |
| Dev & Test (first pass) | `issues: [labeled]` — label set to `ready-for-dev` when Three Amigos returns `READY` |
| PR Review | `pull_request: [opened, synchronize]` |
| Dev & Test (fix-up pass) | `pull_request_review: [submitted]` filtered to `changes_requested` |
| Merge & Backlog | `pull_request_review: [submitted]` filtered to `approved` |

**Architect stays out of this.** Its Requirement Refinement phase is a live
back-and-forth with a human, and a GitHub Actions run can't hold an open
conversation mid-run — it runs to completion per trigger. So Architect
remains an interactive Claude Code session; only its *output* (the finished,
`DEFINED` issues) becomes the artifact the rest of the graph reacts to.

**Known gap:** the Three Amigos → Architect `clarification_questions` loop
has nowhere headless to land, since Architect isn't a workflow. In practice
Three Amigos posts the questions as an issue comment + a `needs-clarification`
label, and it sits until a human (or an interactive Architect session) picks
it up. That keeps Architect human-anchored as designed, but means that one
loop isn't autonomous the way nodes 2–5 are among themselves. Revisit if this
becomes a bottleneck — the fix would be a narrow headless "answer these
specific questions" mode for Architect, distinct from full Requirement
Refinement.

## Cost constraints — GitHub Actions free tier

The user wants this running on GitHub's free tier only. Two separate budgets
matter here and shouldn't be conflated:

1. **GitHub Actions runner minutes** — infrastructure cost, covered below.
2. **LLM API/subscription usage** (Claude, Gemini) — a completely separate
   cost, already covered by the model-tiering discussion elsewhere in this
   repo. Nothing below affects it.

Confirmed current limits for a **GitHub Free** org/account (verified against
GitHub's billing docs, not assumed — this org is on the Free plan):

| | Public repo | Private repo |
|---|---|---|
| Actions minutes | Unlimited, free | 2,000 min/month (Linux) |
| Artifact/cache storage | Unlimited | 500 MB / 10 GB cache |
| Runner OS multiplier | — | Linux 1x, Windows ~1.7x, macOS ~8x |

GitHub bills **wall-clock runner time**, not compute — a job that's mostly
waiting on a Claude/Gemini API response still burns minutes for the entire
wait. For a private target repo, a full issue lifecycle (Three Amigos → Dev
& Test with retries → PR Review with retries → Merge) could plausibly run
15–60 minutes of Actions time depending on retry rounds, putting the ceiling
at roughly 30–130 issues/month before hitting the 2,000-minute cap. Not
unlimited — a real number to watch, not a blocker at current scale.

**Design choices that keep this at $0, in order of impact:**

1. **`ubuntu-latest` only for every node in this pipeline** — never
   `macos-latest`/`windows-latest`. macOS runners burn the 2,000-minute pool
   ~8x faster. If this mobile app's CI ever needs macOS runners for iOS
   builds, keep that pipeline entirely separate from this one so it doesn't
   compete for the same minute budget.
2. **Self-hosted runner, for zero risk regardless of volume** — register an
   existing machine as a GitHub Actions runner and none of its runtime counts
   against the 2,000-minute quota at all, since GitHub isn't providing the
   compute. Strongest lever if the pipeline's usage grows; trade-off is
   owning that machine's uptime/security and installing the `claude`/`gemini`
   CLIs and credentials on it.
3. **The 3-round iteration caps already in this design directly bound
   worst-case minutes per issue** — not originally a cost control, but it
   functions as one.
4. **Keep the Actions spending limit at $0** (Settings → Billing → Spending
   limits — this is the default for Free/Pro). With it at $0, workflows
   simply stop running once free minutes are exhausted until the next
   billing cycle — never a surprise charge.

## Known trade-offs (carried over from the design review)

- Three Amigos (node 2) and Dev/Test (node 3) both run on Gemini — this saves
  cost but means the readiness gate isn't fully decorrelated from the
  implementer. The clarification-question path above gives Three Amigos an
  explicit way to defer to Claude instead of guessing, which mitigates this
  somewhat but doesn't eliminate it.
- CLI orchestration (`claude` / `gemini` invoked headlessly) needs a
  non-interactive permission mode configured, or automated loops will hang on
  the first confirmation prompt.
- Prefer each CLI's structured/JSON output mode over parsing JSON out of
  conversational stdout once this moves to headless automation.
- Human escalation at each loop's iteration cap is now part of the design
  (see above) but not yet implemented anywhere — there's no code, only the
  Definition node's spec, so this is a reminder for whoever builds nodes 2–5.
