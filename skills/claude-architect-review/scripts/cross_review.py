#!/usr/bin/env python3
"""
cross_review.py - Cross-Review Orchestration Script between Gemini and Claude.
Supports claude CLI (Opus/Sonnet) and agy CLI (Claude Sonnet 4.6 Thinking).
Enforces up to 3 iterative debate rounds exclusively mediated via
docs/draft-requisites/implementation-plan.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Dict, Any, Tuple


def find_plan_file(custom_path: str | None = None) -> Path:
    if custom_path:
        p = Path(custom_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Specified plan file not found: {p}")
        return p

    cwd = Path.cwd().resolve()
    default_plan = cwd / "docs" / "draft-requisites" / "implementation-plan.md"
    if default_plan.exists():
        return default_plan

    for parent in [cwd, *cwd.parents]:
        candidate = parent / "docs" / "draft-requisites" / "implementation-plan.md"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find 'docs/draft-requisites/implementation-plan.md'. "
        "Please ensure the implementation plan exists in the target project workspace."
    )


def count_iterations(plan_content: str) -> Tuple[int, int]:
    """
    Returns (gemini_iterations_count, claude_iterations_count) for the active/latest implementation plan.
    """
    sections = re.split(r"^#\s*📋\s*Implementation Plan", plan_content, flags=re.MULTILINE)
    active_section = sections[-1] if sections else plan_content

    gemini_rounds = len(re.findall(r"^##\s*(?:🔍|🚀)\s*(?:Boost\s*)?Review Iteration\s+(\d+)", active_section, re.MULTILINE))
    claude_rounds = len(re.findall(r"^##\s*🏛️\s*Claude(?:\s+(?:Opus|Sonnet))?\s+Review Iteration\s+(\d+)", active_section, re.MULTILINE))
    return gemini_rounds, claude_rounds


def build_claude_prompt(round_num: int, plan_path: Path, model_title: str = "Claude") -> str:
    abs_path = plan_path.resolve().as_posix()
    lines = [
        f"You are the Principal Architect conducting Round {round_num} of an unsparing, hyper-critical Architectural Review of the active implementation plan in '{abs_path}'.",
        "",
        "CRITICAL OPERATIONAL RULES:",
        f"1. EXACT TARGET FILE: The target file is strictly '{abs_path}'. Open and read '{abs_path}' directly, focusing on the latest, active implementation plan section at the bottom of the document.",
        "2. GROUND TRUTH CODEBASE INSPECTION: Inspect the relevant codebase files mentioned in the plan using view_file or grep_search to verify classes, methods, and schemas.",
        "3. UNCOMPROMISING ARCHITECTURAL SCRUTINY: Scrutinize the proposal for all drawbacks, race conditions, edge cases (e.g. 0-byte log files, Textual DataTable event suppression, Rich markup errors), and backward-compatibility hazards.",
        f"4. APPEND REVIEW ITERATION: Using your Edit or Write tool, append a new section at the very end of '{abs_path}' titled exactly:",
        "",
        f"## 🏛️ {model_title} Review Iteration {round_num}",
        "",
        "Structure your appended section with:",
        "- ### ⚖️ Critical Architecture & Drawbacks Critique",
        "- ### 🚨 Unresolved Concerns & Edge Case Vulnerabilities",
        "- ### 🛠️ Mandatory Architectural Safeguards & Required Changes",
        "- ### 🏁 Verdict",
        "Must end with either: `VERDICT: AGREED` (only if 100% sound with zero reservations) or `VERDICT: DISAGREED`.",
        "",
        f"5. OUTPUT SUMMARY: Once '{abs_path}' is updated, output a concise 3-5 bullet point summary to stdout clearly stating whether you AGREED or DISAGREED, and list any outstanding blocking objections."
    ]
    return "\n".join(lines)


def get_session_file(plan_path: Path) -> Path:
    return plan_path.parent / ".claude_cross_review_session"


def invoke_claude(
    prompt: str,
    harness: str = "claude",
    model: str = "sonnet",
    effort: str = "medium",
    session_id: str | None = None,
    is_resume: bool = False,
) -> Tuple[int, str, str]:
    if harness == "agy":
        resolved_model = model
        if model in ("sonnet", "claude-sonnet", "claude-sonnet-4-6", "claude-sonnet-5"):
            resolved_model = "claude-sonnet-4-6"
        elif model in ("opus", "claude-opus", "claude-opus-4-6-thinking"):
            resolved_model = "claude-opus-4-6-thinking"

        cmd = ["agy"]
        if is_resume:
            cmd.append("-c")
        cmd.extend([
            "-p", prompt,
            "--model", resolved_model,
            "--dangerously-skip-permissions",
            "--print-timeout", "10m0s",
        ])
        if resolved_model not in ("claude-sonnet-4-6", "claude-opus-4-6-thinking") and effort:
            cmd.extend(["--effort", effort])
    else:
        cmd = ["claude", "-p", prompt]
        if is_resume and session_id:
            cmd.extend(["-r", session_id])
        elif session_id:
            cmd.extend(["--session-id", session_id])
            cmd.extend(["--model", model, "--effort", effort])
        else:
            cmd.extend(["--model", model, "--effort", effort])

        cmd.append("--dangerously-skip-permissions")

    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["CLAUDE_NON_INTERACTIVE"] = "1"

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


def parse_claude_verdict(plan_content: str, claude_stdout: str, round_num: int) -> str:
    round_match = re.search(
        rf"##\s*🏛️\s*Claude(?:\s+(?:Opus|Sonnet))?\s+Review Iteration\s+{round_num}(.*?)(?:##|\Z)",
        plan_content,
        re.DOTALL
    )
    section_text = round_match.group(1) if round_match else plan_content

    if re.search(r"VERDICT:\s*AGREED", section_text, re.IGNORECASE):
        return "AGREED"
    if re.search(r"VERDICT:\s*DISAGREED", section_text, re.IGNORECASE):
        return "DISAGREED"

    if "VERDICT: AGREED" in claude_stdout.upper():
        return "AGREED"
    if "VERDICT: DISAGREED" in claude_stdout.upper() or "DISAGREE" in claude_stdout.upper():
        return "DISAGREED"

    return "DISAGREED"


def extract_disagreement_points(plan_content: str, round_num: int) -> list[str]:
    points = []
    round_match = re.search(
        rf"##\s*🏛️\s*Claude(?:\s+(?:Opus|Sonnet))?\s+Review Iteration\s+{round_num}(.*?)(?:##|\Z)",
        plan_content,
        re.DOTALL
    )
    if not round_match:
        return ["Unspecified architectural concerns raised in review."]

    section_text = round_match.group(1)
    for line in section_text.splitlines():
        line_clean = line.strip()
        if (line_clean.startswith("- ") or line_clean.startswith("* ") or re.match(r"^\d+\.\s+", line_clean)) and len(line_clean) > 10:
            if not any(header in line_clean for header in ["Verdict", "VERDICT"]):
                points.append(line_clean.lstrip("-*0123456789. "))

    return points[:8] if points else ["Review section detailed specific objections in implementation-plan.md."]


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-Review between Gemini and Claude")
    parser.add_argument("--plan", type=str, default=None, help="Path to implementation-plan.md")
    parser.add_argument("--harness", type=str, choices=["claude", "agy"], default="claude", help="CLI harness (claude or agy)")
    parser.add_argument("--model", type=str, default="sonnet", help="Claude model alias or full name (default: sonnet)")
    parser.add_argument("--effort", type=str, default="medium", help="Reasoning effort (low, medium, high, max; default: medium)")
    parser.add_argument("--max-rounds", type=int, default=3, help="Maximum number of debate rounds")
    parser.add_argument("--check-status", action="store_true", help="Only check status and round count")

    args = parser.parse_args()

    try:
        plan_path = find_plan_file(args.plan)
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)

    plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    gemini_rounds, claude_rounds = count_iterations(plan_content)

    if args.check_status:
        print(json.dumps({
            "status": "ok",
            "plan_path": str(plan_path),
            "gemini_rounds": gemini_rounds,
            "claude_rounds": claude_rounds,
            "max_rounds": args.max_rounds,
        }, indent=2))
        sys.exit(0)

    next_claude_round = claude_rounds + 1
    if next_claude_round > args.max_rounds:
        points = extract_disagreement_points(plan_content, claude_rounds)
        print(json.dumps({
            "status": "cap_reached",
            "message": f"Maximum debate cap of {args.max_rounds} rounds reached without full consensus.",
            "claude_rounds": claude_rounds,
            "gemini_rounds": gemini_rounds,
            "unresolved_points": points,
            "plan_path": str(plan_path)
        }, indent=2))
        sys.exit(2)

    session_file = get_session_file(plan_path)
    session_id: str | None = None
    is_resume = False

    if next_claude_round > 1 and session_file.exists():
        saved_id = session_file.read_text(encoding="utf-8").strip()
        if saved_id:
            session_id = saved_id
            is_resume = True
    elif next_claude_round == 1 or not session_file.exists():
        session_id = str(uuid.uuid4())
        session_file.write_text(session_id, encoding="utf-8")

    model_title = "Claude Sonnet" if "sonnet" in args.model.lower() else "Claude Opus"
    prompt = build_claude_prompt(next_claude_round, plan_path, model_title=model_title)
    code, stdout, stderr = invoke_claude(
        prompt,
        harness=args.harness,
        model=args.model,
        effort=args.effort,
        session_id=session_id,
        is_resume=is_resume,
    )

    # Re-read plan after Claude execution
    updated_plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    new_gemini_rounds, new_claude_rounds = count_iterations(updated_plan_content)

    # If Claude printed its section but forgot to write it to the file, append it
    if new_claude_rounds < next_claude_round:
        verdict_search = re.search(rf"(##\s*🏛️\s*Claude(?:\s+(?:Opus|Sonnet))?\s+Review Iteration\s+{next_claude_round}.*)", stdout, re.DOTALL)
        if verdict_search:
            appended_content = updated_plan_content + "\n\n" + verdict_search.group(1).strip() + "\n"
            plan_path.write_text(appended_content, encoding="utf-8")
            updated_plan_content = appended_content
            new_claude_rounds = next_claude_round

    verdict = parse_claude_verdict(updated_plan_content, stdout, next_claude_round)
    unresolved = extract_disagreement_points(updated_plan_content, next_claude_round) if verdict != "AGREED" else []

    result = {
        "status": "completed",
        "returncode": code,
        "round": next_claude_round,
        "verdict": verdict,
        "max_rounds": args.max_rounds,
        "plan_path": str(plan_path),
        "unresolved_points": unresolved,
        "stdout_snippet": stdout[:600] if stdout else "",
        "stderr_snippet": stderr[:300] if stderr else "",
    }

    if verdict != "AGREED" and next_claude_round >= args.max_rounds:
        result["status"] = "cap_reached"

    # Clean up session file upon consensus or when cap is reached
    if verdict == "AGREED" or result["status"] == "cap_reached":
        if session_file.exists():
            try:
                session_file.unlink()
            except OSError:
                pass

    print(json.dumps(result, indent=2))
    if result["status"] == "cap_reached":
        sys.exit(2)
    elif code != 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
