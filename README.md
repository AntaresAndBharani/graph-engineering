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
| 2 | Three Amigos | Product/Dev/QA readiness gate before coding starts; asks Architect targeted clarification questions when blocked | Gemini 3.7 Flash (High) |
| 3 | Dev & Test | Implement the issue, run local tests, open the PR | Gemini 3.7 Flash (High) |
| 4 | PR Review | Review diff vs. acceptance criteria; exchanges PR comments with Dev until Claude judges it merge-ready | Claude Opus |
| 5 | Merge & Backlog | `gh pr merge` + `gh issue create` for follow-ups | Deterministic ($0) |

Split: **Claude handles definition and review** (nodes 1 & 4, high cost-of-error
points, and the two places a human or another agent needs a decision *from*
Claude). **Gemini handles development and testing** (nodes 2 & 3, high-iteration
work). Node 5 is plain `gh` CLI, no model involved.

Nodes 2 & 3 moved from Gemini 3.1 Pro to **Gemini 3.7 Flash with High thinking
effort** (2026-08-22) specifically to stay on Google AI Studio's free API
tier — see "Claude & Gemini auth / free-tier status" under Cost constraints
below for why, and the caveats that come with it.

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

### Vendor action choice: official actions, not custom-built ones

Considered building a custom GitHub Action for the Gemini nodes instead of
using Google's official one. Verified 2026-08-22 before deciding:

- **`google-github-actions/run-gemini-cli` is not locked to pre-built
  triage/review templates** — it's a lightweight wrapper around the Gemini
  CLI that accepts a fully custom `prompt` input, so the Three Amigos and
  Dev & Test prompt templates in this repo can be used as-is. No need to
  reinvent CLI install/auth handling that Google already maintains.
- **The real gap is output reliability, not flexibility.** The action's
  output is a `summary` field — described as "the *summarized* output," not
  a guaranteed structured-JSON passthrough. This design depends on strict
  JSON (`verdict`, `clarification_questions`, etc.) being parsed
  programmatically downstream, so trusting `summary` risks silent parsing
  failures.
- **Decision:** use the official action for CLI install + auth, but don't
  rely on `summary` for parsing. Have the prompt instruct Gemini to write
  its JSON output to a file in the workspace, then read that file directly
  in the next workflow step — bypasses the action's own summarization
  entirely, keeps auth/install as someone else's maintenance burden.
- **Gemini Code Assist auth is not viable — checked and ruled out**, not
  just unverified: Gemini Code Assist support for the Gemini CLI and IDE
  extensions (Individual, Google AI Pro, and Google AI Ultra tiers) was
  **discontinued 2026-06-18**, with users redirected to Google's Antigravity
  platform instead. Since this predates today, don't spend time on a
  Code-Assist-auth path for `run-gemini-cli` — it's an AI-Studio-API-key (or
  Vertex AI / WIF) decision only. See "Provider quota considerations" below
  for what this means for actual usage limits.

## Cost constraints — GitHub Actions free tier

The user wants this running on GitHub's free tier only. Two separate budgets
matter here and shouldn't be conflated:

1. **GitHub Actions runner minutes** — infrastructure cost, covered in this
   section.
2. **LLM API/subscription usage** (Claude, Gemini) — a separate cost, driven
   by which official vendor action and auth method each node uses. Covered
   in "Claude & Gemini auth / free-tier status" below, since it turned out
   *not* to be free by default and needed a real decision.

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

### Claude & Gemini auth / free-tier status

The official vendor GitHub Actions this pipeline uses
(`anthropics/claude-code-action`, `google-github-actions/run-gemini-cli` —
see Implementation substrate above) call the real APIs, which is a different
cost surface than the $0 GitHub Actions minutes above. Verified 2026-08-22:

**Claude (nodes 1 & 4, PR Review in particular since that's the one running
headless in Actions):** `claude-code-action` supports a
**`CLAUDE_CODE_OAUTH_TOKEN`** (generated locally via `claude setup-token`) —
usage draws from an existing Pro/Max **subscription**, not pay-as-you-go API
billing. Use this, not `ANTHROPIC_API_KEY`, in the workflow config, or every
headless Claude call bills per-token on top of the subscription you're
already paying for.

