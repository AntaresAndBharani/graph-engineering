#!/usr/bin/env python3
"""
Architect Council Review helper (/architect-council-review, /council-review, /council).

A three-member council reviews a requirement, draft plan, or git diff in bounded rounds:

  * Functional Architect (FA): `claude` CLI, claude-opus-5-5, effort high.
    Problem fidelity, user story completeness, Given-When-Then precision, scope creep.
  * Technical Lead (TL): `agy` CLI, claude-opus-5-5, effort medium.
    Implementation feasibility, architectural patterns, minimal change set.
  * Quality Assurance (QA): `agy` CLI, gemini-3.8-flash-high.
    Regression surface, backwards compatibility, test matrix, contract stability.

Protocol:
  Round 1 (Independent Review): the three members review in parallel. Unanimous `VERDICT: AGREED`
  (no BLOCKING objections) ends the run with a `## ✅ Council Consensus` section.
  Round 2 (Cross-Rebuttal): each member gets its peers' Round 1 objections and must ACCEPT, REBUT
  or MODIFY each one. Unanimity ends the run; otherwise the script appends a
  `## ⚠️ Council Deadlock Escalation Report` and exits with code 2.
  The operator then runs `--continue N` (N more cross-rebuttal rounds) or
  `--proceed provision-story` (accept the Recommended Compromise and hand off to /provision-story).

The plan file is the only medium: every round, the dossier and every operator decision are appended
to it, and the round counter is derived from its headings. Reviewers never write to the file (they
run in parallel); the script appends their replies and restores the file if a reviewer edited it.

Exit codes: 0 = consensus / compromise accepted / status, 2 = deadlock (operator decision needed), 1 = error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_PLAN_RELATIVE = Path("docs") / "draft-requisites" / "implementation-plan.md"
DEFAULT_MAX_ROUNDS = 2

DEFAULT_FA_MODEL = "claude-opus-5-5"
DEFAULT_FA_EFFORT = "high"
DEFAULT_TL_MODEL = "claude-opus-5-5"
DEFAULT_TL_EFFORT = "medium"
# agy encodes the Gemini reasoning effort in the model name (gemini-3.8-flash-{low,medium,high}).
DEFAULT_QA_MODEL = "gemini-3.8-flash-high"
DEFAULT_QA_EFFORT: Optional[str] = None

DEFAULT_CLAUDE_TIMEOUT_S = 20 * 60
DEFAULT_AGY_TIMEOUT_S = 20 * 60
DEFAULT_MAX_DIFF_CHARS = 200_000
# Read-only code inspection for the claude reviewer: no Bash, Edit/Write or Agent (subagent fan-out).
CLAUDE_REVIEW_TOOLS = "Read,Grep,Glob"
# Win32 CreateProcessW lpCommandLine limit is 32,767 characters; keep a safety margin.
WINDOWS_CMD_LIMIT = 32_000

PROCEED_TARGETS = ("provision-story",)

STATUS_IDLE = "idle"
STATUS_IN_PROGRESS = "in_progress"
STATUS_CONSENSUS = "consensus"
STATUS_DEADLOCKED = "deadlocked"
STATUS_ACCEPTED = "compromise_accepted"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DEADLOCK = 2


@dataclass(frozen=True)
class Role:
    abbr: str
    name: str
    harness: str  # "claude" or "agy"
    focus: str


ROLE_ORDER: Tuple[str, ...] = ("FA", "TL", "QA")
ROLES: Dict[str, Role] = {
    "FA": Role(
        "FA",
        "Functional Architect",
        "claude",
        "problem fidelity, user story completeness, Given-When-Then precision, and scope creep elimination",
    ),
    "TL": Role(
        "TL",
        "Technical Lead",
        "agy",
        "implementation feasibility, architectural patterns, and the minimal change set strictly necessary for the requirements",
    ),
    "QA": Role(
        "QA",
        "Quality Assurance",
        "agy",
        "regression surface analysis, backwards compatibility, test matrix completeness, and contract stability",
    ),
}

ROUND_HEADING_RE = re.compile(r"^##\s*⚖\ufe0f?\s*Council Round\s+(\d+):.*\((FA|TL|QA)\)\s*$")
SUBJECT_HEADING_RE = re.compile(r"^##\s*🧾\s*Council Subject")
CONSENSUS_HEADING_RE = re.compile(r"^##\s*✅\s*Council Consensus")
ACCEPTED_HEADING_RE = re.compile(r"^##\s*✅\s*Council Compromise Accepted")
DEADLOCK_HEADING_RE = re.compile(r"^##\s*⚠\ufe0f?\s*Council Deadlock Escalation Report")
CONTINUE_HEADING_RE = re.compile(r"^##\s*⏩\s*Council Continuation:\s*\+(\d+)")
SECTION_HEADING_RE = re.compile(r"^#{1,2}\s")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

COUNCIL_KINDS = {"round", "subject", "consensus", "accepted", "deadlock", "continue"}

VERDICT_RULES = [
    "Classify every objection as **[BLOCKING]** or **[NON-BLOCKING]**.",
    "BLOCKING is reserved for defects that would ship a wrong or unsafe result: correctness bugs, data loss, "
    "broken contracts or backwards compatibility, dropped or altered requirements, scope creep beyond the stated "
    "problem, or acceptance criteria that cannot be tested.",
    "Style preferences, optional hardening, and nice-to-have improvements are NON-BLOCKING.",
    "End with `VERDICT: AGREED` when you have no BLOCKING objections (NON-BLOCKING notes are allowed), "
    "otherwise `VERDICT: DISAGREED`.",
]


# --------------------------------------------------------------------------------------------------
# Plan file I/O (newline-preserving)
# --------------------------------------------------------------------------------------------------

def find_plan_file(custom_path: Optional[str] = None) -> Tuple[Path, str]:
    """Resolves the plan file. Returns (path, source) where source is 'argument' or 'default'."""
    if custom_path:
        p = Path(custom_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Specified plan file not found: {p}")
        return p, "argument"

    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / DEFAULT_PLAN_RELATIVE
        if candidate.exists():
            return candidate, "default"
    raise FileNotFoundError(
        f"Could not find '{DEFAULT_PLAN_RELATIVE.as_posix()}'. Pass --plan <path> or create the plan file first."
    )


def read_raw(path: Path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_raw(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def normalize(raw: str) -> str:
    return raw.replace("\r\n", "\n")


def append_blocks(path: Path, blocks: List[str]) -> None:
    """Appends markdown blocks to the plan, keeping the file's newline convention."""
    raw = read_raw(path)
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = normalize(raw).rstrip("\n")
    for block in blocks:
        text += "\n\n" + block.strip("\n")
    text += "\n"
    write_raw(path, text.replace("\n", newline))


