# Merge & Backlog Node

**Implemented and live (2026-08-24)** —
[`merge.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/merge.yml)
in `crosstrainingapp`, commit `25fc328` for the current version. Survived
PR Review's advisory ↔ authoritative flip-flop untouched, needed one change
when PR Review dropped formal GitHub reviews entirely (see "Trigger moved
from review state to a label"), and gained a real second responsibility
later the same day (see "Closing the parent story").

- **Model:** none — fully deterministic, $0 cost.
- **Trigger:** `pull_request: [labeled]` filtered to
  `label.name == 'review:approved'`. This node was written not to care
  *who or what* produces that signal, which is exactly why it survived PR
  Review's authority reversal untouched — it only needed updating once the
  signal's shape changed (a label instead of a review state), not each time
  its origin changed (PO vs. Claude).
- **Output:** the PR merged, and — if that was the last open subtask under
  its parent story — the parent story relabeled `status:done` and closed.

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

## Closing the parent story (2026-08-24)

Found live, not designed ahead of time: story #55's three subtasks
(#56/#57/#58) all merged and auto-closed via GitHub's own "Closes #N"
handling — but the parent story itself stayed open forever. No node in
this pipeline had ever checked "is this the last subtask, and should the
story close too." Every other node reasons about the parent story
explicitly (Three Amigos' batch review, Dev & Test's story-level
`status:ready` gate); this was the one place that only ever looked at the
single subtask/PR directly in front of it.

Fixed here rather than as a new node, since this is the moment a subtask
*finishes* — the natural place to ask "was it the last one." After
merging: find the subtask the PR closes (`Closes #N` in the PR body, same
convention used everywhere else), find that subtask's parent story, and
check whether any sibling subtasks are still open. If none are open,
closes the story with a summary comment. Still fully deterministic — no
model needed for any of this.

Story #55 itself was closed manually with an explanatory comment, since it
finished before this fix existed; every story from here on closes itself.

**2026-08-25: both lookups moved onto GitHub's real Sub-issues API,
replacing the two Python scripts this originally used.** The subtask→parent
lookup (`find_parent_story.py`, which regex-matched a subtask's own body
text) is now `GET /repos/{o}/{r}/issues/{subtask}/parent` — one call, no
script. The open-siblings check (`filter_subtasks_by_parent.py`) is now
`GET /repos/{o}/{r}/issues/{story}/sub_issues`, filtered to open state via
`jq`. Both scripts are deleted — nothing references either one anymore
(confirmed via repo-wide grep before deleting). See `docs/definition-node.md`
for where the relationship actually gets created (Architect, on subtask
creation).

**Also as of 2026-08-25: closing a story now relabels it, not just closes
it.** Previously the story was left carrying `status:ready` forever after
closing — no label distinguished it from one still in flight. Now, right
before `gh issue close`, the story loses `status:ready` and gains
`status:done` (a new label, created once via `gh label create`). Makes a
finished story queryable/filterable, not just "closed" in GitHub's own
state.

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
# Then: find the closed subtask, find its parent story
# (GET .../issues/<subtask>/parent), close the story
# (relabeling status:ready -> status:done first) if no sub-issues
# remain open under it (GET .../issues/<story>/sub_issues).
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

This is the end of the graph for one subtask, and — once every sibling
subtask is also done — for the parent story's issue(s) too. No loop-back
edges — if something goes wrong here (merge conflict, `gh` failure), that's
an infrastructure error to surface directly to the human, not something to
retry against a model.
