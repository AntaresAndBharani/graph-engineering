# PR Review Node

**Implemented and live, authoritative (2026-08-24)** —
[`pr-review.yml`](https://github.com/AntaresAndBharani/crosstrainingapp/blob/main/.github/workflows/pr-review.yml)
in `crosstrainingapp`, commit `82670a5` for the current design. Went through
three shapes in one day — advisory-only, then a real GitHub review, then
comment + label — see "Authority, reversed twice in one day" and "Real `gh
pr review` needed a different identity than the PAT" below for the full
trail, kept rather than erased.

- **Model:** Claude Opus, via `anthropics/claude-code-action` authenticated
  with `CLAUDE_CODE_OAUTH_TOKEN` (subscription-based billing, not
  `ANTHROPIC_API_KEY`) — see the top-level README's "Claude & Gemini auth /
  free-tier status".
- **Trigger:** `pull_request: [opened, synchronize]` — a PR opened by the
  now-automated Dev & Test node (`docs/dev-test-node.md`), not a human.
- **Output:** a PR comment carrying the verdict (marked with
  `<!-- pr-review-verdict -->`) plus a `review:approved` /
  `review:changes-requested` label — **not** a formal GitHub review state.
  That label is what `docs/merge-node.md` and Dev & Test's fix-up trigger
  actually key off. See "Real `gh pr review` needed a different identity
  than the PAT" for why a formal review isn't used.

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

Current behavior matches the *intent* of the original design — `verdict`
gates the merge for real, `CHANGES_REQUESTED` triggers Dev & Test's fix-up
pass (now automated) — but not its literal mechanism. See the next section:
the actual gate ended up being a label, not a formal GitHub review, because
the review call itself turned out to be structurally impossible here.

## Real `gh pr review` needed a different identity than the PAT (2026-08-24)

The reversal above was the *design* decision; the actual `gh pr review
--approve`/`--request-changes` call was never exercised end-to-end until
today, since the advisory version only ever posted a plain comment. First
real run (PRs #59/#60/#62, all open simultaneously) failed on all three
with the same GitHub error: `Can not request changes on your own pull
request`. `ORCHESTRATION_PAT` is the identity `dev-test.yml` uses to open
every PR — so this node was trying to review its own sibling node's work
under the same GitHub account, which GitHub blocks outright, and always
would have regardless of the transient Anthropic-overload issues debugged
earlier the same day.

First fix attempt: submit only the `gh pr review` call as Claude's own
GitHub App identity — `claude-code-action` exposes its installation token
via `outputs.github_token` for exactly this kind of reuse. **This didn't
actually work.** `claude-code-action` revokes that token as part of its own
step's internal cleanup, which runs before the next step in the job gets
control — confirmed via `HTTP 401: Bad credentials` when the later "Apply
review verdict" step tried to reuse it, even though the token was captured
correctly (non-empty, properly masked as a secret in the logs). The token
genuinely does not survive past the step boundary it was minted in.

A credential-based fix needs something independent of Claude's own action
lifecycle, not a reused one — two options existed (let Claude call `gh pr
review` itself from inside its own step, which needs `Bash` added to
`allowedTools` and broadens what an LLM reading untrusted PR content can
execute; or a dedicated second identity — bot PAT or GitHub App — just for
review-submission, which needs that identity actually created first).

**Resolved differently: drop the formal review requirement entirely.**
The PO's call — since a plain `gh pr comment` has no identity restriction
at all (anyone, including the PR author, can comment on their own PR), and
this pipeline already routes everything else through labels, there was no
real reason to fight GitHub's review-identity rule instead of just not
using GitHub reviews. `pr-review.yml` now posts the verdict as a comment
(prefixed with `<!-- pr-review-verdict -->`, same marker pattern
`three-amigos.yml` already used for its own round-counting) and applies
`review:approved` or `review:changes-requested` as a label — removing
whichever of the two was present from a prior round first, same
"remove every plausible prior label" lesson as the `status:awaiting-approval`
promotion bug from earlier the same day. `docs/merge-node.md` and Dev &
Test's fix-up trigger (`docs/dev-test-node.md`) both moved from
`pull_request_review` events to `pull_request: [labeled]` on these two
labels. Neither the Bash-in-allowedTools nor the second-identity option
was needed in the end.

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
  "pr_comment_markdown": "string — posted directly as the PR comment body",
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
# Remove whichever prior verdict label is present, post the comment, add the new label:
gh pr edit <pr_number> --remove-label "review:approved"
gh pr edit <pr_number> --remove-label "review:changes-requested"
gh pr comment <pr_number> --body-file <comment_with_marker>
gh pr edit <pr_number> --add-label "review:approved"            # verdict = APPROVED
gh pr edit <pr_number> --add-label "review:changes-requested"   # verdict = CHANGES_REQUESTED
```

## Routing & iteration cap

```
review:changes-requested -> Dev & Test node (fix-up pass, automated) -> PR Review (re-check via synchronize)
review:approved           -> Merge & Backlog node
```

Both edges fire automatically now (`docs/dev-test-node.md`,
`docs/merge-node.md`), triggered by `pull_request: [labeled]` on these two
labels rather than `pull_request_review` events. Capped at 3 review/fix
rounds — counted from prior comments on the PR starting with
`<!-- pr-review-verdict -->` and containing `CHANGES_REQUESTED`, checked
before running Claude again on a `synchronize` event. At the cap, this node
posts an escalation comment instead of reviewing again, and simply doesn't
apply another verdict label — since nothing merges without
`review:approved`, that alone is enough to stop the loop without needing a
separate blocking state.