**Gemini (nodes 2 & 3, Three Amigos and Dev & Test):** the model choice
directly determines the bill. Google AI Studio's free tier covers **Flash
and Flash-Lite only** — as of April 2026, the Pro family (including what
this design originally specified, Gemini 3.1 Pro) was pulled from the free
tier entirely and is now paid-only. That's why nodes 2 & 3 moved to
**Gemini 3.7 Flash, High thinking effort** instead: still a Flash-family
model (free-tier eligible), with meaningfully better reasoning than default
Flash via the effort setting. Caveats, unresolved — verify before relying on
this in production:
- Whether High-effort calls specifically stay under the free tier's rate
  caps (10 RPM / 250K TPM / 1,500 RPD as of this writing) hasn't been
  confirmed from docs — test with a real, billing-disabled API key first.
- High effort costs ~40% more tokens and a median ~10s / p95 ~50s latency to
  first token versus default effort. Not a dollar cost if it stays in the
  free tier, but it does add directly to GitHub Actions wall-clock minutes
  (bounded by the 2,000/month budget above, and by the 3-round iteration
  cap).
- **Billing trap:** enabling billing on a Gemini API project removes free-tier
  eligibility for *everything* in that project, not just the model that
  needed billing — every call becomes billable from token one. Keep this
  pipeline's Gemini API key in its own dedicated, billing-disabled GCP
  project, separate from any project where Pro/paid access might be needed
  for something else.
- **The free tier itself is not a stable guarantee.** Google cut free-tier
  quotas 50–80% in December 2025 and removed Pro models from free tier
  entirely in April 2026 — two real tightenings within about nine months.
  Design for "currently free, historically shrinking," not permanent — the
  existing iteration caps and human-escalation design already limit worst-
  case usage if quotas tighten further, but the model/effort choice above
  should be revisited if AI Studio changes free-tier Flash terms again.

## Provider quota considerations

Not previously accounted for — this is a different axis from both the
GitHub Actions minutes budget and the dollar-cost/free-tier discussion
above. It's about **usage-quota contention and rate limits**, i.e. whether
requests get throttled or rejected, independent of what anything costs.
Verified 2026-08-22:

**Claude — Architect and PR Review share the same Opus quota bucket.**
Anthropic enforces two limits per subscription: a rolling 5-hour window and
a 7-day weekly cap. On Pro, Sonnet and Opus share one pool; on Max plans
they're split into separate Sonnet/Opus buckets — but that split doesn't
help here, because **both Architect (interactive) and PR Review (headless,
via `CLAUDE_CODE_OAUTH_TOKEN`) use Opus**. If both draw on the same
subscription/account, a burst of automated PR reviews can eat into the
quota needed for an interactive Architect session, and vice versa — this is
a real availability risk, not just a cost one. Rough usage estimates: Pro
~30–40 messages/day, Max 5x ~150–200/day, Max 20x ~600–800/day, and an
agentic PR review (reading a diff, multiple tool calls) likely consumes more
than one simple "message" worth of quota. **Open decision, not yet made:**
whether the automation's `CLAUDE_CODE_OAUTH_TOKEN` should come from the same
Claude account used interactively, or a second, dedicated subscription to
isolate the two usage patterns. Revisit once real PR Review volume is known.

**Gemini — the free tier's rate limits are tight enough to hit from normal
pipeline traffic, not just heavy use.** The AI Studio free tier for Flash is
capped at **10 requests/minute**, 250K tokens/minute, 1,500 requests/day. Ten
RPM is easy to exceed if Three Amigos and Dev & Test both fire within the
same minute — e.g. several issues progressing at once, or a fast
fix-up/review cycle. This reinforces (with a concrete reason now, not just
tidiness) the earlier recommendation to add a `concurrency:` group that
serializes this pipeline's runs rather than letting them fire in parallel.

**Not yet resolved for either provider:** what happens on a 429/rate-limit
error — does the action retry with backoff automatically, or does the job
just fail? Unconfirmed from docs for either `claude-code-action` or
`run-gemini-cli`. Treat a hard rate-limit failure the same as an iteration-
cap breach: escalate to the human rather than silently retrying forever or
leaving the issue/PR in an unclear state.

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
