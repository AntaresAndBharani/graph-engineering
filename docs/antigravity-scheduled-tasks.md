# Antigravity Scheduled Tasks (alternate executor for Three Amigos & Dev & Test)

**Added 2026-08-24**, same day the GitHub Actions `dev-test.yml` run first hit
Gemini's free-tier daily quota exhaustion (see the top-level README's
"Claude & Gemini auth / free-tier status"). Rather than pay for OpenAI API
access or rebuild these two nodes around a different vendor, the PO chose to
keep both existing implementations **and** add a second executor: Antigravity
IDE's own "Schedule Tasks" feature, which polls GitHub on a cron interval and
runs a plain natural-language prompt against a local repo checkout — no
workflow YAML, no `gemini_api_key` secret, no free-tier RPD ceiling shared
across every node.

This file documents the Antigravity side only. The nodes themselves —
responsibilities, output schema, routing, iteration caps — are unchanged and
still fully specified in `docs/three-amigos-node.md` and
`docs/dev-test-node.md`. What's new here is *who runs the node's logic*, not
what the node does.

## Prompts live in files, not inline (2026-08-24 — found via live testing)

The first version of this doc put the full instructions directly in each
task's Prompt field in the Antigravity UI. Live testing showed the field
silently truncates long input mid-sentence — the scheduled Dev & Test run
only ever received "This task covers both first-implementation and fix-up
work for" and nothing else, and (correctly) stopped to ask for the rest of
the prompt rather than guess. No error, no warning — just a cut string.

Fix: the same pattern already used by the GitHub Actions prompts
(`.github/workflows/prompts/*.md` in `crosstrainingapp`, referenced from a
short inline prompt in the workflow YAML). Each Antigravity task's Prompt
field is now just a one-line pointer; the actual instructions live in
`.antigravity/tasks/*.md` in `crosstrainingapp`, committed alongside the
GitHub Actions prompt files. This sidesteps the field's length limit
entirely regardless of its exact value, and keeps the real instructions in
version control where they show up in diffs like everything else in this
pipeline.

**Also split Dev & Test into two tasks** (Implement / Fix-up) instead of one
combined prompt — shorter per task, and matches its two distinct GitHub
Actions trigger modes (`issues: labeled` vs `pull_request_review:
submitted`) rather than asking one prompt to branch between them itself.

## Second bug found via live testing: the local checkout wasn't synced

The very next run of `Dev & Test: Fix-up`, after the file-pointer fix above,
didn't read `dev-test-fixup-scheduled.md` either — its local `crosstrainingapp`
checkout didn't have the commit that added `.antigravity/tasks/` yet. Rather
than fail when the referenced file wasn't there, the agent invented plausible
content for all three task files itself and staged them as a new 3-file diff
(`+62 -0` — i.e. genuinely new to that checkout, not modified). That diff is
a guess at the spec, not the spec — it must not be approved/merged; discard
it and let the checkout sync properly instead.

Fix, attempt 1: put an explicit `git fetch origin && git reset --hard
origin/main` in the inline prompt, before referencing the instructions
file. This is what actually surfaced the real size of the Prompt field's
limit — see the next section.

## Third bug found via live testing: the Prompt field's real limit is ~60-70 characters, not "long"

The attempt-1 fix above was itself truncated — live testing showed the
prompt cut off at "First run: git checkout main && git fetch origin && git
reset --hard" (68 characters), one word short of `origin/main`. The very
first truncation (previous section) had cut at "This task covers both
first-implementation and fix-up work for" (60 characters). Two independent
data points in the same 60-70 character range — this field's actual limit
is far smaller than "keep the prompt reasonably short" suggested; it can't
fit both a sync command and a file reference no matter how tightly worded.

The agent partially executed the truncated command (the sync succeeded —
`git reset --hard` with no target defaults to `HEAD`, a no-op-equivalent
sync-to-nothing that happened to still leave the checkout on `origin/main`
from a manual reset moments earlier) and then stopped, since the rest of
the prompt — the part telling it what to actually do — never arrived.

