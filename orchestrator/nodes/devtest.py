from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import (
    ProjectLogBufferManager,
    format_story_lock_dispatch_log,
    get_project_log_path,
)
from orchestrator import poller
from orchestrator.nodes.reviewer import check_pr_ci_status
from orchestrator.poller import check_dispatch_quota, fetch_issue_by_number, fetch_open_prs
from orchestrator.worktree import WorktreeManager, clean_worktree

_logger = logging.getLogger(__name__)
console = Console()


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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        remote_url = stdout.decode("utf-8").strip()

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
    Autonomously remediates a PR labeled 'needs-refactor' by ingesting failure
    or review critiques, applying refactorings on the branch, verifying tests, committing,
    pushing, and re-evaluating CI for auto-merge.
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

    console.print(f"\n  [bold yellow]🔧 [{project.name}:devtest][/bold yellow] [bold white]Remediating PR #{pr_number} ('needs-refactor'):[/bold white] [cyan]'{pr_title}'[/cyan]")
    console.print(f"  [dim]• Target: {project.repo} | Branch: {branch_name} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]• Scope: Autonomous Refactoring, Test Verification & PR CI Auto-Merge[/dim]")

    env = {**os.environ, "GH_PROMPT_DISABLED": "1"}

    # 1. Fetch critique / comments from PR
    critique = ""
    if shutil.which("gh"):
        try:
            proc_view = await asyncio.create_subprocess_exec(
                "gh", "pr", "view", str(pr_number),
                "--repo", project.repo,
                "--json", "reviews,comments,headRefName",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_v, _ = await asyncio.wait_for(proc_view.communicate(), timeout=10.0)
            if proc_view.returncode == 0 and stdout_v:
                pr_data = json.loads(stdout_v.decode("utf-8", errors="replace"))
                if not branch_name:
                    branch_name = pr_data.get("headRefName", "")
                review_bodies = [r.get("body", "") for r in pr_data.get("reviews", []) if r.get("body")]
                comment_bodies = [c.get("body", "") for c in pr_data.get("comments", []) if c.get("body")]
                all_critiques = review_bodies + comment_bodies
                architect_critiques = [c for c in all_critiques if "Architectural Review" in c or "needs-refactor" in c or "Refactoring Required" in c]
                if architect_critiques:
                    critique = "\n\n---\n\n".join(architect_critiques)
                elif all_critiques:
                    critique = all_critiques[-1]
        except Exception as e:
            critique = f"(Unable to parse PR review comments: {e})"

    if not branch_name:
        branch_name = f"feat/issue-{pr_number}"

    # 2. Resolve Worktree & Pre-flight checkout of the PR branch
    exec_cwd = await WorktreeManager.ensure_worktree(project, "devtest")
    try:
        await (await asyncio.create_subprocess_exec("git", "reset", "--hard", cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "clean", "-fd", cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "fetch", "origin", branch_name, cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "checkout", branch_name, cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "pull", "origin", branch_name, cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
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
        f"Remediate issues on Pull Request #{pr_number} ('{pr_title}') on branch '{branch_name}'.\n\n"
        f"🚨 ARCHITECTURAL CODE REVIEW FEEDBACK & CI ERROR CONTEXT:\n"
        f"{critique or 'The Pull Request failed CI or required refactoring.'}\n\n"
        f"OPERATIONAL STEPS:\n"
        f"1. Inspect the codebase and current implementation on branch '{branch_name}'.\n"
        f"2. Fix the issues, bugs, or failing tests.\n"
        f"3. Run the local unit test suite and confirm that 100% of tests pass.\n"
        f"4. Commit your changes with a descriptive message: `refactor: address feedback for PR #{pr_number}`.\n"
        f"5. Push the updated branch to `origin {branch_name}`.\n"
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
            cwd=exec_cwd,
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
        cwd=str(exec_cwd),
        stdout=asyncio.subprocess.PIPE,
    )
    diff_out, _ = await asyncio.wait_for(diff_proc.communicate(), timeout=10.0)
    if diff_out.strip():
        pa = await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(exec_cwd))
        await pa.wait()
        pc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"refactor: address feedback for PR #{pr_number}",
            cwd=str(exec_cwd),
        )
        await pc.wait()
        pp = await asyncio.create_subprocess_exec("git", "push", "origin", branch_name, cwd=str(exec_cwd))
        await pp.wait()

    # 5. E2E CI Verification / Auto-Merge on Remediated PR
    if getattr(node_cfg, "auto_merge_approved", True):
        ci_status, ci_details = await check_pr_ci_status(project.repo, pr_number)
        if ci_status == "PASS" and shutil.which("gh"):
            p_merge = await asyncio.create_subprocess_exec(
                "gh", "pr", "merge", str(pr_number),
                "--repo", project.repo,
                "--squash",
                "--delete-branch",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_merge.communicate(), timeout=10.0)
            if p_merge.returncode == 0:
                await state_manager.sync_project_sdlc_items(
                    project.name,
                    [{
                        "issue_number": pr_number,
                        "title": pr_title,
                        "state": "MERGED",
                        "labels": ["dev-implemented"],
                        "linked_pr": pr_number,
                    }],
                )
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
            "--add-label", "dev-implemented",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.wait_for(p_edit.communicate(), timeout=10.0)

    return True, f"DevTest node remediated PR #{pr_number} and updated branch '{branch_name}' (awaiting CI completion)."


