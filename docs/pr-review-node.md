# PR Review Node

**Implemented and live, authoritative (2026-08-24)** —
[`pr-review.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/pr-review.yml)
in `crosstrainingapp`, commit `81d9903`. **Went advisory-only for a few
hours the same day, then reversed back** — see "Authority, reversed twice
in one day" below for why, kept as the reasoning trail rather than erased.

- **Model:** Claude Opus, via `anthropics/claude-code-action` authenticated
  with `CLAUDE_CODE_OAUTH_TOKEN` (subscription-based billing, not
  `ANTHROPIC_API_KEY`) — see the top-level README's "Claude & Gemini auth /
  free-tier status".
- **Trigger:** `pull_request: [opened, synchronize]` — a PR opened by the
  now-automated Dev & Test node (`docs/dev-test-node.md`), not a human.
- **Output:** a real GitHub review — `gh pr review --approve` /
  `--request-changes` — whose state is what `docs/merge-node.md` actually
  triggers on.

## Authority, reversed twice in one day

1. **Original design**: authoritative — Claude's `verdict` gates the merge.
2. **First implementation (commit `620f261`)**: made advisory — the PO
   wanted to approve every PR themselves via GitHub's native flow, so this
   node posted a plain `gh pr comment` and had no GitHub review state.
3. **Reversed again within hours (commit `81d9903`)**: back to
   authoritative. The PO decided PR Review had to gate the merge for real —
   and pointed out that an automated Opus↔Gemini review/fix loop can't
   exist at all if a human has to be the one who approves, since that's not
   a loop, it's a human waiting on a comment. This is what actually forced
   Dev & Test off "stays manual" too (`docs/dev-test-node.md`) — the two
   decisions weren't separable once the loop needed to close on its own.

Current behavior matches the original design: `verdict` drives a real
`gh pr review`, `CHANGES_REQUESTED` triggers Dev & Test's fix-up pass (now
automated), capped at 3 rounds (counted from prior `CHANGES_REQUESTED`
reviews on the PR, checked before `pr-review.yml` runs Claude again).

## The PR comment thread is the state

Per the "no message-passing, use shared artifacts" principle in the top-level
README: this node doesn't exchange an internal transcript with anything.
The GitHub PR itself — diff, comments, commit history — is the durable,
checkpointed record.

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
CHANGES_REQUESTED -> Dev & Test node (fix-up pass, automated) -> PR Review (re-check via synchronize)
APPROVED           -> Merge & Backlog node
```

Both edges fire automatically now (`docs/dev-test-node.md`,
`docs/merge-node.md`). Capped at 3 review/fix rounds — counted from prior
`CHANGES_REQUESTED` reviews on the PR (`gh pr view --json reviews`),
checked before running Claude again on a `synchronize` event. At the cap,
this node posts an escalation comment instead of reviewing again, and
simply doesn't produce another review — since nothing merges without an
`APPROVED` review, that alone is enough to stop the loop without needing a
separate blocking state.
