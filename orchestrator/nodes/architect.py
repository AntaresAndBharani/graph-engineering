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


async def run_architect_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes Architect Node (Triage & Decomposition).
    Zero-token gating: if no issues labeled 'needs-triage', exits with 0 tokens consumed.
    """
    node_cfg = project.nodes.get("architect", NodeConfig(harness="claude"))
    if not node_cfg.enabled:
        return False, "Architect node disabled for project."

    trigger = node_cfg.label_trigger or "needs-triage"
    output_label = node_cfg.label_output or "ready-for-dev"
    processed_label = node_cfg.processed_label or "architect-processed"

    # 1. Deterministic Gating (0 Tokens)
    issues = await fetch_issues_with_label(project.repo, trigger, limit=1)
    if not issues:
        return False, f"No issues labeled '{trigger}'. Idle (0 tokens)."

    target_issue = issues[0]
    issue_id = target_issue["number"]
    issue_title = target_issue.get("title", "")

    # 2. Acquire State Lock
    harness_name = node_cfg.harness or "claude"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not found in configuration."

    lock_acquired = await state_manager.acquire_lock(
        issue_id=issue_id,
        repo=project.repo,
        node_type="architect",
        ttl_minutes=harness_cfg.timeout_minutes,
    )
    if not lock_acquired:
        return False, f"Issue #{issue_id} is currently locked by another active run. Skipping."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "architect",
        issue_id=issue_id,
    )

    adapter = AsyncHarnessAdapter(harness_name, harness_cfg)

    # 3. Build Hardened Autonomous Prompt
    context_note = ""
    if project.context_files:
        context_note = f"Read the project context files in your workspace: {', '.join(project.context_files)}."

    prompt = (
        f"You are the Principal Architect operating autonomously in non-interactive batch mode. "
        f"Analyze GitHub Issue #{issue_id} ('{issue_title}'). {context_note}\n\n"
        f"CRITICAL OPERATIONAL RULES:\n"
        f"1. You are fully autonomous. Do NOT ask questions or wait for human confirmation. Perform all required actions immediately.\n"
        f"2. Check if this story's acceptance criteria are ALREADY FULLY IMPLEMENTED on 'main':\n"
        f"   - If ALREADY IMPLEMENTED: Comment on #{issue_id} detailing the existing commit/tests, close the issue using `gh issue close {issue_id} --repo '{project.repo}' --comment 'Closed: Already implemented on main.'`, and conclude.\n"
        f"   - If WORK IS REQUIRED: Break the story down into minimal, testable technical subtasks following 3-amigos and INVEST principles. "
        f"     For each subtask, create a new GitHub issue using `gh issue create --repo '{project.repo}' --title '<subtask title>' --body '<Gherkin acceptance criteria>\n\nParent: #{issue_id}' --label '{output_label}'`.\n"
    )

    # 4. Execute Agnostic Harness (Local OAuth Session)
    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=project.local_path,
        log_file=log_file,
        model=node_cfg.model,
        effort=node_cfg.effort,
    )

    if exit_code != 0:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="architect",
            error_message=f"Harness exited with code {exit_code}. See logs: {log_file.name}",
        )
        if shutil.which("gh"):
            # Mark issue as failed and leave diagnostic comment
            await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "orchestration-failed",
            )
            await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_id),
                "--repo", project.repo,
                "--body", f"🤖 **Architect Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
            )
        return False, f"Architect execution failed on issue #{issue_id} (exit code {exit_code})."

    # 5. Deterministic Post-Execution Verification
    if shutil.which("gh"):
        # Check if parent issue was closed by the architect
        proc_view = await asyncio.create_subprocess_exec(
            "gh", "issue", "view", str(issue_id),
            "--repo", project.repo,
            "--json", "state",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_view, _ = await proc_view.communicate()
        is_closed = False
        if proc_view.returncode == 0 and stdout_view:
            try:
                data = json.loads(stdout_view.decode("utf-8", errors="replace"))
                is_closed = (data.get("state") == "CLOSED")
            except Exception:
                pass

        if is_closed:
            await state_manager.release_lock(issue_id, project.repo, "architect")
            return True, f"Architect node verified issue #{issue_id} was already satisfied and closed it."

        # If issue is still open, check if child issues were created
        proc_children = await asyncio.create_subprocess_exec(
            "gh", "issue", "list",
            "--repo", project.repo,
            "--label", output_label,
            "--search", f"#{issue_id}",
            "--json", "number,title",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_children, _ = await proc_children.communicate()
        children = []
        if proc_children.returncode == 0 and stdout_children:
            try:
                children = json.loads(stdout_children.decode("utf-8", errors="replace"))
            except Exception:
                pass

        if not children:
            # The model exited without closing the issue or creating subtasks
            await state_manager.fail_job(
                issue_id=issue_id,
                repo=project.repo,
                node_type="architect",
                error_message="Architect node finished without creating subtasks or closing the issue.",
            )
            await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", "needs-po-review",
            )
            await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_id),
                "--repo", project.repo,
                "--body", f"🤖 **Architect Escalation**: Architect node evaluated this story but did not create child subtasks (`{output_label}`) or close it. Flagging for PO review (`needs-po-review`). See log trace in `{log_file.name}`.",
            )
            return False, f"Architect node did not produce subtasks for issue #{issue_id}. Flagged with 'needs-po-review'."

        # Subtasks were created: swap trigger label to processed_label
        await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(issue_id),
            "--repo", project.repo,
            "--remove-label", trigger,
            "--add-label", processed_label,
        )

        await state_manager.release_lock(issue_id, project.repo, "architect")
        return True, f"Architect node triaged and decomposed issue #{issue_id} into {len(children)} subtask(s) ('{output_label}')."

    await state_manager.release_lock(issue_id, project.repo, "architect")
    return True, f"Architect node completed evaluation for issue #{issue_id}."