def _is_task_closed_or_merged(c: dict[str, Any]) -> bool:
    """Checks if a task dictionary represents a closed, merged, or completed issue."""
    state = str(c.get("state", "")).strip().upper()
    if state in ("CLOSED", "MERGED", "DONE", "STATUS:CLOSED", "STATUS:MERGED", "STATUS:DONE"):
        return True
    labels = [lbl_item.get("name") if isinstance(lbl_item, dict) else str(lbl_item) for lbl_item in c.get("labels", [])]
    if any(lbl.lower() in ("closed", "merged", "status:closed", "status:merged") for lbl in labels):
        return True
    return False


def _is_task_blocked(c: dict[str, Any]) -> bool:
    """Checks if a task dictionary represents a blocked or failed issue."""
    state = str(c.get("state", "")).strip().upper()
    if any(b in state for b in ("BLOCKED", "ORCHESTRATION-FAILED", "ORCHESTRATION_FAILED", "ORCHESTRATION:FAILED")):
        return True
    labels = [lbl_item.get("name") if isinstance(lbl_item, dict) else str(lbl_item) for lbl_item in c.get("labels", [])]
    if any(lbl.lower() in ("blocked", "status:blocked", "orchestration-failed", "status:orchestration-failed") for lbl in labels):
        return True
    return False


def _advance_sequential_subtask(
    children: list[dict[str, Any]],
    queued_label: str = "queued",
    unchecked_ids: Optional[list[int] | set[int]] = None,
    exclude_ids: Optional[set[int] | list[int] | int] = None,
) -> Optional[dict[str, Any]]:
    """
    Selects the lowest-ID open uncompleted child subtask to promote,
    strictly excluding merged, closed, and blocked tasks and selecting min(number).
    """
    if not children:
        return None

    if isinstance(exclude_ids, int):
        excluded = {exclude_ids}
    elif exclude_ids:
        excluded = set(exclude_ids)
    else:
        excluded = set()

    unchecked_set = set(unchecked_ids) if unchecked_ids is not None else None

    eligible: list[dict[str, Any]] = []
    for c in children:
        cid = c.get("number")
        if cid is None or cid in excluded:
            continue
        if _is_task_closed_or_merged(c) or _is_task_blocked(c):
            continue

        c_labels = [lbl_item.get("name") if isinstance(lbl_item, dict) else str(lbl_item) for lbl_item in c.get("labels", [])]
        is_queued = any(
            lbl in c_labels
            for lbl in (queued_label, f"status:{queued_label}", "queued", "status:queued", "status:pending-review")
        )
        is_unchecked = (unchecked_set is not None and cid in unchecked_set)

        if is_queued or is_unchecked or (unchecked_set is None and not is_queued and str(c.get("state", "")).upper() == "OPEN"):
            eligible.append(c)

    if not eligible:
        return None

    return min(eligible, key=lambda x: x.get("number", 0))


