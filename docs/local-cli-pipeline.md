# Local CLI Pipeline (third executor)

**Added 2026-08-26.** A third executor alongside GitHub Actions
(`docs/*-node.md`'s `.yml` workflows) and Antigravity IDE Scheduled Tasks
(`docs/antigravity-scheduled-tasks.md`): both `claude.exe` and `agy.exe`
(the Antigravity CLI) run directly on the PO's own machine, invoked by
Windows Task Scheduler instead of either a cloud runner or the Antigravity
IDE's own polling feature. Same node responsibilities and output contracts
throughout — this changes *who runs a node's logic and how much it costs*,
not what any node does, same framing already established for the
Antigravity executor itself.

## Why a third executor, not just relocating the existing two

Two separate findings converged the same day. First, Antigravity IDE's
Scheduled Tasks feature hard-locks every task to Flash models ("All
scheduled tasks run as Flash," confirmed in the New Scheduled Task
dialog) — this is what blocked the PO's earlier request to run Backlog
Triage on Opus 4.6 Thinking and Three Amigos on Sonnet 4.6 Thinking.
`agy.exe models` (run directly from a terminal) shows this lock is
specific to the IDE's own scheduler, not the underlying CLI:
`--model claude-opus-4-6-thinking` and `--model claude-sonnet-4-6` are
real, callable values from the command line. Second, every poll — whether
IDE-scheduled or CLI-invoked — pays a "session-setup cost" (loading
instructions, syncing the checkout, initial context) once per poll
regardless of how much real work happens inside; this was already the
reason Three Amigos got folded into Dev & Test on 2026-08-25, and it's the
same tax GitHub Actions pays in the form of a fresh runner + fresh
checkout every invocation.

Both CLIs are already installed and authenticated locally under the PO's
own subscriptions (Claude Pro/Max via `claude setup-token`, Antigravity via
its own Google-account login), and both support resuming a
previously-persisted, disk-backed conversation across **completely
separate process launches** — verified directly before relying on it (see
"Session persistence, verified" below). That means the "dedicated server"
model the PO wanted doesn't require a perpetually-running process: a
scheduled one-shot CLI invocation that resumes a fixed conversation ID
gets the same context-reuse benefit with far less operational fragility
than a resident loop process.

## Fetch → Judge → Act: shrink the LLM's role to pure judgment

The bigger token saver isn't relocating execution — it's changing *what*
gets sent to the LLM at all. Today's Antigravity-executor pattern already
gates on cheap checks (an open PR, an existing label) before doing
expensive work, but those checks still happen *inside* the paid session,
as tool calls the agent itself decides to make. The local-CLI executor
moves discovery and execution out of the LLM call entirely:

1. **Fetch (wrapper script, deterministic, before any CLI call):** the
   `gh`/`git` read calls a node needs — issue lists, PR diffs, comment
   threads, sub-issue trees, round counts from prior verdict comments.
2. **Gate (wrapper script, deterministic):** if there's nothing to act on,
   stop — **do not invoke the CLI at all** for this poll. A genuinely idle
   poll now costs zero tokens, not just cheap ones.
3. **Judge (one short CLI call, no tool/bash access):** the fetched data is
   embedded as text/JSON directly in the prompt; the model returns one
   structured JSON response (`--output-format json`) and nothing else — no
   `--dangerously-skip-permissions`, no bash access, since the model isn't
   running commands, just returning a verdict. Smaller, cheaper, and closes
   off prompt-injection risk from untrusted issue/PR content driving real
   command execution.
4. **Act (wrapper script, deterministic, after the CLI call):** parse the
   JSON, perform the actual `gh`/`git` mutations directly in the script.

The one exception: a node that genuinely needs multi-turn agentic
file/bash access (writing code, running a build, iterating on test
failures) keeps that full agentic shape — this pattern only applies to the
steps in each node that are pure discovery, judgment, or mutation, not to
implementation work itself.

## Execution mechanism: Windows Task Scheduler + one-shot calls, not a resident process

Each migrated node gets its own Task Scheduler entry firing
`powershell.exe -File <wrapper-script>.ps1` on that node's existing
cadence. Rejected: a perpetual `while ($true) { ...; Start-Sleep }` script
— same context-reuse benefit, but it doesn't survive a reboot without its
own "run at startup" wrapper, risks becoming an orphaned process, and Task
Scheduler's own per-run History tab is a better audit trail than one
long-lived process's combined stdout.

Registration uses `Register-ScheduledTask` with `-MultipleInstances
IgnoreNew` (single-flight — never start a new run while the previous one
is still going, the same guarantee the git-writing nodes already need to
avoid two executors racing the same trigger) and `-StartWhenAvailable`
(catch up if the machine was asleep/off at the scheduled time, rather than
silently skipping). Registered under the PO's own logged-on session, no
stored password — the trade-off documented below.

**Operational trade-off, stated plainly:** this executor only runs while
the PO's machine is on, awake, and logged in — unlike GitHub Actions or
Antigravity's own cloud-hosted scheduler. `-StartWhenAvailable` softens a
missed window but doesn't eliminate the dependency. Accepted deliberately
by the PO in exchange for cost control and real model choice.

## Session persistence, verified

Before wiring anything into Task Scheduler, this was tested directly
rather than assumed: `agy.exe --output-format json --print "..."` returns
a `conversation_id` in its JSON envelope; a **separate process invocation**
passing `--conversation <that-id>` correctly recalled context from the
first call. The `usage.cache_read_tokens` field in that second call's
response (16,289 of 22,608 input tokens, ~72%) confirms prompt caching is
genuinely active across separate launches, not just within one running
process — the token-savings premise holds.

For the now-stateless judgment-only calls (see Fetch/Judge/Act above),
each call is already self-contained — the wrapper embeds everything the
model needs directly in the prompt — so there's no meaningful cross-call
context to reuse, and no `--conversation` bookkeeping is needed at all.
Conversation persistence matters specifically for a node's genuinely
agentic, multi-turn steps (e.g. Dev & Test's implement/fix-up passes),
where the model's own accumulated understanding across an
implement→test→fix loop is worth keeping across nearby polls.

## A real bug class found during the first live migration: PowerShell 5.1 native-process argument/stderr handling

Two bugs surfaced building the first migrated node (Backlog Triage, see
`docs/backlog-triage-node.md`) that will recur in every future wrapper
script unless deliberately avoided:

1. **Argument mangling.** `& $exe --print $largeString` — PowerShell 5.1's
   own native-command argument marshaling corrupts a string argument
   containing embedded double quotes or backslash runs, both routine in
   real issue/PR body text (paths, inline-code spans). `agy.exe` received
   a truncated/split argument and errored on an "unexpected argument."
   `ProcessStartInfo.ArgumentList` (the obvious fix) isn't available on
   this machine's .NET Framework version. **Fix:** build the process
   command line by hand using the standard Win32/`CommandLineToArgvW`
   argument-quoting algorithm, and invoke via
   `[System.Diagnostics.Process]::Start` directly rather than PowerShell's
   `&` call operator. Verified against a string containing a backtick, an
   embedded quote, and a trailing backslash before trusting it.
2. **stderr treated as failure.** Both `git` and `gh` write routine,
   non-error status text to stderr (`git checkout`'s "Already on 'main'",
   `gh issue close`'s "✓ Closed issue ..."). Under
   `$ErrorActionPreference = "Stop"`, capturing that via `2>&1` wraps each
   line in a terminating `ErrorRecord` — the underlying command succeeds
   (`$LASTEXITCODE -eq 0`) but the script throws anyway, and if the throw
   is swallowed by a surrounding `try/catch`, every real success gets
   logged as an `ERROR`. **Fix:** temporarily switch to
   `$ErrorActionPreference = "Continue"` around any native `git`/`gh` call
   that's captured via `2>&1`, and check `$LASTEXITCODE` explicitly instead
   of relying on the exception path.

Both fixes are now the standard pattern for every wrapper script in
`scripts/local-pipeline/` — copy them forward rather than rediscovering
this per node.

Two more bugs surfaced building Architect (the third node migrated) that
apply just as broadly:

3. **BOM-less `.ps1` files can misdecode a Unicode em dash as a smart
   quote.** Windows PowerShell 5.1 recognizes curly/smart quotes as string
   delimiters, and reading a `.ps1` file without a BOM can misdecode a
   multi-byte UTF-8 em dash's trailing byte into one, silently truncating
   a *live string literal* and breaking the parse. This only bites inside
   actual code, not comments or comment-based help (which is why
   `run-backlog-triage.ps1`'s own em dashes, all inside its `<# #>` help
   block, never triggered it). **Fix:** use plain ASCII `--` instead of
   Unicode dashes in every string literal in these wrapper scripts, full
   stop — not worth relying on file encoding to get this right every time.
4. **A model can add prose after a complete, valid fenced JSON block,
   despite being told not to** — especially once a Judge call has tool
   access and naturally wants to summarize what it found (seen live:
   Architect's first real test on issue #318 returned valid JSON followed
   by a paragraph explaining its escalation reasoning). The original
   fence-stripping regex was end-anchored (`` ```(?:json)?\s*(.*?)\s*```$
   ``), so trailing text after the closing fence made the whole match fail
   and fell through to trying to parse the raw response — fencing markers,
   prose, and all — as JSON. **Fix:** drop the anchors and extract the
   first fenced block wherever it appears in the response. Applied to all
   three wrappers built so far, not just Architect, since they share the
   identical pattern.

## Critical: `--allowedTools`/`--disallowedTools` don't reliably restrict `claude.exe` in `--print` mode (2026-08-26)

The single most important finding from this migration, and one that
briefly ran live before being caught. Architect's original design gave
its Judge call read-only `--allowedTools "Read Grep Glob"` (no `Write`,
no `Bash`) specifically to preserve real repo-grounding while still
closing off prompt-injection risk from untrusted issue text driving
arbitrary command execution. **That restriction did not work.** Verified
directly, twice, with and without `--permission-mode dontAsk`: the model
still successfully invoked `Bash` and returned real, accurate, live
command output (a real current git commit SHA it could only have gotten
by actually running `git log`) despite `--allowedTools` naming only three
tools. The tool inventory available by default in this CLI's `--print`
mode, when nothing else restricts it, is its **full standard set** — not
just whatever's named in an allowlist. This ran live, unattended, on
`CTA-Architect`'s 5-minute Task Scheduler cadence for a short window
before being caught and the task disabled; no evidence of any actual
harmful action occurred in that window (the checkout's git status stayed
clean throughout, every observed mutation matched what the wrapper itself
performed deterministically), but the underlying restriction was never
the one intended, and that's a real gap in a design whose whole point was
closing off exactly this class of risk.

**Root-caused and fixed the same day:** the documented, correct mechanism
is the top-level `` --tools <list> `` flag ("Specify the list of
available tools from the built-in set" — `""` disables all, an explicit
comma-separated list restricts to exactly those), not
`--allowedTools`/`--disallowedTools`, which apparently operate at a
different layer that doesn't override this CLI's default set here.
Verified directly: `--tools "Read,Grep,Glob"` correctly blocks a real
git/Bash call (the model correctly self-reports no shell tool available,
does not fabricate output) while `Read`/`Grep`/`Glob` continue to
function normally (the same Glob-file-count sanity check used throughout
this migration still returns the right answer). `--tools ""` blocks
execution entirely. Architect now uses `--tools "Read,Grep,Glob"`; PR
Review — which had no tool flags at all, an oversight caught by the same
audit, since its design is pure judgment with *zero* tool access and a
bare `--print` call turned out to leave the same full default set
available — now uses `--tools ""` explicitly rather than relying on
omission.

**Backlog Triage (`agy.exe`) needed no equivalent fix, but for a
different, verified reason, not an assumption:** `agy.exe` has no
`--tools`/`--allowedTools` flag at all. Its safety boundary is
structural — `--dangerously-skip-permissions` is what auto-approves tool
use without a human to ask in headless mode, and Backlog Triage's wrapper
never passes it. Verified directly with a hard, unambiguous test (asked
it to write a real file to disk with no tool flags passed): the file did
not appear. Confirmed safe, not assumed safe — the same standard applied
to the `claude.exe` finding above, just with the opposite result. Keep
this flag out of every judgment-only `agy.exe` call in this pipeline; it
is reserved for Dev & Test's genuinely agentic implement/fix-up passes,
which need it.

**Takeaway for any future wrapper in this pipeline:** never assume a
tool-restriction flag works from its name or from how another vendor's
similar-looking CLI (or that same vendor's GitHub Actions integration)
uses it. Verify with a hard, observable test before trusting it —
self-reported "I don't have that tool" text is suggestive but not proof
(ask for a real, checkable side effect: a file that should or shouldn't
appear, a command whose live output can't be guessed).

## Migration order

Same reasoning as every other staged rollout in this pipeline: safest node
first, git-writing node last, and a hard cutover per node (old executor
off, new one on, in the same sitting) — never two live executors racing
the same trigger, the exact bug class that started the 2026-08-25 session
of fixes.

| Order | Node | CLI | Risk | Status |
|---|---|---|---|---|
| 1 | Backlog Triage | `agy.exe` | No git writes | **Live** (2026-08-26) — see below |
| 2 | PR Review | `claude.exe` | Label/comment only, no git push | Built, not yet cut over — see below |
| 3 | Architect | `claude.exe` | Issue/label only, no git push | **Live** (2026-08-26) — see below |
| 4 | Three Amigos + Dev & Test | `agy.exe` | Real branches/pushes — highest risk | Pending |

**Order deviated from the plan once, for a real reason:** Architect was
migrated before PR Review's live cutover was finished, because migrating
PR Review first left the pipeline stalled — Architect's GitHub Actions
workflow was already disabled (the PO turned off every non-deterministic
GH Actions workflow in one action, ahead of schedule) and its local
replacement wasn't live yet, so 9 real stories sat stuck at
`status:ready-for-architect` with nothing processing them. PR Review had
nothing to review anyway (zero open PRs) until stories flowed again.
Architect jumped the queue to unblock the pipeline; PR Review's wrapper is
already built and just needs a real PR to validate against, which
Architect's batch run should now produce naturally via Three Amigos + Dev
& Test.

### Task 1 — Backlog Triage (2026-08-26, cut over)

- **Wrapper:** `scripts/local-pipeline/run-backlog-triage.ps1` in
  `crosstrainingapp`.
- **Prompt template:** `.antigravity/tasks/backlog-triage.md`, rewritten
  from a fully-agentic instruction file to a judgment-only template (no
  `gh`/`git` instructions remain in it — those live in the wrapper now).
  Preserves the "every issue lands in exactly one cluster this run, a solo
  issue still gets its own story" guarantee from the original design —
  the model is explicitly told `[]` is only valid when the fetched issue
  list itself was empty, never as a way to skip an unclustered issue.
- **Task Scheduler entry:** `CTA-BacklogTriage`, every 6 hours (matching
  the retired Antigravity task's cadence), `-MultipleInstances IgnoreNew`,
  `-StartWhenAvailable`.
- **Verified live** against real GitHub data before cutover: clustered 7
  open `tech-debt` issues into 6 stories (#318–#323), closed all 7 sources
  with the standard absorption comment; a follow-up run with an empty
  backlog confirmed the zero-cost idle path (no `agy.exe` invocation at
  all, logged and exited in under a second).
- **Cut over:** the Antigravity IDE "Backlog Triage" scheduled task is
  disabled; `CTA-BacklogTriage` is the live executor for this node.
- **Files touched:** `crosstrainingapp`'s
  `scripts/local-pipeline/run-backlog-triage.ps1` (new),
  `.antigravity/tasks/backlog-triage.md` (rewritten to judgment-only),
  `.gitignore` (added `logs/local-pipeline/`); Windows Task Scheduler
  `CTA-BacklogTriage`; Antigravity IDE's "Backlog Triage" task disabled.

### Task 2 — PR Review (2026-08-26, built, not yet cut over)

- **Wrapper:** `scripts/local-pipeline/run-pr-review.ps1`. **Prompt
  template:** `.claude/tasks/pr-review.md`, trimmed from
  `.github/workflows/prompts/pr-review.md`'s Read/Grep/Glob/Write-tool
  version down to pure judgment (no tool access at all, enforced via
  `` --tools "" `` — see "Critical" section above; the wrapper embeds the
  PR's title/body/diff and, if present, a linked issue's acceptance
  criteria directly as text, and the diff itself already contains every
  code change in question, so unlike Architect there's no missing context
  a repo-browse would add).
- **New mechanism, no Backlog Triage precedent:** GitHub Actions reacted
  to a real `pull_request: [synchronize]` event; polling has no such
  signal, so each verdict comment now embeds the reviewed commit's SHA
  (`<!-- pr-review-sha:<sha> -->`, right after the existing
  `<!-- pr-review-verdict -->` marker) so the wrapper can tell "already
  reviewed this commit" from "new commits since last review" by comparing
  it to the PR's current `headRefOid`. Escalation-at-round-cap got its own
  dedicated marker (`<!-- pr-review-escalated -->`) rather than matching
  on the escalation message's wording, for the same reason every other
  state check in this pipeline prefers a marker over prose-matching.
- **Verified:** the zero-cost idle path, live (zero open PRs in the repo
  at build time — fetch found nothing, exited without invoking
  `claude.exe`). The Judge/Act path (posting a real verdict, applying
  labels, filing follow-up issues) is **not yet verified against a real
  PR** — none existed to test against. Architect's live cutover (Task 3)
  should produce one naturally via the still-Antigravity-executed Three
  Amigos + Dev & Test, which will complete this node's validation without
  needing a synthetic test PR.
- **Not yet cut over:** GitHub Actions' `pr-review.yml` is already
  disabled (part of the PO's blanket GH Actions shutdown), but no Task
  Scheduler entry exists yet — held until the Judge/Act path is proven
  against a real PR.
- **Files touched:** `crosstrainingapp`'s
  `scripts/local-pipeline/run-pr-review.ps1` (new),
  `.claude/tasks/pr-review.md` (new).

### Task 3 — Architect (2026-08-26, cut over)

- **Wrapper:** `scripts/local-pipeline/run-architect.ps1`, covering all
  three modes (`decompose` / `restructure` / `answer_clarifications`).
  **Prompt templates:** `.claude/tasks/architect-decompose.md`,
  `architect-restructure.md`, `architect-answer-clarifications.md`.
- **The one node so far that keeps tool access after migration.**
  Backlog Triage and PR Review's judgment is fully answerable from
  wrapper-fetched text alone (issue lists, a PR diff); Architect's isn't
  — all three original GitHub Actions prompts explicitly told the model
  to read the repository for existing patterns, integration points, and
  real file paths, and a first attempt at a pure text-only version (no
  tool access, prompt reworded to "don't invent paths you can't verify")
  was reviewed and rejected before going live: it would have traded real
  codebase grounding for hallucination-avoidance, a net quality loss for
  the highest cost-of-error node in the pipeline. Fix: keep read-only
  tool access via `` --tools "Read,Grep,Glob" `` (see "Critical" section
  above for why this specific flag, not `--allowedTools`, is the one that
  actually restricts), and pin
  `Invoke-NativeProcess`'s new `-WorkingDirectory` parameter to the real
  checkout — `Push-Location`/`Pop-Location` around the git-sync step only
  wraps that step, so by the time the Judge call runs the process's actual
  cwd has already reverted to wherever the script was invoked from, which
  would silently break every relative-path Read/Grep/Glob call otherwise.
  Every GitHub *mutation* (create/update/close subtasks, link via the
  real Sub-issues API, post the summary comment, swap labels) still runs
  deterministically in the wrapper, same as every other node — only the
  judgment call itself gets to look around.
- **`gh issue list --label a,b,c` is AND, not OR** — a real correction
  found live while building this node, not assumed from the GitHub Actions
  version (which checked one label per event, so it never had to combine
  them). Architect has three independent trigger labels
  (`status:ready-for-architect` / `needs-revision` / `needs-clarification`)
  that can each independently make an issue eligible; fetching them via
  one combined `--label` call returned nothing even against 9 real
  matching stories. Fixed the same way Backlog Triage already handles
  multiple labels: one separate `gh issue list` call per label.
- **Verified live in two stages, safest first:** a `-OnlyIssueNumbers`
  filter (manual-validation only, unused in normal unattended operation)
  scoped the first real run to a single story (#318) before letting the
  wrapper loose on the full backlog. That first run also caught the
  fenced-JSON prose bug documented above — fixed and re-verified clean on
  the same story before proceeding. The full batch run then processed all
  9 real stories waiting at `status:ready-for-architect` (#207, #254,
  #290, #318–#323) in one poll: created 15 subtasks total across them, all
  correctly linked via the Sub-issues API, all correctly relabeled from
  `status:ready-for-architect` to `status:review`,
  `origin:backlog-triage`/model-tier selection preserved. Output quality
  was consistently grounded in real file/line references, including one
  story (#323) where the model used `git log`/`git show` (via its
  Read/Grep/Glob-adjacent Bash-free tool access — actually just Glob/Grep
  over the working tree, `git show` isn't available without Bash, worth
  double-checking exactly how that citation got resolved if revisited) to
  confirm a stray CHANGELOG.md issue citation was genuinely unrelated
  before removing it, rather than guessing.
- **Cut over:** GitHub Actions' `architect.yml` was already disabled (part
  of the PO's blanket shutdown); `CTA-Architect` (every 5 minutes,
  `-MultipleInstances IgnoreNew`, `-StartWhenAvailable`) is now the live
  executor.
- **Files touched:** `crosstrainingapp`'s
  `scripts/local-pipeline/run-architect.ps1` (new, including the
  `-OnlyIssueNumbers` validation-only filter and the `WorkingDirectory`
  addition to `Invoke-NativeProcess`), the three `.claude/tasks/architect-*.md`
  templates (new); Windows Task Scheduler `CTA-Architect`.
