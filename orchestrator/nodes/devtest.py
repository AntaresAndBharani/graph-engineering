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
from orchestrator.poller import fetch_issues_with_label


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


async def run_devtest_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes 3Amigos DevTest Node (Implementation & Verification).
    Zero-token gating: if no issues labeled 'ready-for-dev', exits with 0 tokens consumed.
    """
    node_cfg = project.nodes.get("devtest", NodeConfig(harness="antigravity"))
    if not node_cfg.enabled:
        return False, "DevTest node disabled for project."

    trigger = node_cfg.label_trigger or "ready-for-dev"
    output_label = node_cfg.label_output or "needs-architect-review"
    branch_prefix = node_cfg.branch_prefix or "feat/issue-"

    # 1. Deterministic Gating (0 Tokens)
    issues = await fetch_issues_with_label(project.repo, trigger, limit=1)
    if not issues:
        return False, f"No issues labeled '{trigger}'. Idle (0 tokens)."

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
        effective_output_label = "architect-approved" if is_conflict_resolution else output_label
        pr_labels = [l.get("name") for l in existing_pr.get("labels", []) if isinstance(l, dict)]
        if effective_output_label not in pr_labels and shutil.which("gh"):
            p_pr_label = await asyncio.create_subprocess_exec(
                "gh", "pr", "edit", str(pr_num),
                "--repo", project.repo,
                "--add-label", effective_output_label,
            )
            await p_pr_label.wait()

        # Update Blackboard status
        if is_conflict_resolution:
            await state_manager.upsert_pr_artifact(
                repo=project.repo,
                pr_number=issue_id,
                node_name="devtest",
                status="CONFLICT_RESOLVED",
                comment=f"DevTest node resolved merge conflicts on PR #{pr_num}.",
            )

        # Transition parent issue to dev-implemented
        if shutil.which("gh"):
            p_issue_edit = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
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
                "linked_pr": pr_num,
            }],
        )

        await state_manager.release_lock(issue_id, project.repo, "devtest")
        return True, f"DevTest node implemented issue #{issue_id} and opened/updated PR #{pr_num} ('{effective_output_label}')."

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
        return False, f"DevTest finished with 0 file changes for issue #{issue_id}."

    # 8. Branch, Commit, Push & PR Lifecycle (if uncommitted changes exist)
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
            )
            await p_pr.wait()

            p_edit = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "dev-implemented",
            )
            await p_edit.wait()
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
        return False, f"Git / PR creation failed: {e}"

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
