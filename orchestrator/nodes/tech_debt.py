from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator.poller import check_dispatch_quota
from orchestrator.nodes.devtest import verify_git_safety
from orchestrator.worktree import (
    WorktreeManager,
    checkout_detached_upstream,
    run_git_command,
)

ACTIVE_SDLC_STATES = {
    "OPEN",
    "PLANNED",
    "ACTIVE",
    "IN_PROGRESS",
    "IN-PROGRESS",
    "QUEUED",
    "REVIEW",
    "UNDER_REVIEW",
    "DEV_IMPLEMENTED",
}

PIPELINE_ACTIVE_LABELS = {
    "architect-approved",
    "needs-architect-review",
    "ready-for-dev",
    "dev-implemented",
    "architect-processed",
    "needs-triage",
    "queued",
    "in-progress",
    "under-review",
}

TECH_DEBT_PROMPT_SYSTEM = """You are the Principal Systems & Software Architect conducting an autonomous Technical Debt and Quality Audit.
Your objective is to thoroughly analyze the provided codebase on branch 'main' for high-impact architectural debt, critical testing gaps, maintainability hazards, and dead or unmaintainable code.

CRITICAL INSTRUCTIONS & NEGATIVE CONSTRAINTS:
1. ONLY surface high-impact, genuinely critical architectural technical debt or test gaps that will tangibly prevent future progress or introduce systemic fragility.
2. DO NOT invent busywork, stylistic quibbles, cosmetic refactorings, or trivial docstring cleanups.
3. If the codebase is clean, well-architected, and free of significant technical debt, YOU MUST OUTPUT EXACTLY:
=== NO TECH DEBT FOUND ===

4. If actionable, high-priority technical debt IS identified, you must output EXACTLY ONE recommendation using the EXACT format below:
=== TECH DEBT RECOMMENDATION ===
Title: [Story]: <Concise, action-oriented story title describing the debt remediation>
Severity: <High | Critical>
Component: <Module or directory path affected>
Description:
<Comprehensive explanation of the architectural debt, why it is a problem, and the concrete risks of leaving it unaddressed>
Acceptance Criteria:
```gherkin
Feature: <Feature title>
  Scenario: <Scenario description>
    Given <precondition>
    When <action>
    Then <expected result>
```
Remediation Plan:
- <Step 1>
- <Step 2>
=== END RECOMMENDATION ===
"""


async def is_project_fully_quiescent(
    project: ProjectConfig,
    state_manager: StateManager,
) -> Tuple[bool, str]:
    """
    Evaluates whether the repository is fully quiescent.
    Quiescence requires:
    1. No active story lock / running job in SQLite blackboard (devtest or architect).
    2. No active SDLC items (state in ACTIVE_SDLC_STATES or pipeline active labels).
    3. No open Pull Requests on GitHub (fail-closed check).
    """
    # 1. Active Story Lock / Running Job Check in active_jobs table
    active_jobs = await state_manager.get_active_jobs()
    now = time.time()
    for job in active_jobs:
        if job.get("repo") == project.repo:
            if job.get("status") == "RUNNING" and job.get("expires_at", 0) > now:
                node_type = job.get("node_type", "active job")
                issue_id = job.get("issue_id", "unknown")
                return False, f"Active story lock is held: Issue #{issue_id} by {node_type}"

    # 2. Open SDLC Stories & Pipeline Active Labels Check
    sdlc_items = await state_manager.get_sdlc_items(project.name)
    for item in sdlc_items:
        state = str(item.get("state", "")).upper().strip()
        if state in ACTIVE_SDLC_STATES:
            return False, f"Active SDLC item found: Issue #{item.get('issue_number', 'unknown')} with state '{state}'"

        raw_labels = item.get("labels", [])
        labels_list: List[str] = []
        if isinstance(raw_labels, list):
            for l in raw_labels:
                if isinstance(l, dict):
                    labels_list.append(str(l.get("name", "")).strip().lower())
                elif isinstance(l, str):
                    labels_list.append(l.strip().lower())
        elif isinstance(raw_labels, str):
            for part in raw_labels.split(","):
                p = part.strip().lower()
                if p:
                    labels_list.append(p)

        for lbl in labels_list:
            if lbl in PIPELINE_ACTIVE_LABELS:
                return False, f"SDLC item #{item.get('issue_number', 'unknown')} holds pipeline active label '{lbl}'"

    # Also check get_active_story fallback
    active_story = await state_manager.get_active_story(project.name)
    if active_story:
        return False, f"Active story lock is held: Issue #{active_story.get('issue_number', 'unknown')}"

    # 3. Fail-Closed GitHub PR Check (CLI `gh pr list --state open`)
    if not shutil.which("gh"):
        return False, "GitHub CLI ('gh') is not installed or not available in PATH (fail-closed)."

    cmd = ["gh", "pr", "list", "--repo", project.repo, "--state", "open", "--json", "number,title", "--limit", "5"]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            err_str = stderr_b.decode("utf-8", errors="replace").strip() if stderr_b else "unknown error"
            return False, f"GitHub CLI failed to query open PRs: {err_str} (fail-closed)"

        stdout_str = stdout_b.decode("utf-8", errors="replace").strip() if stdout_b else ""
        if stdout_str and stdout_str != "[]":
            return False, f"Repository '{project.repo}' has open Pull Requests pending."
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
        return False, "GitHub CLI query for open PRs timed out (fail-closed)."
    except Exception as e:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
        return False, f"GitHub CLI query failed: {e} (fail-closed)"

    return True, ""


