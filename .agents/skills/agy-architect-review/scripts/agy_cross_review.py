#!/usr/bin/env python3
"""
agy_cross_review.py - Tri-Party Cross-Review Orchestration Script.
Coordinates the Tri-Party Review Council:
  1. Author (Synthesizer via implementation-plan.md)
  2. Gemini Architect (gemini-3.8-flash-high via agy CLI)
  3. Claude QA Guardian (sonnet with low effort via claude CLI)
Enforces up to 3 iterative debate rounds exclusively mediated via
docs/draft-requisites/implementation-plan.md with session continuity.
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
from typing import Dict, Any, Tuple, List


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


def count_iterations(plan_content: str) -> Tuple[int, int, int]:
    """
    Returns (author_rounds, gemini_rounds, qa_rounds) for the active implementation plan.
    """
    sections = re.split(r"^#\s*📋\s*Implementation Plan", plan_content, flags=re.MULTILINE)
    active_section = sections[-1] if sections else plan_content

    author_rounds = len(re.findall(r"^##\s*(?:🔍|🚀)\s*(?:Boost\s*)?Review Iteration\s+(\d+)", active_section, re.MULTILINE))
    gemini_rounds = len(re.findall(r"^##\s*🏛️\s*Gemini(?:\s+Architect)?\s+Review Iteration\s+(\d+)", active_section, re.MULTILINE))
    qa_rounds = len(re.findall(r"^##\s*🧪\s*Claude\s+QA\s+Review Iteration\s+(\d+)", active_section, re.MULTILINE))
    return author_rounds, gemini_rounds, qa_rounds


def build_gemini_prompt(round_num: int, plan_path: Path, model_title: str = "Gemini Architect") -> str:
    abs_path = plan_path.resolve().as_posix()
    lines = [
        f"You are the Principal Architect conducting Round {round_num} of an unsparing, hyper-critical Architectural Review of the active implementation plan in '{abs_path}'.",
        "",
        "CRITICAL OPERATIONAL RULES:",
        f"1. EXACT TARGET FILE: The target file is strictly '{abs_path}'. Open and read '{abs_path}' directly, focusing on the latest, active implementation plan section at the bottom of the document.",
        "2. GROUND TRUTH CODEBASE INSPECTION: Inspect the relevant codebase files mentioned in the plan using view_file or grep_search to verify classes, methods, and schemas.",
        "3. UNCOMPROMISING ARCHITECTURAL SCRUTINY: Scrutinize the proposal for all drawbacks, race conditions, edge cases, performance bottlenecks, and backward-compatibility hazards.",
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


def build_qa_prompt(round_num: int, plan_path: Path) -> str:
    abs_path = plan_path.resolve().as_posix()
    lines = [
        f"You are the QA Lead & Requirements Guardian conducting Round {round_num} of the Review of the active implementation plan in '{abs_path}'.",
        "",
        "CRITICAL OPERATIONAL RULES & QA MANDATE:",
        f"1. EXACT TARGET FILE: The target file is strictly '{abs_path}'. Open and read '{abs_path}' directly, focusing on the latest implementation proposal and recent debate iterations.",
        "2. MANDATE 1 - REQUIREMENTS FIDELITY (ANTI-DRIFT GUARDIAN): Cross-check the implementation plan against the operator's original user request and constraints. Verify that technical optimizations, abstractions, or architect debates have NOT dropped, diluted, or altered the operator's core deliverables and invariants.",
        "3. MANDATE 2 - UX/UI EXPERIENCE AUDIT (IF UI EXISTS): If this change involves UI/UX (e.g. mobile/web screens, CLI tables, Rich outputs, interactive dashboard widgets, prompts): rigorously review visual hierarchy, layout ergonomics, responsive states (loading, empty, error, active cursor), and user feedback.",
        "4. MANDATE 3 - FUNCTIONAL RIGOR AUDIT (IF NO UI EXISTS): If backend, infra, or engine only: audit behavioral contracts, input validation, return error messages, failure modes (cold start, zero division, timeout recovery), and fail-closed safety.",
        "5. MANDATE 4 - BDD ACCEPTANCE CRITERIA COMPLETENESS: Ensure Gherkin scenarios (Given/When/Then) cover happy paths, edge cases, and adversarial failure conditions unambiguously.",
        f"6. APPEND REVIEW ITERATION: Using your Edit or Write tool, append a new section at the very end of '{abs_path}' titled exactly:",
        "",
        f"## 🧪 Claude QA Review Iteration {round_num} (Requirements & UX/UI Guardian)",
        "",
        "Structure your appended section with:",
        "- ### 🎯 Requirements Fidelity & Scope Alignment Audit",
        "- ### 🖥️ UX/UI & Functional Rigor Review",
        "- ### 🚨 Edge Cases, Failure Modes & User Impact",
        "- ### 🧪 Acceptance Criteria & Testability Assessment",
        "- ### 🏁 Verdict",
        "Must end with either: `VERDICT: AGREED` (only if requirements fidelity is pristine, UX/functional behavior is solid, and AC is complete) or `VERDICT: DISAGREED`.",
        "",
        f"7. OUTPUT SUMMARY: Once '{abs_path}' is updated, output a concise 3-5 bullet point summary to stdout clearly stating whether you AGREED or DISAGREED, and list any outstanding blocking objections."
    ]
    return "\n".join(lines)


def get_gemini_session_file(plan_path: Path) -> Path:
    return plan_path.parent / ".agy_cross_review_session"


def get_qa_session_file(plan_path: Path) -> Path:
    return plan_path.parent / ".claude_qa_review_session"


def invoke_agy(
    prompt: str,
    model: str = "gemini-3.8-flash-high",
    is_resume: bool = False,
) -> Tuple[int, str, str]:
    cmd = ["agy"]
    if is_resume:
        cmd.append("-c")
    cmd.extend([
        "-p", prompt,
        "--model", model,
        "--dangerously-skip-permissions",
        "--print-timeout", "10m0s",
    ])

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


def invoke_claude(
    prompt: str,
    model: str = "sonnet",
    effort: str = "low",
    session_id: str | None = None,
    is_resume: bool = False,
) -> Tuple[int, str, str]:
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


def parse_gemini_verdict(plan_content: str, stdout: str, round_num: int) -> str:
    round_match = re.search(
        rf"##\s*🏛️\s*Gemini(?:\s+Architect)?\s+Review Iteration\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)",
        plan_content,
        re.DOTALL
    )
    section_text = round_match.group(1) if round_match else plan_content

    if re.search(r"VERDICT:\s*AGREED", section_text, re.IGNORECASE):
        return "AGREED"
    if re.search(r"VERDICT:\s*DISAGREED", section_text, re.IGNORECASE):
        return "DISAGREED"

    if "VERDICT: AGREED" in stdout.upper():
        return "AGREED"
    if "VERDICT: DISAGREED" in stdout.upper() or "DISAGREE" in stdout.upper():
        return "DISAGREED"

    return "DISAGREED"


def parse_qa_verdict(plan_content: str, stdout: str, round_num: int) -> str:
    round_match = re.search(
        rf"##\s*🧪\s*Claude\s+QA\s+Review Iteration\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)",
        plan_content,
        re.DOTALL
    )
    section_text = round_match.group(1) if round_match else plan_content

    if re.search(r"VERDICT:\s*AGREED", section_text, re.IGNORECASE):
        return "AGREED"
    if re.search(r"VERDICT:\s*DISAGREED", section_text, re.IGNORECASE):
        return "DISAGREED"

    if "VERDICT: AGREED" in stdout.upper():
        return "AGREED"
    if "VERDICT: DISAGREED" in stdout.upper() or "DISAGREE" in stdout.upper():
        return "DISAGREED"

    return "DISAGREED"


def extract_disagreement_points(plan_content: str, round_num: int, header_prefix: str = "🏛️") -> list[str]:
    points = []
    round_match = re.search(
        rf"##\s*{header_prefix}.*?Iteration\s+{round_num}(.*?)(?:\n##(?=[^#])|\Z)",
        plan_content,
        re.DOTALL
    )
    if not round_match:
        return ["Unspecified concerns raised in review."]

    section_text = round_match.group(1)
    for line in section_text.splitlines():
        line_clean = line.strip()
        if (line_clean.startswith("- ") or line_clean.startswith("* ") or re.match(r"^\d+\.\s+", line_clean)) and len(line_clean) > 10:
            if not any(header in line_clean for header in ["Verdict", "VERDICT"]):
                points.append(line_clean.lstrip("-*0123456789. "))

    return points[:8] if points else ["Review section detailed specific objections in implementation-plan.md."]


def main() -> None:
    parser = argparse.ArgumentParser(description="Tri-Party Cross-Review Council (Author, Gemini Architect, Claude QA Guardian)")
    parser.add_argument("--plan", type=str, default=None, help="Path to implementation-plan.md")
    parser.add_argument("--model", type=str, default="gemini-3.8-flash-high", help="Gemini model (default: gemini-3.8-flash-high)")
    parser.add_argument("--qa-model", type=str, default="sonnet", help="Claude QA model (default: sonnet)")
    parser.add_argument("--qa-effort", type=str, default="low", help="Claude QA effort (default: low)")
    parser.add_argument("--max-rounds", type=int, default=3, help="Maximum number of debate rounds")
    parser.add_argument("--skip-qa", action="store_true", help="Skip Claude QA review and run Gemini Architect only")
    parser.add_argument("--check-status", action="store_true", help="Only check status and round count")

    args = parser.parse_args()

    try:
        plan_path = find_plan_file(args.plan)
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)

    plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    author_rounds, gemini_rounds, qa_rounds = count_iterations(plan_content)

    if args.check_status:
        print(json.dumps({
            "status": "ok",
            "plan_path": str(plan_path),
            "author_rounds": author_rounds,
            "gemini_rounds": gemini_rounds,
            "qa_rounds": qa_rounds,
            "max_rounds": args.max_rounds,
        }, indent=2))
        sys.exit(0)

    next_round = max(gemini_rounds, qa_rounds) + 1
    if next_round > args.max_rounds:
        points = extract_disagreement_points(plan_content, gemini_rounds, header_prefix="🏛️")
        if not args.skip_qa:
            points.extend(extract_disagreement_points(plan_content, qa_rounds, header_prefix="🧪"))
        print(json.dumps({
            "status": "cap_reached",
            "message": f"Maximum debate cap of {args.max_rounds} rounds reached without full council consensus.",
            "gemini_rounds": gemini_rounds,
            "qa_rounds": qa_rounds,
            "author_rounds": author_rounds,
            "unresolved_points": points[:10],
            "plan_path": str(plan_path)
        }, indent=2))
        sys.exit(2)

    # Gemini Architect session setup
    gemini_session_file = get_gemini_session_file(plan_path)
    is_gemini_resume = False
    if next_round > 1 and gemini_session_file.exists():
        is_gemini_resume = True
    elif next_round == 1 or not gemini_session_file.exists():
        gemini_session_file.write_text("active_session", encoding="utf-8")

    # Claude QA session setup
    qa_session_file = get_qa_session_file(plan_path)
    qa_session_id = None
    is_qa_resume = False
    if not args.skip_qa:
        if next_round > 1 and qa_session_file.exists():
            qa_session_id = qa_session_file.read_text(encoding="utf-8").strip()
            is_qa_resume = bool(qa_session_id)
        if not qa_session_id:
            qa_session_id = f"qa-review-{uuid.uuid4().hex[:8]}"
            qa_session_file.write_text(qa_session_id, encoding="utf-8")

    # 1. Execute Gemini Architect
    gemini_prompt = build_gemini_prompt(next_round, plan_path)
    gemini_code, gemini_stdout, gemini_stderr = invoke_agy(
        gemini_prompt,
        model=args.model,
        is_resume=is_gemini_resume,
    )

    # If Gemini forgot to append to file, append stdout block
    updated_plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
    _, new_gemini_rounds, _ = count_iterations(updated_plan_content)
    if new_gemini_rounds < next_round:
        verdict_search = re.search(rf"(##\s*🏛️\s*Gemini(?:\s+Architect)?\s+Review Iteration\s+{next_round}.*)", gemini_stdout, re.DOTALL)
        if verdict_search:
            appended_content = updated_plan_content + "\n\n" + verdict_search.group(1).strip() + "\n"
            plan_path.write_text(appended_content, encoding="utf-8")
            updated_plan_content = appended_content

    gemini_verdict = parse_gemini_verdict(updated_plan_content, gemini_stdout, next_round)

    # 2. Execute Claude QA Guardian (unless skipped)
    qa_verdict = "AGREED"
    qa_code = 0
    qa_stdout, qa_stderr = "", ""
    if not args.skip_qa:
        qa_prompt = build_qa_prompt(next_round, plan_path)
        qa_code, qa_stdout, qa_stderr = invoke_claude(
            qa_prompt,
            model=args.qa_model,
            effort=args.qa_effort,
            session_id=qa_session_id,
            is_resume=is_qa_resume,
        )

        # If Claude QA forgot to append to file, append stdout block
        updated_plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
        _, _, new_qa_rounds = count_iterations(updated_plan_content)
        if new_qa_rounds < next_round:
            qa_search = re.search(rf"(##\s*🧪\s*Claude\s+QA\s+Review Iteration\s+{next_round}.*)", qa_stdout, re.DOTALL)
            if qa_search:
                appended_content = updated_plan_content + "\n\n" + qa_search.group(1).strip() + "\n"
                plan_path.write_text(appended_content, encoding="utf-8")
                updated_plan_content = appended_content

        qa_verdict = parse_qa_verdict(updated_plan_content, qa_stdout, next_round)

    # 3. Dual Consensus Evaluation
    council_verdict = "AGREED" if (gemini_verdict == "AGREED" and qa_verdict == "AGREED") else "DISAGREED"

    unresolved: List[str] = []
    if gemini_verdict != "AGREED":
        unresolved.extend([f"[Gemini Architect] {p}" for p in extract_disagreement_points(updated_plan_content, next_round, header_prefix="🏛️")])
    if qa_verdict != "AGREED":
        unresolved.extend([f"[Claude QA] {p}" for p in extract_disagreement_points(updated_plan_content, next_round, header_prefix="🧪")])

    result = {
        "status": "completed",
        "round": next_round,
        "council_verdict": council_verdict,
        "gemini_verdict": gemini_verdict,
        "qa_verdict": qa_verdict,
        "max_rounds": args.max_rounds,
        "plan_path": str(plan_path),
        "unresolved_points": unresolved,
        "gemini_returncode": gemini_code,
        "qa_returncode": qa_code,
        "gemini_stdout_snippet": gemini_stdout[:400] if gemini_stdout else "",
        "qa_stdout_snippet": qa_stdout[:400] if qa_stdout else "",
    }

    if council_verdict != "AGREED" and next_round >= args.max_rounds:
        result["status"] = "cap_reached"

    # Clean up session markers upon full consensus or cap
    if council_verdict == "AGREED" or result["status"] == "cap_reached":
        for sf in [gemini_session_file, qa_session_file]:
            if sf.exists():
                try:
                    sf.unlink()
                except OSError:
                    pass

    print(json.dumps(result, indent=2))
    if result["status"] == "cap_reached":
        sys.exit(2)
    elif gemini_code != 0 or qa_code != 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
