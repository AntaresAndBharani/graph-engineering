# Backlog Triage Node

**Executor cut over to local CLI (2026-08-26)** — the Antigravity IDE
scheduled task described below is now disabled; this node runs via
`scripts/local-pipeline/run-backlog-triage.ps1` + Windows Task Scheduler
instead, on the same `0 */6 * * *` cadence. See
`docs/local-cli-pipeline.md` for the executor mechanics (the Fetch →
Judge → Act split, session handling, the PowerShell native-process bugs
found and fixed along the way). This node's own responsibilities, output
contract, and the "every issue lands in exactly one cluster" guarantee
below are unchanged — only *who runs the logic* changed, not what it does.

**Implemented and live (2026-08-25)** —
[`backlog-triage.md`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.antigravity/tasks/backlog-triage.md)
in `crosstrainingapp`, an Antigravity scheduled task. A **new node**, not an
extension of Merge & Backlog (despite the name overlap in "backlog") or of
Architect — it has its own trigger shape (a poll, since no GitHub event
exists for "N backlog issues have accumulated"), its own responsibility
(clustering + synthesis, a judgment call), and its own doc for the same
reason every other node does.

**Generalized from Tech-Debt Triage the same day it shipped.** Built first
as a `tech-debt`-only task; before it had even completed its first poll
cycle, the PO asked for `enhancement` issues handled the same way, in the
same scheduled task. Renamed `tech-debt-triage.md` → `backlog-triage.md`
and generalized the procedure to run once per label — see "Multiple label
categories, never mixed" below.

- **Model:** Gemini 3.7 Flash, High thinking effort — same tier as Three
  Amigos, same class of task: batch judgment over a set of issues, not
  implementation.
- **Trigger:** poll, `0 */6 * * *` (every 6 hours) — deliberately slower
  than Dev & Test's `*/15`. Backlog grooming isn't latency-sensitive the
  way implementation work is; tighten later if the backlog grows faster
  than expected.
- **Output:** zero or more new `type:user-story` issues per label (labeled
  `status:ready-for-architect` directly), and every source issue absorbed
  into one of them closed with a comment linking to it.