async def get_origin_main_sha(cwd: Path | str) -> Optional[str]:
    """
    Retrieves the latest commit SHA for origin/main.
    Fetches origin main first with a 30s timeout.
    """
    await run_git_command(["fetch", "origin", "main"], cwd=cwd, timeout=30.0)
    code, stdout, _ = await run_git_command(["rev-parse", "origin/main"], cwd=cwd, timeout=30.0)
    if code == 0 and stdout.strip():
        return stdout.strip()
    # Fallback to local main if origin/main cannot be resolved
    code_local, stdout_local, _ = await run_git_command(["rev-parse", "main"], cwd=cwd, timeout=30.0)
    if code_local == 0 and stdout_local.strip():
        return stdout_local.strip()
    return None


def parse_tech_debt_recommendation(text: str) -> Optional[Dict[str, str]]:
    """
    Parses structured recommendation from Claude Sonnet output.
    Returns None if '=== NO TECH DEBT FOUND ===' or unparseable.
    """
    if "=== NO TECH DEBT FOUND ===" in text:
        return None

    if "=== TECH DEBT RECOMMENDATION ===\"" not in text and "=== TECH DEBT RECOMMENDATION ===" not in text:
        return None

    pattern = r"=== TECH DEBT RECOMMENDATION ===\s*Title:\s*(?P<title>[^\n]+)\s*Severity:\s*(?P<severity>[^\n]+)\s*Component:\s*(?P<component>[^\n]+)\s*Description:\s*(?P<description>[\s\S]*?)(?:Acceptance Criteria:\s*(?P<ac>```gherkin[\s\S]*?```))?\s*Remediation Plan:\s*(?P<plan>[\s\S]*?)=== END RECOMMENDATION ==="
    match = re.search(pattern, text)
    if match:
        data = match.groupdict()
        return {
            "title": data.get("title", "").strip(),
            "severity": data.get("severity", "").strip(),
            "component": data.get("component", "").strip(),
            "description": data.get("description", "").strip(),
            "acceptance_criteria": (data.get("ac") or "").strip(),
            "remediation_plan": data.get("plan", "").strip(),
        }

    # Fallback relaxed parser
    title_match = re.search(r"Title:\s*([^\n]+)", text)
    if not title_match:
        return None

    title = title_match.group(1).strip()
    sev_match = re.search(r"Severity:\s*([^\n]+)", text)
    severity = sev_match.group(1).strip() if sev_match else "High"
    comp_match = re.search(r"Component:\s*([^\n]+)", text)
    component = comp_match.group(1).strip() if comp_match else "General"

    desc_match = re.search(r"Description:\s*([\s\S]*?)(?:Acceptance Criteria:|Remediation Plan:|=== END RECOMMENDATION ===|$)", text)
    description = desc_match.group(1).strip() if desc_match else ""

    ac_match = re.search(r"(```gherkin[\s\S]*?```)", text)
    ac = ac_match.group(1).strip() if ac_match else ""

    plan_match = re.search(r"Remediation Plan:\s*([\s\S]*?)(?:=== END RECOMMENDATION ===|$)", text)
    plan = plan_match.group(1).strip() if plan_match else ""

    return {
        "title": title,
        "severity": severity,
        "component": component,
        "description": description,
        "acceptance_criteria": ac,
        "remediation_plan": plan,
    }