# --------------------------------------------------------------------------------------------------
# Section parsing & council state
# --------------------------------------------------------------------------------------------------

@dataclass
class Section:
    start: int  # 0-based index of the heading line (0 for a heading-less preamble)
    end: int  # exclusive
    heading: str
    kind: str = "other"
    round_num: int = 0
    role: str = ""
    extra: int = 0

    def line_range(self) -> Tuple[int, int]:
        """1-based inclusive line range."""
        return self.start + 1, self.end


def _classify(section: Section) -> None:
    heading = section.heading.strip()
    m = ROUND_HEADING_RE.match(heading)
    if m:
        section.kind, section.round_num, section.role = "round", int(m.group(1)), m.group(2)
        return
    m = CONTINUE_HEADING_RE.match(heading)
    if m:
        section.kind, section.extra = "continue", int(m.group(1))
        return
    if ACCEPTED_HEADING_RE.match(heading):
        section.kind = "accepted"
    elif CONSENSUS_HEADING_RE.match(heading):
        section.kind = "consensus"
    elif DEADLOCK_HEADING_RE.match(heading):
        section.kind = "deadlock"
    elif SUBJECT_HEADING_RE.match(heading):
        section.kind = "subject"


def parse_sections(lines: List[str]) -> List[Section]:
    """Splits the plan at `#`/`##` headings, ignoring headings inside fenced code blocks."""
    starts: List[int] = []
    in_fence = False
    for idx, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and SECTION_HEADING_RE.match(line):
            starts.append(idx)

    sections: List[Section] = []
    if not starts or starts[0] > 0:
        first = starts[0] if starts else len(lines)
        if any(line.strip() for line in lines[:first]):
            sections.append(Section(0, first, ""))
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        section = Section(start, end, lines[start])
        _classify(section)
        sections.append(section)
    return sections


@dataclass
class CouncilState:
    status: str
    rounds: Dict[int, Dict[str, Section]] = field(default_factory=dict)
    rounds_completed: int = 0
    continuation: int = 0
    max_rounds: int = DEFAULT_MAX_ROUNDS
    session_sections: List[Section] = field(default_factory=list)

    def next_round(self) -> int:
        if self.status in (STATUS_CONSENSUS, STATUS_ACCEPTED):
            return 1
        return self.rounds_completed + 1


