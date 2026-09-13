from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.config import ProjectConfig
from orchestrator.db import StateManager

_logger = logging.getLogger(__name__)


@dataclass
class FunctionalSlice:
    title: str
    body: str
    labels: List[str] = field(default_factory=list)


@dataclass
class ProvisioningPlan:
    pattern: str  # 'A' or 'B'
    parent_title: str
    parent_body: str
    slices: List[FunctionalSlice] = field(default_factory=list)


def parse_decision_plan(plan_content: str, default_pattern: Optional[str] = None) -> ProvisioningPlan:
    """
    Parses the ## 🎯 Final Decision Plan section from an implementation plan.
    Extracts user story, acceptance criteria, and subtasks to construct a ProvisioningPlan.
    """
    sections = re.split(r"^#\s*📋\s*Implementation Plan", plan_content, flags=re.MULTILINE)
    active_section = sections[-1] if sections else plan_content

    final_match = re.search(
        r"##\s*🎯\s*Final Decision Plan.*?(?=\n##(?=[^#])|\Z)",
        active_section,
        re.DOTALL,
    )
    final_text = final_match.group(0) if final_match else active_section

    # 1. Extract User Story / Title
    story_match = re.search(
        r"\*\*As a\*\*\s+(.*?)\s*\n\s*\*\*I want\*\*\s+(.*?)\s*\n\s*\*\*So that\*\*\s+(.*?)(?=\n\s*\n|\n---|---|\Z)",
        final_text,
        re.DOTALL | re.IGNORECASE,
    )

    title_match = re.search(r"^#+\s*(?:📖\s*)?(?:User Story|Title):\s*(.+)$", final_text, re.MULTILINE | re.IGNORECASE)

    if title_match:
        parent_title = title_match.group(1).strip()
    elif story_match:
        want = story_match.group(2).strip().rstrip(",.")
        parent_title = f"feat: {want[:80]}"
    else:
        # Fallback to topic header if available
        topic_match = re.search(r"#\s*📋\s*Implementation Plan[^:]*:\s*(.+)$", active_section, re.MULTILINE)
        if topic_match:
            parent_title = f"feat: {topic_match.group(1).strip()}"
        else:
            parent_title = "feat: New Feature Implementation"

    # 2. Extract Subtasks / Slices
    slices: List[FunctionalSlice] = []
    
    # Check for Subtask lines in INVEST or Subtask Breakdown
    subtask_matches = re.findall(
        r"(?:^|\n)\s*(?:\d+\.|\*|-)\s+\*\*Subtask\s+\d+\s*\(([^)]+)\)\s*:\*\*\s*(.+?)(?=\n\s*(?:\d+\.|\*|-)\s+\*\*Subtask|\n---|---|\Z)",
        final_text,
        re.DOTALL,
    )

    if not subtask_matches:
        # Alternate syntax: - Subtask 1: title
        subtask_matches = re.findall(
            r"(?:^|\n)\s*(?:\d+\.|\*|-)\s+\*\*Subtask\s+\d+:\*\*\s*(.+?)(?=\n\s*(?:\d+\.|\*|-)\s+\*\*Subtask|\n---|---|\Z)",
            final_text,
            re.DOTALL,
        )
        if subtask_matches:
            subtask_matches = [("", sm) for sm in subtask_matches]

    if subtask_matches:
        for idx, (bracket_info, detail) in enumerate(subtask_matches, start=1):
            detail_clean = detail.strip()
            first_line = detail_clean.split("\n")[0].strip().lstrip("-*0123456789. ")
            slice_title = f"feat: {bracket_info.strip()}" if bracket_info and len(bracket_info.strip()) > 3 else f"feat: Slice {idx} - {first_line[:70]}"
            slice_body = detail_clean
            slices.append(FunctionalSlice(title=slice_title, body=slice_body))

    # 3. Determine Pattern
    pattern = default_pattern.upper() if default_pattern else None
    if not pattern:
        if len(slices) <= 1:
            pattern = "A"
        else:
            pattern = "B"

    return ProvisioningPlan(
        pattern=pattern,
        parent_title=parent_title,
        parent_body=final_text.strip(),
        slices=slices,
    )


async def check_project_active_lock(project_name: str, state_manager: StateManager) -> Optional[int]:
    """
    Checks if an active story lock is already held for the given project.
    Returns the active locked issue number if one exists, else None.
    """
    return await state_manager.get_active_locked_story_id(project_name)


def run_gh_command(cmd: List[str]) -> Tuple[int, str, str]:
    """Runs a GitHub CLI command safely without interactive prompts."""
    import os
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def create_gh_issue(repo: str, title: str, body: str, labels: List[str]) -> int:
    """Creates a GitHub issue and returns its issue number."""
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
    for lbl in labels:
        cmd.extend(["--label", lbl])
    code, stdout, stderr = run_gh_command(cmd)
    if code != 0:
        raise RuntimeError(f"Failed to create issue in {repo}: {stderr or stdout}")
    
    # stdout is the issue URL, e.g. https://github.com/AntaresAndBharani/graph-engineering/issues/197
    match = re.search(r"/issues/(\d+)", stdout)
    if not match:
        raise ValueError(f"Could not parse issue number from gh output: '{stdout}'")
    return int(match.group(1))


