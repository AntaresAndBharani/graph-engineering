#!/usr/bin/env python3
"""
cross_review.py - Cross-Review Orchestration Script between Gemini and Claude (Opus High).
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
    Returns (gemini_iterations_count, claude_iterations_count).
    """
    gemini_rounds = len(re.findall(r"^##\s*(?:🔍|🚀)\s*(?:Boost\s*)?Review Iteration\s+(\d+)", plan_content, re.MULTILINE))
    claude_rounds = len(re.findall(r"^##\s*🏛️\s*Claude(?:\s+Opus)?\s+Review Iteration\s+(\d+)", plan_content, re.MULTILINE))
    return gemini_rounds, claude_rounds


def build_claude_prompt(round_num: int, plan_rel_path: str) -> str:
    return (
        f"You are the Principal Architect conducting Round {round_num} of an unsparing, hyper-critical "
        f"Architectural Review of the implementation plan at '{plan_rel_path}'.\n\n"
        f"CRITICAL OPERATIONAL RULES:\n"
        f"1. GROUND TRUTH VERIFICATION: Read '{plan_rel_path}' completely. Then use your Read/Grep/Bash tools "
        f"to inspect the actual codebase files mentioned in the plan. Verify schemas, method signatures, "
        f"classes, and configuration in the live workspace.\n"
        f"2. UNCOMPROMISING ARCHITECTURAL SCRUTINY: Identify all drawbacks, scalability bottlenecks, "
        f"data integrity risks, concurrency/lock contentions, edge cases, breaking changes, and backward-compatibility hazards.\n"
        f"3. APPEND REVIEW ITERATION: Using your Edit or Write tool, append a new section at the end of '{plan_rel_path}' "
        f"titled exactly:\n\n"
        f"## 🏛️ Claude Opus Review Iteration {round_num}\n\n"
        f"Structure your appended section with:\n"
        f"- ### ⚖️ Critical Architecture & Drawbacks Critique\n"
        f"- ### 🚨 Unresolved Concerns & Edge Case Vulnerabilities\n"
        f"- ### 🛠️ Mandatory Architectural Safeguards & Required Changes\n"
        f"- ### 🏁 Verdict\n"
        f"Must end with either: `VERDICT: AGREED` (only if 100% sound with zero reservations) or `VERDICT: DISAGREED`.\n\n"
        f"4. OUTPUT SUMMARY: Once '{plan_rel_path}' is updated, output a concise 3-5 bullet point summary to stdout "
        f"clearly stating whether you AGREED or DISAGREED, and list any outstanding blocking objections."
    )


def invoke_claude(prompt: str, model: str = "opus", effort: str = "high") -> Tuple[int, str, str]:
    cmd = [
        "claude",
        "-p", prompt,
        "--model", model,
        "--effort", effort,
        "--dangerously-skip-permissions",
    ]

    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["CLAUDE_NON_INTERACTIVE"] = "1"

    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    return process.returncode, process.stdout, process.stderr


def parse_claude_verdict(plan_content: str, claude_stdout: str, round_num: int) -> str:
    """
    Determines if Claude agreed or disagreed.
    """
    round_match = re.search(
        rf"##\s*🏛️\s*Claude(?:\s+Opus)?\s+Review Iteration\s+{round_num}(.*?)(?:##|\Z)",
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
    """
    Extracts bullet points or concerns under Claude's review iteration.
    """
    points = []
    round_match = re.search(
        rf"##\s*🏛️\s*Claude(?:\s+Opus)?\s+Review Iteration\s+{round_num}(.*?)(?:##|\Z)",
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
    parser = argparse.ArgumentParser(description="Cross-Review between Gemini and Claude (Opus High)")
    parser.add_argument("--plan", type=str, default=None, help="Path to implementation-plan.md")
    parser.add_argument("--model", type=str, default="opus", help="Claude model alias or full name")
    parser.add_argument("--effort", type=str, default="high", help="Reasoning effort (low, medium, high, max)")
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

    try:
        rel_path = plan_path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        rel_path = str(plan_path)

    prompt = build_claude_prompt(next_claude_round, rel_path)
    code, stdout, stderr = invoke_claude(prompt, model=args.model, effort=args.effort)

    # Re-read plan after Claude execution
    updated_plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    new_gemini_rounds, new_claude_rounds = count_iterations(updated_plan_content)

    # If Claude printed its section but forgot to write it to the file, append it
    if new_claude_rounds < next_claude_round:
        verdict_search = re.search(rf"(##\s*🏛️\s*Claude(?:\s+Opus)?\s+Review Iteration\s+{next_claude_round}.*)", stdout, re.DOTALL)
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

    print(json.dumps(result, indent=2))
    if result["status"] == "cap_reached":
        sys.exit(2)
    elif code != 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
