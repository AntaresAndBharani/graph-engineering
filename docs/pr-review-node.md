# PR Review Node

**Not implemented — definition only**, per the current-scope note in the
top-level README. Documented so the full pipeline can be reviewed together.

- **Model:** Claude Opus.
- **Trigger:** a PR opened or updated by the Dev & Test node.
- **Output:** an `APPROVED` or `CHANGES_REQUESTED` verdict, posted as a real
  GitHub PR review — not an internal message.

## The PR comment thread is the state

Per the "no message-passing, use shared artifacts" principle in the top-level
README: this node and the Dev & Test node do not exchange an internal
transcript. The GitHub PR itself — diff, review comments, commit history —
is the durable, checkpointed record. The orchestrator only needs to track
`pr_number` and the latest `review_status`, not a running conversation log.

**Authority rule:** only this node may set `review_status: APPROVED`. The Dev
node can push commits and reply in the thread claiming something is fixed,
but that claim doesn't count until this node re-reviews and says so.

## Review guidelines

1. **Scope verification** — does the diff satisfy all acceptance criteria
   without introducing unrequested features? Are edge cases (network drops,
   invalid auth state, race conditions, cache sync) handled?
2. **Architecture & code quality** — separation of concerns, state management
   patterns, security (hardcoded secrets, validation, permissions),
   performance (rebuilds, leaks, unclosed streams).
3. **Blocking vs. follow-up** — blocking = broken acceptance criteria,
   security flaws, regressions, unhandled crashes; must be fixed before
   merge. Non-blocking = refactors, minor perf, valuable-but-out-of-scope
   enhancements — never block the PR for these, log them as separate issues
   instead (handled by the Merge & Backlog node).

## Output schema

```json
{
  "verdict": "APPROVED | CHANGES_REQUESTED",
  "summary": "string",
  "pr_review_markdown": "string — posted directly as the GitHub PR review body",
  "blocking_issues": [
    { "file": "string", "issue": "string", "suggested_fix": "string" }
  ],
  "followup_backlog_issues": [
    { "title": "string", "body": "string", "labels": ["string"] }
  ]
}
```

## Actions

```bash
gh pr diff <pr_number>
gh pr review <pr_number> --approve -b "<pr_review_markdown>"       # verdict = APPROVED
gh pr review <pr_number> --request-changes -b "<pr_review_markdown>"  # verdict = CHANGES_REQUESTED
```

## Routing & iteration cap

```
CHANGES_REQUESTED -> Dev & Test node (fix-up pass) -> PR Review (re-check)
APPROVED           -> Merge & Backlog node
```

Shares the same 3-round circuit breaker as the rest of the pipeline. If a PR
is still not `APPROVED` after 3 review/fix rounds, escalate to the human
instead of forcing another round or merging with open blocking issues.
