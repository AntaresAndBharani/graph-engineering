# PR Review Node

**Implemented and live (2026-08-24)** —
[`pr-review.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/pr-review.yml)
in `crosstrainingapp`, commit `620f261`. **Redesigned from the original
scope below before building it** — see "Advisory, not authoritative" first.

- **Model:** Claude Opus, via `anthropics/claude-code-action` authenticated
  with `CLAUDE_CODE_OAUTH_TOKEN` (subscription-based billing, not
  `ANTHROPIC_API_KEY`) — see the top-level README's "Claude & Gemini auth /
  free-tier status".
- **Trigger:** `pull_request: [opened, synchronize]` — a PR the PO opened
  themselves via the existing (manual, unautomated) `.antigravity`
  Developer/Tester flow. This node does not wait for or depend on Dev & Test
  being automated; it reacts to whatever PR shows up.
- **Output:** a plain PR **comment** (never a formal GitHub review state) —
  see "Advisory, not authoritative."

## Advisory, not authoritative (2026-08-24 — changed before implementation)

The design below (and the rest of this doc) originally gave PR Review
authority to set `review_status: APPROVED`, gating the merge. **That changed
during scoping, before any code was written**: the PO explicitly wants to
review and approve every PR themselves, via GitHub's own native PR-approval
mechanism — not have Claude's verdict be the actual gate. So:

- This node posts `pr_comment_markdown` as a plain `gh pr comment`, never
  `gh pr review --approve` / `--request-changes`. It has no GitHub-level
  review state at all.
- The PO's own approval (a real GitHub PR review, done by hand) is what
  triggers `docs/merge-node.md` — not this node's `verdict` field. The
  `verdict`/`blocking_issues` fields still exist in the output schema below
  and are genuinely useful (a real first pass, clearly separating blocking
  from follow-up), they just don't drive automation on their own anymore.
- This also means the CHANGES_REQUESTED → Dev & Test → re-check loop in
  "Routing & iteration cap" below never actually executes automatically —
  the PO reads the comment and decides whether to push a fix themselves,
  same as they'd read any human reviewer's comment.

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

## Routing & iteration cap (design intent; not what actually runs — see above)

```
CHANGES_REQUESTED -> Dev & Test node (fix-up pass) -> PR Review (re-check)
APPROVED           -> Merge & Backlog node
```

This was the original automated routing. As built, neither edge fires
automatically: the PO reads the comment and the diff themselves, and their
own GitHub approval — not this node's `verdict` — is what
`docs/merge-node.md` actually reacts to. `synchronize` still re-triggers
this node on every new commit, so a fresh comment does appear each time,
it just isn't wired to a state machine anymore.