- **No local checkout needed.** Pure `gh issue` reads/writes — no file
  edits, no git, no test running. Same as Three Amigos, and for the same
  reason it needs no coordination with Dev & Test's working-tree usage
  (see `docs/antigravity-scheduled-tasks.md`'s "Concurrency" section).

## Why this exists

PR Review's `followup_backlog_issues` step (see `docs/pr-review-node.md`)
files a real GitHub issue for every non-blocking observation it makes
during review, labeled whatever the LLM judged fit — `tech-debt` for
hygiene/hardening items, `enhancement` for genuine improvements that
weren't worth blocking the PR on. Nothing else in the pipeline ever
touches either kind: every other node gates on `type:user-story` or
`type:subtask`, and a bare `tech-debt`/`enhancement` issue has neither, so
both are structurally invisible to the automation. "Merge & Backlog" has
"Backlog" in its name but never processed these — confirmed by reading
`merge.yml`, it only merges PRs and closes finished parent stories.

Before this node existed, the only resolution path was manual: the PO
hand-picks related issues and folds them into a story. Real precedent —
issues #64 and #66 were closed 2026-08-24 with "Closed as absorbed and
consolidated into parent story #63." This node automates exactly that
pattern, on its own schedule, rather than waiting for a human to notice
the pile — first for `tech-debt`, then generalized to `enhancement` the
same day.

## Multiple label categories, never mixed

The task runs its full procedure (list → cluster → synthesize → close)
independently for each label in its list (currently `tech-debt`,
`enhancement`). **A cluster or story never spans two labels, even when
issues from different labels are about the exact same file or feature** —
confirmed explicitly by the PO when asked. `tech-debt` and `enhancement`
are different kinds of work (hygiene/hardening vs. new capability); mixing
them would blur what the resulting story is actually asking an implementer
to do. In practice this can mean two separate stories touching overlapping
code (e.g. a `tech-debt` "harden this script" story and an `enhancement`
"add a feature to this script" story existing side by side) — accepted as
the cost of keeping each story single-category.

Adding a third label later means adding it to this task's label list and
nothing else — the procedure itself doesn't change per label.

## Responsibilities (per label, run independently)

1. List the open backlog for that label.
2. Cluster it by theme — shared file/script, shared root cause, shared
   category of concern — within that label only. Every issue lands in
   exactly one cluster this run; a cluster can be a single issue.
   **Confirmed by the PO explicitly:** a lone issue with no obvious
   cluster-mate still gets its own solo story immediately, rather than
   waiting indefinitely for company that may never arrive.
3. For each cluster, synthesize one `type:user-story` issue following the
   `user-story.yml` template's field structure — framed honestly for
   what it actually is (engineering-hygiene cleanup for `tech-debt`, a
   genuine improvement for `enhancement`), never a fabricated product
   story with invented business impact/OKRs. The body explicitly lists
   every source issue number and which label it came from. Also labeled
   `origin:backlog-triage` (2026-08-25) — originally just for origin
   tracking, now also read by Architect (`docs/definition-node.md`) to
   pick Sonnet 5 over Opus for this story, a cost decision. Don't drop
   this label even for a single-issue solo-cluster story.
4. Close each absorbed issue with a comment naming the new story, using
   the same wording the PO's own manual precedent used. The original
   label stays on the closed issue — still searchable by label after
   close, matching #64/#66.

## Routing

```
tech-debt issues (open)     --(clustered by theme)-->  new type:user-story,
enhancement issues (open)   --(clustered separately)-->  status:ready-for-architect
        |
        v (each absorbed issue)
      closed, "absorbed into #N" comment
```

The new story enters the normal pipeline exactly like a PO-drafted one —
Architect picks it up on the next `issues: labeled` event with
`status:ready-for-architect`, no special-casing needed downstream. This
node is a **side input into Architect**, not a sixth step in the main
1→5 chain.

## `status:ready-for-architect` directly — no PO definition pass

**Confirmed by the PO explicitly**, same reasoning as the 2026-08-25
removal of the Three Amigos → Dev & Test approval gate: each source issue
already came from a real PR Review pass with concrete, specific detail (a
`title`/`body`/`suggested_fix` triple), so the synthesized story doesn't
need a PO definition pass before Architect can usefully decompose it —
unlike a raw, unrefined idea, which does need that pass. If this turns out
to produce poorly-scoped stories in practice, revisit this specific choice
rather than assuming it applies to every future automated-story-creation
case the same way.

## Idempotent by construction

Same pattern as every other node in this pipeline: absorbed issues are
closed immediately, so the next poll's `--state open` query naturally
excludes them. No separate dedup bookkeeping, no timestamp tracking, no
"already processed" marker needed — the state transition itself (open →
closed) is the idempotency mechanism. Holds independently per label, since
each label's pass only ever looks at its own open backlog.

## Why a new node instead of extending Merge & Backlog or Architect

- **Not Merge & Backlog:** that node's actual job is `gh pr merge` plus
  closing a finished parent story — both triggered by real PR/issue
  events. This node has no event to react to; it has to poll. Folding a
  poll-based clustering job into an event-triggered merge job would give
  it two unrelated trigger shapes for no shared benefit.
- **Not Architect:** Architect's job is decomposing an already-defined
  story into subtasks — it doesn't create stories from raw backlog. This
  node's output (a synthesized story) is Architect's *input*, upstream of
  it, not a mode of it.
- **Not a modification of PR Review**, even though PR Review is what files
  the source issues — PR Review's job ends the moment it creates a
  `tech-debt`/`enhancement` issue; grouping a backlog of them together
  later, across many separate PR Review runs, is a different task with a
  different trigger shape entirely.

## Verified live, first run (2026-08-25)

Before the `enhancement` generalization even landed, the `tech-debt`-only
version completed a full real cycle unattended: created 4 stories (#106,
#107, #108, #112) from the 12 open `tech-debt` issues, Architect picked
each one up automatically via `status:ready-for-architect` and decomposed
it into subtasks (now sitting at `status:review`, waiting on Three
Amigos), and separately, story #63 (already in flight, unrelated to this
node) closed itself correctly with `status:done` once its last subtask
merged — confirming the whole `status:ready-for-architect` →
Architect → Three Amigos chain, and the Merge & Backlog close-out logic,
both work end-to-end with no PO step anywhere in between.

## Out of scope (do not build yet)

Re-clustering or re-titling a backlog issue that's already been absorbed
into a story but where the story later gets split/restructured by
Architect — Architect's existing restructure mode already handles subtask-
level splits/merges once a story is in flight; this node's job ends the
moment the story is created and the source issues are closed.
