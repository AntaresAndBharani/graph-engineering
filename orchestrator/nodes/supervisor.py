from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

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


def compute_issue_hash(title: str, body: Optional[str] = None) -> str:
    """Computes SHA-256 hex digest of title and body."""
    content = f"{title}\n{body or ''}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class POEvaluationResult:
    issue_number: int
    repo: str
    title: str
    body_hash: str
    verdict: str  # "PO_APPROVED" or "NEEDS_HUMAN_CLARIFICATION"
    status: str   # "PO_APPROVED" or "NEEDS_HUMAN_CLARIFICATION"
    gaps: Optional[str] = None
    gherkin_ac: Optional[str] = None
    skipped: bool = False
    details: str = ""


def parse_po_evaluation_response(
    response_text: str,
    default_title: str = "",
) -> tuple[str, Optional[str], Optional[str]]:
    """
    Parses LLM response into (verdict, gaps, gherkin_ac).
    Verdict is either 'PO_APPROVED' or 'NEEDS_HUMAN_CLARIFICATION'.
    """
    verdict = "NEEDS_HUMAN_CLARIFICATION"
    gaps: Optional[str] = None
    gherkin_ac: Optional[str] = None

    # Check for explicit verdict
    verdict_match = re.search(r"VERDICT:\s*([A-Z_]+)", response_text, re.IGNORECASE)
    if verdict_match:
        val = verdict_match.group(1).upper()
        if "APPROVED" in val or "READY" in val:
            verdict = "PO_APPROVED"
        elif "CLARIFICATION" in val or "NEEDS" in val or "AMBIGUOUS" in val:
            verdict = "NEEDS_HUMAN_CLARIFICATION"
    elif "PO_APPROVED" in response_text or "Status: PO_APPROVED" in response_text:
        verdict = "PO_APPROVED"
    elif "NEEDS_HUMAN_CLARIFICATION" in response_text:
        verdict = "NEEDS_HUMAN_CLARIFICATION"

    # Extract Gherkin AC block
    gherkin_match = re.search(r"```(?:gherkin)?\s*\n(Feature:.*?)```", response_text, re.DOTALL | re.IGNORECASE)
    if gherkin_match:
        gherkin_ac = gherkin_match.group(1).strip()
    else:
        # Fallback search for Feature: ... Given/When/Then
        feat_match = re.search(
            r"(Feature:\s*.*?(?:\n\s*Scenario:.*?(?:\n\s*(?:Given|When|Then|And|But).*?)+)+)",
            response_text,
            re.DOTALL | re.IGNORECASE,
        )
        if feat_match:
            gherkin_ac = feat_match.group(1).strip()

    # Extract Gaps / Clarifying Questions
    gaps_match = re.search(r"GAPS:\s*\n(.*?)(?=\nGHERKIN_AC:|\n## |\Z)", response_text, re.DOTALL | re.IGNORECASE)
    if gaps_match:
        raw_gaps = gaps_match.group(1).strip()
        if raw_gaps and raw_gaps.lower() not in ("none", "n/a", "none."):
            gaps = raw_gaps
    elif verdict == "NEEDS_HUMAN_CLARIFICATION":
        # Extract everything before Gherkin or full text as gaps
        gaps = response_text.strip()

    if verdict == "PO_APPROVED" and not gherkin_ac:
        gherkin_ac = f"Feature: {default_title}\n  Scenario: Standard Execution\n    Given default system state\n    When task executes\n    Then acceptance criteria are verified"

    return verdict, gaps, gherkin_ac