def compute_state(sections: List[Section], base_max_rounds: int = DEFAULT_MAX_ROUNDS) -> CouncilState:
    """
    Derives the council state from the plan headings. A session is closed by a Consensus or an
    accepted Compromise; everything after the last closing marker belongs to the open session.
    """
    terminal_idxs = [i for i, s in enumerate(sections) if s.kind in ("consensus", "accepted")]
    last_terminal = terminal_idxs[-1] if terminal_idxs else -1
    open_session = [s for s in sections[last_terminal + 1:] if s.kind in COUNCIL_KINDS]

    if last_terminal >= 0 and not any(s.kind in ("round", "deadlock", "continue") for s in open_session):
        prev_terminal = terminal_idxs[-2] if len(terminal_idxs) > 1 else -1
        session = sections[prev_terminal + 1:last_terminal + 1]
        status = STATUS_CONSENSUS if sections[last_terminal].kind == "consensus" else STATUS_ACCEPTED
    else:
        session = sections[last_terminal + 1:]
        status = ""

    rounds: Dict[int, Dict[str, Section]] = {}
    continuation = 0
    last_council_kind = ""
    for s in session:
        if s.kind == "round":
            rounds.setdefault(s.round_num, {})[s.role] = s
        elif s.kind == "continue":
            continuation += s.extra
        if s.kind in COUNCIL_KINDS and s.kind != "subject":
            last_council_kind = s.kind

    completed = 0
    while all(role in rounds.get(completed + 1, {}) for role in ROLE_ORDER):
        completed += 1

    if not status:
        if last_council_kind == "deadlock":
            status = STATUS_DEADLOCKED
        elif completed == 0:
            status = STATUS_IDLE
        else:
            status = STATUS_IN_PROGRESS

    return CouncilState(
        status=status,
        rounds=rounds,
        rounds_completed=completed,
        continuation=continuation,
        max_rounds=base_max_rounds + continuation,
        session_sections=[s for s in session],
    )


def load_state(plan_path: Path, base_max_rounds: int) -> Tuple[List[str], List[Section], CouncilState]:
    lines = normalize(read_raw(plan_path)).split("\n")
    sections = parse_sections(lines)
    return lines, sections, compute_state(sections, base_max_rounds)


def subject_sections(sections: List[Section]) -> List[Section]:
    """The subject is every non-council section plus the most recent `🧾 Council Subject` (diff) section."""
    subjects = [s for s in sections if s.kind == "subject"]
    latest_subject = subjects[-1] if subjects else None
    return [s for s in sections if s.kind not in COUNCIL_KINDS or s is latest_subject]


def merge_ranges(sections: List[Section]) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    for s in sorted(sections, key=lambda x: x.start):
        lo, hi = s.line_range()
        if hi < lo:
            continue
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], hi))
        else:
            ranges.append((lo, hi))
    return ranges


def _text_of(lines: List[str], ranges: List[Tuple[int, int]]) -> str:
    return "\n".join("\n".join(lines[lo - 1:hi]) for lo, hi in ranges).strip()


# --------------------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------------------

