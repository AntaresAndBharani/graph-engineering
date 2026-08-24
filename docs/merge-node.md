# Merge & Backlog Node

**Implemented and live (2026-08-24)** —
[`merge.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/merge.yml)
in `crosstrainingapp`, commit `82670a5` for the current trigger. Survived
PR Review's advisory ↔ authoritative flip-flop untouched, but *did* need
one change later the same day when PR Review dropped formal GitHub reviews
entirely — see "Trigger moved from review state to a label" below.

- **Model:** none — fully deterministic, $0 cost.
- **Trigger:** `pull_request: [labeled]` filtered to
  `label.name == 'review:approved'`. This node was written not to care
  *who or what* produces that signal, which is exactly why it survived PR
  Review's authority reversal untouched — it only needed updating once the
  signal's shape changed (a label instead of a review state), not each time
  its origin changed (PO vs. Claude).
- **Output:** the PR merged. Nothing else.

## Trigger moved from review state to a label (2026-08-24)

`pr-review.yml` dropped `gh pr review --approve`/`--request-changes`
entirely — GitHub blocks a formal review from the same identity that
opened the PR, which `ORCHESTRATION_PAT` always is here (see
`docs/pr-review-node.md`'s "Real `gh pr review` needed a different identity
than the PAT" for the full debugging trail). It now applies a
`review:approved` label instead of a review state, so this node's trigger
moved from `pull_request_review: [submitted]` + `review.state == approved`
to `pull_request: [labeled]` + `label.name == 'review:approved'`. Nothing
else about this node changed — still doesn't care who/what applied the
label, same as it never cared who/what the prior review state came from.

## What changed from the original design

Two simplifications, both because merging only ever needed "an APPROVED
review exists," regardless of who/what produces it:

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
