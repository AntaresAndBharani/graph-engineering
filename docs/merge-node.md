# Merge & Backlog Node

**Not implemented — definition only**, per the current-scope note in the
top-level README. Documented so the full pipeline can be reviewed together.

- **Model:** none — fully deterministic, $0 cost.
- **Trigger:** PR Review verdict `APPROVED`.
- **Output:** a merged PR and new backlog issues for anything the reviewer
  deferred.

## Actions

```bash
gh pr merge <pr_number> --squash --delete-branch
```

Then, for each entry in the PR Reviewer's `followup_backlog_issues`
(`docs/pr-review-node.md`):

```bash
gh issue create --title "<title>" --body "<body>" --label "<labels>"
```

Recommended labels for these: `enhancement`, `tech-debt`, `backlog`, plus a
reference back to the originating PR number in the body so context isn't
lost.

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
