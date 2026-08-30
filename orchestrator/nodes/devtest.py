from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator import poller
from orchestrator.nodes.reviewer import check_pr_ci_status
from orchestrator.poller import check_dispatch_quota, fetch_issues_with_label, fetch_open_prs


async def verify_git_safety(local_path: Path, expected_repo: str) -> tuple[bool, str]:
    """
    Validates that local_path is a valid git repository whose remote.origin.url
    matches the expected repo string to prevent accidental deletion in wrong directories.
    """
    git_dir = local_path / ".git"
    if not git_dir.exists():
        return False, f"Safety check failed: '{local_path}' does not contain a .git directory."

    if not shutil.which("git"):
        return False, "Safety check failed: 'git' binary not found in PATH."

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "config", "--get", "remote.origin.url",
            cwd=str(local_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        remote_url = stdout.decode("utf-8").strip()

        # Normalize repo identifiers (e.g. git@github.com:org/repo.git or https://github.com/org/repo)
        if expected_repo.lower() not in remote_url.lower():
            return False, f"Safety check failed: local git remote '{remote_url}' does not match expected repo '{expected_repo}'."
    except Exception as e:
        return False, f"Safety check failed: error reading remote URL: {e}"

    return True, "Safety verified."


async def _remediate_refactor_pr(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    node_cfg: NodeConfig,
    pr_number: int,
    pr_title: str,
    branch_name: str,
) -> tuple[bool, str]:
    """
    Autonomously remediates a PR labeled 'needs-refactor' by ingesting the Architect's
    code review critique, applying refactorings on the branch, verifying tests, committing,
    pushing, and relabeling to 'needs-architect-review'.
    """
    is_safe, safety_msg = await verify_git_safety(project.local_path, project.repo)
    if not is_safe:
        return False, safety_msg

    harness_name = node_cfg.harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not found in configuration."

    allowed, q_res = await poller.check_dispatch_quota(project, "devtest", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    retry_cfg = getattr(harness_cfg, "retry", None)
    max_retries = getattr(retry_cfg, "max_retries", 0) if retry_cfg else 0
    lock_ttl = int(harness_cfg.timeout_minutes * (1 + max_retries) + 5)

    lock_acquired = await state_manager.acquire_lock(
        issue_id=pr_number,
        repo=project.repo,
        node_type="devtest_refactor",
        ttl_minutes=lock_ttl,
    )
    if not lock_acquired:
        return False, f"PR #{pr_number} is locked by another active refactor run. Skipping."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "devtest",
        issue_id=f"pr_{pr_number}_refactor",
    )

    from rich.console import Console
    console = Console()
    console.print(f"\n  [bold yellow]🔧 [{project.name}:devtest][/bold yellow] [bold white]Remediating PR #{pr_number} ('needs-refactor'):[/bold white] [cyan]'{pr_title}'[/cyan]")
    console.print(f"  [dim]• Target: {project.repo} | Branch: {branch_name} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]• Scope: Autonomous Architectural Review Remediation & Test Verification[/dim]")

    # 1. Fetch Architect review critique from PR comments and reviews
    architect_critique = ""
    if shutil.which("gh"):
        try:
            proc_view = await asyncio.create_subprocess_exec(
                "gh", "pr", "view", str(pr_number),
                "--repo", project.repo,
                "--json", "reviews,comments,headRefName",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_v, _ = await proc_view.communicate()
            if proc_view.returncode == 0 and stdout_v:
                pr_data = json.loads(stdout_v.decode("utf-8", errors="replace"))
                if not branch_name:
                    branch_name = pr_data.get("headRefName", "")
                review_bodies = [r.get("body", "") for r in pr_data.get("reviews", []) if r.get("body")]
                comment_bodies = [c.get("body", "") for c in pr_data.get("comments", []) if c.get("body")]
                all_critiques = review_bodies + comment_bodies
                architect_critiques = [c for c in all_critiques if "Architectural Review" in c or "needs-refactor" in c or "Refactoring Required" in c]
                if architect_critiques:
                    architect_critique = "\n\n---\n\n".join(architect_critiques)
                elif all_critiques:
                    architect_critique = all_critiques[-1]
        except Exception as e:
            architect_critique = f"(Unable to parse PR review comments: {e})"

    if not branch_name:
        branch_name = f"feat/issue-{pr_number}"

    # 2. Pre-flight checkout of the PR branch
    try:
        p1 = await asyncio.create_subprocess_exec("git", "reset", "--hard", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p1.wait()
        p2 = await asyncio.create_subprocess_exec("git", "clean", "-fd", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p2.wait()
        p3 = await asyncio.create_subprocess_exec("git", "fetch", "origin", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p3.wait()
        p4 = await asyncio.create_subprocess_exec("git", "checkout", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p4.wait()
        p5 = await asyncio.create_subprocess_exec("git", "pull", "origin", branch_name, cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p5.wait()
    except Exception as e:
        await state_manager.fail_job(
            issue_id=pr_number,
            repo=project.repo,
            node_type="devtest_refactor",
            error_message=f"Pre-flight checkout failed: {e}",
        )
        await state_manager.release_lock(pr_number, project.repo, "devtest_refactor")
        return False, f"Pre-flight checkout failed for PR #{pr_number} ({branch_name}): {e}"

    # 3. Build refactoring prompt
    prompt = (
        f"You are the 3-Amigos Developer & QA Engineer operating autonomously in non-interactive batch mode.\n"
        f"Remediate the Architectural Code Review feedback on Pull Request #{pr_number} ('{pr_title}') on branch '{branch_name}'.\n\n"
        f"🚨 ARCHITECTURAL CODE REVIEW FEEDBACK:\n"
        f"{architect_critique or 'The Architect requested refactoring to adhere to domain boundaries, dynamic TTL locking, and .graph/architecture.md standards.'}\n\n"
        f"OPERATIONAL STEPS:\n"
        f"1. Read .graph/architecture.md and understand the requested architectural changes.\n"
        f"2. Inspect the current implementation on branch '{branch_name}'.\n"
        f"3. Refactor the code strictly addressing the Architect's critique while maintaining all existing passing tests.\n"
        f"4. Run the local unit test suite and confirm that 100% of tests pass.\n"
        f"5. Commit your changes with a descriptive message: `refactor: address architect code review feedback for PR #{pr_number}`.\n"
        f"6. Push the updated branch to `origin {branch_name}`.\n"
    )

    adapter = AsyncHarnessAdapter(
        harness_name,
        harness_cfg,
        state_manager=state_manager,
        project_name=project.name,
        node_name="devtest",
        issue_number=pr_number,
    )
    try:
        exit_code = await adapter.execute(
            prompt=prompt,
            cwd=project.local_path,
            log_file=log_file,
            model=node_cfg.model,
            effort=node_cfg.effort,
            console_prefix=f"[{project.name}:devtest-refactor]",
        )
    finally:
        await state_manager.release_lock(pr_number, project.repo, "devtest_refactor")

    if exit_code != 0:
        await state_manager.fail_job(
            issue_id=pr_number,
            repo=project.repo,
            node_type="devtest_refactor",
            error_message=f"Refactor harness exited with code {exit_code}. See logs: {log_file.name}",
        )
        return False, f"DevTest refactor failed on PR #{pr_number} (exit code {exit_code})."

    # 4. Check git status and push if uncommitted changes remain
    diff_proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=str(project.local_path),
        stdout=asyncio.subprocess.PIPE,
    )
    diff_out, _ = await diff_proc.communicate()
    if diff_out.strip():
        pa = await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(project.local_path))
        await pa.wait()
        pc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"refactor: address architect feedback for PR #{pr_number}",
            cwd=str(project.local_path),
        )
        await pc.wait()
        pp = await asyncio.create_subprocess_exec("git", "push", "origin", branch_name, cwd=str(project.local_path))
        await pp.wait()

    # 5. E2E CI Verification / Auto-Merge on Remediated PR
    if getattr(node_cfg, "auto_merge_approved", True):
        ci_status, ci_details = await check_pr_ci_status(project.repo, pr_number)
        if ci_status == "PASS" and shutil.which("gh"):
            await (await asyncio.create_subprocess_exec(
                "gh", "pr", "review", str(pr_number),
                "--repo", project.repo,
                "--approve",
                "--body", "🤖 **DevTest Quality Gate**: Remediated PR passed all tests & CI checks (100% Green). Auto-merging into main.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )).wait()

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
                await state_manager.sync_project_sdlc_items(
                    project.name,
                    [{
                        "issue_number": pr_number,
                        "title": pr_title,
                        "state": "MERGED",
                        "labels": ["merged"],
                        "linked_pr": pr_number,
                    }],
                )
                await state_manager.delete_pr_artifact(project.repo, pr_number)
                try:
                    await _advance_parent_and_unlock_next_subtask(project, state_manager, pr_number)
                except Exception:
                    pass
                return True, f"DevTest node remediated PR #{pr_number}, verified CI 100% Green, and merged into main."

    if shutil.which("gh"):
        p_edit = await asyncio.create_subprocess_exec(
            "gh", "pr", "edit", str(pr_number),
            "--repo", project.repo,
            "--remove-label", "needs-refactor",
            "--add-label", "needs-architect-review",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_edit.wait()

        p_comment = await asyncio.create_subprocess_exec(
            "gh", "pr", "comment", str(pr_number),
            "--repo", project.repo,
            "--body", f"🤖 **DevTest Refactor Complete**: Architectural review feedback addressed on branch `{branch_name}`. Returning to `needs-architect-review`.",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_comment.wait()

    return True, f"DevTest node remediated PR #{pr_number} and transitioned to 'needs-architect-review'."


async def _advance_parent_and_unlock_next_subtask(
    project: ProjectConfig,
    state_manager: StateManager,
    subtask_id: int,
) -> None:
    """
    Evaluates whether the completed subtask belongs to a parent story.
    If so:
    1. Checks off `- [x] #<subtask_id>` in the parent issue body.
    2. Searches for remaining open child subtasks with label 'queued'.
    3. If any queued subtask exists, unlocks the first queued subtask in sequence
       (removes 'queued', applies 'ready-for-dev').
    4. If 100% of child subtasks for the parent are closed, transitions the parent
       issue to 'dev-implemented' and closes the parent story.
    """
    if not shutil.which("gh"):
        return

    # 1. Fetch subtask details to find if it has a Parent reference
    proc_sub = await asyncio.create_subprocess_exec(
        "gh", "issue", "view", str(subtask_id),
        "--repo", project.repo,
        "--json", "body,title,labels",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_sub, _ = await proc_sub.communicate()
    if proc_sub.returncode != 0 or not stdout_sub:
        return

    parent_id = None
    try:
        sub_data = json.loads(stdout_sub.decode("utf-8", errors="replace"))
        sub_body = sub_data.get("body", "")
        m = re.search(r"Parent:\s*#(\d+)", sub_body, re.IGNORECASE)
        if m:
            parent_id = int(m.group(1))
    except Exception:
        pass

    if not parent_id:
        return

    # 2. Fetch parent issue details
    proc_parent = await asyncio.create_subprocess_exec(
        "gh", "issue", "view", str(parent_id),
        "--repo", project.repo,
        "--json", "body,title,labels,state",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_p, _ = await proc_parent.communicate()
    if proc_parent.returncode != 0 or not stdout_p:
        return

    try:
        parent_data = json.loads(stdout_p.decode("utf-8", errors="replace"))
    except Exception:
        return

    parent_body = parent_data.get("body", "")
    if not parent_body:
        return

    # 3. Check off this subtask in the parent body
    updated_body = re.sub(
        rf"(-\s*\[\s*\]\s*#{subtask_id}\b)",
        f"- [x] #{subtask_id}",
        parent_body,
    )

    if updated_body != parent_body:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(updated_body)
            temp_path = tf.name
        try:
            p_update = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(parent_id),
                "--repo", project.repo,
                "--body-file", temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_update.wait()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    # 4. Search for all child subtasks referencing Parent: #<parent_id>
    proc_children = await asyncio.create_subprocess_exec(
        "gh", "issue", "list",
        "--repo", project.repo,
        "--search", f"#{parent_id}",
        "--state", "all",
        "--json", "number,title,state,labels",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_c, _ = await proc_children.communicate()
    children = []
    if proc_children.returncode == 0 and stdout_c:
        try:
            all_res = json.loads(stdout_c.decode("utf-8", errors="replace"))
            children = [c for c in all_res if c.get("number") != parent_id]
        except Exception:
            pass

    if not children:
        return

    children.sort(key=lambda x: x.get("number", 0))

    # Check if any open subtasks with 'queued' exist
    queued_children = []
    for c in children:
        c_state = str(c.get("state", "")).upper()
        if c_state != "CLOSED":
            c_labels = [l.get("name") if isinstance(l, dict) else str(l) for l in c.get("labels", [])]
            if "queued" in c_labels or "status:queued" in c_labels:
                queued_children.append(c)

    if queued_children:
        next_child = queued_children[0]
        next_id = next_child["number"]
        queued_lbl = "status:queued" if "status:queued" in [l.get("name") if isinstance(l, dict) else str(l) for l in next_child.get("labels", [])] else "queued"
        ready_lbl = "status:ready-for-dev" if queued_lbl == "status:queued" else "ready-for-dev"

        p_promote = await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(next_id),
            "--repo", project.repo,
            "--remove-label", queued_lbl,
            "--add-label", ready_lbl,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_promote.wait()

        p_comment = await asyncio.create_subprocess_exec(
            "gh", "issue", "comment", str(parent_id),
            "--repo", project.repo,
            "--body", f"🤖 **DevTest Sequential Advance**: Subtask #{subtask_id} completed and merged. Unlocked next sequential subtask #{next_id} (`{ready_lbl}`).",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_comment.wait()
    else:
        # Check if 100% of all children are now CLOSED
        all_closed = all(str(c.get("state", "")).upper() == "CLOSED" for c in children)
        if all_closed:
            p_edit_parent = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(parent_id),
                "--repo", project.repo,
                "--remove-label", "architect-processed",
                "--remove-label", "status:in-progress",
                "--add-label", "dev-implemented",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_edit_parent.wait()

            child_list_str = ", ".join(f"#{c.get('number')}" for c in children)
            p_close_parent = await asyncio.create_subprocess_exec(
                "gh", "issue", "close", str(parent_id),
                "--repo", project.repo,
                "--comment", f"🎉 **Parent Story Completed**: 100% of child subtasks ({child_list_str}) have been implemented, verified against CI, and merged into main.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_close_parent.wait()

            await state_manager.sync_project_sdlc_items(
                project.name,
                [{
                    "issue_number": parent_id,
                    "title": parent_data.get("title", f"Parent Story #{parent_id}"),
                    "state": "CLOSED",
                    "labels": ["dev-implemented"],
                    "item_type": "STORY",
                }],
            )


async def _verify_and_auto_merge_pr(
    project: ProjectConfig,
    state_manager: StateManager,
    pr_number: int,
    issue_id: int,
    issue_title: str,
    trigger_label: str,
    auto_merge_approved: bool = True,
    is_conflict_resolution: bool = False,
    default_output_label: str = "needs-architect-review",
) -> tuple[bool, str]:
    """
    Performs E2E verification on a DevTest implementation PR:
    1. Checks remote GitHub Actions CI status (`check_pr_ci_status`).
    2. If CI is PASS and auto_merge_approved is True:
       - Approves the PR.
       - Squashes and merges the PR into main (`--delete-branch`).
       - Transitions parent issue to 'dev-implemented' and closes the issue.
       - Syncs SDLC item in StateManager to MERGED.
       - Evaluates parent story sequential advance / parent closure.
    3. If CI is FAIL:
       - Flags the PR with 'needs-refactor'.
       - Posts a comment detailing failing checks.
    4. If CI is PENDING or auto_merge_approved is False:
       - Relabels PR to 'needs-architect-review' (or leaves pending).
    """
    from rich.console import Console
    console = Console()

    if not auto_merge_approved:
        effective_output_label = "architect-approved" if is_conflict_resolution else default_output_label
        if shutil.which("gh"):
            p_pr_label = await asyncio.create_subprocess_exec(
                "gh", "pr", "edit", str(pr_number),
                "--repo", project.repo,
                "--add-label", effective_output_label,
            )
            await p_pr_label.wait()
            p_issue_edit = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger_label,
                "--add-label", "dev-implemented",
            )
            await p_issue_edit.wait()
        await state_manager.sync_project_sdlc_items(
            project.name,
            [{
                "issue_number": issue_id,
                "title": issue_title,
                "state": "IN_PROGRESS",
                "labels": ["dev-implemented"],
                "linked_pr": pr_number,
            }],
        )
        return True, f"DevTest node implemented issue #{issue_id} and opened PR #{pr_number} ('{effective_output_label}')."

    # E2E CI Verification & Auto-Merge
    ci_status, ci_details = await check_pr_ci_status(project.repo, pr_number)
    console.print(f"  [{project.name}:devtest] [dim]PR #{pr_number} CI Status: {ci_status} ({ci_details})[/dim]")

    if ci_status == "PASS":
        if shutil.which("gh"):
            # 1. Quality gate approval review
            p_approve = await asyncio.create_subprocess_exec(
                "gh", "pr", "review", str(pr_number),
                "--repo", project.repo,
                "--approve",
                "--body", "🤖 **DevTest Quality Gate**: 100% passing local tests and green remote CI. Auto-merging into main.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_approve.wait()

            # 2. Squash and merge
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
                console.print(f"  [{project.name}:devtest] [bold green]✓ DevTest E2E Complete: PR #{pr_number} auto-merged into main[/bold green]")
                
                # 3. Close issue & mark dev-implemented
                p_issue_edit = await asyncio.create_subprocess_exec(
                    "gh", "issue", "edit", str(issue_id),
                    "--repo", project.repo,
                    "--remove-label", trigger_label,
                    "--add-label", "dev-implemented",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p_issue_edit.wait()

                p_close = await asyncio.create_subprocess_exec(
                    "gh", "issue", "close", str(issue_id),
                    "--repo", project.repo,
                    "--comment", f"🎉 **DevTest E2E Completed**: Implemented, verified against CI, and merged via PR #{pr_number}.",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p_close.wait()

                await state_manager.sync_project_sdlc_items(
                    project.name,
                    [{
                        "issue_number": issue_id,
                        "title": issue_title,
                        "state": "MERGED",
                        "labels": ["dev-implemented"],
                        "linked_pr": pr_number,
                    }],
                )
                await state_manager.delete_pr_artifact(project.repo, pr_number)

                # 4. Advance parent story sequence / unlock next queued subtask
                try:
                    await _advance_parent_and_unlock_next_subtask(project, state_manager, issue_id)
                except Exception as ex:
                    console.print(f"  [{project.name}:devtest] [dim yellow]Parent sequential advance notice: {ex}[/dim yellow]")

                return True, f"DevTest node implemented issue #{issue_id}, verified CI 100% Green, and auto-merged PR #{pr_number} into main."

    elif ci_status == "FAIL":
        if shutil.which("gh"):
            p_fail = await asyncio.create_subprocess_exec(
                "gh", "pr", "edit", str(pr_number),
                "--repo", project.repo,
                "--add-label", "needs-refactor",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_fail.wait()
            p_comm = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", project.repo,
                "--body", f"🤖 **DevTest Quality Gate**: Remote CI checks failed ({ci_details}). Flagging with `needs-refactor` for autonomous remediation.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_comm.wait()

        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="devtest",
            error_type="CI_FAILURE",
            error_message=f"PR #{pr_number} failed CI checks: {ci_details}",
            issue_number=issue_id,
        )
        return False, f"DevTest PR #{pr_number} failed CI checks ({ci_details}). Tagged 'needs-refactor'."

    # CI is PENDING or checks running
    if shutil.which("gh"):
        p_label = await asyncio.create_subprocess_exec(
            "gh", "pr", "edit", str(pr_number),
            "--repo", project.repo,
            "--add-label", "dev-implemented",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_label.wait()
        p_issue = await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(issue_id),
            "--repo", project.repo,
            "--remove-label", trigger_label,
            "--add-label", "dev-implemented",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_issue.wait()

    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": issue_id,
            "title": issue_title,
            "state": "IN_PROGRESS",
            "labels": ["dev-implemented"],
            "linked_pr": pr_number,
        }],
    )
    return True, f"DevTest node implemented issue #{issue_id} and opened PR #{pr_number} (CI checks pending)."


async def run_devtest_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes 3Amigos DevTest Node (Implementation & Verification).
    Zero-token gating: if no issues labeled 'ready-for-dev' and no PRs labeled 'needs-refactor', exits with 0 tokens consumed.
    """
    node_cfg = project.nodes.get("devtest", NodeConfig(harness="antigravity"))
    if not node_cfg.enabled:
        return False, "DevTest node disabled for project."

    # Phase 1: Remediate PRs with 'needs-refactor'
    refactor_prs = await fetch_open_prs(project.repo, label="needs-refactor", limit=1)
    if refactor_prs:
        harness_name = node_cfg.harness or "antigravity"
        allowed, q_res = await check_dispatch_quota(project, "devtest", config, state_manager, harness_name=harness_name)
        if not allowed:
            return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."
        target_pr = refactor_prs[0]
        pr_number = target_pr["number"]
        pr_title = target_pr.get("title", "")
        branch_name = target_pr.get("headRefName", "")
        return await _remediate_refactor_pr(
            project=project,
            config=config,
            state_manager=state_manager,
            node_cfg=node_cfg,
            pr_number=pr_number,
            pr_title=pr_title,
            branch_name=branch_name,
        )

    trigger = node_cfg.label_trigger or "ready-for-dev"
    output_label = node_cfg.label_output or "needs-architect-review"
    branch_prefix = node_cfg.branch_prefix or "feat/issue-"

    # 1. Deterministic Gating (0 Tokens)
    issues = await fetch_issues_with_label(project.repo, trigger, limit=1)
    if not issues:
        return False, f"No PRs labeled 'needs-refactor' and no issues labeled '{trigger}'. Idle (0 tokens)."

    # Pre-Flight Quota Gating (Pure local SQLite calculation, 0 LLM tokens)
    harness_name = node_cfg.harness or "antigravity"
    allowed, q_res = await check_dispatch_quota(project, "devtest", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    target_issue = issues[0]
    issue_id = target_issue["number"]
    issue_title = target_issue.get("title", "")

    # Sync picked-up issue into SDLC Blackboard memory
    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": issue_id,
            "title": issue_title,
            "state": "OPEN",
            "labels": [trigger],
        }],
    )

    # 2. Destructive Git Safety Check
    is_safe, safety_msg = await verify_git_safety(project.local_path, project.repo)
    if not is_safe:
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="devtest",
            error_type="SAFETY_ERROR",
            error_message=safety_msg,
            issue_number=issue_id,
        )
        return False, safety_msg

    # 3. Acquire State Lock
    harness_name = node_cfg.harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not found in configuration."

    retry_cfg = getattr(harness_cfg, "retry", None)
    max_retries = getattr(retry_cfg, "max_retries", 0) if retry_cfg else 0
    lock_ttl = int(harness_cfg.timeout_minutes * (1 + max_retries) + 5)

    lock_acquired = await state_manager.acquire_lock(
        issue_id=issue_id,
        repo=project.repo,
        node_type="devtest",
        ttl_minutes=lock_ttl,
    )
    if not lock_acquired:
        return False, f"Issue #{issue_id} is currently locked by another active run. Skipping."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "devtest",
        issue_id=issue_id,
    )

    # 4. Pre-Flight Cleanup: wipe aborted AI artifacts and ensure clean workspace
    from rich.console import Console
    console = Console()
    console.print(f"\n  [bold blue]⚡ [{project.name}:devtest][/bold blue] [bold white]Implementing Subtask #{issue_id}:[/bold white] [cyan]'{issue_title}'[/cyan]")
    console.print(f"  [dim]• Target: {project.repo} | Branch: {branch_prefix}{issue_id} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]• Scope: 3-Amigos TDD Development, Test Verification & PR Creation[/dim]")

    try:
        await (await asyncio.create_subprocess_exec("git", "reset", "--hard", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "clean", "-fd", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "checkout", "main", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "pull", "origin", "main", cwd=str(project.local_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
    except Exception as e:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Pre-flight git reset failed: {e}",
        )
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="devtest",
            error_type="PREFLIGHT_ERROR",
            error_message=f"Pre-flight git reset failed: {e}",
            issue_number=issue_id,
        )
        return False, f"Pre-flight reset failed: {e}"

    adapter = AsyncHarnessAdapter(harness_name, harness_cfg)

    # 5. Check Blackboard for Pre-Approved Context (AC 5)
    artifact = await state_manager.get_pr_artifact(project.repo, issue_id)
    is_conflict_resolution = artifact is not None and artifact.get("status") == "APPROVED_WITH_CONFLICT"

    context_note = ""
    if is_conflict_resolution:
        context_note = (
            "🚨 CRITICAL - PRE-APPROVED CODE (BLACKBOARD CONTEXT):\n"
            f"PR/Issue #{issue_id} has already passed ARCHITECTURAL CODE REVIEW ({artifact.get('comment')}).\n"
            "DO NOT rewrite domain models, architectural contracts, or business logic.\n"
            "Your objective is STRICTLY to reconcile git merge conflicts against origin/main, verify the test suite passes, commit, and push.\n"
        )
    elif project.context_files:
        files_str = ", ".join(project.context_files)
        context_note = (
            f"Read the methodology and architecture files listed in: {files_str}.\n"
            f"Implement the code strictly adhering to those local repository standards.\n"
        )

    if is_conflict_resolution:
        prompt = (
            f"You are the 3-Amigos Developer & QA Engineer operating autonomously in non-interactive batch mode.\n"
            f"Resolve git merge conflicts against origin/main for pre-approved Issue/PR #{issue_id} ('{issue_title}').\n\n"
            f"{context_note}\n"
            f"OPERATIONAL STEPS:\n"
            f"1. Fetch origin and merge origin/main into the branch ('{branch_prefix}{issue_id}').\n"
            f"2. Inspect and cleanly resolve all conflict markers (<<<<<<< HEAD ... ======= ... >>>>>>>).\n"
            f"3. Run the local unit test suite and ensure all tests pass.\n"
            f"4. Commit with a message 'chore(merge): resolve conflicts with main for #{issue_id}'.\n"
            f"5. Push the branch to origin.\n"
        )
    else:
        prompt = (
            f"You are the 3-Amigos Developer & QA Engineer operating autonomously in non-interactive batch mode.\n"
            f"Implement the technical requirements for Issue #{issue_id} ('{issue_title}').\n\n"
            f"{context_note}"
            f"OPERATIONAL STEPS:\n"
            f"1. Read the Gherkin acceptance criteria in Issue #{issue_id} and local context files.\n"
            f"2. Write comprehensive unit and integration tests covering all Given/When/Then scenarios.\n"
            f"3. Implement the minimal clean code required to make all tests pass.\n"
            f"4. Verify that the entire test suite and linter pass cleanly.\n"
            f"5. Commit changes with a descriptive message and push your branch ('{branch_prefix}{issue_id}').\n"
            f"6. Open a Pull Request using `gh pr create --title '<title>' --body 'Closes #{issue_id}'`.\n"
        )

    adapter = AsyncHarnessAdapter(
        harness_name,
        harness_cfg,
        state_manager=state_manager,
        project_name=project.name,
        node_name="devtest",
        issue_number=issue_id,
    )
    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=project.local_path,
        log_file=log_file,
        model=node_cfg.model,
        effort=node_cfg.effort,
        console_prefix=f"[{project.name}:devtest]",
    )

    if exit_code != 0:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Harness exited with code {exit_code}. See logs: {log_file.name}",
        )
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="devtest",
            error_type="HARNESS_ERROR",
            error_message=f"Harness exited with code {exit_code}. See logs: {log_file.name}",
            issue_number=issue_id,
        )
        if shutil.which("gh"):
            p1 = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "orchestration-failed",
            )
            await p1.wait()

            p2 = await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_id),
                "--repo", project.repo,
                "--body", f"🤖 **DevTest Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
            )
            await p2.wait()
        return False, f"DevTest execution failed on issue #{issue_id} (exit code {exit_code})."

    # 6. Verify if PR was already created by the harness (autonomous lifecycle)
    branch_name = f"{branch_prefix}{issue_id}"
    existing_pr: Optional[Dict[str, Any]] = None

    if shutil.which("gh"):
        proc_pr = await asyncio.create_subprocess_exec(
            "gh", "pr", "list",
            "--repo", project.repo,
            "--search", f"#{issue_id}",
            "--state", "open",
            "--json", "number,title,labels,headRefName",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_pr, _ = await proc_pr.communicate()
        if proc_pr.returncode == 0 and stdout_pr:
            try:
                prs = json.loads(stdout_pr.decode("utf-8", errors="replace"))
                if prs:
                    existing_pr = prs[0]
            except Exception:
                pass

    if existing_pr:
        pr_num = existing_pr["number"]
        if is_conflict_resolution:
            await state_manager.upsert_pr_artifact(
                repo=project.repo,
                pr_number=issue_id,
                node_name="devtest",
                status="CONFLICT_RESOLVED",
                comment=f"DevTest node resolved merge conflicts on PR #{pr_num}.",
            )

        ran, msg = await _verify_and_auto_merge_pr(
            project=project,
            state_manager=state_manager,
            pr_number=pr_num,
            issue_id=issue_id,
            issue_title=issue_title,
            trigger_label=trigger,
            auto_merge_approved=getattr(node_cfg, "auto_merge_approved", True),
            is_conflict_resolution=is_conflict_resolution,
            default_output_label=output_label,
        )
        await state_manager.release_lock(issue_id, project.repo, "devtest")
        return ran, msg

    # 7. Fallback: Check Git Diff (Did the model leave uncommitted code?)
    diff_proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=str(project.local_path),
        stdout=asyncio.subprocess.PIPE,
    )
    diff_out, _ = await diff_proc.communicate()
    if not diff_out.strip():
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message="Model finished but left 0 git changes and no PR was created.",
        )
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="devtest",
            error_type="ZERO_DIFF_ERROR",
            error_message="Model finished but left 0 git changes and no PR was created.",
            issue_number=issue_id,
        )
        await state_manager.release_lock(issue_id, project.repo, "devtest")
        return False, f"DevTest finished with 0 file changes for issue #{issue_id}."

    # 8. Branch, Commit, Push & PR Lifecycle (if uncommitted changes exist)
    created_pr_num = None
    try:
        await (await asyncio.create_subprocess_exec("git", "checkout", "-B", branch_name, cwd=str(project.local_path))).wait()
        await (await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(project.local_path))).wait()
        await (await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"feat: implement #{issue_id} - {issue_title}", cwd=str(project.local_path)
        )).wait()
        await (await asyncio.create_subprocess_exec("git", "push", "-u", "origin", branch_name, cwd=str(project.local_path))).wait()

        if shutil.which("gh"):
            p_pr = await asyncio.create_subprocess_exec(
                "gh", "pr", "create",
                "--repo", project.repo,
                "--title", f"feat: resolve #{issue_id} - {issue_title}",
                "--body", f"Automated 3-Amigos DevTest implementation.\n\nCloses #{issue_id}",
                "--label", output_label,
                cwd=str(project.local_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_c, _ = await p_pr.communicate()
            if stdout_c:
                url_str = stdout_c.decode("utf-8", errors="replace").strip()
                if "/pull/" in url_str:
                    try:
                        created_pr_num = int(url_str.split("/pull/")[-1].split()[0])
                    except Exception:
                        pass
    except Exception as e:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Git / PR creation failed: {e}",
        )
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="devtest",
            error_type="GIT_ERROR",
            error_message=f"Git / PR creation failed: {e}",
            issue_number=issue_id,
        )
        await state_manager.release_lock(issue_id, project.repo, "devtest")
        return False, f"Git / PR creation failed: {e}"

    if created_pr_num:
        ran, msg = await _verify_and_auto_merge_pr(
            project=project,
            state_manager=state_manager,
            pr_number=created_pr_num,
            issue_id=issue_id,
            issue_title=issue_title,
            trigger_label=trigger,
            auto_merge_approved=getattr(node_cfg, "auto_merge_approved", True),
            default_output_label=output_label,
        )
        await state_manager.release_lock(issue_id, project.repo, "devtest")
        return ran, msg

    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": issue_id,
            "title": issue_title,
            "state": "IN_PROGRESS",
            "labels": ["dev-implemented"],
        }],
    )

    await state_manager.release_lock(issue_id, project.repo, "devtest")
    return True, f"DevTest node implemented issue #{issue_id} and opened PR with label '{output_label}'."

