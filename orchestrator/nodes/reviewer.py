from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
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


async def resolve_pr_merge_conflicts(
    project: ProjectConfig,
    config: GlobalConfig,
    pr_number: int,
    branch_name: str,
    node_cfg: NodeConfig,
) -> tuple[bool, str]:
    """
    Autonomously resolves git merge conflicts on a PR branch against origin/main.
    1. Checks out the PR branch and pulls latest main.
    2. If conflicts occur, uses AI harness to analyze and resolve conflict markers cleanly.
    3. Commits and pushes the resolved branch to origin.
    """
    from rich.console import Console
    console = Console()
    console.print(f"  [{project.name}:reviewer] [bold yellow]⚠️ Merge conflicts detected on PR #{pr_number} ({branch_name}). Launching Autonomous Conflict Resolver...[/bold yellow]")

    # 1. Pre-flight clean and branch checkout
    try:
        await (await asyncio.create_subprocess_exec("git", "reset", "--hard", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "clean", "-fd", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "fetch", "origin", "main", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "fetch", "origin", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "checkout", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "pull", "origin", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
    except Exception as e:
        return False, f"Failed pre-flight checkout for {branch_name}: {e}"

    # 2. Try git merge origin/main
    proc_merge = await asyncio.create_subprocess_exec(
        "git", "merge", "origin/main",
        cwd=str(project.local_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_m, stderr_m = await proc_merge.communicate()
    if proc_merge.returncode == 0:
        # Merged cleanly without conflicts! Push immediately.
        proc_push = await asyncio.create_subprocess_exec(
            "git", "push", "origin", branch_name,
            cwd=str(project.local_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc_push.wait()
        return True, f"Cleanly merged {branch_name} with origin/main and pushed."

    # 3. Conflict markers present; invoke cost-effective AI harness to resolve conflicts cleanly
    harness_name = node_cfg.conflict_harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        harness_name = node_cfg.harness or "antigravity"
        harness_cfg = config.harnesses.get(harness_name)

    if not harness_cfg:
        await (await asyncio.create_subprocess_exec("git", "merge", "--abort", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        return False, "No AI harness configured for conflict resolution."

    model = node_cfg.conflict_model or "gemini-3.7-flash-low"
    effort = node_cfg.conflict_effort

    console.print(f"  [{project.name}:conflict-resolver] [bold cyan]⚡ Resolving Merge Conflicts via {harness_name}/{model}[/bold cyan]")

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "reviewer",
        issue_id=f"conflict_pr_{pr_number}",
    )

    prompt = (
        f"You are the Autonomous Conflict Resolution Engineer operating in non-interactive batch mode.\n"
        f"PR #{pr_number} on branch '{branch_name}' has git merge conflicts against 'origin/main' in repository '{project.repo}'.\n\n"
        f"OPERATIONAL MISSION:\n"
        f"1. Run `git status` to identify all files with merge conflicts (both modified).\n"
        f"2. Inspect each conflicting file and conflict markers (<<<<<<< HEAD ... ======= ... >>>>>>>).\n"
        f"3. Intelligently merge the changes, keeping both the new functionality from the feature branch and the latest updates from main.\n"
        f"4. Verify that all conflict markers are completely removed.\n"
        f"5. Stage the resolved files: `git add .`\n"
        f"6. Commit the merge: `git commit -m 'chore(merge): resolve merge conflicts with main for PR #{pr_number}'`\n"
        f"7. Push to origin: `git push origin {branch_name}`\n"
    )

    adapter = AsyncHarnessAdapter(harness_name, harness_cfg)
    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=project.local_path,
        log_file=log_file,
        model=model,
        effort=effort,
        console_prefix=f"[{project.name}:conflict-resolver]",
    )

    if exit_code != 0:
        await (await asyncio.create_subprocess_exec("git", "merge", "--abort", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        return False, f"AI conflict resolution failed (exit code {exit_code})."

    # Verify if merge was committed and workspace is clean
    p_status = await asyncio.create_subprocess_exec("git", "status", "--porcelain", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout_st, _ = await p_status.communicate()
    if p_status.returncode == 0 and not stdout_st.strip():
        # Ensure branch is pushed
        p_push = await asyncio.create_subprocess_exec("git", "push", "origin", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p_push.wait()
        return True, f"Autonomous Conflict Resolver resolved conflicts on PR #{pr_number} and pushed {branch_name}."
    else:
        await (await asyncio.create_subprocess_exec("git", "merge", "--abort", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        return False, "Conflict resolution ended with uncommitted changes."


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

    trigger = node_cfg.label_trigger or "architect-approved"
    auto_merge = node_cfg.auto_merge_approved if node_cfg.auto_merge_approved is not None else True

    # 1. Deterministic Gating (0 Tokens)
    prs = await fetch_open_prs(project.repo, label=trigger, limit=50)
    if not prs:
        # Fallback check for backwards compatibility with needs-architect-review if architect review disabled
        if trigger == "architect-approved":
            arch_node = project.nodes.get("architect")
            if not arch_node or not arch_node.enabled:
                prs = await fetch_open_prs(project.repo, label="needs-architect-review", limit=50)
        if not prs:
            return False, f"No PRs labeled '{trigger}'. Idle (0 tokens)."

    from rich.console import Console
    console = Console()

    merged_prs: List[int] = []
    pending_prs: List[int] = []
    conflict_prs: List[int] = []

    for target_pr in prs:
        pr_number = target_pr["number"]
        pr_title = target_pr.get("title", "")
        pr_state = str(target_pr.get("state", "OPEN")).upper()
        is_merged = bool(target_pr.get("merged", False))
        mergeable = target_pr.get("mergeable", "UNKNOWN")
        branch_name = target_pr.get("headRefName", "")

        # 2. Acquire State Lock
        lock_acquired = await state_manager.acquire_lock(
            issue_id=pr_number,
            repo=project.repo,
            node_type="reviewer",
            ttl_minutes=15,
        )
        if not lock_acquired:
            continue

        # AC 2: True Post-Merge Handling
        if pr_state == "CLOSED":
            if is_merged:
                console.print(f"\n  [bold green]🔍 [{project.name}:reviewer][/bold green] [bold white]PR #{pr_number} Verified Merged into main[/bold white]")
                await state_manager.delete_pr_artifact(project.repo, pr_number)
                await state_manager.release_lock(pr_number, project.repo, "reviewer")
                merged_prs.append(pr_number)
                continue
            else:
                console.print(f"\n  [dim]🔍 [{project.name}:reviewer] PR #{pr_number} closed without merge. Releasing lock.[/dim]")
                await state_manager.delete_pr_artifact(project.repo, pr_number)
                await state_manager.release_lock(pr_number, project.repo, "reviewer")
                continue

        console.print(f"\n  [bold green]🔍 [{project.name}:reviewer][/bold green] [bold white]Evaluating PR #{pr_number}:[/bold white] [cyan]'{pr_title}'[/cyan]")
        console.print(f"  [dim]• Target: {project.repo} | Status: Remote CI Quality Gate & Auto-Merge[/dim]")

        # AC 3: Async Deferral when GitHub is calculating mergeability
        if mergeable in (None, "UNKNOWN"):
            console.print(f"  [{project.name}:reviewer] [dim]PR #{pr_number} mergeability is computing (UNKNOWN/None). Deferring cleanly (0 tokens).[/dim]")
            await state_manager.release_lock(pr_number, project.repo, "reviewer")
            pending_prs.append(pr_number)
            continue

        # AC 4: Check & Resolve Merge Conflicts Autonomously with Blackboard Persistence
        if mergeable == "CONFLICTING":
            # Record review decision on the Blackboard
            await state_manager.upsert_pr_artifact(
                repo=project.repo,
                pr_number=pr_number,
                node_name="reviewer",
                status="APPROVED_WITH_CONFLICT",
                comment=f"Code approved for PR #{pr_number} ('{pr_title}'), but branch '{branch_name}' has git merge conflicts against origin/main.",
            )

            if branch_name:
                resolved, res_msg = await resolve_pr_merge_conflicts(
                    project, config, pr_number, branch_name, node_cfg
                )
                if resolved:
                    console.print(f"  [{project.name}:reviewer] [bold green]✓ {res_msg}[/bold green]")
                    await state_manager.upsert_pr_artifact(
                        repo=project.repo,
                        pr_number=pr_number,
                        node_name="reviewer",
                        status="CONFLICT_RESOLVED",
                        comment=f"Autonomous conflict resolver resolved conflicts on '{branch_name}'.",
                    )
                    # PR is now updated on GitHub; remote CI will trigger. Release lock and wait for CI.
                    await state_manager.release_lock(pr_number, project.repo, "reviewer")
                    pending_prs.append(pr_number)
                    continue
                else:
                    console.print(f"  [{project.name}:reviewer] [bold red]✗ Conflict resolution failed: {res_msg}[/bold red]")

            if shutil.which("gh"):
                p1 = await asyncio.create_subprocess_exec(
                    "gh", "pr", "edit", str(pr_number),
                    "--repo", project.repo,
                    "--remove-label", trigger,
                    "--add-label", "needs-po-review",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p1.wait()
                p2 = await asyncio.create_subprocess_exec(
                    "gh", "pr", "comment", str(pr_number),
                    "--repo", project.repo,
                    "--body", f"🤖 **Reviewer Node**: PR #{pr_number} has merge conflicts that could not be autonomously resolved. Flagging for PO review (`needs-po-review`).",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p2.wait()
            await state_manager.release_lock(pr_number, project.repo, "reviewer")
            conflict_prs.append(pr_number)
            continue

        # 4. Check Remote CI Checks Status (0 Tokens)
        ci_status, ci_details = await check_pr_ci_status(project.repo, pr_number)
        if ci_status == "PENDING":
            await state_manager.release_lock(pr_number, project.repo, "reviewer")
            pending_prs.append(pr_number)
            continue

        if ci_status == "FAIL":
            if shutil.which("gh"):
                p1 = await asyncio.create_subprocess_exec(
                    "gh", "pr", "edit", str(pr_number),
                    "--repo", project.repo,
                    "--remove-label", trigger,
                    "--add-label", "needs-po-review",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p1.wait()
                p2 = await asyncio.create_subprocess_exec(
                    "gh", "pr", "comment", str(pr_number),
                    "--repo", project.repo,
                    "--body", f"🤖 **Reviewer Node**: PR #{pr_number} CI checks failed ({ci_details}). Flagging for review (`needs-po-review`).",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p2.wait()
            await state_manager.release_lock(pr_number, project.repo, "reviewer")
            conflict_prs.append(pr_number)
            continue

        # 5. Deterministic Approval & Auto-Merge
        if auto_merge and shutil.which("gh"):
            p_comment = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", project.repo,
                "--body", "🤖 **Reviewer Gatekeeper**: Deterministic Quality Gate passed (CI 100% Green, mergeable). Approving and executing auto-merge into `main`.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_comment.wait()

            p_approve = await asyncio.create_subprocess_exec(
                "gh", "pr", "review", str(pr_number),
                "--repo", project.repo,
                "--approve",
                "--body", "🤖 **Architect Review**: Approved (Quality Gate 100% Green).",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_approve.wait()

            p_merge = await asyncio.create_subprocess_exec(
                "gh", "pr", "merge", str(pr_number),
                "--repo", project.repo,
                "--squash",
                "--delete-branch",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_merge.wait()

            if p_merge.returncode == 0:
                merged_prs.append(pr_number)
                await state_manager.delete_pr_artifact(project.repo, pr_number)
                console.print(f"  [{project.name}:reviewer] [bold green]✓ Successfully auto-merged PR #{pr_number} into main[/bold green]")
            else:
                console.print(f"  [{project.name}:reviewer] [bold yellow]Could not merge PR #{pr_number}[/bold yellow]")

        await state_manager.release_lock(pr_number, project.repo, "reviewer")

    if merged_prs:
        merged_list = ", #".join(map(str, merged_prs))
        return True, f"Reviewer auto-merged {len(merged_prs)} approved PR(s) into main: #{merged_list}."
    elif conflict_prs:
        return False, f"Reviewer flagged {len(conflict_prs)} PR(s) with conflicts/failures."
    elif pending_prs:
        return False, f"{len(pending_prs)} PR(s) waiting for remote CI checks."

    return False, "No mergeable PRs processed."

