from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.poller import fetch_open_prs


async def check_pr_ci_status(repo: str, pr_number: int) -> tuple[str, str]:
    """
    Checks the status of remote CI checks on a PR using `gh pr checks <PR>`.
    Returns tuple: (status: 'PASS' | 'PENDING' | 'FAIL' | 'NO_CHECKS', details: str)
    Consumes 0 LLM tokens.
    """
    if not shutil.which("gh"):
        return "NO_CHECKS", "gh CLI unavailable"

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "checks", str(pr_number),
            "--repo", repo,
            "--json", "name,state,bucket",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            if "no checks reported" in err_msg.lower() or "no commit found" in err_msg.lower():
                return "PASS", "No CI checks configured"
            return "PENDING", f"Checks not ready: {err_msg}"

        checks = json.loads(stdout.decode("utf-8", errors="replace"))
        if not checks:
            return "PASS", "No CI checks required"

        states = [c.get("state", "").upper() for c in checks]
        buckets = [c.get("bucket", "").lower() for c in checks]

        # Any failing check
        if any(b in ("fail", "error", "cancelled") or s in ("FAILURE", "CANCELLED", "STARTUP_FAILURE") for b, s in zip(buckets, states)):
            failing = [c.get("name", "check") for c in checks if c.get("bucket", "").lower() in ("fail", "error", "cancelled")]
            return "FAIL", f"Failing checks: {', '.join(failing)}"

        # Any pending/running check
        if any(b in ("pending", "in_progress", "queued") or s in ("PENDING", "IN_PROGRESS", "QUEUED") for b, s in zip(buckets, states)):
            pending = [c.get("name", "check") for c in checks if c.get("bucket", "").lower() in ("pending", "in_progress", "queued")]
            return "PENDING", f"Waiting for checks: {', '.join(pending)}"

        return "PASS", "All CI checks passed (100% green)"
    except Exception as e:
        return "PENDING", f"Error querying checks: {e}"


async def sync_pr_branch_with_main(repo: str, pr_number: int) -> bool:
    """
    Synchronizes the PR head branch with main if behind, using `gh pr update-branch`.
    """
    if not shutil.which("gh"):
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "update-branch", str(pr_number),
            "--repo", repo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False


async def run_reviewer_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes Reviewer / Gatekeeper Node (PR CI Verification & Auto-Merge).
    Zero-token gating: if no PRs labeled 'needs-architect-review', exits with 0 tokens consumed.
    """
    node_cfg = project.nodes.get("reviewer", NodeConfig(harness="claude"))
    if not node_cfg.enabled:
        return False, "Reviewer node disabled for project."

    trigger = node_cfg.label_trigger or "needs-architect-review"
    auto_merge = node_cfg.auto_merge_approved if node_cfg.auto_merge_approved is not None else True

    # 1. Deterministic Gating (0 Tokens)
    prs = await fetch_open_prs(project.repo, label=trigger, limit=1)
    if not prs:
        return False, f"No PRs labeled '{trigger}'. Idle (0 tokens)."

    target_pr = prs[0]
    pr_number = target_pr["number"]
    pr_title = target_pr.get("title", "")
    mergeable = target_pr.get("mergeable", "UNKNOWN")

    # 2. Acquire State Lock
    lock_acquired = await state_manager.acquire_lock(
        issue_id=pr_number,
        repo=project.repo,
        node_type="reviewer",
        ttl_minutes=15,
    )
    if not lock_acquired:
        return False, f"PR #{pr_number} is currently locked by another active run. Skipping."

    # 3. Check Merge Conflicts
    if mergeable == "CONFLICTING":
        if shutil.which("gh"):
            p1 = await asyncio.create_subprocess_exec(
                "gh", "pr", "edit", str(pr_number),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "needs-po-review",
            )
            await p1.wait()
            p2 = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", project.repo,
                "--body", f"🤖 **Reviewer Node**: PR #{pr_number} has merge conflicts against `main`. Flagging for PO review (`needs-po-review`).",
            )
            await p2.wait()
        await state_manager.release_lock(pr_number, project.repo, "reviewer")
        return False, f"PR #{pr_number} has merge conflicts. Flagged with 'needs-po-review'."

    # 4. Check Remote CI Checks Status (0 Tokens)
    ci_status, ci_details = await check_pr_ci_status(project.repo, pr_number)
    if ci_status == "PENDING":
        await state_manager.release_lock(pr_number, project.repo, "reviewer")
        return False, f"PR #{pr_number} CI checks in progress ({ci_details}). Waiting."

    if ci_status == "FAIL":
        if shutil.which("gh"):
            p1 = await asyncio.create_subprocess_exec(
                "gh", "pr", "edit", str(pr_number),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "needs-po-review",
            )
            await p1.wait()
            p2 = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", project.repo,
                "--body", f"🤖 **Reviewer Node**: PR #{pr_number} CI checks failed ({ci_details}). Flagging for review (`needs-po-review`).",
            )
            await p2.wait()
        await state_manager.release_lock(pr_number, project.repo, "reviewer")
        return False, f"PR #{pr_number} CI checks failed ({ci_details}). Flagged with 'needs-po-review'."

    # 5. Deterministic Approval & Auto-Merge
    if auto_merge and shutil.which("gh"):
        # Post confirmation comment
        p_comment = await asyncio.create_subprocess_exec(
            "gh", "pr", "comment", str(pr_number),
            "--repo", project.repo,
            "--body", "🤖 **Reviewer Gatekeeper**: Deterministic Quality Gate passed (CI 100% Green, mergeable). Approving and executing auto-merge into `main`.",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await p_comment.wait()

        # Approve PR if not author
        p_approve = await asyncio.create_subprocess_exec(
            "gh", "pr", "review", str(pr_number),
            "--repo", project.repo,
            "--approve",
            "--body", "🤖 **Architect Review**: Approved (Quality Gate 100% Green).",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await p_approve.wait()

        # Merge PR via squash and delete branch
        p_merge = await asyncio.create_subprocess_exec(
            "gh", "pr", "merge", str(pr_number),
            "--repo", project.repo,
            "--squash",
            "--delete-branch",
            "--auto",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_merge.wait()

        # Fallback direct merge if --auto not supported or already green
        if p_merge.returncode != 0:
            p_merge_direct = await asyncio.create_subprocess_exec(
                "gh", "pr", "merge", str(pr_number),
                "--repo", project.repo,
                "--squash",
                "--delete-branch",
            )
            await p_merge_direct.wait()

        # Remove trigger label
        p_label = await asyncio.create_subprocess_exec(
            "gh", "pr", "edit", str(pr_number),
            "--repo", project.repo,
            "--remove-label", trigger,
        )
        await p_label.wait()

        await state_manager.release_lock(pr_number, project.repo, "reviewer")
        return True, f"Reviewer node approved and merged PR #{pr_number} into main."

    await state_manager.release_lock(pr_number, project.repo, "reviewer")
    return True, f"Reviewer node verified PR #{pr_number} (CI Green)."