**Fix, final: move the sync step out of the inline prompt entirely.** It's
now step 0 inside each instructions file itself (see `.antigravity/tasks/`
in `crosstrainingapp`) — self-healing on every run once the file is found,
regardless of how stale the checkout was. The inline Prompt field is
reduced to the bare minimum: `Run <path>`, nothing else. Filenames were
also shortened (`three-amigos-scheduled.md` → `three-amigos.md`, etc.) to
keep every inline prompt well under the ~60 character danger zone with
margin to spare, since the exact limit still isn't confirmed precisely —
only bounded to roughly that range by these two failures.

This does mean the very first run against a stale/empty checkout could
still fail to find the file and fall back to inventing one, exactly as
before — that risk is now down to a single occurrence per checkout, not a
recurring one, since every successful read from then on re-syncs itself
before doing anything else.

## Concurrency: one story in flight at a time, no lock needed (2026-08-24, simplified same day)

Raised before it ever caused a real failure — worth naming as prevention,
not a postmortem. All three tasks share one local checkout, so the concern
was two runs mutating that same working tree at once.

**First attempt: a dedicated lock issue.** Built a tracking issue
(`crosstrainingapp#61`) whose body held `Status: locked/unlocked` plus an
owner and timestamp, checked by every task before touching git, with a
60-minute staleness window against a crashed run jamming it forever. It
worked, but added a fourth GitHub artifact with no product meaning sitting
in the same tracker as real stories — confusing on its own terms, and more
mechanism than the actual problem needed.

**Simplified same day, by explicit choice:** process one user story at a
time — slower, but cheaper (fewer concurrent Gemini calls) and easier to
reason about for now. This turns out to remove the lock requirement
entirely rather than just relocate it:

- **Dev & Test: Implement** now checks `gh pr list --state open` before
  doing anything else. If any PR is open — for any story — it stops; no
  new implementation work starts this poll.
- **Dev & Test: Fix-up** only ever acts on a PR that's already open. Since
  Implement refuses to start while one exists, the two are mutually
  exclusive by construction: whenever Fix-up has something to do, Implement
  is guaranteed to skip, and whenever Implement is willing to touch git,
  there's no open PR for Fix-up to act on either. No shared state needed to
  enforce that — it falls out of the one-story-at-a-time rule for free.
- **Three Amigos** never touched the working tree to begin with — pure
  `gh` reads/writes on issues, no file edits, no git. It never needed
  coordinating with the other two; the lock step was removed from it
  entirely rather than simplified.

`crosstrainingapp#61` and the `pipeline:locked` label were closed/deleted
rather than left dangling once the mechanism was gone.

**Known gap, accepted for now:** this doesn't protect against the *same*
task's own poll overlapping itself — e.g. an Implement run still mid-fix
when the next poll fires 30 minutes later, before it's pushed anything.
Not addressed yet; revisit if it actually happens (single-subtask Gemini
passes are expected to finish well inside 30 minutes in practice).

## Both prompts start at the parent story, never at a subtask directly (2026-08-24)

