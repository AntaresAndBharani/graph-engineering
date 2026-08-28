from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import shutil

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator import poller


MANAGED_WORKFLOW_LABELS = {
    "needs-triage",
    "ready-for-dev",
    "needs-architect-review",
    "dev-implemented",
    "architect-processed",
    "needs-po-review",
    "orchestration-failed",
    "tech-debt",
    "enhancement",
}


def parse_iso_timestamp(ts_str: str) -> float:
    """Parses ISO-8601 UTC timestamp string to epoch seconds."""
    try:
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return time.time()


async def check_repository_anomalies(
    project: ProjectConfig,
    state_manager: StateManager,
) -> List[Dict[str, Any]]:
    """
    Deterministically scans for methodology, label status, and SLA anomalies without LLM tokens.
    Returns a list of anomaly descriptors.
    """
    anomalies: List[Dict[str, Any]] = []
    now = time.time()
    sla_threshold_seconds = 12 * 3600  # 12 hours SLA threshold

    # 1. Check open PRs with merge conflicts
    prs = await poller.fetch_open_prs(project.repo)
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

    # 3. Check All Open Issues (Label Status Audit & 12-Hour SLA)
    open_issues = await poller.fetch_all_open_issues(project.repo, limit=100)
    for issue in open_issues:
        issue_num = issue.get("number")
        issue_title = issue.get("title", "")
        labels_list = [l.get("name") for l in issue.get("labels", []) if isinstance(l, dict)]
        labels_set = set(labels_list)

        is_maintenance = bool(labels_set.intersection({"tech-debt", "enhancement"}))
        has_managed_label = bool(labels_set.intersection(MANAGED_WORKFLOW_LABELS))

        # 3a. Issue Status Validation: Unclassified / Missing Managed Label
        if not has_managed_label:
            anomalies.append({
                "type": "UNCLASSIFIED_ISSUE",
                "issue_id": issue_num,
                "title": issue_title,
                "details": f"Issue #{issue_num} has no managed workflow label. Needs triage.",
            })

        # 3b. 12-Hour Stale Issue SLA Check (Excluding tech-debt & enhancement)
        if not is_maintenance and "needs-po-review" not in labels_set:
            created_at_str = issue.get("createdAt", "")
            if created_at_str:
                created_ts = parse_iso_timestamp(created_at_str)
                age_seconds = now - created_ts
                if age_seconds > sla_threshold_seconds:
                    age_hours = age_seconds / 3600.0
                    anomalies.append({
                        "type": "STALE_ISSUE_SLA",
                        "issue_id": issue_num,
                        "title": issue_title,
                        "age_hours": age_hours,
                        "details": f"Issue #{issue_num} has been open for {age_hours:.1f}h (> 12h SLA). Escalating to PO review.",
                    })

    return anomalies


async def run_supervisor_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Executes Consistency Supervisor Node.
    Zero-token gating: if no anomalies, returns (False, 'Idle') without LLM calls.
    """
    node_cfg = project.nodes.get("supervisor", NodeConfig(harness="claude"))
    if not node_cfg.enabled:
        return False, "Supervisor node disabled for project."

    # 1. Schedule Gating (Default: 3600s / 1 hour)
    if not force:
        last_run = await state_manager.get_last_run("supervisor", project.repo)
        if last_run is not None:
            elapsed = time.time() - last_run
            interval = getattr(config.settings, "supervisor_interval_seconds", 3600)
            if elapsed < interval:
                return False, f"Supervisor check not due ({int((interval - elapsed) / 60)}m remaining). Idle (0 tokens)."

    # 2. Deterministic Anomaly Filter (0 Tokens)
    anomalies = await check_repository_anomalies(project, state_manager)
    await state_manager.record_node_run("supervisor", project.repo)
    if not anomalies:
        return False, "No workflow anomalies detected. State is consistent (0 tokens)."

    # 2. Handle Simple Self-Heals or Escalate to PO
    healing_actions: List[str] = []

    for anomaly in anomalies:
        anomaly_type = anomaly["type"]

        if anomaly_type == "MERGE_CONFLICT":
            pr_num = anomaly["pr_number"]
            if shutil.which("gh"):
                cmd_label = ["gh", "pr", "edit", str(pr_num), "--repo", project.repo, "--add-label", "needs-po-review"]
                cmd_comment = [
                    "gh", "pr", "comment", str(pr_num),
                    "--repo", project.repo,
                    "--body", f"🤖 **Supervisor Notification**: PR #{pr_num} has unresolved merge conflicts. Flagging for PO / Developer review (`needs-po-review`).",
                ]
                try:
                    p1 = await asyncio.create_subprocess_exec(*cmd_label)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment)
                    await p2.wait()
                    healing_actions.append(f"Flagged PR #{pr_num} with needs-po-review.")
                except Exception as e:
                    healing_actions.append(f"Failed to flag PR #{pr_num}: {e}")

        elif anomaly_type == "FAILED_JOB":
            issue_id = anomaly["issue_id"]
            healing_actions.append(f"Logged failed job for Issue #{issue_id}.")

        elif anomaly_type == "UNCLASSIFIED_ISSUE":
            issue_id = anomaly["issue_id"]
            if shutil.which("gh"):
                cmd_label = ["gh", "issue", "edit", str(issue_id), "--repo", project.repo, "--add-label", "needs-triage"]
                cmd_comment = [
                    "gh", "issue", "comment", str(issue_id),
                    "--repo", project.repo,
                    "--body", "🤖 **Supervisor Status Audit**: Issue was missing a managed workflow label. Automatically assigned `needs-triage` for Architect review.",
                ]
                try:
                    p1 = await asyncio.create_subprocess_exec(*cmd_label)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment)
                    await p2.wait()
                    healing_actions.append(f"Labeled Issue #{issue_id} with needs-triage.")
                except Exception as e:
                    healing_actions.append(f"Failed to label Issue #{issue_id}: {e}")

        elif anomaly_type == "STALE_ISSUE_SLA":
            issue_id = anomaly["issue_id"]
            age_hours = anomaly.get("age_hours", 12.0)
            if shutil.which("gh"):
                cmd_label = ["gh", "issue", "edit", str(issue_id), "--repo", project.repo, "--add-label", "needs-po-review"]
                cmd_comment = [
                    "gh", "issue", "comment", str(issue_id),
                    "--repo", project.repo,
                    "--body", f"🤖 **Supervisor SLA Alert**: Issue #{issue_id} has been open for {age_hours:.1f} hours (> 12 hours threshold). Flagged with `needs-po-review` for priority escalation.",
                ]
                try:
                    p1 = await asyncio.create_subprocess_exec(*cmd_label)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment)
                    await p2.wait()
                    healing_actions.append(f"Escalated stale Issue #{issue_id} ({age_hours:.1f}h) with needs-po-review.")
                except Exception as e:
                    healing_actions.append(f"Failed to escalate Issue #{issue_id}: {e}")

    return True, f"Supervisor handled {len(anomalies)} anomalies: {'; '.join(healing_actions)}"
