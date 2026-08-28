from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

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
        remote_url = stdout.decode("utf-8", errors="replace").strip().lower()

        # Check if expected repo is a substring of the remote URL (handles https://github.com/org/repo.git and git@github.com:org/repo.git)
        clean_expected = expected_repo.strip().lower().replace(".git", "")
        clean_remote = remote_url.replace(".git", "")

        if clean_expected not in clean_remote:
            return False, f"Safety check failed: git remote '{remote_url}' does not match expected repo '{expected_repo}'."

        return True, "Safety check passed."
    except Exception as e:
        return False, f"Safety check failed: error reading git remote: {e}"


async def run_devtest_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes 3AmigosDevTest Node (Code & Test Generation, PR Creation).
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

    # 2. Acquire State Lock
    harness_name = node_cfg.harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not found in configuration."

    lock_acquired = await state_manager.acquire_lock(
        issue_id=issue_id,
        repo=project.repo,
        node_type="devtest",
        ttl_minutes=harness_cfg.timeout_minutes,
    )
    if not lock_acquired:
        return False, f"Issue #{issue_id} is currently locked by another active run. Skipping."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "devtest",
        issue_id=issue_id,
    )

    # 3. Destructive Git Safety Check
    is_safe, safety_msg = await verify_git_safety(project.local_path, project.repo)
    if not is_safe:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Git safety check failed: {safety_msg}",
        )
        return False, f"Aborting DevTest for issue #{issue_id}: {safety_msg}"

    # 4. Workspace Sanitization
    try:
        p1 = await asyncio.create_subprocess_exec("git", "reset", "--hard", cwd=str(project.local_path))
        await p1.wait()
        p2 = await asyncio.create_subprocess_exec("git", "clean", "-fd", cwd=str(project.local_path))
        await p2.wait()
        p3 = await asyncio.create_subprocess_exec("git", "checkout", "main", cwd=str(project.local_path))
        await p3.wait()
        p4 = await asyncio.create_subprocess_exec("git", "pull", "origin", "main", cwd=str(project.local_path))
        await p4.wait()
    except Exception as e:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Workspace sanitization failed: {e}",
        )
        return False, f"Workspace sanitization failed: {e}"

    # 5. Execute Agnostic Harness (Local OAuth Session)
    adapter = AsyncHarnessAdapter(harness_name, harness_cfg)
    prompt = (
        f"You are the 3-Amigos Developer & QA Engineer. Implement the technical requirements for Issue #{issue_id} ('{issue_title}').\n"
        f"1. Read the Gherkin acceptance criteria in the issue and context files.\n"
        f"2. Write comprehensive unit and integration tests covering all Given/When/Then scenarios.\n"
        f"3. Implement the minimal clean code required to make all tests pass.\n"
        f"4. Verify that the entire test suite passes before concluding.\n"
    )

    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=project.local_path,
        log_file=log_file,
        model=node_cfg.model,
    )

    if exit_code != 0:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Harness exited with code {exit_code}. See logs: {log_file.name}",
        )
        if shutil.which("gh"):
            await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "orchestration-failed",
            )
            await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_id),
                "--repo", project.repo,
                "--body", f"🤖 **DevTest Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
            )
        return False, f"DevTest execution failed on issue #{issue_id} (exit code {exit_code})."

    # 6. Verify Git Diff (Did the model produce code?)
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
            error_message="Model finished but left 0 git changes.",
        )
        return False, f"DevTest finished with 0 file changes for issue #{issue_id}."

    # 7. Branch, Commit, Push & PR Lifecycle
    branch_name = f"{branch_prefix}{issue_id}"
    try:
        await (await asyncio.create_subprocess_exec("git", "checkout", "-B", branch_name, cwd=str(project.local_path))).wait()
        await (await asyncio.create_subprocess_exec("git", "add", "-A", cwd=str(project.local_path))).wait()
        await (await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"feat: implement #{issue_id} - {issue_title}", cwd=str(project.local_path)
        )).wait()
        await (await asyncio.create_subprocess_exec("git", "push", "-u", "origin", branch_name, cwd=str(project.local_path))).wait()

        if shutil.which("gh"):
            await asyncio.create_subprocess_exec(
                "gh", "pr", "create",
                "--repo", project.repo,
                "--title", f"feat: resolve #{issue_id} - {issue_title}",
                "--body", f"Automated 3-Amigos DevTest implementation.\n\nCloses #{issue_id}",
                "--label", output_label,
                cwd=str(project.local_path),
            )
            await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "dev-implemented",
            )
    except Exception as e:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="devtest",
            error_message=f"Git / PR creation failed: {e}",
        )
        return False, f"Git / PR creation failed: {e}"

    await state_manager.release_lock(issue_id, project.repo, "devtest")
    return True, f"DevTest node implemented issue #{issue_id} and opened PR with label '{output_label}'."
