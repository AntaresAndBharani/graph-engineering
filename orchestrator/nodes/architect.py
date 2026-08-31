from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator.poller import check_dispatch_quota, fetch_issues_with_label, fetch_open_prs
from orchestrator.worktree import WorktreeManager

console = Console()


async def sync_parent_subtask_links(
    repo: str,
    parent_id: int,
    processed_label: str,
    trigger_label: str,
) -> int:
    """
    Deterministically searches for child subtasks referencing the parent issue,
    ensures the parent issue body contains the '## Subtasks' checklist,
    and posts an audit comment if missing.
    Returns the count of linked children.
    """
    if not shutil.which("gh"):
        return 0

    # 1. Fetch parent issue details
    # 1. Fetch parent issue details (including comments)
    proc_parent = await asyncio.create_subprocess_exec(
        "gh", "issue", "view", str(parent_id),
        "--repo", repo,
        "--json", "body,labels,title,comments",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_p, _ = await proc_parent.communicate()
    if proc_parent.returncode != 0 or not stdout_p:
        return 0

    try:
        parent_data = json.loads(stdout_p.decode("utf-8", errors="replace"))
    except Exception:
        return 0

    parent_body = parent_data.get("body", "")

    # 2. Search for child subtasks referencing Parent: #<parent_id>
    proc_search = await asyncio.create_subprocess_exec(
        "gh", "issue", "list",
        "--repo", repo,
        "--search", f"#{parent_id}",
        "--json", "number,title,state",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_s, _ = await proc_search.communicate()
    results = []
    if proc_search.returncode == 0 and stdout_s:
        try:
            results = json.loads(stdout_s.decode("utf-8", errors="replace"))
        except Exception:
            pass

    found_subtask_ids = {c.get("number") for c in results if c.get("number") != parent_id}

    # Also discover subtask IDs mentioned in parent triage comments
    for comment in parent_data.get("comments", []):
        c_body = comment.get("body", "")
        for m in re.finditer(r"#(\d+)", c_body):
            cid = int(m.group(1))
            if cid != parent_id:
                found_subtask_ids.add(cid)

    children_dict = {c.get("number"): c for c in results if c.get("number") in found_subtask_ids}

    # Fetch details for any subtask IDs found in comments but missed by search
    for sub_id in sorted(found_subtask_ids - set(children_dict.keys())):
        try:
            p_sub = await asyncio.create_subprocess_exec(
                "gh", "issue", "view", str(sub_id),
                "--repo", repo,
                "--json", "number,title,state",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out_s, _ = await p_sub.communicate()
            if p_sub.returncode == 0 and out_s:
                s_data = json.loads(out_s.decode("utf-8", errors="replace"))
                children_dict[sub_id] = s_data
        except Exception:
            pass

    children = list(children_dict.values())
    if not children:
        return 0

    # Sort children by issue number
    children.sort(key=lambda x: x.get("number", 0))

    # Check if all children are already listed in the parent body
    missing_links = [c for c in children if f"#{c['number']}" not in parent_body]
    if missing_links:
        subtasks_md = "\n\n## Subtasks\n" + "\n".join([
            f"- [{'x' if c.get('state') == 'CLOSED' else ' '}] #{c['number']} - {c.get('title', '')}"
            for c in children
        ])

        if "## Subtasks" in parent_body:
            new_body = re.sub(r"## Subtasks.*?(?=\n## |\Z)", subtasks_md.strip(), parent_body, flags=re.DOTALL)
        else:
            new_body = parent_body.rstrip() + subtasks_md

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(new_body)
            temp_path = tf.name

        try:
            p_edit = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(parent_id),
                "--repo", repo,
                "--body-file", temp_path,
                "--remove-label", trigger_label,
                "--add-label", processed_label,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_edit.wait()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        links_list = "\n".join([f"- #{c['number']}: {c.get('title', '')}" for c in children])
        p_comment = await asyncio.create_subprocess_exec(
            "gh", "issue", "comment", str(parent_id),
            "--repo", repo,
            "--body", f"🤖 **Architect Decomposition Complete**: Decomposed into {len(children)} subtask(s):\n{links_list}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_comment.wait()

    return len(children)


async def _sync_architecture_plane(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    node_cfg: NodeConfig,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Pillar 1 & 2: Bootstraps or refreshes .graph/architecture.md.
    Uses specialized research harness (Antigravity gemini-3.7-flash-high) for cost-effective web research.
    Gated by research_interval_seconds (default 7 days / weekly).
    """
    arch_file = project.local_path / ".graph" / "architecture.md"
    arch_missing = not arch_file.exists()

    last_run = await state_manager.get_last_run("architect_research", project.repo)
    now = time.time()
    interval = node_cfg.research_interval_seconds or 604800  # 7 days

    if not force and not arch_missing:
        if last_run is not None and (now - last_run < interval):
            return False, "Living architecture plane up-to-date (weekly SLA active)."
        elif last_run is None:
            await state_manager.record_node_run("architect_research", project.repo)
            return False, "Living architecture plane initialized (weekly SLA active)."

    harness_name = node_cfg.research_harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        harness_name = node_cfg.harness or "claude"
        harness_cfg = config.harnesses.get(harness_name)

    if not harness_cfg:
        return False, f"Research harness '{harness_name}' not found."

    allowed, q_res = await check_dispatch_quota(project, "architect", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    lock_acquired = await state_manager.acquire_lock(
        issue_id="architecture_sync",
        repo=project.repo,
        node_type="architect_research",
        ttl_minutes=harness_cfg.timeout_minutes,
    )
    if not lock_acquired:
        return False, "Architecture synchronization is currently locked."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "architect",
        issue_id="arch_sync",
    )

    model = node_cfg.research_model or "gemini-3.7-flash-high"
    effort = node_cfg.research_effort

    console.print(f"\n  [bold cyan]🏛️ [{project.name}:architect][/bold cyan] [bold white]Living Architecture Plane Synchronization[/bold white]")
    console.print(f"  [dim]• Target: {project.repo} | Scope: Repository Architecture Standards ('.graph/architecture.md')[/dim]")
    console.print(f"  [dim]• Harness: {harness_name} ({model}) | Frequency: Weekly (7-day SLA)[/dim]")

    prompt = (
        f"You are the Principal Systems Architect operating in non-interactive batch mode.\n"
        f"Perform an authoritative Architecture Analysis, Best-Practice Modernization, and Living Documentation Update for repository '{project.repo}'.\n\n"
        f"CRITICAL RULES:\n"
        f"- This task is strictly an architectural inspection and documentation update for '.graph/architecture.md'.\n"
        f"- Do NOT run heavy project test suites (such as gradle test/lint or test scripts) or modify production application source code.\n\n"
        f"OPERATIONAL STEPS:\n"
        f"1. Inspect the codebase structure, build files (e.g. build.gradle.kts, package.json, Cargo.toml, pyproject.toml), design patterns, and package conventions in this workspace.\n"
        f"2. Search the web for current industry best practices, modern framework standards, clean architecture principles, and idiomatic patterns for this specific technology stack.\n"
        f"3. Write or update '.graph/architecture.md' in the workspace.\n"
        f"   The document MUST authoritatively define:\n"
        f"   - ## System Overview & Technology Stack\n"
        f"   - ## Layer Boundaries & Clean Architecture (Domain, Data, Presentation/UI separation of concerns)\n"
        f"   - ## Directory & Package Structure Guidelines\n"
        f"   - ## Design Patterns, State Management & Dependency Injection\n"
        f"   - ## Architectural Constraints & Anti-Patterns (e.g. No circular dependencies, No UI logic in Domain)\n"
        f"4. Commit changes: `git add .graph/architecture.md && git commit -m 'docs(architecture): update architectural standards'`.\n"
    )

    exec_cwd = await WorktreeManager.ensure_worktree(project, "architect")

    adapter = AsyncHarnessAdapter(
        harness_name,
        harness_cfg,
        state_manager=state_manager,
        project_name=project.name,
        node_name="architect",
    )
    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=exec_cwd,
        log_file=log_file,
        model=model,
        effort=effort,
        console_prefix=f"[{project.name}:architect-plane]",
    )

    await state_manager.release_lock("architecture_sync", project.repo, "architect_research")

    if exit_code == 0:
        await state_manager.record_node_run("architect_research", project.repo)
        return True, "Architect synchronized and modernized .graph/architecture.md standards."
    else:
        return False, f"Architecture sync failed (exit code {exit_code}). See {log_file.name}."


async def _review_pr_architecture(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    node_cfg: NodeConfig,
) -> tuple[bool, str]:
    """
    Pillar 3: Architectural Code Review of Implementation PRs.
    Uses primary harness (Claude Sonnet 5) to inspect PR diff against .graph/architecture.md.
    """
    review_trigger = node_cfg.review_trigger or "needs-architect-review"
    prs = await fetch_open_prs(project.repo, label=review_trigger, limit=1)
    if not prs:
        return False, f"No PRs labeled '{review_trigger}'. Idle (0 tokens)."

    target_pr = prs[0]
    pr_number = target_pr["number"]
    pr_title = target_pr.get("title", "")

    # Sync PR into SDLC Blackboard memory
    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": pr_number,
            "title": pr_title,
            "state": "OPEN",
            "labels": [review_trigger],
            "linked_pr": pr_number,
        }],
    )

    harness_name = node_cfg.harness or "claude"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not configured."

    allowed, q_res = await check_dispatch_quota(project, "architect", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    retry_cfg = getattr(harness_cfg, "retry", None)
    max_retries = getattr(retry_cfg, "max_retries", 0) if retry_cfg else 0
    lock_ttl = int(harness_cfg.timeout_minutes * (1 + max_retries) + 5)

    lock_acquired = await state_manager.acquire_lock(
        issue_id=pr_number,
        repo=project.repo,
        node_type="architect_review",
        ttl_minutes=lock_ttl,
    )
    if not lock_acquired:
        return False, f"PR #{pr_number} is locked by active review run."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "architect",
        issue_id=f"pr_{pr_number}",
    )

    console.print(f"\n  [bold magenta]🔍 [{project.name}:architect][/bold magenta] [bold white]Architectural Review on Pull Request #{pr_number}:[/bold white] [cyan]'{pr_title}'[/cyan]")
    console.print(f"  [dim]• Target: {project.repo} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]• Scope: Clean Architecture Contracts & Domain Boundaries against '.graph/architecture.md'[/dim]")

    prompt = (
        f"You are the Principal Architect operating in non-interactive batch mode.\n"
        f"Perform an Architectural Code Review on Pull Request #{pr_number} ('{pr_title}') for repository '{project.repo}'.\n\n"
        f"CRITICAL MISSION:\n"
        f"- You are NOT verifying functional testing or unit test execution (DevTest and CI handle that).\n"
        f"- You are verifying ARCHITECTURAL INTEGRITY against .graph/architecture.md and Clean Architecture standards.\n\n"
        f"OPERATIONAL STEPS:\n"
        f"1. View the PR diff using `gh pr diff {pr_number} --repo '{project.repo}'`.\n"
        f"2. Read .graph/architecture.md in the repository root.\n"
        f"3. Evaluate the diff:\n"
        f"   - Are domain boundaries, layer isolation, and clean separation of concerns respected?\n"
        f"   - Are there architectural anti-patterns, circular dependencies, or inappropriate tight coupling?\n"
        f"   - Does the implementation follow established design patterns and conventions?\n"
        f"4. DECISION & ACTIONS:\n"
        f"   - CASE A: ARCHITECTURALLY SOUND (Approved):\n"
        f"     `gh pr review {pr_number} --repo '{project.repo}' --approve --body '🤖 **Architectural Sign-Off**: The implementation adheres to domain boundaries, clean architecture patterns, and conventions in .graph/architecture.md.'`\n"
        f"     `gh pr edit {pr_number} --repo '{project.repo}' --remove-label '{review_trigger}' --add-label 'architect-approved'`\n"
        f"   - CASE B: ARCHITECTURAL VIOLATIONS (Changes Requested):\n"
        f"     `gh pr review {pr_number} --repo '{project.repo}' --comment --body '🤖 **Architectural Review - Refactoring Required**:\n<Constructive breakdown of architectural issues with guidance>'`\n"
        f"     `gh pr edit {pr_number} --repo '{project.repo}' --remove-label '{review_trigger}' --add-label 'needs-refactor'`\n"
    )

    exec_cwd = await WorktreeManager.ensure_worktree(project, "architect")

    adapter = AsyncHarnessAdapter(
        harness_name,
        harness_cfg,
        state_manager=state_manager,
        project_name=project.name,
        node_name="architect",
        issue_number=pr_number,
    )
    try:
        exit_code = await adapter.execute(
            prompt=prompt,
            cwd=exec_cwd,
            log_file=log_file,
            model=node_cfg.model,
            effort=node_cfg.effort,
            console_prefix=f"[{project.name}:architect-review]",
        )
    finally:
        await state_manager.release_lock(pr_number, project.repo, "architect_review")

    if exit_code == 0:
        return True, f"Architect completed architectural review on PR #{pr_number}."
    else:
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="architect",
            error_type="HARNESS_ERROR",
            error_message=f"Architect review on PR #{pr_number} failed (exit code {exit_code}).",
            issue_number=pr_number,
        )
        return False, f"Architect review on PR #{pr_number} failed (exit code {exit_code})."


def build_triage_prompt(
    project: ProjectConfig,
    issue_id: int,
    issue_title: str,
    trigger: str,
    output_label: str,
    processed_label: str,
    po_record: Optional[Dict[str, Any]] = None,
    has_active_story: bool = False,
    queued_label: str = "queued",
) -> str:
    """
    Constructs the triage and decomposition prompt for the Architect harness.
    Incorporates pre-approved Gherkin Acceptance Criteria from the Blackboard when available.
    Adjusts subtask labeling and parent story state based on active-story concurrency.
    """
    context_note = ""
    if project.context_files:
        context_note = f"Read the project context files in your workspace: {', '.join(project.context_files)}."

    po_ac_context = ""
    target_parent_label = "planned" if has_active_story else processed_label

    if has_active_story:
        decomposition_instruction = (
            f"     - An active story is currently running in this project. Create all Subtasks 1..N (Queued): `gh issue create --repo '{project.repo}' --title '<subtask N title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{queued_label}'`.\n"
        )
    else:
        decomposition_instruction = (
            f"     - Create Subtask 1 (Active): `gh issue create --repo '{project.repo}' --title '<subtask 1 title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{output_label}'`.\n"
            f"     - Create Subtasks 2..N (Queued): `gh issue create --repo '{project.repo}' --title '<subtask N title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{queued_label}'`.\n"
        )

    if po_record and po_record.get("status") == "PO_APPROVED" and po_record.get("gherkin_ac"):
        gherkin_ac = str(po_record["gherkin_ac"]).strip()
        po_ac_context = (
            f"\nPRE-APPROVED ACCEPTANCE CRITERIA (from PO Blackboard):\n"
            f"The Product Owner proxy has already evaluated and approved the following Gherkin Acceptance Criteria for this issue:\n"
            f"```gherkin\n{gherkin_ac}\n```\n"
            f"CRITICAL: Do NOT re-derive acceptance criteria from scratch. Incorporate and decompose directly from these pre-approved Gherkin criteria into subtasks.\n"
        )
        if has_active_story:
            decomposition_instruction = (
                f"     - An active story is currently running in this project. Create all Subtasks 1..N (Queued) using the pre-approved Gherkin acceptance criteria above: `gh issue create --repo '{project.repo}' --title '<subtask N title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{queued_label}'`.\n"
            )
        else:
            decomposition_instruction = (
                f"     - Create Subtask 1 (Active) using the pre-approved Gherkin acceptance criteria above: `gh issue create --repo '{project.repo}' --title '<subtask 1 title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{output_label}'`.\n"
                f"     - Create Subtasks 2..N (Queued) using the pre-approved Gherkin acceptance criteria above: `gh issue create --repo '{project.repo}' --title '<subtask N title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{queued_label}'`.\n"
            )

    prompt = (
        f"You are the Principal Architect operating autonomously in non-interactive batch mode.\n"
        f"Perform Triage, Classification, and Architectural Decomposition for GitHub Issue #{issue_id} ('{issue_title}'). {context_note}\n"
        f"{po_ac_context}\n"
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
        f"{decomposition_instruction}"
        f"     - Update the parent story to '{target_parent_label}' and remove '{trigger}':\n"
        f"       `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label '{target_parent_label}'`\n"
        f"     - Post a comment on the parent issue listing all created subtask numbers in sequential order.\n"
    )
    return prompt


async def _triage_story(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    node_cfg: NodeConfig,
) -> tuple[bool, str]:
    """
    Pillar 4: Story Triage, Classification, and INVEST Decomposition.
    Uses primary harness (Claude Sonnet 5).
    """
    trigger = node_cfg.label_trigger or "needs-triage"
    output_label = node_cfg.label_output or "ready-for-dev"
    processed_label = node_cfg.processed_label or "architect-processed"
    queued_label = node_cfg.queued_label or "queued"

    # Zero-token lookahead pre-gate check: evaluate planned stories capacity
    max_planned = getattr(project, "max_planned_stories", None)
    if max_planned is None:
        max_planned = getattr(config.settings, "max_planned_stories", 2)

    planned_count = await state_manager.count_planned_stories(project.name)
    if planned_count >= max_planned:
        notice = f"[{project.name}|architect] Lookahead limit reached ({planned_count}/{max_planned}). Pausing decomposition."
        console.print(f"  [yellow]{notice}[/yellow]")
        return False, notice

    issues = await fetch_issues_with_label(project.repo, trigger, limit=1)
    if not issues:
        return False, f"No issues labeled '{trigger}'. Idle (0 tokens)."

    target_issue = issues[0]
    issue_id = target_issue["number"]
    issue_title = target_issue.get("title", "")

    # Sync issue into SDLC Blackboard memory
    await state_manager.sync_project_sdlc_items(
        project.name,
        [{
            "issue_number": issue_id,
            "title": issue_title,
            "state": "OPEN",
            "labels": [trigger],
        }],
    )

    harness_name = node_cfg.harness or "claude"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not found in configuration."

    allowed, q_res = await check_dispatch_quota(project, "architect", config, state_manager, harness_name=harness_name)
    if not allowed:
        return False, f"Quota throttled for harness '{q_res.harness_name}'. Dispatch deferred (Renewal in {q_res.formatted_eta})."

    retry_cfg = getattr(harness_cfg, "retry", None)
    max_retries = getattr(retry_cfg, "max_retries", 0) if retry_cfg else 0
    lock_ttl = int(harness_cfg.timeout_minutes * (1 + max_retries) + 5)

    lock_acquired = await state_manager.acquire_lock(
        issue_id=issue_id,
        repo=project.repo,
        node_type="architect",
        ttl_minutes=lock_ttl,
    )
    if not lock_acquired:
        return False, f"Issue #{issue_id} is currently locked by another active run. Skipping."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "architect",
        issue_id=issue_id,
    )

    # Ingest pre-approved Gherkin AC from Blackboard if available
    po_record = await state_manager.get_po_tracking(project.repo, issue_id)

    # Evaluate active story state for queueing
    active_story = await state_manager.get_active_story(project.name)
    has_active_story = (active_story is not None)
    target_parent_label = "planned" if has_active_story else processed_label

    prompt = build_triage_prompt(
        project=project,
        issue_id=issue_id,
        issue_title=issue_title,
        trigger=trigger,
        output_label=output_label,
        processed_label=processed_label,
        po_record=po_record,
        has_active_story=has_active_story,
        queued_label=queued_label,
    )

    console.print(f"\n  [bold magenta]⚡ [{project.name}:architect][/bold magenta] [bold white]Evaluating User Story #{issue_id}:[/bold white] [cyan]'{issue_title}'[/cyan]")
    console.print(f"  [dim]• Target: {project.repo} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]• Scope: Issue Classification, 3-Amigos Triage & INVEST Subtask Decomposition[/dim]")

    exec_cwd = await WorktreeManager.ensure_worktree(project, "architect")

    adapter = AsyncHarnessAdapter(
        harness_name,
        harness_cfg,
        state_manager=state_manager,
        project_name=project.name,
        node_name="architect",
        issue_number=issue_id,
    )
    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=exec_cwd,
        log_file=log_file,
        model=node_cfg.model,
        effort=node_cfg.effort,
        console_prefix=f"[{project.name}:architect]",
    )

    if exit_code != 0:
        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="architect",
            error_message=f"Harness exited with code {exit_code}. See logs: {log_file.name}",
        )
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="architect",
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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p1.wait()

            p2 = await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(issue_id),
                "--repo", project.repo,
                "--body", f"🤖 **Architect Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p2.wait()
        return False, f"Architect execution failed on issue #{issue_id} (exit code {exit_code})."

    linked_count = await sync_parent_subtask_links(project.repo, issue_id, target_parent_label, trigger)

    if shutil.which("gh"):
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
            await state_manager.sync_project_sdlc_items(
                project.name,
                [{
                    "issue_number": issue_id,
                    "title": issue_title,
                    "state": "CLOSED",
                    "labels": current_labels,
                    "item_type": "STORY",
                }],
            )
            await state_manager.release_lock(issue_id, project.repo, "architect")
            return True, f"Architect node verified issue #{issue_id} was already satisfied and closed it."

        if linked_count > 0 or target_parent_label in current_labels or "planned" in current_labels or processed_label in current_labels:
            story_state = "PLANNED" if (has_active_story or "planned" in current_labels) else "OPEN"
            assigned_label = "planned" if (has_active_story or "planned" in current_labels) else processed_label
            await state_manager.sync_project_sdlc_items(
                project.name,
                [{
                    "issue_number": issue_id,
                    "title": issue_title,
                    "state": story_state,
                    "labels": [assigned_label],
                    "item_type": "STORY",
                }],
            )
            await state_manager.release_lock(issue_id, project.repo, "architect")
            if has_active_story or "planned" in current_labels:
                return True, f"Architect node triaged and queued issue #{issue_id} behind active story into {linked_count} subtask(s) ('{assigned_label}')."
            return True, f"Architect node triaged and decomposed issue #{issue_id} into {linked_count} linked subtask(s) ('{output_label}')."

        if trigger not in current_labels:
            await state_manager.sync_project_sdlc_items(
                project.name,
                [{
                    "issue_number": issue_id,
                    "title": issue_title,
                    "state": "OPEN",
                    "labels": current_labels,
                }],
            )
            await state_manager.release_lock(issue_id, project.repo, "architect")
            labels_str = ", ".join(current_labels) or "no labels"
            return True, f"Architect node classified and transitioned issue #{issue_id} to [{labels_str}]."

        await state_manager.fail_job(
            issue_id=issue_id,
            repo=project.repo,
            node_type="architect",
            error_message="Architect node finished without classifying the issue or creating subtasks.",
        )
        await state_manager.record_anomaly_event(
            project_name=project.name,
            node_name="architect",
            error_type="UNCLASSIFIED_ESCALATION",
            error_message="Architect node finished without classifying the issue or creating subtasks.",
            issue_number=issue_id,
        )
        p_edit = await asyncio.create_subprocess_exec(
            "gh", "issue", "edit", str(issue_id),
            "--repo", project.repo,
            "--remove-label", trigger,
            "--add-label", "needs-po-review",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_edit.wait()

        p_comment = await asyncio.create_subprocess_exec(
            "gh", "issue", "comment", str(issue_id),
            "--repo", project.repo,
            "--body", f"🤖 **Architect Escalation**: Architect node evaluated this issue but could not determine classification. Flagging for PO review (`needs-po-review`). See log trace in `{log_file.name}`.",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p_comment.wait()
        return False, f"Architect node could not classify issue #{issue_id}. Flagged with 'needs-po-review'."

    await state_manager.release_lock(issue_id, project.repo, "architect")
    return True, f"Architect node completed evaluation on issue #{issue_id}."


async def run_architect_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    force_research: bool = False,
) -> tuple[bool, str]:
    """
    Executes the Architect Node across its 3 governance pillars:
    1. Living Architecture Plane synchronization (Bootstrap & Weekly Modernization via Antigravity).
    2. Architectural PR Code Review (Enforce contracts via Claude Sonnet).
    3. Story Triage & INVEST Decomposition (Decompose stories via Claude Sonnet).
    """
    node_cfg = project.nodes.get("architect", NodeConfig(harness="claude"))
    if not project.is_node_enabled("architect"):
        return False, "Architect node disabled for project."

    # 1. Living Architecture Plane Sync & Weekly Modernization
    arch_ran, arch_msg = await _sync_architecture_plane(
        project, config, state_manager, node_cfg, force=force_research
    )
    if arch_ran:
        return True, arch_msg

    # 2. Architectural PR Review Gate
    review_ran, review_msg = await _review_pr_architecture(
        project, config, state_manager, node_cfg
    )
    if review_ran:
        return True, review_msg

    # 3. Story Triage & INVEST Decomposition
    triage_ran, triage_msg = await _triage_story(
        project, config, state_manager, node_cfg
    )
    if triage_ran:
        return True, triage_msg

    if "Lookahead limit reached" in triage_msg:
        return False, triage_msg

    return False, "No architecture sync due, no PRs awaiting architectural review, no issues to triage. Idle (0 tokens)."