def update_gh_issue_body(repo: str, issue_number: int, body: str) -> None:
    cmd = ["gh", "issue", "edit", str(issue_number), "--repo", repo, "--body", body]
    code, stdout, stderr = run_gh_command(cmd)
    if code != 0:
        raise RuntimeError(f"Failed to update issue #{issue_number} body: {stderr or stdout}")


def post_gh_issue_comment(repo: str, issue_number: int, comment: str) -> None:
    cmd = ["gh", "issue", "comment", str(issue_number), "--repo", repo, "--body", comment]
    code, stdout, stderr = run_gh_command(cmd)
    if code != 0:
        raise RuntimeError(f"Failed to comment on issue #{issue_number}: {stderr or stdout}")


async def provision_story(
    project: ProjectConfig,
    plan: ProvisioningPlan,
    state_manager: StateManager,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Executes Pattern A or Pattern B provisioning for the target project.
    """
    # Pre-Flight Safety Guard
    active_lock = await check_project_active_lock(project.name, state_manager)
    if active_lock and not force:
        raise RuntimeError(
            f"Project '{project.name}' currently has an active locked story #{active_lock}. "
            "Provisioning a new feature will cause queue starvation under Invariant 1. "
            f"Complete or close story #{active_lock} first, or specify --force to override."
        )

    result: Dict[str, Any] = {
        "project": project.name,
        "repo": project.repo,
        "pattern": plan.pattern,
        "parent_issue": None,
        "child_issues": [],
        "dry_run": dry_run,
    }

    if dry_run:
        if plan.pattern == "A":
            result["child_issues"].append({
                "type": "STANDALONE",
                "title": plan.parent_title,
                "labels": ["ready-for-dev"],
            })
        else:
            result["parent_issue"] = {
                "type": "PARENT",
                "title": plan.parent_title,
                "labels": ["architect-processed"],
            }
            for i, s in enumerate(plan.slices, start=1):
                lbl = "ready-for-dev" if i == 1 else "queued"
                result["child_issues"].append({
                    "slice": i,
                    "title": s.title,
                    "labels": [lbl],
                })
        return result

    # Execution Mode
    if plan.pattern == "A":
        # Pattern A: Standalone Task
        labels = ["ready-for-dev"]
        issue_num = create_gh_issue(
            repo=project.repo,
            title=plan.parent_title,
            body=plan.parent_body,
            labels=labels,
        )
        result["child_issues"].append({
            "issue_number": issue_num,
            "type": "STANDALONE",
            "title": plan.parent_title,
            "labels": labels,
        })
    else:
        # Pattern B: Decomposed Feature Story
        # 1. Create Parent Issue
        parent_labels = ["architect-processed"]
        parent_id = create_gh_issue(
            repo=project.repo,
            title=plan.parent_title,
            body=plan.parent_body,
            labels=parent_labels,
        )
        result["parent_issue"] = {
            "issue_number": parent_id,
            "type": "PARENT",
            "title": plan.parent_title,
            "labels": parent_labels,
        }

        # 2. Create Child Slices
        child_ids: List[int] = []
        checklist_lines: List[str] = ["\n\n## Subtasks"]
        for idx, sl in enumerate(plan.slices, start=1):
            slice_label = "ready-for-dev" if idx == 1 else "queued"
            slice_body = f"Parent: #{parent_id}\n\n{sl.body}"
            child_id = create_gh_issue(
                repo=project.repo,
                title=sl.title,
                body=slice_body,
                labels=[slice_label],
            )
            child_ids.append(child_id)
            checklist_lines.append(f"- [ ] #{child_id} - {sl.title}")
            result["child_issues"].append({
                "issue_number": child_id,
                "slice": idx,
                "title": sl.title,
                "labels": [slice_label],
            })

        # 3. Update Parent Body with Checklist
        updated_parent_body = plan.parent_body + "\n" + "\n".join(checklist_lines) + "\n"
        update_gh_issue_body(project.repo, parent_id, updated_parent_body)

        # 4. Post Parent Linkage Comment (Triple-Redundancy Defense against API Search-Lag)
        comment_body = f"Child issues: {', '.join(f'#{cid}' for cid in child_ids)}"
        post_gh_issue_comment(project.repo, parent_id, comment_body)

    # 5. Immediate SQLite State Re-sync
    try:
        from orchestrator import poller
        await poller.poll_project_sdlc_items(project, state_manager)
    except Exception as e:
        _logger.warning("Immediate state sync encountered exception (will sync on next tick): %s", e)

    return result
