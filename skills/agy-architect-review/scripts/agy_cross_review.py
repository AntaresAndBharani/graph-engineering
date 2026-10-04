#!/usr/bin/env python3
"""
agy_cross_review.py - Two-Tier Architectural & Functional Cross-Review Orchestration Script.
Coordinates the Two-Tier Review Council:
  Tier 1: Tri-Party Council
    1. Author (Synthesizer via the plan file)
    2. System Architect (Gemini 3.8 Flash High, via agy CLI)
    3. QA Guardian (Gemini 3.8 Flash Medium, via agy CLI)
  Tier 2: Chief Functional Architect
    4. Chief Functional Architect (Claude Opus 5.5 via claude CLI; high effort, max on the final executive round)

Execution Protocol:
  - Council debates for up to 3 iterative rounds per cycle.
  - Every 3 iterations of the council (or upon dual council consensus), the proposal
    passes to the Chief Functional Architect.
  - The Chief Functional Architect reviews in the most critical way possible to verify:
      1. Does the solution genuinely fix the initial problem and root cause?
      2. Does the design strictly adhere to industry standards and best practices?
  - If changes are required, the Chief Functional Architect provides actionable amendments
    and sends the plan back to the Council for a fresh 3-iteration cycle.
  - After the 3rd iteration of the Chief Functional Architect (hard cap), if no agreement
    has been reached, the Chief Functional Architect unilaterally decides and provides the final solution.

Token discipline:
  - Completed plans are archived to an archive/ folder next to the plan file so it only holds the active plan.
  - Every round is a fresh, non-persisted session; reviewers read only the line ranges they need.
  - claude reviewers (Chief FA, and the Architect when it runs on Claude) get the plan excerpts inlined
    through stdin and return their section as the reply, which this script appends. They only get
    read-only code tools (no Bash, Edit/Write or Agent subagents) and no MCP servers, so a review is a
    few turns instead of a page-by-page read-and-edit loop.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ARCHITECT_MODEL = "gemini-3.8-flash-high"
DEFAULT_ARCHITECT_EFFORT: Optional[str] = None
# agy encodes the reasoning effort in the model name (gemini-3.8-flash-{low,medium,high}).
DEFAULT_QA_MODEL = "gemini-3.8-flash-medium"
DEFAULT_QA_EFFORT: Optional[str] = None
DEFAULT_CHIEF_FA_MODEL = "claude-opus-5-5"
# Same effort as interactive Claude Code; max is kept for the final round, which dictates the binding solution.
DEFAULT_CHIEF_FA_EFFORT = "high"
DEFAULT_CHIEF_FA_FINAL_EFFORT = "max"
DEFAULT_CLAUDE_TIMEOUT_S = 20 * 60

# Read-only code inspection for claude reviewers: no Bash, Edit/Write or Agent (subagent fan-out).
CLAUDE_REVIEW_TOOLS = "Read,Grep,Glob"
# The final Chief FA round also rewrites the Final Decision Plan in place.
CLAUDE_EXECUTIVE_TOOLS = "Read,Grep,Glob,Edit"

PLAN_HEADING_RE = re.compile(r"^#\s*📋\s*Implementation Plan")
ARCHITECT_HEADING_RE = r"##\s*🏛️\s*(?:(?:Gemini|Principal|System)\s+)?Architect\s+Review Iteration"
QA_HEADING_RE = r"##\s*🧪\s*(?:(?:Claude|Gemini)\s+)?QA\s+Review Iteration"
CHIEF_FA_HEADING_RE = r"##\s*🏛️\s*Chief\s+Functional\s+Architect\s+Review\s+Iteration"
AUTHOR_HEADING_RE = r"##\s*(?:🔍|🚀|💬)\s*(?:Boost\s*)?Review Iteration"
FINAL_PLAN_HEADING_RE = r"##\s*🎯\s*Final Decision Plan"
PROPOSAL_HEADING_RE = r"##\s*(?:📝|📋)\s*Initial"

VERDICT_RULES = [
    "Classify every objection as **[BLOCKING]** or **[NON-BLOCKING]**.",
    "BLOCKING is reserved for defects that would ship a wrong or unsafe result: correctness bugs, data loss, race conditions, "
    "security holes, dropped or altered operator requirements, or acceptance criteria that cannot be tested.",
    "Style preferences, optional hardening, and nice-to-have improvements are NON-BLOCKING.",
    "End with `VERDICT: AGREED` when there are no BLOCKING objections (NON-BLOCKING notes are allowed), "
    "otherwise `VERDICT: DISAGREED`.",
]


def find_plan_file(custom_path: str | None = None) -> Path:
    if custom_path:
        p = Path(custom_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Specified plan file not found: {p}")
        return p

    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "docs" / "draft-requisites" / "implementation-plan.md"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find 'docs/draft-requisites/implementation-plan.md'. "
        "Please ensure the implementation plan exists in the target project workspace."
    )


def _plan_start_lines(lines: List[str]) -> List[int]:
    """0-based indexes of top-level `# 📋 Implementation Plan` headings outside fenced code blocks."""
    starts: List[int] = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and PLAN_HEADING_RE.match(line):
            starts.append(i)
    return starts


def active_section(plan_content: str) -> str:
    """Returns the text of the latest implementation plan (the whole file if it has no plan heading)."""
    lines = plan_content.splitlines(keepends=True)
    starts = _plan_start_lines(lines)
    return "".join(lines[starts[-1]:]) if starts else plan_content


def count_iterations(plan_content: str) -> Tuple[int, int, int, int]:
    """
    Returns (author_rounds, architect_rounds, qa_rounds, chief_fa_rounds) for the active implementation plan.
    """
    section = active_section(plan_content)
    author_rounds = len(re.findall(rf"^{AUTHOR_HEADING_RE}\s+(\d+)", section, re.MULTILINE))
    architect_rounds = len(re.findall(rf"^{ARCHITECT_HEADING_RE}\s+(\d+)", section, re.MULTILINE))
    qa_rounds = len(re.findall(rf"^{QA_HEADING_RE}\s+(\d+)", section, re.MULTILINE))
    chief_fa_rounds = len(re.findall(rf"^{CHIEF_FA_HEADING_RE}\s+(\d+)", section, re.MULTILINE))
    return author_rounds, architect_rounds, qa_rounds, chief_fa_rounds


def count_council_rounds_in_cycle(plan_content: str) -> int:
    """
    Counts completed council rounds in the current cycle (since the last Chief FA iteration).
    """
    section = active_section(plan_content)
    chief_fa_matches = list(re.finditer(rf"^{CHIEF_FA_HEADING_RE}\s+\d+", section, re.MULTILINE))
    if chief_fa_matches:
        last_chief_fa_end = chief_fa_matches[-1].end()
        cycle_text = section[last_chief_fa_end:]
    else:
        cycle_text = section

    arch_rounds = len(re.findall(rf"^{ARCHITECT_HEADING_RE}\s+\d+", cycle_text, re.MULTILINE))
    qa_rounds = len(re.findall(rf"^{QA_HEADING_RE}\s+\d+", cycle_text, re.MULTILINE))
    return max(arch_rounds, qa_rounds)


def _slugify(title: str) -> str:
    title = PLAN_HEADING_RE.sub("", title).strip()
    title = re.sub(r"^[&:\s]*(?:Refinement(?:\s+Lifecycle)?\s*:?)?", "", title).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].rstrip("-") or "plan"


def archive_plans(plan_path: Path, keep_active: bool = True, now: Optional[datetime] = None) -> List[Path]:
    """
    Moves completed plans out of the plan file into an archive/ folder next to it,
    one file per plan, preserving the audit trail verbatim.
    keep_active=True archives every plan except the latest; keep_active=False archives all of them.
    Returns the archive files written.
    """
    content = plan_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    starts = _plan_start_lines(lines)
    if not starts or (keep_active and len(starts) < 2):
        return []

    cut = starts[-1] if keep_active else len(lines)

    # Any preamble before the first plan heading travels with the first archived plan.
    boundaries = [0, *starts[1:]] if starts[0] > 0 else starts
    chunks = [(b, e) for b, e in zip(boundaries, [*boundaries[1:], len(lines)]) if b < cut]

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    archive_dir = plan_path.parent / "archive"
    archive_dir.mkdir(exist_ok=True)
    written: List[Path] = []
    for idx, (begin, end) in enumerate(chunks, start=1):
        end = min(end, cut)
        title = next((ln for ln in lines[begin:end] if PLAN_HEADING_RE.match(ln)), "plan")
        target = archive_dir / f"{stamp}-{idx:02d}-{_slugify(title)}.md"
        target.write_text("".join(lines[begin:end]), encoding="utf-8")
        written.append(target)

    remaining = "".join(lines[cut:])
    plan_path.write_text(remaining, encoding="utf-8")
    return written


def _section_ranges(lines: List[str], offset: int) -> List[Tuple[int, int, str]]:
    """(start_line, end_line, heading) for every `## ` section from `offset`, 1-based and fence-aware."""
    headings: List[Tuple[int, str]] = []
    in_fence = False
    for i in range(offset, len(lines)):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#{1,2}\s", line):
            headings.append((i, line.strip()))
    ranges = []
    for n, (i, heading) in enumerate(headings):
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        if heading.startswith("## "):
            ranges.append((i + 1, end, heading))
    return ranges


def _guide_ranges(plan_content: str, role: str, round_num: int) -> List[Tuple[str, Tuple[int, int, str]]]:
    """
    (label, (start_line, end_line, heading)) for every section a reviewer needs, so it never pages
    through the whole file: the operator's original proposal, the current Final Decision Plan, the
    latest author iteration, and previous relevant reviews. Lines are 1-based and inclusive.
    Falls back to the whole active plan when none of those sections exist.
    """
    lines = plan_content.splitlines()
    starts = _plan_start_lines(lines)
    offset = starts[-1] if starts else 0
    ranges = _section_ranges(lines, offset)

    def last(pattern: str) -> Optional[Tuple[int, int, str]]:
        matches = [r for r in ranges if re.match(pattern, r[2])]
        return matches[-1] if matches else None

    def first(pattern: str) -> Optional[Tuple[int, int, str]]:
        matches = [r for r in ranges if re.match(pattern, r[2])]
        return matches[0] if matches else None

    wanted = [
        ("Operator's original proposal / requirements", first(PROPOSAL_HEADING_RE)),
        ("Current Final Decision Plan", last(FINAL_PLAN_HEADING_RE)),
        ("Latest author iteration", last(AUTHOR_HEADING_RE)),
    ]

    if role == "chief_fa":
        wanted.append(("Latest Architect review", last(ARCHITECT_HEADING_RE)))
        wanted.append(("Latest QA review", last(QA_HEADING_RE)))
        if round_num > 1:
            wanted.append((f"Your previous review (iteration {round_num - 1})", last(rf"{CHIEF_FA_HEADING_RE}\s+{round_num - 1}\b")))
    else:
        own_heading = ARCHITECT_HEADING_RE if role == "architect" else QA_HEADING_RE
        if round_num > 1:
            wanted.append((f"Your previous review (round {round_num - 1})", last(rf"{own_heading}\s+{round_num - 1}\b")))
        chief_fa_review = last(CHIEF_FA_HEADING_RE)
        if chief_fa_review:
            wanted.append(("Latest Chief Functional Architect review", chief_fa_review))

    found = [(label, r) for label, r in wanted if r]
    if not found:
        found = [("Active plan", (offset + 1, len(lines), ""))]
    return found


def reading_guide(plan_content: str, role: str, round_num: int) -> List[str]:
    """The line ranges a reviewer must read, one `- label: lines a-b (`heading`)` entry per section."""
    return [
        f"- {label}: lines {start}-{end}" + (f" (`{heading[:90]}`)" if heading else "")
        for label, (start, end, heading) in _guide_ranges(plan_content, role, round_num)
    ]


def plan_excerpts(plan_content: str, role: str, round_num: int) -> str:
    """The reading-guide sections themselves, so a claude reviewer gets them inline instead of reading the file."""
    lines = plan_content.splitlines()
    blocks = []
    for label, (start, end, _heading) in _guide_ranges(plan_content, role, round_num):
        body = "\n".join(lines[start - 1:end])
        blocks.append(f'<plan_excerpt label="{label}" lines="{start}-{end}">\n{body}\n</plan_excerpt>')
    return "\n\n".join(blocks)


def _common_rules(abs_path: str, guide: List[str], heading: str, inline: bool = False) -> List[str]:
    """
    Operational rules shared by every reviewer. `inline=True` is the claude mode: the plan excerpts
    are embedded in the prompt and the review comes back as the reply instead of a file edit.
    """
    if inline:
        reading_rule = [
            f"1. PLAN: the sections of '{abs_path}' you need are embedded above in <plan_excerpt> blocks. "
            "Do NOT open the plan file; everything relevant from it is already in this prompt.",
        ]
        output_rule = (
            "3. RETURN YOUR REVIEW as your final reply (the orchestrator appends it to the plan file; "
            "do NOT write it to any file). Start the reply with this exact heading line and output nothing before it:"
        )
    else:
        reading_rule = [
            f"1. TARGET FILE: '{abs_path}'. Read ONLY these line ranges (use offset/limit reads); do not page through the rest of the file:",
            *guide,
        ]
        output_rule = f"3. APPEND YOUR REVIEW at the very end of '{abs_path}' with a new section titled exactly:"
    return [
        *reading_rule,
        "2. CODEBASE INSPECTION: Only open files the plan names, plus targeted grep lookups to verify specific claims (at most ~8 files). "
        "Do NOT execute test suites or background commands; evaluate static code directly.",
        output_rule,
        "",
        heading,
        "",
    ]


def _with_excerpts(prompt_lines: List[str], excerpts: Optional[str]) -> str:
    """Puts the inlined plan excerpts ahead of the instructions (long material first, then the task)."""
    if excerpts is None:
        return "\n".join(prompt_lines)
    return "\n".join(["PLAN EXCERPTS (verbatim from the plan file):", "", excerpts, "", *prompt_lines])


def _summary_rule(abs_path: str, inline: bool, verdicts: str) -> str:
    if inline:
        return f"4. OUTPUT: reply with your review section only (heading through the final `VERDICT:` line), ending with {verdicts}."
    return f"4. OUTPUT SUMMARY: Once '{abs_path}' is updated, output a concise 3-5 bullet summary stating {verdicts} and listing any BLOCKING objections."


def build_architect_prompt(round_num: int, plan_path: Path, guide: List[str], excerpts: Optional[str] = None) -> str:
    """`excerpts` switches to the inline claude mode (plan sections in the prompt, review returned as the reply)."""
    abs_path = plan_path.resolve().as_posix()
    lines = [
        f"You are the System Architect conducting Round {round_num} of the Architectural Review of the active implementation plan in '{abs_path}'.",
        "Be rigorous about real defects and proportionate about everything else.",
        "",
        "OPERATIONAL RULES:",
        *_common_rules(abs_path, guide, f"## 🏛️ Architect Review Iteration {round_num}", inline=excerpts is not None),
        "Structure your section with:",
        "- ### ⚖️ Architecture & Drawbacks Critique (race conditions, edge cases, performance, backward compatibility)",
        "- ### 🚨 Objections (each tagged [BLOCKING] or [NON-BLOCKING])",
        "- ### 🛠️ Required Changes (for BLOCKING objections only)",
        "- ### 🏁 Verdict",
        "",
        "VERDICT RULES:",
        *[f"- {rule}" for rule in VERDICT_RULES],
        "",
        _summary_rule(abs_path, excerpts is not None, "AGREED or DISAGREED"),
    ]
    return _with_excerpts(lines, excerpts)


def build_qa_prompt(round_num: int, plan_path: Path, guide: List[str]) -> str:
    abs_path = plan_path.resolve().as_posix()
    lines = [
        f"You are the QA Lead & Requirements Guardian conducting Round {round_num} of the Review of the active implementation plan in '{abs_path}'.",
        "",
        "OPERATIONAL RULES:",
        *_common_rules(abs_path, guide, f"## 🧪 QA Review Iteration {round_num} (Requirements & UX/UI Guardian)"),
        "QA MANDATE:",
        "- MANDATE 1 - REQUIREMENTS FIDELITY (ANTI-DRIFT GUARDIAN): Cross-check the plan against the operator's original request and constraints. "
        "Verify that optimizations, abstractions, or architect debates have NOT dropped, diluted, or altered the operator's core deliverables and invariants.",
        "- MANDATE 2 - UX/UI EXPERIENCE AUDIT (IF UI EXISTS): Review visual hierarchy, layout ergonomics, responsive states (loading, empty, error, active cursor), and user feedback.",
        "- MANDATE 3 - FUNCTIONAL RIGOR AUDIT (IF NO UI EXISTS): Audit behavioral contracts, input validation, error messages, failure modes (cold start, zero division, timeout recovery), and fail-closed safety.",
        "- MANDATE 4 - BDD ACCEPTANCE CRITERIA COMPLETENESS: Ensure Gherkin scenarios (Given/When/Then) cover happy paths, edge cases, and adversarial failure conditions unambiguously.",
        "",
        "Structure your appended section with:",
        "- ### 🎯 Requirements Fidelity & Scope Alignment Audit",
        "- ### 🖥️ UX/UI & Functional Rigor Review",
        "- ### 🚨 Objections (each tagged [BLOCKING] or [NON-BLOCKING])",
        "- ### 🧪 Acceptance Criteria & Testability Assessment",
        "- ### 🏁 Verdict",
        "",
        "VERDICT RULES:",
        *[f"- {rule}" for rule in VERDICT_RULES],
        "",
        f"4. OUTPUT SUMMARY: Once '{abs_path}' is updated, output a concise 3-5 bullet summary stating AGREED or DISAGREED and listing any BLOCKING objections.",
    ]
    return "\n".join(lines)


def build_chief_fa_prompt(
    round_num: int,
    plan_path: Path,
    guide: List[str],
    is_final_round: bool = False,
    excerpts: Optional[str] = None,
) -> str:
    """`excerpts` switches to the inline claude mode (plan sections in the prompt, review returned as the reply)."""
    abs_path = plan_path.resolve().as_posix()
    inline = excerpts is not None
    lines = [
        f"You are the Chief Functional Architect conducting Iteration {round_num} of the Two-Tier Architectural Review of the active implementation plan in '{abs_path}'.",
        "Your mandate is to evaluate the proposed solution in the MOST CRITICAL WAY POSSIBLE.",
        "",
        "EVALUATIVE MANDATE & CORE AUDIT PILLARS:",
        "1. PROBLEM-SOLUTION FIT & ROOT CAUSE: Does the proposed architecture/solution genuinely fix the initial problem, root causes, and user requirements? Has anything essential been omitted, diluted, or swept under the rug?",
        "2. INDUSTRY STANDARDS & BEST PRACTICES: Does the design strictly adhere to modern software architecture standards, robust engineering principles, separation of concerns, testability, and operational resilience?",
        "",
        "OPERATIONAL RULES:",
        *_common_rules(
            abs_path, guide, f"## 🏛️ Chief Functional Architect Review Iteration {round_num}: Problem-Solution & Standards Audit", inline=inline
        ),
        "Structure your section with:",
        "- ### 🎯 Problem-Solution Fit & Root Cause Audit",
        "- ### 📐 Industry Standards & Best Practices Compliance",
        "- ### 🚨 Deficiencies & Functional Amendments (each tagged [BLOCKING] or [NON-BLOCKING])",
        "- ### 🛠️ Mandated Amendments for Council (actionable changes to be addressed in the next council cycle)",
        "- ### 🏁 Verdict",
        "",
        "VERDICT RULES:",
        "- Classify every objection as **[BLOCKING]** or **[NON-BLOCKING]**.",
        "- Conclude with `VERDICT: APPROVED` when there are no BLOCKING deficiencies (solution genuinely fixes the root problem and follows industry standards).",
        "- Conclude with `VERDICT: CHANGES REQUIRED` if there are any BLOCKING deficiencies requiring council rework.",
    ]

    if is_final_round:
        lines.extend([
            "",
            "CRITICAL EXECUTIVE AUTHORITY (FINAL ITERATION):",
            "This is Iteration 3 (Hard Cap). If you conclude that changes are still required and agreement was not reached,",
            "you MUST exercise executive authority and unilaterally provide the definitive solution. Add a subsection to your section:",
            "- ### 🎯 Definitive Executive Resolution",
            "Detailing the exact, binding architectural and functional solution that must be implemented,",
            f"and update the `## 🎯 Final Decision Plan & User Story Specification` of '{abs_path}' in place with `[Executive Resolution: Dictated by Chief Functional Architect]`"
            + (" (use the Edit tool; that section is the only part of the file you may change)." if inline else "."),
            "End your section with `VERDICT: EXECUTIVE RESOLUTION DICTATED`.",
        ])

    lines.extend([
        "",
        _summary_rule(abs_path, inline, "APPROVED or CHANGES REQUIRED (or EXECUTIVE RESOLUTION DICTATED)"),
    ])
    return _with_excerpts(lines, excerpts)


def _parse_claude_json(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """
    Splits `claude -p --output-format json` output into (reply_text, metrics).
    Non-JSON output is returned unchanged with `output_format_error` set, so the caller can still
    recover a printed section while the metrics show that something is off.
    """
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
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "subagents_spawned": (data.get("subagent_stats") or {}).get("spawned"),
        "is_error": bool(data.get("is_error")),
    }
    return str(data.get("result") or ""), metrics


def invoke_claude(
    prompt: str,
    model: str,
    effort: str,
    tools: str = CLAUDE_REVIEW_TOOLS,
    timeout_s: int = DEFAULT_CLAUDE_TIMEOUT_S,
) -> Tuple[int, str, str, Dict[str, Any]]:
    """
    Runs one fresh, non-persisted headless claude session (no resume: the plan file carries the history).

    The prompt goes through stdin, so inlined plan excerpts never hit the Windows 32,767-character
    command-line limit. `--tools` removes every other built-in tool (with --dangerously-skip-permissions an
    allowlist would not), and `--strict-mcp-config` without `--mcp-config` starts no MCP servers.
    Returns (returncode, reply_text, stderr, metrics).
    """
    cmd = [
        "claude", "-p",
        "--model", model,
        "--effort", effort,
        "--tools", tools,
        "--strict-mcp-config",
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]

    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["CLAUDE_NON_INTERACTIVE"] = "1"

    try:
        process = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"claude timed out after {timeout_s}s", {"timed_out": True, "timeout_s": timeout_s}

    reply, metrics = _parse_claude_json(process.stdout)
    returncode = process.returncode
    if returncode == 0 and metrics.get("is_error"):
        returncode = 1
    return returncode, reply, process.stderr, metrics


def invoke_agy(prompt: str, model: str, effort: Optional[str] = None) -> Tuple[int, str, str]:
    """Runs one fresh headless agy session (never `-c`: the plan file carries the history)."""
    cmd = ["agy", "-p", prompt, "--model", model]
    if effort:
        cmd.extend(["--effort", effort])
    cmd.extend(["--dangerously-skip-permissions", "--print-timeout", "15m"])

    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["AGY_NON_INTERACTIVE"] = "1"

    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
    )
    return process.returncode, process.stdout, process.stderr


def _parse_verdict(plan_content: str, stdout: str, round_num: int, heading_re: str) -> str:
    round_match = re.search(
        rf"{heading_re}\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)",
        active_section(plan_content),
        re.DOTALL,
    )
    section_text = round_match.group(1) if round_match else stdout

    if re.search(r"VERDICT:\s*AGREED", section_text, re.IGNORECASE):
        return "AGREED"
    if re.search(r"VERDICT:\s*DISAGREED", section_text, re.IGNORECASE):
        return "DISAGREED"

    if "VERDICT: AGREED" in stdout.upper():
        return "AGREED"
    return "DISAGREED"


def parse_architect_verdict(plan_content: str, stdout: str, round_num: int) -> str:
    return _parse_verdict(plan_content, stdout, round_num, ARCHITECT_HEADING_RE)


def parse_qa_verdict(plan_content: str, stdout: str, round_num: int) -> str:
    return _parse_verdict(plan_content, stdout, round_num, QA_HEADING_RE)


def parse_chief_fa_verdict(plan_content: str, stdout: str, round_num: int) -> str:
    round_match = re.search(
        rf"{CHIEF_FA_HEADING_RE}\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)",
        active_section(plan_content),
        re.DOTALL,
    )
    section_text = round_match.group(1) if round_match else stdout

    if re.search(r"VERDICT:\s*EXECUTIVE\s+RESOLUTION", section_text, re.IGNORECASE):
        return "EXECUTIVE_RESOLUTION"
    if re.search(r"VERDICT:\s*(?:APPROVED|AGREED)", section_text, re.IGNORECASE):
        return "APPROVED"
    if re.search(r"VERDICT:\s*(?:CHANGES\s+REQUIRED|DISAGREED|REJECTED)", section_text, re.IGNORECASE):
        return "CHANGES REQUIRED"

    if "VERDICT: EXECUTIVE RESOLUTION" in stdout.upper():
        return "EXECUTIVE_RESOLUTION"
    if "VERDICT: APPROVED" in stdout.upper() or "VERDICT: AGREED" in stdout.upper():
        return "APPROVED"
    return "CHANGES REQUIRED"


def extract_chief_fa_points(plan_content: str, round_num: int) -> list[str]:
    points = []
    round_match = re.search(
        rf"{CHIEF_FA_HEADING_RE}\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)",
        active_section(plan_content),
        re.DOTALL,
    )
    if not round_match:
        return ["Unspecified concerns raised in Chief Functional Architect review."]

    bullets = []
    for line in round_match.group(1).splitlines():
        line_clean = line.strip()
        if (line_clean.startswith("- ") or line_clean.startswith("* ") or re.match(r"^\d+\.\s+", line_clean)) and len(line_clean) > 10:
            if not any(header in line_clean.upper() for header in ["VERDICT", "DATE", "AUTHOR"]):
                bullets.append(line_clean.lstrip("-*0123456789. "))

    blocking = [b for b in bullets if "[BLOCKING]" in b.upper()]
    points = blocking or bullets
    return points[:8] if points else ["Chief Functional Architect detailed specific amendments in implementation-plan.md."]


def extract_disagreement_points(
    plan_content: str,
    round_num: int,
    header_prefix: Optional[str] = None,
    heading_re: Optional[str] = None,
) -> list[str]:
    if heading_re is None:
        if header_prefix == "🧪":
            pattern = rf"{QA_HEADING_RE}\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)"
        else:
            pattern = rf"{ARCHITECT_HEADING_RE}\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)"
    else:
        pattern = rf"{heading_re}\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)"

    round_match = re.search(
        pattern,
        active_section(plan_content),
        re.DOTALL,
    )
    if not round_match:
        return ["Unspecified concerns raised in review."]

    bullets = []
    for line in round_match.group(1).splitlines():
        line_clean = line.strip()
        if (line_clean.startswith("- ") or line_clean.startswith("* ") or re.match(r"^\d+\.\s+", line_clean)) and len(line_clean) > 10:
            if not any(header in line_clean for header in ["Verdict", "VERDICT"]):
                bullets.append(line_clean.lstrip("-*0123456789. "))

    # Surface blocking objections only when reviewers tagged them; otherwise fall back to all bullets.
    blocking = [b for b in bullets if "[BLOCKING]" in b.upper()]
    points = blocking or bullets
    return points[:8] if points else ["Review section detailed specific objections in implementation-plan.md."]


def _append_from_stdout_if_missing(plan_path: Path, stdout: str, heading_re: str, round_num: int, count_index: int) -> str:
    """If a reviewer printed its section instead of appending it, append the printed section to the plan."""
    content = plan_path.read_text(encoding="utf-8", errors="replace")
    if count_iterations(content)[count_index] >= round_num:
        return content
    printed = re.search(rf"({heading_re}\s+{round_num}.*)", stdout, re.DOTALL)
    if printed:
        content = content + "\n\n" + printed.group(1).strip() + "\n"
        plan_path.write_text(content, encoding="utf-8")
    return content


def execute_chief_fa(
    plan_path: Path,
    round_num: int,
    model: str,
    effort: str,
    max_chief_fa_rounds: int,
    final_effort: str = DEFAULT_CHIEF_FA_FINAL_EFFORT,
    timeout_s: int = DEFAULT_CLAUDE_TIMEOUT_S,
) -> Tuple[int, str, str, str, List[str], Dict[str, Any]]:
    """
    Invokes Chief Functional Architect for iteration `round_num`.
    The final (executive) iteration runs at `final_effort` and may edit the Final Decision Plan in place;
    every other iteration is read-only and returns its section for this script to append.
    Returns (code, reply, updated_plan_content, verdict, unresolved_points, metrics).
    """
    plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    is_final_round = (round_num >= max_chief_fa_rounds)
    guide = reading_guide(plan_content, "chief_fa", round_num)
    prompt = build_chief_fa_prompt(
        round_num, plan_path, guide, is_final_round=is_final_round,
        excerpts=plan_excerpts(plan_content, "chief_fa", round_num),
    )
    code, stdout, _, metrics = invoke_claude(
        prompt,
        model=model,
        effort=final_effort if is_final_round else effort,
        tools=CLAUDE_EXECUTIVE_TOOLS if is_final_round else CLAUDE_REVIEW_TOOLS,
        timeout_s=timeout_s,
    )
    updated_plan_content = _append_from_stdout_if_missing(
        plan_path, stdout, CHIEF_FA_HEADING_RE, round_num, count_index=3
    )
    verdict = parse_chief_fa_verdict(updated_plan_content, stdout, round_num)
    unresolved = []
    if verdict != "APPROVED":
        unresolved = extract_chief_fa_points(updated_plan_content, round_num)
    return code, stdout, updated_plan_content, verdict, unresolved, metrics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Two-Tier Cross-Review Council (Author, System Architect, QA Guardian, Chief Functional Architect)"
    )
    parser.add_argument(
        "--plan",
        "--path",
        "--file",
        dest="plan",
        type=str,
        default=None,
        help="Path to the implementation plan file, absolute or relative to the current directory "
        "(default: search upward from the current directory for docs/draft-requisites/implementation-plan.md)",
    )
    parser.add_argument("--architect-model", type=str, default=DEFAULT_ARCHITECT_MODEL, help=f"Architect model (default: {DEFAULT_ARCHITECT_MODEL})")
    parser.add_argument("--architect-effort", type=str, default=DEFAULT_ARCHITECT_EFFORT, help=f"Architect effort (default: {DEFAULT_ARCHITECT_EFFORT})")
    parser.add_argument("--qa-model", type=str, default=DEFAULT_QA_MODEL, help=f"QA model, run via agy (default: {DEFAULT_QA_MODEL})")
    parser.add_argument("--qa-effort", type=str, default=DEFAULT_QA_EFFORT, help="Optional agy --effort for QA (default: none; the effort is part of the model name)")
    parser.add_argument("--chief-fa-model", type=str, default=DEFAULT_CHIEF_FA_MODEL, help=f"Chief Functional Architect model (default: {DEFAULT_CHIEF_FA_MODEL})")
    parser.add_argument("--chief-fa-effort", type=str, default=DEFAULT_CHIEF_FA_EFFORT, help=f"Chief Functional Architect effort (default: {DEFAULT_CHIEF_FA_EFFORT})")
    parser.add_argument(
        "--chief-fa-final-effort",
        type=str,
        default=DEFAULT_CHIEF_FA_FINAL_EFFORT,
        help=f"Chief Functional Architect effort on the final executive iteration (default: {DEFAULT_CHIEF_FA_FINAL_EFFORT})",
    )
    parser.add_argument(
        "--claude-timeout",
        type=int,
        default=DEFAULT_CLAUDE_TIMEOUT_S,
        help=f"Seconds before a claude reviewer session is killed (default: {DEFAULT_CLAUDE_TIMEOUT_S})",
    )
    parser.add_argument("--max-rounds", type=int, default=3, help="Maximum number of debate rounds for the council per cycle (default: 3)")
    parser.add_argument("--max-chief-fa-rounds", type=int, default=3, help="Maximum number of Chief Functional Architect validation rounds (default: 3)")
    parser.add_argument("--run-chief-fa", "--chief-fa", dest="run_chief_fa", action="store_true", help="Force running Chief Functional Architect review directly")
    parser.add_argument("--skip-chief-fa", action="store_true", help="Skip the Chief Functional Architect review and run the Council only")
    parser.add_argument("--skip-qa", action="store_true", help="Skip the QA review and run the Architect only")
    parser.add_argument("--check-status", action="store_true", help="Only check status and round count")
    parser.add_argument("--archive", action="store_true", help="Archive ALL plans in the live file (use before starting a new feature) and exit")

    args = parser.parse_args()

    try:
        plan_path = find_plan_file(args.plan)
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)

    plan_source = "argument" if args.plan else "default"
    if plan_source == "default":
        print(
            f"WARNING: no --plan given; using default plan file {plan_path}. "
            "Pass --plan with the file the operator named.",
            file=sys.stderr,
        )

    if args.archive:
        written = archive_plans(plan_path, keep_active=False)
        print(json.dumps({
            "status": "archived",
            "plan_path": str(plan_path),
            "plan_source": plan_source,
            "archived_files": [str(p) for p in written],
        }, indent=2))
        sys.exit(0)

    # Keep only the active plan in the live file so reviewers never re-read finished features.
    archived = [] if args.check_status else archive_plans(plan_path, keep_active=True)

    plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    author_rounds, architect_rounds, qa_rounds, chief_fa_rounds = count_iterations(plan_content)
    council_rounds_in_cycle = count_council_rounds_in_cycle(plan_content)

    if args.check_status:
        print(json.dumps({
            "status": "ok",
            "plan_path": str(plan_path),
            "plan_source": plan_source,
            "author_rounds": author_rounds,
            "architect_rounds": architect_rounds,
            "qa_rounds": qa_rounds,
            "chief_fa_rounds": chief_fa_rounds,
            "council_rounds_in_cycle": council_rounds_in_cycle,
            "max_rounds": args.max_rounds,
            "max_chief_fa_rounds": args.max_chief_fa_rounds,
        }, indent=2))
        sys.exit(0)

    # Direct Chief Functional Architect invocation
    if args.run_chief_fa:
        next_chief_fa_round = chief_fa_rounds + 1
        is_final_round = (next_chief_fa_round >= args.max_chief_fa_rounds)
        code, stdout, updated_plan_content, verdict, unresolved, metrics = execute_chief_fa(
            plan_path, next_chief_fa_round, args.chief_fa_model, args.chief_fa_effort, args.max_chief_fa_rounds,
            final_effort=args.chief_fa_final_effort, timeout_s=args.claude_timeout,
        )
        result = {
            "status": "completed",
            "role": "chief_fa",
            "chief_fa_round": next_chief_fa_round,
            "chief_fa_verdict": verdict,
            "overall_verdict": verdict,
            "max_chief_fa_rounds": args.max_chief_fa_rounds,
            "plan_path": str(plan_path),
            "plan_source": plan_source,
            "archived_files": [str(p) for p in archived],
            "unresolved_points": unresolved,
            "chief_fa_returncode": code,
            "chief_fa_stdout_snippet": stdout[:400] if stdout else "",
            "chief_fa_metrics": metrics,
        }
        if verdict == "EXECUTIVE_RESOLUTION":
            result["status"] = "executive_resolution_dictated"
            exit_code = 0
        elif verdict == "APPROVED":
            result["status"] = "completed"
            exit_code = 0
        else:
            if is_final_round:
                result["status"] = "cap_reached"
                exit_code = 2
            else:
                result["status"] = "chief_fa_changes_required"
                result["message"] = f"Chief Functional Architect Iteration {next_chief_fa_round} required changes. Returning to Council for a new 3-round cycle."
                exit_code = 0
        print(json.dumps(result, indent=2))
        sys.exit(exit_code if code == 0 else 1)

    # Standard run: check if Council has already reached cap in this cycle without Chief FA running
    if council_rounds_in_cycle >= args.max_rounds and not args.skip_chief_fa:
        next_chief_fa_round = chief_fa_rounds + 1
        is_final_round = (next_chief_fa_round >= args.max_chief_fa_rounds)
        code, stdout, updated_plan_content, verdict, unresolved, metrics = execute_chief_fa(
            plan_path, next_chief_fa_round, args.chief_fa_model, args.chief_fa_effort, args.max_chief_fa_rounds,
            final_effort=args.chief_fa_final_effort, timeout_s=args.claude_timeout,
        )
        result = {
            "status": "completed",
            "role": "chief_fa",
            "chief_fa_round": next_chief_fa_round,
            "chief_fa_verdict": verdict,
            "overall_verdict": verdict,
            "max_chief_fa_rounds": args.max_chief_fa_rounds,
            "plan_path": str(plan_path),
            "plan_source": plan_source,
            "archived_files": [str(p) for p in archived],
            "unresolved_points": unresolved,
            "chief_fa_returncode": code,
            "chief_fa_stdout_snippet": stdout[:400] if stdout else "",
            "chief_fa_metrics": metrics,
        }
        if verdict == "EXECUTIVE_RESOLUTION":
            result["status"] = "executive_resolution_dictated"
            exit_code = 0
        elif verdict == "APPROVED":
            result["status"] = "completed"
            exit_code = 0
        else:
            if is_final_round:
                result["status"] = "cap_reached"
                exit_code = 2
            else:
                result["status"] = "chief_fa_changes_required"
                result["message"] = f"Chief Functional Architect Iteration {next_chief_fa_round} required changes. Returning to Council for a new 3-round cycle."
                exit_code = 0
        print(json.dumps(result, indent=2))
        sys.exit(exit_code if code == 0 else 1)

    next_round = max(architect_rounds, qa_rounds) + 1

    # 1. System Architect
    architect_guide = reading_guide(plan_content, "architect", next_round)
    architect_metrics: Optional[Dict[str, Any]] = None
    if "gemini" in args.architect_model.lower():
        architect_prompt = build_architect_prompt(next_round, plan_path, architect_guide)
        architect_code, architect_stdout, _ = invoke_agy(architect_prompt, model=args.architect_model, effort=args.architect_effort)
    else:
        architect_prompt = build_architect_prompt(
            next_round, plan_path, architect_guide, excerpts=plan_excerpts(plan_content, "architect", next_round)
        )
        architect_code, architect_stdout, _, architect_metrics = invoke_claude(
            architect_prompt, model=args.architect_model, effort=args.architect_effort or "medium", timeout_s=args.claude_timeout
        )
    updated_plan_content = _append_from_stdout_if_missing(plan_path, architect_stdout, ARCHITECT_HEADING_RE, next_round, count_index=1)
    architect_verdict = parse_architect_verdict(updated_plan_content, architect_stdout, next_round)

    # 2. QA Guardian via agy (unless skipped)
    qa_verdict = "AGREED"
    qa_code = 0
    qa_stdout = ""
    if not args.skip_qa:
        qa_prompt = build_qa_prompt(next_round, plan_path, reading_guide(updated_plan_content, "qa", next_round))
        qa_code, qa_stdout, _ = invoke_agy(qa_prompt, model=args.qa_model, effort=args.qa_effort)
        updated_plan_content = _append_from_stdout_if_missing(plan_path, qa_stdout, QA_HEADING_RE, next_round, count_index=2)
        qa_verdict = parse_qa_verdict(updated_plan_content, qa_stdout, next_round)

    # 3. Dual Consensus Evaluation
    council_verdict = "AGREED" if (architect_verdict == "AGREED" and qa_verdict == "AGREED") else "DISAGREED"

    council_unresolved: List[str] = []
    if architect_verdict != "AGREED":
        council_unresolved.extend([f"[Architect] {p}" for p in extract_disagreement_points(updated_plan_content, next_round, heading_re=ARCHITECT_HEADING_RE)])
    if qa_verdict != "AGREED":
        council_unresolved.extend([f"[QA] {p}" for p in extract_disagreement_points(updated_plan_content, next_round, heading_re=QA_HEADING_RE)])

    new_council_rounds_in_cycle = council_rounds_in_cycle + 1
    council_cycle_finished = (council_verdict == "AGREED" or new_council_rounds_in_cycle >= args.max_rounds)

    # Check if Council cycle completion triggers Chief Functional Architect review
    if council_cycle_finished and not args.skip_chief_fa:
        next_chief_fa_round = chief_fa_rounds + 1
        is_final_round = (next_chief_fa_round >= args.max_chief_fa_rounds)
        chief_fa_code, chief_fa_stdout, updated_plan_content, chief_fa_verdict, chief_fa_unresolved, chief_fa_metrics = execute_chief_fa(
            plan_path, next_chief_fa_round, args.chief_fa_model, args.chief_fa_effort, args.max_chief_fa_rounds,
            final_effort=args.chief_fa_final_effort, timeout_s=args.claude_timeout,
        )

        result = {
            "status": "completed",
            "round": next_round,
            "council_verdict": council_verdict,
            "architect_verdict": architect_verdict,
            "qa_verdict": qa_verdict,
            "chief_fa_verdict": chief_fa_verdict,
            "chief_fa_round": next_chief_fa_round,
            "overall_verdict": chief_fa_verdict,
            "max_rounds": args.max_rounds,
            "max_chief_fa_rounds": args.max_chief_fa_rounds,
            "plan_path": str(plan_path),
            "plan_source": plan_source,
            "archived_files": [str(p) for p in archived],
            "council_unresolved_points": council_unresolved,
            "chief_fa_unresolved_points": chief_fa_unresolved,
            "architect_returncode": architect_code,
            "qa_returncode": qa_code,
            "chief_fa_returncode": chief_fa_code,
            "architect_stdout_snippet": architect_stdout[:400] if architect_stdout else "",
            "qa_stdout_snippet": qa_stdout[:400] if qa_stdout else "",
            "chief_fa_stdout_snippet": chief_fa_stdout[:400] if chief_fa_stdout else "",
            "chief_fa_metrics": chief_fa_metrics,
        }
        if architect_metrics is not None:
            result["architect_metrics"] = architect_metrics

        if chief_fa_verdict == "APPROVED":
            result["status"] = "completed"
            exit_code = 0
        elif chief_fa_verdict == "EXECUTIVE_RESOLUTION":
            result["status"] = "executive_resolution_dictated"
            exit_code = 0
        else:
            if is_final_round:
                result["status"] = "cap_reached"
                exit_code = 2
            else:
                result["status"] = "chief_fa_changes_required"
                result["message"] = f"Chief Functional Architect Iteration {next_chief_fa_round} required changes. Returning to Council for a new 3-round cycle."
                exit_code = 0

        print(json.dumps(result, indent=2))
        if architect_code != 0 or qa_code != 0 or chief_fa_code != 0:
            sys.exit(1)
        sys.exit(exit_code)

    # Council-only completion (e.g. cycle not finished yet, or --skip-chief-fa)
    result = {
        "status": "completed",
        "round": next_round,
        "council_verdict": council_verdict,
        "architect_verdict": architect_verdict,
        "qa_verdict": qa_verdict,
        "overall_verdict": council_verdict,
        "max_rounds": args.max_rounds,
        "plan_path": str(plan_path),
        "plan_source": plan_source,
        "archived_files": [str(p) for p in archived],
        "unresolved_points": council_unresolved,
        "architect_returncode": architect_code,
        "qa_returncode": qa_code,
        "architect_stdout_snippet": architect_stdout[:400] if architect_stdout else "",
        "qa_stdout_snippet": qa_stdout[:400] if qa_stdout else "",
    }
    if architect_metrics is not None:
        result["architect_metrics"] = architect_metrics

    if council_verdict != "AGREED" and new_council_rounds_in_cycle >= args.max_rounds:
        result["status"] = "cap_reached"

    print(json.dumps(result, indent=2))
    if result["status"] == "cap_reached":
        sys.exit(2)
    elif architect_code != 0 or qa_code != 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