Same principle as the Three Amigos batch redesign the day before
(`docs/three-amigos-node.md` "What changed") — **the parent `type:user-story`
issue controls everything**, extended here to Dev & Test as well. Both
prompts below begin by listing open `type:user-story` issues and only ever
reach a subtask (or a subtask's PR) by discovering it as a child of the story
currently being processed. Neither prompt queries `type:subtask` issues or
open PRs as its starting point. This matters beyond consistency: a subtask
read in isolation is missing the story's overall business intent and
definition-of-done, which the parent-first read grounds every action in —
not just for Three Amigos' structural checks (split/merge/missing coverage),
but for Dev & Test's actual implementation and fix-up work too.

## The `status:ready` approval gate lives on the story here, not the subtask (2026-08-24)

Pushed one level further than just discovery order: **the status label that
actually authorizes implementation is checked on the parent
`type:user-story` issue, never on a subtask.** Dev & Test's implementation
poll below queries `type:user-story AND status:ready` — it never checks a
subtask's own labels to decide whether to start work. This means Three
Amigos' Antigravity prompt (Task 1) had to change too: on a `READY` batch
verdict, it now adds `status:awaiting-approval` to the **story** itself (not
only to each subtask, which it still does for per-subtask visibility), so
the PO has one label to flip on the story — `status:awaiting-approval` →
`status:ready` — to authorize the entire batch of subtasks underneath it at
once, rather than relabeling every subtask individually.

**This intentionally diverges from the GitHub Actions executor.**
`dev-test.yml` (see `docs/dev-test-node.md`) still triggers off
`status:ready` on an individual **subtask** — that design is unchanged and
is what's actually live today. The two executors are not meant to run
side by side with different subtasks approved through different labels; per
the toggle above, only one executor is active per node at a time, so this
divergence is safe as long as the PO knows which one is currently active and
labels the right issue accordingly (**the story**, if Antigravity's Dev &
Test task is the active one).

## Two executors, switchable at any time

The GitHub Actions implementations (`three-amigos.yml`, `dev-test.yml`) are
**not being removed**. Both executors read and write the exact same GitHub
state — issue labels, comments, branches, PRs — so `pr-review.yml` and
`merge.yml` downstream have no idea which one ran and need no changes either
way.

To avoid both executors firing for the same event (double implementation,
duplicate reviews), only one should be active at a time per node:

```bash
# Switch a node to Antigravity: disable its GitHub Actions workflow
gh workflow disable three-amigos.yml -R AntaresAndBharani/crosstrainingapp
gh workflow disable dev-test.yml -R AntaresAndBharani/crosstrainingapp

# Switch back to GitHub Actions at any time: re-enable, and stop/pause
# the corresponding Antigravity scheduled task
gh workflow enable three-amigos.yml -R AntaresAndBharani/crosstrainingapp
gh workflow enable dev-test.yml -R AntaresAndBharani/crosstrainingapp
```

`gh workflow disable` suspends automatic triggering without touching the
file — the workflow reappears exactly as it was the moment it's re-enabled.
This is deliberately a one-command, fully reversible toggle so the choice of
executor never becomes a one-way door.

**Unverified assumption, flagged rather than assumed true:** the working
theory is that Antigravity's own usage is billed/limited separately from the
raw Google AI Studio free-tier API key `dev-test.yml`/`three-amigos.yml` use
via `GEMINI_API_KEY` — that's the whole reason this move is expected to help
with the quota problem. This hasn't actually been confirmed against
Antigravity's billing/plan terms; worth checking before leaning on it as the
long-term answer rather than a workaround.

## Known gap: polling has no event to key off of

The GitHub Actions versions are triggered by a specific event
(`issues: labeled`, `pull_request_review: submitted`) — by construction they
run exactly once per event. A scheduled task instead re-scans GitHub state
from scratch every N minutes, so each prompt below has to explicitly avoid
redoing work it already did on a prior poll:

- **Three Amigos** — naturally idempotent. Once a story's `status:review`
  label is removed (by the promotion or escalation step), it stops matching
  the query on the next poll. No extra bookkeeping needed.
- **Dev & Test, implementation work** — naturally idempotent, though the
  mechanism is one level removed since the gate is on the story: a subtask's
  `status:awaiting-approval` is removed the moment it's picked up, so once
  every subtask under a `status:ready` story has moved past that label, the
  story keeps matching the poll but there's nothing left under it to act on.
  `status:ready` is deliberately never removed from the story itself — no
  need, since a story with zero `status:awaiting-approval` subtasks left is
  already a no-op every subsequent poll.
- **Dev & Test, fix-up work — not naturally idempotent.** A PR's review stays
  `CHANGES_REQUESTED` until PR Review (Claude, on GitHub Actions, unchanged)
  re-reviews it on the next `synchronize` event — which won't happen until
  *after* a fix is pushed. Every poll in between would otherwise see the same
  "this story has a subtask PR that needs a fix" state and redo it. The
  prompt below explicitly checks for a commit/comment already posted after
  the triggering review's timestamp before acting, to close this gap.

## Task 1 — Three Amigos

| | |
|---|---|
| Antigravity task name | `Three Amigos (crosstrainingapp)` |
| Repository / folder | `crosstrainingapp` |
| Node replaced | Three Amigos batch review (`docs/three-amigos-node.md`) |
| Schedule | Custom, `*/30 * * * *` (every 30 min — adjust to taste; nothing here is latency-sensitive) |
| Type | Scheduled |

Prompt (paste exactly — this is the whole Prompt field; keep it this short,
see "Third bug" above):

```
Run .antigravity/tasks/three-amigos.md
```

Full instructions: [`three-amigos.md`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.antigravity/tasks/three-amigos.md)
— its own step 0 re-syncs the checkout to `origin/main` before anything
else, so the inline prompt doesn't need to.
Summary: polls `type:user-story` + `status:review` issues, discovers
subtasks only as children of the story being processed, batch-reviews them
as a Product/Developer/QA panel, posts one verdict comment marked
`<!-- three-amigos-verdict -->`, and on `READY` promotes both every subtask
and the story itself to `status:awaiting-approval` — never adding
`status:ready` itself.

## Task 2 — Dev & Test: Implement

| | |
|---|---|
| Antigravity task name | `Dev & Test: Implement (crosstrainingapp)` |
| Repository / folder | `crosstrainingapp` |
| Node replaced | Dev & Test, first-implementation pass (`docs/dev-test-node.md`) |
| Schedule | Custom, `*/30 * * * *` (adjust to taste) |
| Type | Scheduled |

**Runs locally on Windows, not CI** — the instructions file uses the `.bat`
form of the test command (`docs/dev-test-node.md`'s documented local-dev
command), the *opposite* correction from `dev-test.yml`'s Linux CI runner,
which needed the non-`.bat` form. Same underlying gotcha, opposite
direction — worth re-checking any time these instructions are copied
elsewhere.

Prompt (paste exactly — this is the whole Prompt field; keep it this short,
see "Third bug" above):

```
Run .antigravity/tasks/dev-test-implement.md
```

Full instructions: [`dev-test-implement.md`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.antigravity/tasks/dev-test-implement.md)
— its own step 0 re-syncs the checkout to `origin/main` before anything
else, so the inline prompt doesn't need to.
Summary: polls `type:user-story` + `status:ready` issues (the story's own
label, never a subtask's), implements every subtask under it still labeled
`status:awaiting-approval`, runs `.\gradlew.bat testDebugUnitTest` (up to 3
attempts), and on success opens a PR and relabels the subtask
`status:in-progress` — or `status:needs-po-input` on failure/escalation.
Never touches the story's own `status:ready` label.

## Task 3 — Dev & Test: Fix-up

| | |
|---|---|
| Antigravity task name | `Dev & Test: Fix-up (crosstrainingapp)` |
| Repository / folder | `crosstrainingapp` |
| Node replaced | Dev & Test, PR-Review fix-up pass (`docs/dev-test-node.md`) |
| Schedule | Custom, `*/30 * * * *` (adjust to taste) |
| Type | Scheduled |

Same `.bat`-vs-CI note as Task 2 applies here.

Prompt (paste exactly — this is the whole Prompt field; keep it this short,
see "Third bug" above):

```
Run .antigravity/tasks/dev-test-fixup.md
```

Full instructions: [`dev-test-fixup.md`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.antigravity/tasks/dev-test-fixup.md)
— its own step 0 re-syncs the checkout to `origin/main` before anything
else, so the inline prompt doesn't need to.
Summary: polls stories for subtask PRs whose latest review is
`CHANGES_REQUESTED`, skips any it already fixed since that review (checked
by comparing commit/comment timestamps — the one part of this pipeline that
isn't naturally idempotent under polling, see below), addresses the
blocking issues, re-runs tests, and pushes a fix or comments on what's
still blocking.

## What this doesn't change

- Architect and PR Review stay on Claude via GitHub Actions — this switch is
  scoped to the two Gemini-backed nodes only, per the PO's request.
- Merge & Backlog (`merge.yml`) needs no changes under either executor — it
  already only checks for an `APPROVED` review, regardless of source.
- The output contracts (labels, comment markers, PR body structure) are
  written to match the GitHub Actions versions exactly, so switching executors
  mid-flight for a given issue/PR doesn't strand it in a state the other
  executor wouldn't recognize.