def build_prompt(
    role: Role,
    round_num: int,
    plan_path: Path,
    lines: List[str],
    sections: List[Section],
    state: CouncilState,
    inline: bool,
) -> str:
    rebuttal = round_num > 1
    mode = "Cross-Rebuttal" if rebuttal else "Independent Review"
    subject_ranges = merge_ranges(subject_sections(sections))
    previous = state.rounds.get(round_num - 1, {}) if rebuttal else {}
    peer_sections = [previous[r] for r in ROLE_ORDER if r != role.abbr and r in previous]
    own_section = previous.get(role.abbr)

    out: List[str] = [
        f"You are the {role.name} ({role.abbr}) on the Architect Council, a three-member review board "
        f"(Functional Architect, Technical Lead, Quality Assurance). This is Round {round_num} ({mode}) "
        f"of the subject in `{plan_path}`.",
        "",
        f"Your lens: {role.focus}. Stay inside this lens; the other members cover the rest.",
        "",
        "Rules:",
    ]
    if inline:
        out.append("- Everything you need from the plan file is inlined below. Do not re-read the plan file.")
    else:
        out.append(f"- Read ONLY these line ranges of `{plan_path}` (do not read the rest of the file):")
        out.extend(f"  - Lines {lo}-{hi}: subject under review" for lo, hi in subject_ranges)
        for s in peer_sections:
            lo, hi = s.line_range()
            out.append(f"  - Lines {lo}-{hi}: {s.role} review from Round {round_num - 1}")
        if own_section:
            lo, hi = own_section.line_range()
            out.append(f"  - Lines {lo}-{hi}: your own review from Round {round_num - 1}")
    out.extend([
        "- Inspect codebase files only when the subject names them (at most about 8 files, targeted searches). "
        "Never run builds, tests, or state-changing commands.",
        "- Do NOT create, edit, or delete any file. Reply with your review only; the council script appends it to the plan.",
        "- Use only `###` headings in your reply (never `#` or `##`).",
    ])
    if rebuttal:
        out.append(
            "- This is a cross-rebuttal: for every peer objection, state ACCEPT, REBUT (with evidence), or MODIFY "
            "(with the amended wording). Change your verdict when the peers convince you; hold it when they do not."
        )
    else:
        out.append("- This is an independent review: judge the subject on its own merits.")
    out.extend(f"- {rule}" for rule in VERDICT_RULES)
    out.extend([
        "",
        "Reply format (exactly these headings, in this order):",
        "### Agreed Points",
        "- <one point of the subject you endorse per bullet>",
        "### Objections",
        "- [BLOCKING] <defect and the concrete fix>",
        "- [NON-BLOCKING] <improvement>",
        "(write `- None` when you have no objections)",
    ])
    if rebuttal:
        out.extend([
            "### Rebuttals",
            "- <peer abbreviation>: <objection summary>: ACCEPT | REBUT | MODIFY: <reason or amended wording>",
        ])
    out.extend([
        "### Position",
        "<one paragraph stating your stance>",
        "### Proposed Compromise",
        "<one paragraph: the smallest change that would make you agree, or `None` when you agree>",
        "",
        "VERDICT: AGREED | VERDICT: DISAGREED",
    ])

    if inline:
        out.extend(["", "===== SUBJECT UNDER REVIEW =====", _text_of(lines, subject_ranges)])
        for s in peer_sections:
            out.extend(["", f"===== {s.role} REVIEW (ROUND {round_num - 1}) =====", _text_of(lines, [s.line_range()])])
        if own_section:
            out.extend(["", f"===== YOUR OWN REVIEW (ROUND {round_num - 1}) =====", _text_of(lines, [own_section.line_range()])])
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------
# Harness invocation
# --------------------------------------------------------------------------------------------------

@dataclass
class RoleOutcome:
    returncode: int
    reply: str
    stderr: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


def _parse_claude_json(stdout: str) -> Tuple[str, Dict[str, Any]]:
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout or "", {"output_format_error": "claude did not return JSON output"}
    if not isinstance(data, dict):
        return stdout, {"output_format_error": "claude JSON output is not an object"}
    usage = data.get("usage") or {}
    metrics: Dict[str, Any] = {
        "duration_ms": data.get("duration_ms"),
        "num_turns": data.get("num_turns"),
        "total_cost_usd": data.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "is_error": bool(data.get("is_error")),
    }
    return str(data.get("result") or ""), metrics


def _non_interactive_env(marker: str) -> Dict[str, str]:
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env[marker] = "1"
    return env


