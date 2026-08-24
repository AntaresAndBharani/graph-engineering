# Merge & Backlog Node

**Implemented and live (2026-08-24)** —
[`merge.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/merge.yml)
in `crosstrainingapp`, commit `620f261`. Simpler than originally designed —
see "What changed" below.

- **Model:** none — fully deterministic, $0 cost.
- **Trigger:** `pull_request_review: [submitted]` filtered to
  `review.state == 'approved'` — **the PO's own GitHub PR approval**, not
  PR Review's `verdict` (see `docs/pr-review-node.md` "Advisory, not
  authoritative" — that node has no GitHub review state to trigger off of
  at all).
- **Output:** the PR merged. Nothing else.

## What changed from the original design

Two simplifications, both because the trigger changed from "an automated
verdict" to "the PO clicked approve" — at that point there's nothing left
to decide:

- **No backlog-issue creation here.** PR Review already files
  `followup_backlog_issues` immediately when it runs (see
  `docs/pr-review-node.md`), rather than waiting for merge. Deferring that
  to this node would have meant tracking follow-ups in state across the
  now-unused review/fix loop for no benefit.
- **No release logic to build.** `crosstrainingapp` already tags and
  publishes a GitHub Release on every push to `main` (its existing
  `release.yml`), so merging is the entire job.

## Actions

```bash
gh pr merge <pr_number> --auto --squash --delete-branch
```

`--auto` waits for required checks (the repo's own `build.yml`) to go green
rather than failing immediately if the PO's approval lands before CI
finishes — approval and CI completion have no guaranteed order.

## Why no model here

Every prior node already produced a structured, validated decision —
`APPROVED` from the PR Reviewer, a list of specific follow-up issues. There's
nothing left to reason about; running this through an LLM would just add
cost and a hallucination surface for a mechanical action. This is the
pipeline's cheapest node by design, matching the "reserve LLMs for actual
judgment calls" principle used throughout.

## Terminal state

This is the end of the graph for one requirement's issue(s). No loop-back
edges — if something goes wrong here (merge conflict, `gh` failure), that's
an infrastructure error to surface directly to the human, not something to
retry against a model.