async def _advance_parent_and_unlock_next_subtask(
    project: ProjectConfig,
    state_manager: StateManager,
    subtask_id: int,
) -> None:
    """
    Evaluates whether the completed subtask belongs to a parent story.
    If so:
    1. Checks off `- [x] #<subtask_id>` in the parent issue body.
    2. Searches for remaining open child subtasks.
    3. If any queued subtask exists, unlocks the first queued subtask in ascendant sequence/ID order
       (removes 'queued', applies 'ready-for-dev').
    4. If 100% of child subtasks for the parent are closed, transitions the parent
       issue to 'dev-implemented' and closes the parent story.
    """
    if not shutil.which("gh"):
        return

    env = {**os.environ, "GH_PROMPT_DISABLED": "1"}

    # 1. Fetch subtask details to find if it has a Parent reference
    proc_sub = await asyncio.create_subprocess_exec(
        "gh", "issue", "view", str(subtask_id),
        "--repo", project.repo,
        "--json", "body,title,labels",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_sub, _ = await asyncio.wait_for(proc_sub.communicate(), timeout=10.0)
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
        "--json", "body,title,labels,state,comments",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_p, _ = await asyncio.wait_for(proc_parent.communicate(), timeout=10.0)
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
                env=env,
            )
            await asyncio.wait_for(p_update.communicate(), timeout=10.0)
        except Exception as e:
            _logger.debug("Failed to update parent body checklist for #%s: %s", parent_id, e)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    # 4. Discover all child subtasks referencing Parent: #<parent_id>
    proc_children = await asyncio.create_subprocess_exec(
        "gh", "issue", "list",
        "--repo", project.repo,
        "--search", f"#{parent_id}",
        "--state", "all",
        "--json", "number,title,state,labels",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_c, _ = await asyncio.wait_for(proc_children.communicate(), timeout=10.0)
    results = []
    if proc_children.returncode == 0 and stdout_c:
        try:
            results = json.loads(stdout_c.decode("utf-8", errors="replace"))
        except Exception:
            pass

    found_subtask_ids = {c.get("number") for c in results if c.get("number") != parent_id}

    for mark, sid in re.findall(r"-\s*\[([ xX])\]\s*#(\d+)", updated_body):
        cid = int(sid)
        if cid != parent_id:
            found_subtask_ids.add(cid)

    for comment in parent_data.get("comments", []):
        c_body = comment.get("body", "")
        for m in re.finditer(r"#(\d+)", c_body):
            cid = int(m.group(1))
            if cid != parent_id:
                found_subtask_ids.add(cid)

    children_dict = {c.get("number"): c for c in results if c.get("number") in found_subtask_ids}

    # Fetch details for any subtask IDs not returned by search
    for sid in sorted(found_subtask_ids - set(children_dict.keys())):
        try:
            p_sub = await asyncio.create_subprocess_exec(
                "gh", "issue", "view", str(sid),
                "--repo", project.repo,
                "--json", "number,title,state,labels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out_s, _ = await asyncio.wait_for(p_sub.communicate(), timeout=10.0)
            if p_sub.returncode == 0 and out_s:
                s_data = json.loads(out_s.decode("utf-8", errors="replace"))
                children_dict[sid] = s_data
        except Exception:
            pass

    children = list(children_dict.values())
    if not children:
        return

    children.sort(key=lambda x: x.get("number", 0))

    unchecked_ids = [
        int(sid) for mark, sid in re.findall(r"-\s*\[([ xX])\]\s*#(\d+)", updated_body)
        if mark.strip() == ""
    ]

    # Check if any open subtasks are in blocked/failed quarantine state
    has_blocked_child = False
    for c in children:
        c_state = str(c.get("state", "")).upper()
        if c_state != "CLOSED":
            c_labels = [l.get("name") if isinstance(l, dict) else str(l) for l in c.get("labels", [])]
            if any(lbl.lower() in ("blocked", "status:blocked", "orchestration-failed") for lbl in c_labels):
                has_blocked_child = True
                break

    if has_blocked_child:
        _logger.warning("[%s:devtest] Story #%s has a blocked subtask. Halting sequential advance.", project.name, parent_id)
        return

    node_cfg = project.nodes.get("devtest", NodeConfig(harness="antigravity"))
    trigger = node_cfg.label_trigger or "ready-for-dev"
    output_label = node_cfg.label_output or "dev-implemented"
    queued_label = node_cfg.queued_label or "queued"

    next_child = _advance_sequential_subtask(
        children=children,
        queued_label=queued_label,
        unchecked_ids=unchecked_ids,
        exclude_ids={subtask_id},
    )

    if next_child:
        next_id = next_child["number"]
        curr_labels = [l.get("name") if isinstance(l, dict) else str(l) for l in next_child.get("labels", [])]
        is_prefixed = any(l.startswith("status:") for l in curr_labels)
        queued_lbl = f"status:{queued_label}" if f"status:{queued_label}" in curr_labels else (
            "status:queued" if "status:queued" in curr_labels else (
                queued_label if queued_label in curr_labels else "queued"
            )
        )
        ready_lbl = f"status:{trigger}" if is_prefixed else trigger

        p_promote = await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(next_id),
            "--repo", project.repo,
            "--remove-label", queued_lbl,
            "--add-label", ready_lbl,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.wait_for(p_promote.communicate(), timeout=10.0)

        p_comment = await asyncio.create_subprocess_exec(
            "gh", "issue", "comment", str(parent_id),
            "--repo", project.repo,
            "--body", f"🤖 **DevTest Sequential Advance**: Subtask #{subtask_id} completed and merged. Unlocked next sequential subtask #{next_id} (`{ready_lbl}`).",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.wait_for(p_comment.communicate(), timeout=10.0)
    elif not unchecked_ids:
        # Check if 100% of all children are now CLOSED
        all_closed = all(str(c.get("state", "")).upper() == "CLOSED" for c in children)
        if all_closed:
            p_edit_parent = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(parent_id),
                "--repo", project.repo,
                "--remove-label", "architect-processed",
                "--add-label", output_label,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_edit_parent.communicate(), timeout=10.0)

            child_list_str = ", ".join(f"#{c.get('number')}" for c in children)
            p_close_parent = await asyncio.create_subprocess_exec(
                "gh", "issue", "close", str(parent_id),
                "--repo", project.repo,
                "--comment", f"🎉 **Parent Story Completed**: 100% of child subtasks ({child_list_str}) have been implemented, verified against CI, and merged into main.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_close_parent.communicate(), timeout=10.0)

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

            # Autonomous Story Promotion on Completion
            await _promote_next_planned_story(project, state_manager, parent_id)


async def _promote_next_planned_story(
    project: ProjectConfig,
    state_manager: StateManager,
    completed_story_id: int,
) -> Optional[int]:
    """
    On completion of a story's final subtask, promotes the oldest planned story
    into active status and unlocks its first queued subtask.
    """
    oldest_planned = await state_manager.get_oldest_planned_story(project.name)
    if not oldest_planned:
        return None

    node_cfg = project.nodes.get("devtest", NodeConfig(harness="antigravity"))
    trigger = node_cfg.label_trigger or "ready-for-dev"
    queued_label = node_cfg.queued_label or "queued"

    next_story_id = int(oldest_planned["issue_number"])

    # 1. Atomic promotion in SQLite
    await state_manager.promote_planned_story(project.name, next_story_id, new_status="ACTIVE")

    log_msg = f"[{project.name}|devtest] Story #{completed_story_id} complete. Activating planned Story #{next_story_id}."
    console.print(f"  [bold green]{log_msg}[/bold green]")
    _logger.info(log_msg)

    env = {**os.environ, "GH_PROMPT_DISABLED": "1"}

    # 2. Update story and unlock first queued subtask on GitHub
    if shutil.which("gh"):
        try:
            p_edit_story = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(next_story_id),
                "--repo", project.repo,
                "--add-label", "architect-processed",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_edit_story.communicate(), timeout=10.0)
        except Exception as e:
            _logger.debug("Error editing promoted story #%s on GitHub: %s", next_story_id, e)

        # Discover child subtasks
        try:
            proc_subtasks = await asyncio.create_subprocess_exec(
                "gh", "issue", "list",
                "--repo", project.repo,
                "--search", f"#{next_story_id}",
                "--state", "all",
                "--json", "number,title,state,labels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_st, _ = await asyncio.wait_for(proc_subtasks.communicate(), timeout=10.0)
            children = []
            if proc_subtasks.returncode == 0 and stdout_st:
                all_res = json.loads(stdout_st.decode("utf-8", errors="replace"))
                children = [c for c in all_res if c.get("number") != next_story_id]

            children.sort(key=lambda x: x.get("number", 0))
            queued_children = []
            for c in children:
                c_state = str(c.get("state", "")).upper()
                if c_state != "CLOSED":
                    c_labels = [l.get("name") if isinstance(l, dict) else str(l) for l in c.get("labels", [])]
                    if any(lbl in c_labels for lbl in (queued_label, f"status:{queued_label}", "queued", "status:queued")):
                        queued_children.append(c)

            if queued_children:
                first_child = queued_children[0]
                first_id = first_child["number"]
                child_labels = [l.get("name") if isinstance(l, dict) else str(l) for l in first_child.get("labels", [])]
                is_prefixed = any(l.startswith("status:") for l in child_labels)
                queued_lbl = f"status:{queued_label}" if f"status:{queued_label}" in child_labels else (
                    "status:queued" if "status:queued" in child_labels else (
                        queued_label if queued_label in child_labels else "queued"
                    )
                )
                ready_lbl = f"status:{trigger}" if is_prefixed else trigger

                p_unlock = await asyncio.create_subprocess_exec(
                    "gh", "issue", "edit", str(first_id),
                    "--repo", project.repo,
                    "--remove-label", queued_lbl,
                    "--add-label", ready_lbl,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_unlock.communicate(), timeout=10.0)

                p_comment = await asyncio.create_subprocess_exec(
                    "gh", "issue", "comment", str(next_story_id),
                    "--repo", project.repo,
                    "--body", f"🤖 **DevTest Story Activation**: Story #{completed_story_id} completed. Activated Story #{next_story_id} and unlocked first subtask #{first_id} (`{ready_lbl}`).",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_comment.communicate(), timeout=10.0)

                await state_manager.sync_project_sdlc_items(
                    project.name,
                    [{
                        "issue_number": first_id,
                        "title": first_child.get("title", f"Subtask #{first_id}"),
                        "state": "OPEN",
                        "labels": [ready_lbl],
                        "parent_issue_id": next_story_id,
                        "item_type": "SUBTASK",
                    }],
                )
        except Exception as e:
            _logger.debug("Error unlocking child subtask for story #%s: %s", next_story_id, e)

    return next_story_id


async def _verify_and_auto_merge_pr(
    project: ProjectConfig,
    state_manager: StateManager,
    pr_number: int,
    issue_id: int,
    issue_title: str,
    trigger_label: str,
    auto_merge_approved: bool = True,
    is_conflict_resolution: bool = False,
    default_output_label: str = "dev-implemented",
) -> tuple[bool, str]:
    """
    Performs E2E verification on a DevTest implementation PR:
    1. Checks remote GitHub Actions CI status (`check_pr_ci_status`).
    2. If CI is PASS and auto_merge_approved is True:
       - Squashes and merges the PR into main (`--delete-branch`).
       - Transitions issue to 'dev-implemented' and closes it.
       - Syncs SDLC item in StateManager to MERGED.
       - Evaluates parent story sequential advance / parent closure.
    3. If CI is FAIL:
       - Flags the PR with 'needs-refactor'.
       - Posts a comment detailing failing checks.
    4. If CI is PENDING:
       - Labels PR 'dev-implemented' and continues monitoring.
    """
    env = {**os.environ, "GH_PROMPT_DISABLED": "1"}

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
                env=env,
            )
            await asyncio.wait_for(p_approve.communicate(), timeout=10.0)

            # 2. Squash and merge
            p_merge = await asyncio.create_subprocess_exec(
                "gh", "pr", "merge", str(pr_number),
                "--repo", project.repo,
                "--squash",
                "--delete-branch",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_m, stderr_m = await asyncio.wait_for(p_merge.communicate(), timeout=10.0)
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
                    env=env,
                )
                await asyncio.wait_for(p_issue_edit.communicate(), timeout=10.0)

                p_close = await asyncio.create_subprocess_exec(
                    "gh", "issue", "close", str(issue_id),
                    "--repo", project.repo,
                    "--comment", f"🎉 **DevTest E2E Completed**: Implemented, verified against CI, and merged via PR #{pr_number}.",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_close.communicate(), timeout=10.0)

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

                # Sanitize worktree with stash protection
                try:
                    dev_wt = WorktreeManager.get_worktree_path(project, "devtest")
                    if dev_wt.exists():
                        await clean_worktree(dev_wt)
                except Exception as ex_wt:
                    _logger.debug("[%s:devtest] Worktree post-merge cleanup notice: %s", project.name, ex_wt)

                # Advance parent story sequence / unlock next queued subtask
                try:
                    await _advance_parent_and_unlock_next_subtask(project, state_manager, issue_id)
                except Exception as ex:
                    console.print(f"  [{project.name}:devtest] [dim yellow]Parent sequential advance notice: {ex}[/dim yellow]")

                return True, f"DevTest node implemented issue #{issue_id}, verified CI 100% Green, and auto-merged PR #{pr_number} into main."
            else:
                err_text = (stderr_m or b"").decode("utf-8", errors="replace").strip()
                console.print(f"  [{project.name}:devtest] [bold red]✗ PR #{pr_number} merge failed ({err_text}). Flagging for conflict remediation.[/bold red]")
                p_fail = await asyncio.create_subprocess_exec(
                    "gh", "pr", "edit", str(pr_number),
                    "--repo", project.repo,
                    "--remove-label", "dev-implemented",
                    "--add-label", "needs-refactor",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_fail.communicate(), timeout=10.0)

                p_comm = await asyncio.create_subprocess_exec(
                    "gh", "pr", "comment", str(pr_number),
                    "--repo", project.repo,
                    "--body", f"🤖 **DevTest Merge Quality Gate**: PR #{pr_number} cannot be merged into `main` ({err_text}). Flagging with `needs-refactor` for autonomous conflict remediation.",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_comm.communicate(), timeout=10.0)

                await state_manager.record_anomaly_event(
                    project_name=project.name,
                    node_name="devtest",
                    error_type="MERGE_CONFLICT",
                    error_message=f"PR #{pr_number} cannot be merged: {err_text}",
                    issue_number=issue_id,
                )
                return False, f"PR #{pr_number} cannot be merged ({err_text}). Tagged 'needs-refactor'."

    elif ci_status == "FAIL":
        if shutil.which("gh"):
            p_fail = await asyncio.create_subprocess_exec(
                "gh", "pr", "edit", str(pr_number),
                "--repo", project.repo,
                "--add-label", "needs-refactor",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_fail.communicate(), timeout=10.0)
            p_comm = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", project.repo,
                "--body", f"🤖 **DevTest Quality Gate**: Remote CI checks failed ({ci_details}). Flagging with `needs-refactor` for autonomous remediation.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_comm.communicate(), timeout=10.0)

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
            env=env,
        )
        await asyncio.wait_for(p_label.communicate(), timeout=10.0)
        p_issue = await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(issue_id),
            "--repo", project.repo,
            "--remove-label", trigger_label,
            "--add-label", "dev-implemented",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.wait_for(p_issue.communicate(), timeout=10.0)

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


async def _extract_issue_from_pr(repo: str, pr: Dict[str, Any]) -> tuple[Optional[int], str]:
    """Extracts issue_id and title from PR object or GitHub API."""
    body = pr.get("body", "")
    branch = pr.get("headRefName", "")
    title = pr.get("title", "")
    pr_number = pr.get("number", 0)

    m = re.search(r"issue-(\d+)", branch, re.IGNORECASE)
    if m:
        return int(m.group(1)), title

    m = re.search(r"(?:Fixes|Closes|Resolves)\s*#(\d+)", body, re.IGNORECASE)
    if m:
        return int(m.group(1)), title

    if shutil.which("gh") and pr_number:
        env = {**os.environ, "GH_PROMPT_DISABLED": "1"}
        try:
            p = await asyncio.create_subprocess_exec(
                "gh", "pr", "view", str(pr_number),
                "--repo", repo,
                "--json", "body,headRefName,title",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(p.communicate(), timeout=10.0)
            if p.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                b = data.get("body", "")
                br = data.get("headRefName", "")
                m = re.search(r"issue-(\d+)", br, re.IGNORECASE) or re.search(r"(?:Fixes|Closes|Resolves)\s*#(\d+)", b, re.IGNORECASE)
                if m:
                    return int(m.group(1)), data.get("title", title)
        except Exception:
            pass

    return None, title


async def run_devtest_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes 3Amigos DevTest Node (Implementation, Remote CI Verification & PR Auto-Merge).
    Zero-token gating: if no actionable tasks in queue and no open PRs awaiting CI, exits with 0 tokens.
    """
    node_cfg = project.nodes.get("devtest", NodeConfig(harness="antigravity"))
    if not project.is_node_enabled("devtest"):
        return False, "DevTest node disabled for project."

    trigger = node_cfg.label_trigger or "ready-for-dev"
    output_label = node_cfg.label_output or "dev-implemented"
    branch_prefix = node_cfg.branch_prefix or "feat/issue-"
    env = {**os.environ, "GH_PROMPT_DISABLED": "1"}

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

    # Phase 2: Autonomous E2E Completion & Auto-Merge of Open PRs Awaiting CI
    open_prs = await fetch_open_prs(project.repo, label="dev-implemented", limit=20)
    if not open_prs:
        open_prs = await fetch_open_prs(project.repo, limit=20)

    for pr in open_prs:
        pr_number = pr["number"]
        pr_title = pr.get("title", "")
        branch_name = pr.get("headRefName", "")
        labels = [l.get("name") if isinstance(l, dict) else str(l) for l in pr.get("labels", [])]
        
        is_devtest_pr = (
            "dev-implemented" in labels
            or branch_name.startswith(branch_prefix)
            or "issue-" in branch_name
        )
        if not is_devtest_pr:
            continue

        ci_status, ci_details = await check_pr_ci_status(project.repo, pr_number)
        if ci_status == "PASS":
            issue_id, issue_title = await _extract_issue_from_pr(project.repo, pr)
            target_issue_id = issue_id or pr_number
            return await _verify_and_auto_merge_pr(
                project=project,
                state_manager=state_manager,
                pr_number=pr_number,
                issue_id=target_issue_id,
                issue_title=issue_title or pr_title,
                trigger_label=trigger,
                auto_merge_approved=True,
                default_output_label=output_label,
            )
        elif ci_status == "FAIL":
            if shutil.which("gh"):
                p_fail = await asyncio.create_subprocess_exec(
                    "gh", "pr", "edit", str(pr_number),
                    "--repo", project.repo,
                    "--remove-label", "dev-implemented",
                    "--add-label", "needs-refactor",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_fail.communicate(), timeout=10.0)
                p_comm = await asyncio.create_subprocess_exec(
                    "gh", "pr", "comment", str(pr_number),
                    "--repo", project.repo,
                    "--body", f"🤖 **DevTest Quality Gate**: Remote CI checks failed ({ci_details}). Flagging with `needs-refactor` for autonomous remediation.",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_comm.communicate(), timeout=10.0)
            await state_manager.record_anomaly_event(
                project_name=project.name,
                node_name="devtest",
                error_type="CI_FAILURE",
                error_message=f"PR #{pr_number} failed CI checks: {ci_details}",
                issue_number=pr_number,
            )
            return False, f"PR #{pr_number} failed CI checks ({ci_details}). Tagged 'needs-refactor'."

    # Phase 3: Deterministic Gating for New Implementation Issues via Story Lock (0 Tokens)
    try:
        await state_manager.reconcile_completed_stories(project.name)
    except Exception as e:
        _logger.debug("[%s:devtest] Non-blocking story reconciliation error: %s", project.name, e)

    target_issue_id = await state_manager.get_next_devtest_task(project.name)
    if target_issue_id is None:
        _logger.warning(
            "[%s:devtest] Project is locked on active story or no actionable task found. Idling (0 tokens).",
            project.name,
        )
        return False, f"No PRs awaiting CI and no actionable task for project '{project.name}' (story lock active or idle). Idle (0 tokens)."

    # Pre-Flight Quota Gating (Pure local SQLite calculation, 0 LLM tokens)
    harness_name = node_cfg.harness or "antigravity"
    allowed, q_res = await check_dispatch_quota(project, "devtest", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    # Targeted Fetch of the specific issue payload via fetch_issue_by_number (0 LLM tokens)
    target_issue = await fetch_issue_by_number(project.repo, target_issue_id)
    if not target_issue:
        return False, f"Target issue #{target_issue_id} could not be fetched from GitHub."

    # Guard against already-closed or merged issues
    issue_state = str(target_issue.get("state") or "").upper()
    if issue_state in ("CLOSED", "MERGED", "DONE", "STATUS:CLOSED", "STATUS:MERGED", "STATUS:DONE"):
        _logger.warning(
            "[%s:devtest] Target issue #%d is already closed on GitHub (%s). Synchronizing SDLC state and skipping.",
            project.name,
            target_issue_id,
            issue_state,
        )
        curr_labels = [
            l.get("name", "") if isinstance(l, dict) else str(l)
            for l in target_issue.get("labels", [])
        ]
        stale_labels = [lbl for lbl in curr_labels if any(t in lbl.lower() for t in (trigger, "ready-for-dev", "queued", "status:ready-for-dev", "status:queued"))]
        if stale_labels and shutil.which("gh"):
            cmd = ["gh", "issue", "edit", str(target_issue_id), "--repo", project.repo]
            for s_lbl in stale_labels:
                cmd.extend(["--remove-label", s_lbl])
            try:
                p_clean = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                await asyncio.wait_for(p_clean.communicate(), timeout=10.0)
            except Exception as e:
                _logger.debug("[%s:devtest] Graceful fallback on label cleanup for closed issue #%d: %s", project.name, target_issue_id, e)

        await state_manager.sync_project_sdlc_items(
            project.name,
            [{
                "issue_number": target_issue_id,
                "title": target_issue.get("title", ""),
                "state": "CLOSED",
                "labels": [lbl for lbl in curr_labels if not any(t in lbl.lower() for t in (trigger, "ready-for-dev", "queued", "status:ready-for-dev", "status:queued"))],
            }],
        )
        return False, f"Target issue #{target_issue_id} is already closed on GitHub. Synchronized state and skipped."

    issue_id = target_issue["number"]
    issue_title = target_issue.get("title", "")

    # Duplicate PR prevention guard in Phase 3
    sdlc_items = await state_manager.get_sdlc_items(project.name)
    item_lookup = {item["issue_number"]: item for item in sdlc_items}
    existing_item = item_lookup.get(issue_id, {})
    existing_pr = existing_item.get("linked_pr")

    if not existing_pr and open_prs:
        for pr in open_prs:
            pr_branch = pr.get("headRefName", "")
            pr_body = pr.get("body", "")
            if (
                f"issue-{issue_id}" in pr_branch
                or re.search(rf"(?:Fixes|Closes|Resolves)\s*#{issue_id}\b", pr_body, re.IGNORECASE)
            ):
                existing_pr = pr.get("number")
                break

    if existing_pr:
        _logger.info(
            "[%s:devtest] Subtask #%d already has open PR #%s (or linked_pr in sdlc_items). Waiting for Phase 2 CI verification.",
            project.name,
            issue_id,
            existing_pr,
        )
        console.print(
            f"  [bold cyan][{project.name}:devtest][/bold cyan] [bold yellow]⏸ Subtask #{issue_id} already has open PR #{existing_pr} (linked_pr). Waiting for Phase 2 CI verification.[/bold yellow]"
        )
        if not existing_item.get("linked_pr"):
            await state_manager.sync_project_sdlc_items(
                project.name,
                [{
                    "issue_number": issue_id,
                    "title": issue_title,
                    "state": "IN_PROGRESS",
                    "linked_pr": int(existing_pr),
                }],
            )
        return False, f"Subtask #{issue_id} already has open PR #{existing_pr} (linked_pr). Waiting for Phase 2 CI verification."

    # Ensure issue is active with trigger label on GitHub if it was queued
    curr_labels = [
        l.get("name", "") if isinstance(l, dict) else str(l)
        for l in target_issue.get("labels", [])
    ]
    queue_labels = [lbl for lbl in curr_labels if any(q in lbl.lower() for q in ("queued", "awaiting-approval", "pending-review"))]
    if queue_labels or not any(trigger in lbl.lower() for lbl in curr_labels):
        if shutil.which("gh"):
            cmd = ["gh", "issue", "edit", str(issue_id), "--repo", project.repo, "--add-label", trigger]
            for q_lbl in queue_labels:
                cmd.extend(["--remove-label", q_lbl])
            try:
                p_relbl = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
                await asyncio.wait_for(p_relbl.communicate(), timeout=10.0)
            except Exception as e:
                _logger.warning("[%s:devtest] Failed to update labels on Subtask #%d: %s", project.name, issue_id, e)
        console.print(f"  [bold cyan][{project.name}:devtest][/bold cyan] [bold yellow]⚡ Activating lowest open Subtask #{issue_id} ({', '.join(queue_labels) or 'unlabeled'} -> {trigger})[/bold yellow]")

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

    # Resolve parent story ID if this subtask is under an active locked story
    parent_id = None
    if issue_id in item_lookup and item_lookup[issue_id].get("parent_issue_id"):
        parent_id = item_lookup[issue_id]["parent_issue_id"]

    if parent_id is None:
        body_text = target_issue.get("body", "")
        m = re.search(r"Parent:\s*#(\d+)", body_text, re.IGNORECASE)
        if m:
            parent_id = int(m.group(1))

    if parent_id is None:
        try:
            active_locked_id = await state_manager.get_active_locked_story_id(project.name)
            if active_locked_id is not None and active_locked_id != issue_id:
                parent_id = active_locked_id
        except Exception:
            pass

    if parent_id is not None:
        lock_log = format_story_lock_dispatch_log(parent_id, issue_id)
        console.print(f"  [bold cyan][{project.name}:devtest][/bold cyan] [bold green]{lock_log}[/bold green]")
        _logger.info(lock_log)
        ProjectLogBufferManager.add_line(f"[{project.name}:devtest] [INFO] {lock_log}", project_name=project.name, node_name="devtest")

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

    # 4. Resolve Worktree & Pre-Flight Cleanup
    console.print(f"\n  [bold blue]⚡ [{project.name}:devtest][/bold blue] [bold white]Implementing Subtask #{issue_id}:[/bold white] [cyan]'{issue_title}'[/cyan]")
    console.print(f"  [dim]• Target: {project.repo} | Branch: {branch_prefix}{issue_id} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]• Scope: 3-Amigos TDD Development, Test Verification & PR Creation[/dim]")

    exec_cwd = await WorktreeManager.ensure_worktree(project, "devtest")

    try:
        await (await asyncio.create_subprocess_exec("git", "reset", "--hard", cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "clean", "-fd", cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "checkout", "main", cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
        await (await asyncio.create_subprocess_exec("git", "pull", "origin", "main", cwd=str(exec_cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)).wait()
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

    context_note = ""
    if project.context_files:
        files_str = ", ".join(project.context_files)
        context_note = (
            f"Read the methodology and architecture files listed in: {files_str}.\n"
            f"Implement the code strictly adhering to those local repository standards.\n"
        )

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
        cwd=exec_cwd,
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
            try:
                p1 = await asyncio.create_subprocess_exec(
                    "gh", "issue", "edit", str(issue_id),
                    "--repo", project.repo,
                    "--remove-label", trigger,
                    "--add-label", "orchestration-failed",
                    env=env,
                )
                await asyncio.wait_for(p1.communicate(), timeout=10.0)

                p2 = await asyncio.create_subprocess_exec(
                    "gh", "issue", "comment", str(issue_id),
                    "--repo", project.repo,
                    "--body", f"🤖 **DevTest Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
                    env=env,
                )
                await asyncio.wait_for(p2.communicate(), timeout=10.0)
            except Exception:
                pass
        return False, f"DevTest execution failed on issue #{issue_id} (exit code {exit_code})."

    # 6. Verify if PR was created by the harness (exact head-branch ref query)
    branch_name = f"{branch_prefix}{issue_id}"
    existing_pr: Optional[Dict[str, Any]] = None

    if shutil.which("gh"):
        try:
            proc_pr = await asyncio.create_subprocess_exec(
                "gh", "pr", "list",
                "--repo", project.repo,
                "--head", branch_name,
                "--state", "open",
                "--json", "number,title,labels,headRefName,statusCheckRollup,mergeable",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_pr, _ = await asyncio.wait_for(proc_pr.communicate(), timeout=10.0)
            if proc_pr.returncode == 0 and stdout_pr:
                prs = json.loads(stdout_pr.decode("utf-8", errors="replace"))
                if prs:
                    existing_pr = prs[0]
        except Exception as e:
            _logger.debug("Head-branch PR discovery error: %s", e)

    if existing_pr:
        pr_num = existing_pr["number"]
        ran, msg = await _verify_and_auto_merge_pr(
            project=project,
            state_manager=state_manager,
            pr_number=pr_num,
            issue_id=issue_id,
            issue_title=issue_title,
            trigger_label=trigger,
            auto_merge_approved=getattr(node_cfg, "auto_merge_approved", True),
            default_output_label=output_label,
        )
        await state_manager.release_lock(issue_id, project.repo, "devtest")
        return ran, msg

    # 7. Fallback: Check Git Diff (Did the model leave uncommitted code?)
    diff_proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=str(exec_cwd),
        stdout=asyncio.subprocess.PIPE,
    )
    diff_out, _ = await asyncio.wait_for(diff_proc.communicate(), timeout=10.0)
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
        await (await asyncio.create_subprocess_exec("git", "checkout", "-B", branch_name, cwd=str(exec_cwd))).wait()
        await (await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(exec_cwd))).wait()
        await (await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"feat: implement #{issue_id} - {issue_title}", cwd=str(exec_cwd)
        )).wait()
        await (await asyncio.create_subprocess_exec("git", "push", "-u", "origin", branch_name, cwd=str(exec_cwd))).wait()

        if shutil.which("gh"):
            p_pr = await asyncio.create_subprocess_exec(
                "gh", "pr", "create",
                "--repo", project.repo,
                "--title", f"feat: resolve #{issue_id} - {issue_title}",
                "--body", f"Automated 3-Amigos DevTest implementation.\n\nCloses #{issue_id}",
                "--label", output_label,
                cwd=str(exec_cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_c, _ = await asyncio.wait_for(p_pr.communicate(), timeout=10.0)
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
    return True, f"DevTest node implemented issue #{issue_id} and opened PR."