def invoke_claude(prompt: str, model: str, effort: str, timeout_s: int = DEFAULT_CLAUDE_TIMEOUT_S) -> RoleOutcome:
    """Fresh, non-persisted, read-only headless claude session. The prompt goes through stdin."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--effort", effort,
        "--tools", CLAUDE_REVIEW_TOOLS,
        "--strict-mcp-config",
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]
    try:
        process = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            env=_non_interactive_env("CLAUDE_NON_INTERACTIVE"),
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return RoleOutcome(124, "", f"claude timed out after {timeout_s}s", {"timed_out": True})
    except FileNotFoundError:
        return RoleOutcome(127, "", "claude CLI not found on PATH")
    reply, metrics = _parse_claude_json(process.stdout)
    code = process.returncode
    if code == 0 and metrics.get("is_error"):
        code = 1
    return RoleOutcome(code, reply, process.stderr, metrics)


def invoke_agy(prompt: str, model: str, effort: Optional[str] = None, timeout_s: int = DEFAULT_AGY_TIMEOUT_S) -> RoleOutcome:
    """Fresh headless agy session (never `-c`: the plan file carries the history)."""
    cmd = ["agy", "-p", prompt, "--model", model]
    if effort:
        cmd.extend(["--effort", effort])
    cmd.extend(["--dangerously-skip-permissions", "--print-timeout", f"{max(1, timeout_s // 60)}m"])
    if sys.platform == "win32" and len(subprocess.list2cmdline(cmd)) > WINDOWS_CMD_LIMIT:
        return RoleOutcome(1, "", "agy prompt exceeds the Windows command-line limit")
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=_non_interactive_env("AGY_NON_INTERACTIVE"),
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s + 60,
        )
    except subprocess.TimeoutExpired:
        return RoleOutcome(124, "", f"agy timed out after {timeout_s}s", {"timed_out": True})
    except FileNotFoundError:
        return RoleOutcome(127, "", "agy CLI not found on PATH")
    return RoleOutcome(process.returncode, process.stdout or "", process.stderr or "")


@dataclass(frozen=True)
class RoleConfig:
    model: str
    effort: Optional[str]


def role_configs(args: argparse.Namespace) -> Dict[str, RoleConfig]:
    return {
        "FA": RoleConfig(args.fa_model, args.fa_effort),
        "TL": RoleConfig(args.tl_model, args.tl_effort),
        "QA": RoleConfig(args.qa_model, args.qa_effort),
    }


def run_role(abbr: str, prompt: str, config: RoleConfig, args: argparse.Namespace) -> RoleOutcome:
    if ROLES[abbr].harness == "claude":
        return invoke_claude(prompt, config.model, config.effort or DEFAULT_FA_EFFORT, args.claude_timeout)
    return invoke_agy(prompt, config.model, config.effort, args.agy_timeout)


# --------------------------------------------------------------------------------------------------
# Review parsing
# --------------------------------------------------------------------------------------------------

@dataclass
class Review:
    verdict: str
    agreed: List[str] = field(default_factory=list)
    blocking: List[str] = field(default_factory=list)
    non_blocking: List[str] = field(default_factory=list)
    position: str = ""
    compromise: str = ""
    flags: List[str] = field(default_factory=list)


_NONE_RE = re.compile(r"^\W*(none|n/?a)\W*$", re.IGNORECASE)
_BLOCKING_RE = re.compile(r"^\**\[BLOCKING\]\**:?\s*(.*)$", re.IGNORECASE)
_NON_BLOCKING_RE = re.compile(r"^\**\[NON-BLOCKING\]\**:?\s*(.*)$", re.IGNORECASE)
_VERDICT_RE = re.compile(r"VERDICT:\s*\**\s*(AGREED|DISAGREED)", re.IGNORECASE)


def _subsection(reply: str, title: str) -> str:
    m = re.search(
        rf"^#{{2,4}}\s*{re.escape(title)}\s*$(.*?)(?=^#{{2,4}}\s|^\**VERDICT:|\Z)",
        reply,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _bullets(text: str) -> List[str]:
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:2] in ("- ", "* "):
            item = stripped[2:].strip()
            if item and not _NONE_RE.match(item):
                items.append(item)
    return items


def _paragraph(text: str) -> str:
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return "" if _NONE_RE.match(text or "none") else text


def parse_review(reply: str) -> Review:
    reply = normalize(reply or "")
    verdicts = _VERDICT_RE.findall(reply)
    flags: List[str] = []
    if verdicts:
        verdict = verdicts[-1].upper()
    else:
        verdict = "DISAGREED"  # fail closed
        flags.append("verdict_missing")

    blocking, non_blocking = [], []
    for item in _bullets(_subsection(reply, "Objections")):
        m = _BLOCKING_RE.match(item)
        if m:
            if m.group(1).strip() and not _NONE_RE.match(m.group(1)):
                blocking.append(m.group(1).strip())
            continue
        m = _NON_BLOCKING_RE.match(item)
        non_blocking.append((m.group(1) if m else item).strip())

    if verdict == "AGREED" and blocking:
        verdict = "DISAGREED"
        flags.append("agreed_with_blocking_objections")

    return Review(
        verdict=verdict,
        agreed=_bullets(_subsection(reply, "Agreed Points")),
        blocking=blocking,
        non_blocking=[n for n in non_blocking if n],
        position=_paragraph(_subsection(reply, "Position")),
        compromise=_paragraph(_subsection(reply, "Proposed Compromise")),
        flags=flags,
    )


def sanitize_reply(reply: str) -> str:
    """Demotes `#`/`##` headings (outside code fences) so a reply can never split the plan's sections."""
    out, in_fence = [], False
    for line in normalize(reply).strip().split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and SECTION_HEADING_RE.match(line):
            line = "### " + line.lstrip("#").strip()
        out.append(line.rstrip())
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------

def render_round_block(abbr: str, round_num: int, config: RoleConfig, reply: str) -> str:
    role = ROLES[abbr]
    mode = "Cross-Rebuttal" if round_num > 1 else "Independent Review"
    effort = config.effort or "(in model name)"
    return (
        f"## ⚖️ Council Round {round_num}: {role.name} ({abbr})\n"
        f"_Harness: `{role.harness}` · Model: `{config.model}` · Effort: `{effort}` · Mode: {mode}_\n\n"
        f"{sanitize_reply(reply)}"
    )


def _merge_points(reviews: Dict[str, Review]) -> List[Tuple[str, List[str]]]:
    merged: Dict[str, Tuple[str, List[str]]] = {}
    for abbr in ROLE_ORDER:
        for point in reviews[abbr].agreed:
            key = re.sub(r"\W+", " ", point).strip().lower()
            if key not in merged:
                merged[key] = (point, [])
            merged[key][1].append(abbr)
    return list(merged.values())


