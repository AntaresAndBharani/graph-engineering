from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import shutil

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator.poller import fetch_open_prs, fetch_issues_with_label


async def check_repository_anomalies(
    project: ProjectConfig,
    state_manager: StateManager,
) -> List[Dict[str, Any]]:
    """
    Deterministically scans for methodology and workflow anomalies without LLM tokens.
    Returns a list of anomaly descriptors.
    """
    anomalies: List[Dict[str, Any]] = []

    # 1. Check open PRs with merge conflicts
    prs = await fetch_open_prs(project.repo)
    for pr in prs:
        if pr.get("mergeable") == "CONFLICTING":
            anomalies.append({
                "type": "MERGE_CONFLICT",
                "pr_number": pr.get("number"),
                "title": pr.get("title"),
                "url": pr.get("url"),
                "details": f"PR #{pr.get('number')} has unresolved git merge conflicts.",
            })

    # 2. Check active jobs with FAILED status in state DB
    active_jobs = await state_manager.get_active_jobs()
    for job in active_jobs:
        if job.get("repo") == project.repo and job.get("status") == "FAILED":
            anomalies.append({
                "type": "FAILED_JOB",
                "issue_id": job.get("issue_id"),
                "node_type": job.get("node_type"),
                "details": f"Job for Issue #{job.get('issue_id')} ({job.get('node_type')}) failed: {job.get('error_message')}",
            })

    return anomalies


async def run_supervisor_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
) -> tuple[bool, str]:
    """
    Executes Consistency Supervisor Node.
    Zero-token gating: if no anomalies, returns (False, 'Idle') without LLM calls.
    """
    node_cfg = project.nodes.get("supervisor", NodeConfig(harness="claude"))
    if not node_cfg.enabled:
        return False, "Supervisor node disabled for project."

    # 1. Deterministic Anomaly Filter (0 Tokens)
    anomalies = await check_repository_anomalies(project, state_manager)
    if not anomalies:
        return False, "No workflow anomalies detected. State is consistent (0 tokens)."

    harness_cfg = config.harnesses.get(node_cfg.harness)
    if not harness_cfg:
        return False, f"Harness '{node_cfg.harness}' not found in configuration."

    adapter = AsyncHarnessAdapter(node_cfg.harness, harness_cfg)
    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "supervisor",
    )

    # 2. Handle Simple Self-Heals or Escalate to PO
    healing_actions: List[str] = []

    for anomaly in anomalies:
        if anomaly["type"] == "MERGE_CONFLICT":
            pr_num = anomaly["pr_number"]
            # Apply needs-po-review label and comment
            if shutil.which("gh"):
                cmd_label = ["gh", "pr", "edit", str(pr_num), "--repo", project.repo, "--add-label", "needs-po-review"]
                cmd_comment = [
                    "gh",
                    "pr",
                    "comment",
                    str(pr_num),
                    "--repo",
                    project.repo,
                    "--body",
                    f"🤖 **Supervisor Notification**: PR #{pr_num} has unresolved merge conflicts. Flagging for PO / Developer review (`needs-po-review`).",
                ]
                try:
                    p1 = await asyncio.create_subprocess_exec(*cmd_label)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment)
                    await p2.wait()
                    healing_actions.append(f"Flagged PR #{pr_num} with needs-po-review.")
                except Exception as e:
                    healing_actions.append(f"Failed to flag PR #{pr_num}: {e}")

        elif anomaly["type"] == "FAILED_JOB":
            issue_id = anomaly["issue_id"]
            healing_actions.append(f"Logged failed job for Issue #{issue_id}.")

    return True, f"Supervisor handled {len(anomalies)} anomalies: {'; '.join(healing_actions)}"
