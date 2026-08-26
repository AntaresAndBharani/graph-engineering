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

## Migration order

Same reasoning as every other staged rollout in this pipeline: safest node
first, git-writing node last, and a hard cutover per node (old executor
off, new one on, in the same sitting) — never two live executors racing
the same trigger, the exact bug class that started the 2026-08-25 session
of fixes.

| Order | Node | CLI | Risk | Status |
|---|---|---|---|---|
| 1 | Backlog Triage | `agy.exe` | No git writes | **Live** (2026-08-26) — see below |
| 2 | PR Review | `claude.exe` | Label/comment only, no git push | Pending |
| 3 | Architect | `claude.exe` | Issue/label only, no git push | Pending |
| 4 | Three Amigos + Dev & Test | `agy.exe` | Real branches/pushes — highest risk | Pending |

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

## Files touched, this node

- `crosstrainingapp`: `scripts/local-pipeline/run-backlog-triage.ps1` (new),
  `.antigravity/tasks/backlog-triage.md` (rewritten to judgment-only),
  `.gitignore` (added `logs/local-pipeline/`).
- Windows Task Scheduler (local machine, not a repo artifact):
  `CTA-BacklogTriage`.
- Antigravity IDE: "Backlog Triage" scheduled task disabled.
