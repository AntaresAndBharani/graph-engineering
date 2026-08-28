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
    Executes Architect Node (Triage, Classification & Decomposition).
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
        f"You are the Principal Architect operating autonomously in non-interactive batch mode.\n"
        f"Perform Triage, Classification, and Architectural Decomposition for GitHub Issue #{issue_id} ('{issue_title}'). {context_note}\n\n"
        f"CRITICAL OPERATIONAL RULES:\n"
        f"1. You are fully autonomous. Do NOT ask questions in chat or wait for human confirmation. Perform all required actions immediately using GitHub CLI (`gh`).\n"
        f"2. CLASSIFY AND ROUTE THE ISSUE ACCORDING TO ITS NATURE:\n"
        f"   - **Case 1: ALREADY IMPLEMENTED ON MAIN**: If this issue's acceptance criteria are already satisfied, close it using:\n"
        f"     `gh issue close {issue_id} --repo '{project.repo}' --comment 'Closed: Already implemented on main.'`\n"
        f"   - **Case 2: STANDALONE BUG / DIRECT SUBTASK** (Small, self-contained, does not need subtask breakdown): Route directly to development by labeling it '{output_label}' and removing '{trigger}':\n"
        f"     `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label '{output_label}'`\n"
        f"     `gh issue comment {issue_id} --repo '{project.repo}' --body '🤖 **Architect Triage**: Classified as a standalone technical task. Labeled {output_label} for DevTest implementation.'`\n"
        f"   - **Case 3: TECH DEBT OR NON-BLOCKING REFACTOR**: Route to daily BAU maintenance by labeling it 'tech-debt' and removing '{trigger}':\n"
        f"     `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label 'tech-debt'`\n"
        f"   - **Case 4: MINOR ENHANCEMENT / FEATURE REQUEST**: Route to daily BAU maintenance by labeling it 'enhancement' and removing '{trigger}':\n"
        f"     `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label 'enhancement'`\n"
        f"   - **Case 5: AMBIGUOUS / INSUFFICIENT INFO**: If requirements are unclear or need product decisions, escalate to PO by labeling 'needs-po-review' and removing '{trigger}':\n"
        f"     `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label 'needs-po-review'`\n"
        f"     `gh issue comment {issue_id} --repo '{project.repo}' --body '🤖 **Architect Triage**: Requirements are ambiguous. Flagging for PO review with questions: <clarification questions>'`\n"
        f"   - **Case 6: FULL USER STORY / COMPLEX FEATURE**: Decompose into minimal, testable subtasks following 3-amigos and INVEST principles:\n"
        f"     - If subtasks already exist on GitHub, ensure all open subtasks are labeled '{output_label}'.\n"
        f"     - Otherwise, create new subtask issues: `gh issue create --repo '{project.repo}' --title '<subtask title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{output_label}'`.\n"
        f"     - Update the parent story to '{processed_label}' and remove '{trigger}':\n"
        f"       `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label '{processed_label}'`\n"
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
                "--body", f"🤖 **Architect Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
            )
            await p2.wait()
        return False, f"Architect execution failed on issue #{issue_id} (exit code {exit_code})."

    # 5. Deterministic Post-Execution Verification
    if shutil.which("gh"):
        # Check current state and labels of parent issue
        proc_view = await asyncio.create_subprocess_exec(
            "gh", "issue", "view", str(issue_id),
            "--repo", project.repo,
            "--json", "state,labels",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_view, _ = await proc_view.communicate()
        is_closed = False
        current_labels = []
        if proc_view.returncode == 0 and stdout_view:
            try:
                data = json.loads(stdout_view.decode("utf-8", errors="replace"))
                is_closed = (data.get("state") == "CLOSED")
                current_labels = [l.get("name") for l in data.get("labels", []) if isinstance(l, dict)]
            except Exception:
                pass

        if is_closed:
            await state_manager.release_lock(issue_id, project.repo, "architect")
            return True, f"Architect node verified issue #{issue_id} was already satisfied and closed it."

        # If the architect classified/triaged the issue into another status (e.g. ready-for-dev, tech-debt, enhancement, needs-po-review, architect-processed)
        if trigger not in current_labels:
            await state_manager.release_lock(issue_id, project.repo, "architect")
            labels_str = ", ".join(current_labels) or "no labels"
            return True, f"Architect node classified and transitioned issue #{issue_id} to [{labels_str}]."

        # Check if child subtasks were created or exist on GitHub
        proc_children = await asyncio.create_subprocess_exec(
            "gh", "issue", "list",
            "--repo", project.repo,
            "--search", f"#{issue_id}",
            "--json", "number,title,labels",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_children, _ = await proc_children.communicate()
        children: List[Dict[str, Any]] = []
        if proc_children.returncode == 0 and stdout_children:
            try:
                all_found = json.loads(stdout_children.decode("utf-8", errors="replace"))
                children = [c for c in all_found if c.get("number") != issue_id]
            except Exception:
                pass

        if children:
            # Transition parent story to architect-processed
            p_edit = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(issue_id),
                "--repo", project.repo,
                "--remove-label", trigger,
                "--add-label", processed_label,
            )
            await p_edit.wait()

            await state_manager.release_lock(issue_id, project.repo, "architect")
            return True, f"Architect node triaged and decomposed issue #{issue_id} into {len(children)} subtask(s) ('{output_label}')."

        # If no classification action was taken and no subtasks exist, escalate to needs-po-review
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="architect",
            error_message="Architect node finished without classifying the issue or creating subtasks.",
        )
        p_edit = await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(issue_id),
            "--repo", project.repo,
            "--remove-label", trigger,
            "--add-label", "needs-po-review",
        )
        await p_edit.wait()

        p_comment = await asyncio.create_subprocess_exec(
            "gh", "issue", "comment", str(issue_id),
            "--repo", project.repo,
            "--body", f"🤖 **Architect Escalation**: Architect node evaluated this issue but could not determine classification. Flagging for PO review (`needs-po-review`). See log trace in `{log_file.name}`.",
        )
        await p_comment.wait()
        return False, f"Architect node could not classify issue #{issue_id}. Flagged with 'needs-po-review'."

    await state_manager.release_lock(issue_id, project.repo, "architect")
    return True, f"Architect node completed evaluation on issue #{issue_id}."
