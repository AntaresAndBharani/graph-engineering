# Dev & Test Node

**Two interchangeable executors as of 2026-08-24** — the GitHub Actions
implementation below, or an Antigravity scheduled task polling on a cron
interval instead (added the same day, after this node's first automated run
hit Gemini's free-tier daily quota exhaustion). Same responsibilities, same
output contract, only one should be active at a time — see
`docs/antigravity-scheduled-tasks.md` for the prompt, schedule, and the
toggle between them, including the local-vs-CI `gradlew`/`gradlew.bat`
distinction that flips direction for this executor.

**Antigravity execution detail (2026-08-25):** the live Antigravity
executor now runs Three Amigos' logic first, unconditionally, at the start
of every poll that would otherwise just run this node's steps — a cost
optimization (fewer separate scheduled-task sessions), not a change to
either node's responsibilities. See
`docs/antigravity-scheduled-tasks.md`'s "Three Amigos merged into Dev &
Test" and `docs/three-amigos-node.md`. This node's own step order below
(now Steps 2-5 in the merged file, Steps 1-4 as originally designed here)
and output contract are unchanged. The disabled GitHub Actions twin
(`dev-test.yml`) is untouched and remains its own separate workflow.

**Automated after all — reversed the same day, 2026-08-24.** Stayed manual
for a few hours; the PO then decided PR Review had to become authoritative
again (real GitHub review, gates the merge), and for the resulting
Opus↔Gemini review/fix loop to run without a human in it, Dev & Test had
to stop being manual too — both the first implementation pass and the
fix-up response to `CHANGES_REQUESTED` are automated now. Implemented and
live —
[`dev-test.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/dev-test.yml),
commit `81d9903`. Highest-risk node in the pipeline by far: real code
writes, real Gradle test runs, real branches/PRs — everything before this
only manipulated issues/labels/comments. Went through Plan Mode before
being built, unlike every other node in this repo.

- **Two triggers, one workflow:** `status:ready` on a `type:subtask`
  (first pass — the PO's own approval-gate relabel, unchanged) fires
  `implement` mode; a PR labeled `review:changes-requested` fires `fixup`
  mode. That label replaced a `pull_request_review` (`state:
  changes_requested`) trigger later the same day, once `pr-review.yml`
  dropped formal GitHub reviews entirely — see
  `docs/pr-review-node.md`'s "Real `gh pr review` needed a different
  identity than the PAT" for why.
- **Gemini runs its own implement→test→fix loop internally**, capped at 3
  attempts by the prompt (`docs/dev-test-node.md`'s own copy of the
  original design still describes the fields; the actual prompt for the
  GitHub Actions executor lives in `crosstrainingapp`'s `dev-test.yml`,
  and for the Antigravity executor in `.antigravity/tasks/dev-test.md` —
  one merged file as of 2026-08-24, see
  `docs/antigravity-scheduled-tasks.md`'s "Fourth bug" for why it was
  originally two and got merged back into one), rather than this workflow
  re-invoking the CLI per retry — it already has full bash/write access by
  default ("YOLO mode," observed unprompted in an earlier Three Amigos
  run's logs), so one rich session that iterates on its own is simpler
  than restarting a fresh CLI call each attempt.
- **Real command, not the documented local one:** `./gradlew
  testDebugUnitTest --stacktrace` (Linux CI form, matching `build.yml`) —
  not `.\gradlew.bat`, which is documented for local Windows dev in
  `GEMINI.md`/`.agents/rules/` and doesn't exist on the runner. Caught
  before the first run, not after a failure.
- **`ORCHESTRATION_PAT` needed re-scoping** — Contents write + Pull
  requests write, in addition to its original Issues read/write. This is
  the first node that does a raw `git push`, not just `gh` API calls for
  issue/label/comment manipulation.

- **Model:** Gemini 3.7 Flash, High thinking effort (changed 2026-08-22 from
  Gemini 3.1 Pro to stay on Google AI Studio's free tier — see the top-level
  README's "Claude & Gemini auth / free-tier status").
- **Trigger:** an issue labeled `status:ready` (the *existing* label in
  `crosstrainingapp`, reused as the go-ahead — not a new label), **or** a PR
  labeled `review:changes-requested` by PR Review (this node handles both
  first implementation and fix-up passes — same actor, same
  responsibility). **As of 2026-08-25, `status:ready` is applied
  automatically by Three Amigos' `READY` verdict**, not by the PO — see
  `docs/three-amigos-node.md` "Routing." The original 2026-08-23 design
  (quoted below for history) had the PO relabel manually after Three
  Amigos landed on `status:awaiting-approval`; the PO's explicit call was
  to remove that checkpoint so the Opus↔Gemini review/fix loop can run
  fully without a human in it, same shape of change PR Review's authority
  went through. Which label this node actually gates on differs by
  executor (see `docs/antigravity-scheduled-tasks.md`): the live
  Antigravity flow's trigger is `status:ready` on the **story**; the
  disabled GitHub Actions `dev-test.yml` below still triggers per
  **subtask** — both are now set automatically, just at different
  granularity, unchanged from before this revision.
- **Output:** a branch, commits, and an open (or updated) PR.
- **Local test runner cost:** $0 — no LLM involved in actually running tests.

## Responsibilities

1. Create branch `feat/issue-<id>` (or reuse the existing branch when this is
   a fix-up pass triggered by PR Review).
2. Read the issue body, acceptance criteria, and any `bdd_scenarios`
   generated by Three Amigos.
3. Implement the change.
4. Run the project's actual local test suite — for `crosstrainingapp`
   that's `.\gradlew.bat testDebugUnitTest --no-daemon` (per its
   `GEMINI.md`/`.antigravity/rules.md`), not `flutter test` as earlier
   drafts of this doc assumed before the target repo was confirmed. Internal
   fix→test retries here are capped at 3, same circuit-breaker pattern used
   everywhere else in this pipeline; unresolved after 3 attempts escalates to
   the human rather than opening a broken PR.
5. **E2E Visual & Functional Verification Gate:** If modifying UI components,
   navigation, or user flows, execute targeted delta E2E tests (e.g.
   `.\scripts\run-e2e-tests.ps1 -Delta`) and capture visual artifacts to
   `docs/screenshots/` (and push release assets to the QA repository). See
   `docs/e2e-testing-recommendations.md` for the full 5-pillar architecture.
6. Once local tests pass: `gh pr create` (first pass) referencing the issue,
   or push a new commit to the existing branch (fix-up pass).
7. **Publish sticky PR evidence:** Post or update the `<!-- e2e-evidence -->`
   and unit test summary comments on the PR (e.g. `.\scripts\post-e2e-evidence.ps1`)
   so PR Review and the PO have direct visual evidence of pass/fail status.

## PR description quality (2026-08-22 — not optional)

Per the README's "every node maintains the human-readable artifact"
principle: the PR description is not a formality, it's what the PO and PR
Reviewer actually read. `gh pr create` with a bare title is not sufficient.
The body must include:
- What changed and why, in plain language — not just a diff summary.
- A link back to the issue and which acceptance criteria this addresses.
- What was tested and the result (the actual local test output summary, not
  just "tests pass").

This applies on the first pass and every fix-up pass — update the
description if the scope of the PR changes across commits, don't leave it
describing only the original implementation once fixes land.

## Handling PR Reviewer feedback

Per the "Interaction design for nodes 2 & 4" section in the top-level README,
this node does **not** get a private channel back to the PR Reviewer. The
GitHub PR comment thread is the shared artifact both sides read and write:

- Read the reviewer's `blocking_issues` from its latest PR comment starting
  with `<!-- pr-review-verdict -->` (`gh pr view --json comments`) — PR
  Review posts its verdict as a comment now, not a formal review body; see
  `docs/pr-review-node.md`.
- Push a fix commit addressing each blocking item, and reply in the PR
  thread referencing what changed.
- **Never set the review/merge-readiness verdict itself.** This node can say
  "addressed" in a comment; only the PR Reviewer node's next pass can mark
  `review_status: APPROVED`. That authority boundary is deliberate (see
  README's "Authority for a verdict stays with the node responsible for it").

## Resolving approved-but-conflicting PRs (2026-08-25 — new responsibility)

Found live: PR #148 was `review:approved` but had fallen behind `main`
(many other subtask PRs merged while it waited) and developed a real git
conflict. Dev & Test's own "any PR open? stop" gate (see `docs/antigravity-scheduled-tasks.md`'s
"Concurrency") has no way to distinguish "waiting on review" from "stuck
and will never resolve itself" — so this one PR silently jammed 11 other
`status:ready` stories with no visible error anywhere, until the PO
noticed and asked why.

**Fix, not just a flag: Dev & Test now checks for this first, before even
fix-up work** (see `.antigravity/tasks/dev-test.md`'s Step 1). For any
subtask PR that's `review:approved` with `mergeable: CONFLICTING`: rebase
onto `main`, re-run the real test suite, and push. Only resolve a conflict
hunk when it's unambiguously additive on both sides (the two real
instances seen so far were both `CHANGELOG.md` entries from concurrent
PRs); anything requiring judgment about which side's logic should win
escalates to `status:needs-po-input` rather than guessing.

**Why this belongs in Dev & Test, not Merge & Backlog:** Merge & Backlog
is deliberately deterministic/model-free (see `docs/merge-node.md` "Why no
model here") — conflict resolution sometimes needs judgment, so it belongs
where the judgment-capable actor already is. Dev & Test already owns
"keep this PR moving toward mergeable" (that's what fix-up does for review
feedback); this is the same responsibility for a different kind of
not-yet-mergeable state, not a new one.

**Complementary fix, not a replacement:** `crosstrainingapp`'s
`.gitattributes` now sets `CHANGELOG.md merge=union` — git's built-in
union merge driver takes both sides instead of conflicting on the specific
collision shape seen twice so far (two PRs each appending an entry to the
same `## [Unreleased]` section). Verified locally against a synthetic
two-sided conflict before relying on it. This doesn't cover every conflict
class (a real code conflict still needs the step above, or a human) — it
just removes the one recurring cause before it produces a stuck PR at all.

**Doesn't extend to GitHub Actions' `dev-test.yml`** (disabled) — it's
purely event-triggered per subtask/PR, with no polling mechanism to hang a
"check for stale conflicting PRs" pass off of. Same category of
Antigravity-only exception already established for Backlog Triage.

## State fields this node reads/writes

```
current_issue_id, branch_name, pr_number, pr_diff,
test_output, error_count, iteration_count
```

## Out of scope for this node

Deciding whether the PR is done. That's the PR Reviewer's job, not this
node's, even when this node is confident the fix is correct.