def render_consensus(round_num: int, reviews: Dict[str, Review]) -> str:
    out = [
        f"## ✅ Council Consensus (Round {round_num})",
        "**Status:** ✅ Unanimous agreement: FA, TL and QA returned `VERDICT: AGREED` with no BLOCKING objections.",
        "",
        "### Agreed Points",
    ]
    points = _merge_points(reviews)
    out.extend([f"- {text} ({', '.join(who)})" for text, who in points] or ["- (none listed)"])
    notes = [f"- [{abbr}] {note}" for abbr in ROLE_ORDER for note in reviews[abbr].non_blocking]
    out.extend(["", "### Non-Blocking Notes to Fold In"])
    out.extend(notes or ["- None"])
    out.extend([
        "",
        "### Next Step",
        "Compile `## 🎯 Final Decision Plan & User Story Specification` from the subject and the notes above, "
        "get operator approval, then run `/provision-story`.",
    ])
    return "\n".join(out)


def recommended_compromise(reviews: Dict[str, Review]) -> List[str]:
    agreeing = [a for a in ROLE_ORDER if reviews[a].verdict == "AGREED"]
    dissenting = [a for a in ROLE_ORDER if a not in agreeing]
    if len(agreeing) >= 2:
        out = [
            f"**Majority ({', '.join(agreeing)}) accepts the subject.** Adopt it and resolve the dissenting "
            f"BLOCKING objections from {', '.join(dissenting)} with the smallest change below:",
        ]
        for abbr in dissenting:
            out.append(f"- **{abbr}:** {reviews[abbr].compromise or 'No compromise proposed; address the BLOCKING objections listed above.'}")
        return out
    out = ["**No majority.** Synthesized mitigation plan: apply every member's minimal compromise below, then re-run the council."]
    for abbr in ROLE_ORDER:
        out.append(f"- **{abbr}:** {reviews[abbr].compromise or ('None (agrees).' if reviews[abbr].verdict == 'AGREED' else 'No compromise proposed.')}")
    return out


def render_dossier(round_num: int, reviews: Dict[str, Review], plan_arg: str) -> str:
    out = [
        f"## ⚠️ Council Deadlock Escalation Report (Round {round_num})",
        "",
        "### 1. Consensus Items (Agreed)",
    ]
    unanimous = [(text, who) for text, who in _merge_points(reviews) if len(who) == len(ROLE_ORDER)]
    partial = [(text, who) for text, who in _merge_points(reviews) if len(who) < len(ROLE_ORDER)]
    out.extend(f"- {text}" for text, _ in unanimous)
    out.extend(f"- {text} ({', '.join(who)})" for text, who in partial)
    if not unanimous and not partial:
        out.append("- (none listed)")

    out.extend(["", "### 2. Disputed Items (Unresolved)"])
    for abbr in ROLE_ORDER:
        review = reviews[abbr]
        label = "QA Lead" if abbr == "QA" else ROLES[abbr].name
        position = review.position or "(no position stated)"
        out.append(f"- **{abbr} Position** ({label}, `VERDICT: {review.verdict}`): {position}")
        out.extend(f"  - [BLOCKING] {item}" for item in review.blocking)

    out.extend(["", "### 3. Recommended Compromise"])
    out.extend(recommended_compromise(reviews))

    out.extend([
        "",
        "### 4. Required Action",
        "Select one of the following commands:",
        f"- `orchestrator council{plan_arg} --continue 2` (Run 2 additional deliberation rounds)",
        f"- `orchestrator council{plan_arg} --proceed provision-story` (Accept compromise and invoke provision-story)",
    ])
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------------

def _plan_arg(args: argparse.Namespace, plan_source: str) -> str:
    return f' --plan "{args.plan}"' if plan_source == "argument" and args.plan else ""


def unresolved_points(reviews: Dict[str, Review]) -> List[str]:
    return [f"[{abbr}] {item}" for abbr in ROLE_ORDER for item in reviews[abbr].blocking]