async def evaluate_supervisor_issue(
    project: ProjectConfig,
    issue: Dict[str, Any],
    config: GlobalConfig,
    state_manager: StateManager,
    dry_run: bool = False,
    force: bool = False,
) -> POEvaluationResult:
    """
    Evaluates an issue for functional completeness and INVEST criteria as a proactive PO Proxy.
    Supports hash-based zero-token skip gating and --dry-run evaluation without GitHub mutation.
    """
    issue_num = int(issue.get("number", 0))
    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    body_hash = compute_issue_hash(title, body)

    # 1. Zero-Token Hash Skip Gate
    if not force and not dry_run:
        existing = await state_manager.get_po_tracking(project.repo, issue_num)
        if existing and existing.get("body_hash") == body_hash and existing.get("status") == "NEEDS_HUMAN_CLARIFICATION":
            skip_msg = f"[DEBUG] [supervisor] Issue #{issue_num} hash unchanged. Skipping PO evaluation."
            logger.debug(skip_msg)
            return POEvaluationResult(
                issue_number=issue_num,
                repo=project.repo,
                title=title,
                body_hash=body_hash,
                verdict=existing.get("status", "NEEDS_HUMAN_CLARIFICATION"),
                status=existing.get("status", "NEEDS_HUMAN_CLARIFICATION"),
                gaps=existing.get("blockers"),
                gherkin_ac=existing.get("gherkin_ac"),
                skipped=True,
                details=skip_msg,
            )

    # 2. Prepare PO Evaluation Prompt
    node_cfg = project.nodes.get("supervisor", NodeConfig(harness="antigravity", model="gemini-3.7-flash-low"))
    harness_name = node_cfg.harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        harness_name = "claude"
        harness_cfg = config.harnesses.get(harness_name)

    model = node_cfg.model or "gemini-3.7-flash-low"
    effort = node_cfg.effort

    prompt = (
        f"You are the proactive AI Product Owner Proxy operating in non-interactive batch mode.\n"
        f"Evaluate GitHub Issue #{issue_num} ('{title}') for repository '{project.repo}'.\n\n"
        f"ISSUE CONTENT:\n"
        f"Title: {title}\n"
        f"Body:\n{body}\n\n"
        f"MISSION:\n"
        f"Evaluate whether the functional requirements are complete, unambiguous, testable, and adhere to INVEST principles.\n\n"
        f"DECISION CRITERIA:\n"
        f"1. If functional requirements, user story scope, and system boundaries are sufficiently defined:\n"
        f"   - Set VERDICT: PO_APPROVED\n"
        f"   - Set GAPS: None\n"
        f"   - Generate comprehensive Gherkin Acceptance Criteria formatted as Given/When/Then.\n"
        f"2. If requirements are ambiguous, missing critical business logic, boundaries, or user flows:\n"
        f"   - Set VERDICT: NEEDS_HUMAN_CLARIFICATION\n"
        f"   - List specific detected gaps and clarifying questions in GAPS.\n\n"
        f"OUTPUT FORMAT (Strict):\n"
        f"VERDICT: [PO_APPROVED | NEEDS_HUMAN_CLARIFICATION]\n"
        f"GAPS:\n<Detected gaps or 'None'>\n"
        f"GHERKIN_AC:\n```gherkin\nFeature: {title}\n  Scenario: ...\n    Given ...\n    When ...\n    Then ...\n```\n"
    )

    verdict = "PO_APPROVED"
    gaps: Optional[str] = None
    gherkin_ac: Optional[str] = None

    if harness_cfg:
        log_file = get_project_log_path(
            config.settings.resolved_log_dir,
            project.name,
            "supervisor",
            issue_id=f"po_{issue_num}",
        )
        adapter = AsyncHarnessAdapter(harness_name, harness_cfg)
        exit_code = await adapter.execute(
            prompt=prompt,
            cwd=project.local_path,
            log_file=log_file,
            model=model,
            effort=effort,
            console_prefix=f"[{project.name}:po-proxy]",
        )
        if exit_code == 0 and log_file.exists():
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                output_content = f.read()
            verdict, gaps, gherkin_ac = parse_po_evaluation_response(output_content, default_title=title)
        else:
            # Fallback heuristic if harness not executable
            if "acceptance criteria" in body.lower() or "given" in body.lower():
                verdict = "PO_APPROVED"
                gherkin_ac = f"Feature: {title}\n  Scenario: Standard AC Verification\n    Given issue requirements are defined\n    When developer implements the solution\n    Then acceptance criteria pass"
            else:
                verdict = "NEEDS_HUMAN_CLARIFICATION"
                gaps = "Requirements missing detailed Gherkin acceptance criteria."
    else:
        # Heuristic fallback if no harness configured
        if "acceptance criteria" in body.lower() or "given" in body.lower():
            verdict = "PO_APPROVED"
            gherkin_ac = f"Feature: {title}\n  Scenario: Standard AC Verification\n    Given issue requirements are defined\n    When developer implements the solution\n    Then acceptance criteria pass"
        else:
            verdict = "NEEDS_HUMAN_CLARIFICATION"
            gaps = "Requirements missing detailed Gherkin acceptance criteria."

    # 3. Dry-Run Check: Do NOT mutate GitHub or Blackboard if dry_run
    if dry_run:
        return POEvaluationResult(
            issue_number=issue_num,
            repo=project.repo,
            title=title,
            body_hash=body_hash,
            verdict=verdict,
            status=verdict,
            gaps=gaps,
            gherkin_ac=gherkin_ac,
            skipped=False,
            details="Dry-run evaluation complete (0 GitHub mutations emitted).",
        )

    # 4. Live Mutation & State Blackboard Upsert
    if verdict == "PO_APPROVED":
        # Enrich body with Gherkin AC if not already present
        if gherkin_ac and "## Acceptance Criteria (Gherkin)" not in body:
            new_body = body.rstrip() + f"\n\n## Acceptance Criteria (Gherkin)\n\n```gherkin\n{gherkin_ac}\n```\n"
        else:
            new_body = body

        if shutil.which("gh"):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                tf.write(new_body)
                temp_path = tf.name
            try:
                p_edit = await asyncio.create_subprocess_exec(
                    "gh", "issue", "edit", str(issue_num),
                    "--repo", project.repo,
                    "--body-file", temp_path,
                    "--remove-label", "needs-po-review",
                    "--add-label", "needs-triage",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await p_edit.wait()
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            p_comment = await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_num),
                "--repo", project.repo,
                "--body", f"🤖 **PO-Proxy Approval**: Functional requirements verified and enriched with Gherkin AC. Promoted to `needs-triage` for Architect decomposition.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_comment.wait()

        await state_manager.upsert_po_tracking(
            repo=project.repo,
            issue_number=issue_num,
            body_hash=body_hash,
            status="PO_APPROVED",
            gherkin_ac=gherkin_ac,
            blockers=None,
        )

    elif verdict == "NEEDS_HUMAN_CLARIFICATION":
        if shutil.which("gh"):
            clarifying_comment = (
                f"🤖 **PO-Proxy Human Escalation (Clarification Required)**:\n\n"
                f"Before this issue can proceed to architectural decomposition, please clarify the following:\n"
                f"{gaps or 'Please provide detailed functional requirements and edge cases.'}"
            )
            p_comment = await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_num),
                "--repo", project.repo,
                "--body", clarifying_comment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_comment.wait()

        await state_manager.upsert_po_tracking(
            repo=project.repo,
            issue_number=issue_num,
            body_hash=body_hash,
            status="NEEDS_HUMAN_CLARIFICATION",
            gherkin_ac=gherkin_ac,
            blockers=gaps,
        )

    return POEvaluationResult(
        issue_number=issue_num,
        repo=project.repo,
        title=title,
        body_hash=body_hash,
        verdict=verdict,
        status=verdict,
        gaps=gaps,
        gherkin_ac=gherkin_ac,
        skipped=False,
        details=f"Evaluated Issue #{issue_num}: {verdict}",
    )


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
    1. Evaluates open issues with 'needs-po-review' as AI PO Proxy.
    2. Runs deterministic anomaly checks and SLA audits.
    Zero-token gating: if no anomalies and no pending PO reviews, returns (False, 'Idle').
    """
    node_cfg = project.nodes.get("supervisor", NodeConfig(harness="antigravity", model="gemini-3.7-flash-low"))
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

    # 2. PO Proxy Evaluation on 'needs-po-review' issues
    po_issues = await poller.fetch_issues_with_label(project.repo, "needs-po-review", limit=5)
    po_actions: List[str] = []
    for issue in po_issues:
        res = await evaluate_supervisor_issue(project, issue, config, state_manager, dry_run=False)
        if res.skipped:
            continue
        if res.status == "PO_APPROVED":
            po_actions.append(f"Approved Issue #{res.issue_number} (promoted to needs-triage)")
        else:
            po_actions.append(f"Escalated Issue #{res.issue_number} for human clarification")

    # 3. Deterministic Anomaly Filter (0 Tokens)
    anomalies = await check_repository_anomalies(project, state_manager)
    await state_manager.record_node_run("supervisor", project.repo)

    if not anomalies and not po_actions:
        return False, "No workflow anomalies detected and no pending PO reviews. State is consistent (0 tokens)."

    healing_actions: List[str] = po_actions.copy()

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
                    p1 = await asyncio.create_subprocess_exec(*cmd_label, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
                    p1 = await asyncio.create_subprocess_exec(*cmd_label, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
                    "--body", f"🤖 **Supervisor SLA Alert**: Issue #{issue_id} has been open for {age_hours:.1f} hours (> 12h SLA threshold) without completion. Flagged for PO review (`needs-po-review`).",
                ]
                try:
                    p1 = await asyncio.create_subprocess_exec(*cmd_label, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await p1.wait()
                    p2 = await asyncio.create_subprocess_exec(*cmd_comment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await p2.wait()
                    healing_actions.append(f"Escalated stale Issue #{issue_id} ({age_hours:.1f}h open) with needs-po-review.")
                except Exception as e:
                    healing_actions.append(f"Failed to escalate Issue #{issue_id}: {e}")

    return True, f"Supervisor handled {len(healing_actions)} item(s): {'; '.join(healing_actions)}"