async def run_tech_debt_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    force: bool = False,
) -> Tuple[bool, str]:
    """
    Executes the Technical Debt Node.
    Runs exclusively on full development quiescence.
    """
    td_cfg = getattr(project, "tech_debt", None)
    node_cfg = project.nodes.get("tech_debt") or NodeConfig(
        enabled=td_cfg.enabled if td_cfg else False,
        harness=td_cfg.harness if td_cfg and td_cfg.harness else "claude",
        model=td_cfg.model if td_cfg else "sonnet",
        effort=td_cfg.effort if td_cfg else "low",
        interval_seconds=td_cfg.interval_seconds if td_cfg else 14400,
    )

    if not project.is_node_enabled("tech_debt"):
        return False, "Technical debt node disabled for project."

    # 1. Strict Quiescence Gate
    is_quiescent, reason = await is_project_fully_quiescent(project, state_manager)
    if not is_quiescent:
        return False, f"Development active ({reason}). Idle (0 tokens)."

    # 2. Upstream Commit SHA Resolution & Decoupled SHA Guard
    current_sha = await get_origin_main_sha(project.local_path)
    if not current_sha:
        return False, f"Unable to determine origin/main commit SHA for '{project.repo}'. Idle (0 tokens)."

    last_audit = await state_manager.get_last_tech_debt_audit(project.name)

    if not force and last_audit:
        last_sha = last_audit.get("commit_sha")
        if last_sha == current_sha:
            return False, f"Commit {current_sha[:7]} already audited for tech-debt. Idle (0 tokens)."

        # 3. Cooldown Interval Throttling
        last_time = last_audit.get("audited_at", 0)
        elapsed = time.time() - last_time
        interval = getattr(node_cfg, "interval_seconds", None)
        if interval is None:
            interval = getattr(config.settings, "tech_debt_interval_seconds", 14400)

        if elapsed < interval:
            rem_m = int((interval - elapsed) / 60)
            return False, f"Tech-debt node in cooldown ({rem_m}m remaining). Idle (0 tokens)."

    # 4. Acquire Lock
    harness_name = node_cfg.harness or "claude"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not configured."

    allowed, q_res = await check_dispatch_quota(project, "tech_debt", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    lock_acquired = await state_manager.acquire_lock(
        issue_id="tech_debt",
        repo=project.repo,
        node_type="tech_debt",
        ttl_minutes=20,
    )
    if not lock_acquired:
        return False, "Technical debt node locked by an ongoing audit. Skipping."

    # 5. Prepare Workspace in Detached HEAD Mode
    worktrees_on = getattr(project, "worktrees_enabled", True)
    audit_cwd = project.local_path
    worktree_used = False

    try:
        if worktrees_on:
            wt_path = await WorktreeManager.ensure_worktree(project, "tech_debt")
            if wt_path and wt_path != project.local_path:
                ok = await checkout_detached_upstream(wt_path, branch="main", timeout=30.0)
                if ok:
                    audit_cwd = wt_path
                    worktree_used = True
                else:
                    await WorktreeManager.remove_worktree(project, "tech_debt")

        if not worktree_used:
            # Primary workspace fallback: verify git safety, fetch read-only
            safe, safe_msg = await verify_git_safety(project.local_path, project.repo)
            if not safe:
                await state_manager.release_lock("tech_debt", project.repo, "tech_debt")
                return False, f"Primary workspace git safety check failed: {safe_msg}. Aborting tech-debt audit."
            await run_git_command(["fetch", "origin", "main"], cwd=project.local_path, timeout=30.0)

        # 6. Execute Claude Sonnet with low effort
        log_file = get_project_log_path(
            config.settings.resolved_log_dir,
            project.name,
            "tech_debt",
        )

        prompt = (
            f"{TECH_DEBT_PROMPT_SYSTEM}\n\n"
            f"Project: {project.name}\n"
            f"Repository: {project.repo}\n"
            f"Auditing Commit: {current_sha}\n\n"
            f"Please review the repository structure, code patterns, and tests at {audit_cwd}."
        )

        adapter = AsyncHarnessAdapter(
            harness_name,
            harness_cfg,
            state_manager=state_manager,
            project_name=project.name,
            node_name="tech_debt",
        )
        model = node_cfg.model or "sonnet"
        effort = node_cfg.effort or "low"

        exit_code = await adapter.execute(
            prompt=prompt,
            cwd=audit_cwd,
            log_file=log_file,
            model=model,
            effort=effort,
            console_prefix=f"[{project.name}:tech_debt]",
        )

        if exit_code != 0:
            await state_manager.record_anomaly_event(
                project_name=project.name,
                node_name="tech_debt",
                error_type="HARNESS_ERROR",
                error_message=f"Tech debt harness exited with code {exit_code}. See {log_file.name}",
            )
            return False, f"Tech debt audit harness execution failed with code {exit_code}."

        # 7. Parse Recommendation & Dispatch Audited GitHub Issue
        content = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        rec = parse_tech_debt_recommendation(content)

        if not rec:
            # Clean codebase zero-issue idempotency
            await state_manager.record_tech_debt_audit(
                project_name=project.name,
                commit_sha=current_sha,
                status="CLEAN",
                issue_number=None,
                details="Codebase audit completed: No actionable technical debt identified.",
            )
            return True, "Codebase audit completed: No actionable technical debt identified."

        # High-impact debt identified: dispatch GitHub issue
        created_issue_num: Optional[int] = None
        issue_title = rec["title"]
        if not issue_title.startswith("[Story]:"):
            issue_title = f"[Story]: {issue_title}"

        body_parts = [
            f"## Technical Debt Summary\n**Severity:** {rec['severity']}\n**Component:** {rec['component']}\n**Audited Commit:** `{current_sha}`\n",
            f"## Description\n{rec['description']}\n",
        ]
        if rec.get("acceptance_criteria"):
            body_parts.append(f"## Acceptance Criteria (Gherkin)\n{rec['acceptance_criteria']}\n")
        if rec.get("remediation_plan"):
            body_parts.append(f"## Remediation Plan\n{rec['remediation_plan']}\n")

        issue_body = "\n".join(body_parts)

        if shutil.which("gh"):
            env = dict(os.environ)
            p_issue = await asyncio.create_subprocess_exec(
                "gh", "issue", "create",
                "--repo", project.repo,
                "--title", issue_title,
                "--body", issue_body,
                "--label", "story,needs-triage",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_issue, _ = await p_issue.communicate()
            if stdout_issue:
                out_txt = stdout_issue.decode("utf-8", errors="replace").strip()
                m = re.search(r"/issues/(\d+)", out_txt)
                if m:
                    created_issue_num = int(m.group(1))

        await state_manager.record_tech_debt_audit(
            project_name=project.name,
            commit_sha=current_sha,
            status="STORY_CREATED",
            issue_number=created_issue_num,
            details=f"Created story issue #{created_issue_num}: {issue_title}" if created_issue_num else issue_title,
        )
        return True, f"Tech debt audit surfaced actionable debt: created issue #{created_issue_num or 'unknown'}."

    finally:
        if worktree_used:
            try:
                await WorktreeManager.remove_worktree(project, "tech_debt")
            except Exception:
                pass
        await state_manager.release_lock("tech_debt", project.repo, "tech_debt")