def attach_diff(plan_path: Path, diff_range: str, max_chars: int) -> Optional[str]:
    """Appends `git diff <range>` as the council subject. Returns an error message on failure."""
    try:
        proc = subprocess.run(
            ["git", "diff", diff_range],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return "git not found on PATH"
    if proc.returncode != 0:
        return f"git diff {diff_range} failed: {proc.stderr.strip()}"
    diff = proc.stdout.strip("\n")
    if not diff.strip():
        return f"git diff {diff_range} is empty"
    note = ""
    if len(diff) > max_chars:
        diff = diff[:max_chars]
        note = f"\n_Diff truncated to {max_chars} characters._"
    fence = "````" if "```" in diff else "```"
    append_blocks(plan_path, [f"## 🧾 Council Subject: git diff {diff_range}\n{fence}diff\n{diff}\n{fence}{note}"])
    return None


def run_round(round_num: int, plan_path: Path, args: argparse.Namespace) -> Tuple[Dict[str, RoleOutcome], List[str]]:
    """Runs FA, TL and QA in parallel and appends their replies. Returns (outcomes, warnings)."""
    lines, sections, state = load_state(plan_path, args.max_rounds)
    configs = role_configs(args)
    prompts = {
        abbr: build_prompt(ROLES[abbr], round_num, plan_path, lines, sections, state, inline=ROLES[abbr].harness == "claude")
        for abbr in ROLE_ORDER
    }
    snapshot = read_raw(plan_path)
    with ThreadPoolExecutor(max_workers=len(ROLE_ORDER)) as pool:
        futures = {abbr: pool.submit(run_role, abbr, prompts[abbr], configs[abbr], args) for abbr in ROLE_ORDER}
        outcomes = {abbr: futures[abbr].result() for abbr in ROLE_ORDER}

    warnings: List[str] = []
    if read_raw(plan_path) != snapshot:
        write_raw(plan_path, snapshot)
        warnings.append(f"A reviewer modified the plan file during Round {round_num}; the edit was reverted.")

    if all(o.returncode == 0 and o.reply.strip() for o in outcomes.values()):
        append_blocks(plan_path, [render_round_block(a, round_num, configs[a], outcomes[a].reply) for a in ROLE_ORDER])
    return outcomes, warnings


def deliberate(plan_path: Path, plan_source: str, args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    _, _, state = load_state(plan_path, args.max_rounds)
    round_num = state.next_round()
    max_rounds = args.max_rounds if round_num == 1 else state.max_rounds
    result: Dict[str, Any] = {"plan_path": str(plan_path), "plan_source": plan_source, "warnings": []}

    if args.diff_range:
        if round_num != 1:
            return EXIT_ERROR, {**result, "status": "error", "error": "--diff-range can only be attached at the start of a council session."}
        error = attach_diff(plan_path, args.diff_range, args.max_diff_chars)
        if error:
            return EXIT_ERROR, {**result, "status": "error", "error": error}

    reviews: Dict[str, Review] = {}
    while round_num <= max_rounds:
        outcomes, warnings = run_round(round_num, plan_path, args)
        result["warnings"].extend(warnings)
        if "FA" in outcomes and outcomes["FA"].metrics:
            result.setdefault("fa_metrics", []).append({"round": round_num, **outcomes["FA"].metrics})
        failed = {a: o for a, o in outcomes.items() if o.returncode != 0 or not o.reply.strip()}
        if failed:
            return EXIT_ERROR, {
                **result,
                "status": "error",
                "round": round_num,
                "error": "Reviewer invocation failed; the round was not recorded.",
                "failures": {a: {"returncode": o.returncode, "stderr": o.stderr.strip()[-2000:]} for a, o in failed.items()},
            }

        reviews = {abbr: parse_review(outcomes[abbr].reply) for abbr in ROLE_ORDER}
        result.update({
            "round": round_num,
            "max_rounds": max_rounds,
            "verdicts": {a: reviews[a].verdict for a in ROLE_ORDER},
            "review_flags": {a: reviews[a].flags for a in ROLE_ORDER if reviews[a].flags},
        })
        if all(r.verdict == "AGREED" for r in reviews.values()):
            append_blocks(plan_path, [render_consensus(round_num, reviews)])
            return EXIT_OK, {
                **result,
                "status": STATUS_CONSENSUS,
                "unresolved_points": [],
                "next_action": "Compile the Final Decision Plan & User Story Specification, get operator approval, then run /provision-story.",
            }
        round_num += 1

    append_blocks(plan_path, [render_dossier(round_num - 1, reviews, _plan_arg(args, plan_source))])
    return EXIT_DEADLOCK, {
        **result,
        "status": STATUS_DEADLOCKED,
        "unresolved_points": unresolved_points(reviews),
        "next_action": "Show the Council Deadlock Escalation Report to the operator and wait for "
        "`--continue N` or `--proceed provision-story`.",
    }


def status_payload(plan_path: Path, plan_source: str, state: CouncilState) -> Dict[str, Any]:
    return {
        "status": "status",
        "council_status": state.status,
        "rounds_completed": state.rounds_completed,
        "max_rounds": state.max_rounds,
        "continuation_rounds": state.continuation,
        "plan_path": str(plan_path),
        "plan_source": plan_source,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Architect Council (FA, TL, QA) bounded-round review.")
    parser.add_argument("--plan", "--path", "--file", dest="plan", type=str, default=None,
                        help=f"Plan / requirement file (default: {DEFAULT_PLAN_RELATIVE.as_posix()}).")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--continue", dest="continue_rounds", type=int, default=None, metavar="N",
                        help="After a deadlock: run N additional cross-rebuttal rounds.")
    action.add_argument("--proceed", dest="proceed", choices=PROCEED_TARGETS, default=None,
                        help="After a deadlock: accept the Recommended Compromise and hand off to the target skill.")
    action.add_argument("--check-status", action="store_true", help="Report council state without invoking any LLM.")
    parser.add_argument("--diff-range", type=str, default=None,
                        help="Attach `git diff <range>` (run in the current directory) as the council subject at session start.")
    parser.add_argument("--max-diff-chars", type=int, default=DEFAULT_MAX_DIFF_CHARS)
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                        help=f"Rounds before a deadlock is declared (default: {DEFAULT_MAX_ROUNDS}).")
    parser.add_argument("--fa-model", default=DEFAULT_FA_MODEL)
    parser.add_argument("--fa-effort", default=DEFAULT_FA_EFFORT)
    parser.add_argument("--tl-model", default=DEFAULT_TL_MODEL)
    parser.add_argument("--tl-effort", default=DEFAULT_TL_EFFORT)
    parser.add_argument("--qa-model", default=DEFAULT_QA_MODEL)
    parser.add_argument("--qa-effort", default=DEFAULT_QA_EFFORT)
    parser.add_argument("--claude-timeout", type=int, default=DEFAULT_CLAUDE_TIMEOUT_S)
    parser.add_argument("--agy-timeout", type=int, default=DEFAULT_AGY_TIMEOUT_S)
    return parser


def run(argv: Optional[List[str]] = None) -> Tuple[int, Dict[str, Any]]:
    args = build_parser().parse_args(argv)
    try:
        plan_path, plan_source = find_plan_file(args.plan)
    except FileNotFoundError as exc:
        return EXIT_ERROR, {"status": "error", "error": str(exc)}
    if plan_source == "default":
        print(f"[council] No --plan given; using default plan file {plan_path}", file=sys.stderr)

    _, _, state = load_state(plan_path, args.max_rounds)
    base = {"plan_path": str(plan_path), "plan_source": plan_source}

    if args.check_status:
        return EXIT_OK, status_payload(plan_path, plan_source, state)

    if args.continue_rounds is not None:
        if args.continue_rounds < 1:
            return EXIT_ERROR, {**base, "status": "error", "error": "--continue needs a positive number of rounds."}
        if state.status != STATUS_DEADLOCKED:
            return EXIT_ERROR, {**base, "status": "error", "error": f"--continue only applies after a deadlock (council status: {state.status})."}
        new_cap = state.max_rounds + args.continue_rounds
        append_blocks(plan_path, [
            f"## ⏩ Council Continuation: +{args.continue_rounds} rounds\n"
            f"_Operator extended the deliberation by {args.continue_rounds} cross-rebuttal rounds (new cap: {new_cap})._"
        ])
        return deliberate(plan_path, plan_source, args)

    if args.proceed:
        if state.status != STATUS_DEADLOCKED:
            return EXIT_ERROR, {**base, "status": "error", "error": f"--proceed only applies after a deadlock (council status: {state.status})."}
        append_blocks(plan_path, [
            f"## ✅ Council Compromise Accepted (Round {state.rounds_completed})\n"
            f"**Resolution Type:** Operator accepted the Recommended Compromise from the Council Deadlock Escalation Report.\n"
            f"**Hand-off:** `{args.proceed}`. Compile `## 🎯 Final Decision Plan & User Story Specification` from the "
            f"compromise, then run `/{args.proceed}` (`python -m orchestrator.cli story provision <project> --file <plan>`)."
        ])
        return EXIT_OK, {
            **base,
            "status": STATUS_ACCEPTED,
            "round": state.rounds_completed,
            "proceed": args.proceed,
            "next_action": "Compile the Final Decision Plan from the Recommended Compromise, then run /provision-story.",
        }

    if state.status == STATUS_DEADLOCKED:
        return EXIT_DEADLOCK, {
            **status_payload(plan_path, plan_source, state),
            "status": STATUS_DEADLOCKED,
            "next_action": "The council is deadlocked. Run with --continue N or --proceed provision-story.",
        }

    return deliberate(plan_path, plan_source, args)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    code, payload = run(argv)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
