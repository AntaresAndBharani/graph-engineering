from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator.poller import check_dispatch_quota, fetch_issues_with_label
from orchestrator.worktree import WorktreeManager

_logger = logging.getLogger(__name__)
console = Console()


async def sync_parent_subtask_links(
    repo: str,
    parent_id: int,
    processed_label: str = "architect-processed",
    trigger_label: str = "needs-triage",
) -> int:
    """
    Deterministically searches for child subtasks referencing the parent issue,
    ensures the parent issue body contains the '## Subtasks' checklist,
    and posts an audit comment if missing.
    Returns the count of linked children.
    """
    if not shutil.which("gh"):
        return 0

    env = {**os.environ, "GH_PROMPT_DISABLED": "1"}

    # 1. Fetch parent issue details (including comments)
    try:
        proc_parent = await asyncio.create_subprocess_exec(
            "gh", "issue", "view", str(parent_id),
            "--repo", repo,
            "--json", "body,labels,title,comments",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_p, _ = await asyncio.wait_for(proc_parent.communicate(), timeout=10.0)
        if proc_parent.returncode != 0 or not stdout_p:
            return 0
        parent_data = json.loads(stdout_p.decode("utf-8", errors="replace"))
    except Exception:
        return 0

    parent_body = parent_data.get("body", "")

    # 2. Discover child subtasks from parent comments and recent repo issues
    found_subtask_ids = set()
    for comment in parent_data.get("comments", []):
        c_body = comment.get("body", "")
        for m in re.finditer(r"#(\d+)", c_body):
            cid = int(m.group(1))
            if cid != parent_id:
                found_subtask_ids.add(cid)

    # Search by text reference as fallback
    try:
        proc_search = await asyncio.create_subprocess_exec(
            "gh", "issue", "list",
            "--repo", repo,
            "--search", f"#{parent_id}",
            "--json", "number,title,state",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_s, _ = await asyncio.wait_for(proc_search.communicate(), timeout=10.0)
        if proc_search.returncode == 0 and stdout_s:
            results = json.loads(stdout_s.decode("utf-8", errors="replace"))
            for c in results:
                if c.get("number") and c.get("number") != parent_id:
                    found_subtask_ids.add(c["number"])
    except Exception:
        pass

    if not found_subtask_ids:
        return 0

    children = []
    for sub_id in sorted(found_subtask_ids):
        try:
            p_sub = await asyncio.create_subprocess_exec(
                "gh", "issue", "view", str(sub_id),
                "--repo", repo,
                "--json", "number,title,state",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out_s, _ = await asyncio.wait_for(p_sub.communicate(), timeout=10.0)
            if p_sub.returncode == 0 and out_s:
                s_data = json.loads(out_s.decode("utf-8", errors="replace"))
                children.append(s_data)
        except Exception:
            pass

    if not children:
        return 0

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

        try:
            p_edit = await asyncio.create_subprocess_exec(
                "gh", "issue", "edit", str(parent_id),
                "--repo", repo,
                "--body", new_body,
                "--remove-label", trigger_label,
                "--add-label", processed_label,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_edit.communicate(), timeout=10.0)
        except Exception as e:
            _logger.warning("Failed to edit parent issue #%s: %s", parent_id, e)

        links_list = "\n".join([f"- #{c['number']}: {c.get('title', '')}" for c in children])
        try:
            p_comment = await asyncio.create_subprocess_exec(
                "gh", "issue", "comment", str(parent_id),
                "--repo", repo,
                "--body", f"ðŸ¤– **Architect Decomposition Complete**: Decomposed into {len(children)} subtask(s):\n{links_list}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(p_comment.communicate(), timeout=10.0)
        except Exception as e:
            _logger.warning("Failed to comment on parent issue #%s: %s", parent_id, e)

    return len(children)


async def _sync_architecture_plane(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    node_cfg: NodeConfig,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Pillar 1: Bootstraps or refreshes .graph/architecture.md.
    Uses specialized research harness (Antigravity gemini-3.8-flash-high) for cost-effective web research.
    Gated by research_interval_seconds (default 7 days / weekly).
    """
    graph_dir = project.local_path / ".graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    arch_file = graph_dir / "architecture.md"
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

    model = node_cfg.research_model or "gemini-3.8-flash-high"
    effort = node_cfg.research_effort

    console.print(f"\n  [bold cyan]ðŸ›ï¸ [{project.name}:architect][/bold cyan] [bold white]Living Architecture Plane Synchronization[/bold white]")
    console.print(f"  [dim]â€¢ Target: {project.repo} | Scope: Repository Architecture Standards ('.graph/architecture.md')[/dim]")
    console.print(f"  [dim]â€¢ Harness: {harness_name} ({model}) | Frequency: Weekly (7-day SLA)[/dim]")

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


def build_triage_prompt(
    project: ProjectConfig,
    issue_id: int,
    issue_title: str,
    trigger: str = "needs-triage",
    output_label: str = "ready-for-dev",
    processed_label: str = "architect-processed",
    queued_label: str = "queued",
    **kwargs: Any,
) -> str:
    """
    Constructs the 3-case triage and decomposition prompt for the Architect harness.
    Outputs INVEST-compliant subtasks labeled 'queued' and transitions parent to 'architect-processed'.
    """
    context_note = ""
    if project.context_files:
        context_note = f"Read the project context files in your workspace: {', '.join(project.context_files)}."

    prompt = (
        f"You are the Principal Architect operating autonomously in non-interactive batch mode.\n"
        f"Perform Triage, Classification, and Architectural Decomposition for GitHub Issue #{issue_id} ('{issue_title}'). {context_note}\n\n"
        f"CRITICAL OPERATIONAL RULES:\n"
        f"1. You are fully autonomous. Do NOT ask questions in chat or wait for human confirmation. Perform all required actions immediately using GitHub CLI (`gh`).\n"
        f"2. CLASSIFY AND ROUTE THE ISSUE ACCORDING TO ITS NATURE (3 CASES ONLY):\n"
        f"   - **Case 1: ALREADY IMPLEMENTED ON MAIN**: If this issue's acceptance criteria are already satisfied in the codebase, close it immediately:\n"
        f"     `gh issue close {issue_id} --repo '{project.repo}' --comment 'Closed: Already implemented on main.'`\n"
        f"   - **Case 2: STANDALONE TASK / SMALL BUG** (Small, self-contained, does not require subtask breakdown): Route directly to development by labeling it '{output_label}' and removing '{trigger}':\n"
        f"     `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label '{output_label}'`\n"
        f"     `gh issue comment {issue_id} --repo '{project.repo}' --body 'ðŸ¤– **Architect Triage**: Classified as a standalone technical task. Labeled {output_label} for DevTest implementation.'`\n"
        f"   - **Case 3: FULL USER STORY / COMPLEX FEATURE**: Decompose into minimal, testable technical subtasks following 3-amigos and INVEST principles:\n"
        f"     - Create all Subtasks 1..N (Queued): `gh issue create --repo '{project.repo}' --title '<subtask N title>' --body '<Gherkin acceptance criteria>\\n\\nParent: #{issue_id}' --label '{queued_label}'`\n"
        f"     - Update the parent story to '{processed_label}' and remove '{trigger}':\n"
        f"       `gh issue edit {issue_id} --repo '{project.repo}' --remove-label '{trigger}' --add-label '{processed_label}'`\n"
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
    Pillar 2: Story Triage, Classification, and INVEST Decomposition.
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
        _logger.debug(notice)
        return False, notice

    # Persistent idle backoff gate check: evaluate restart-resilient timestamp
    backoff_seconds = getattr(node_cfg, "lookahead_backoff_seconds", None)
    if backoff_seconds is None:
        backoff_seconds = getattr(config.settings, "lookahead_backoff_seconds", 1200)

    last_idle_sweep = await state_manager.get_last_idle_sweep_timestamp(project.name)
    if last_idle_sweep is not None and backoff_seconds > 0:
        elapsed = time.time() - last_idle_sweep
        if elapsed < backoff_seconds:
            remaining_secs = int(backoff_seconds - elapsed)
            minutes, seconds = divmod(remaining_secs, 60)
            _logger.debug(
                "[%s:architect] Idle backoff active. Next sweep in %dm %02ds.",
                project.name, minutes, seconds
            )
            return False, f"Idle backoff active (next sweep in {minutes}m {seconds:02d}s)."

    issues = await fetch_issues_with_label(project.repo, trigger, limit=1)
    if not issues:
        await state_manager.update_idle_sweep_timestamp(project.name, time.time())
        return False, f"No issues labeled '{trigger}'. Idle (0 tokens)."

    # Backlog has work: reset idle backoff timestamp
    await state_manager.update_idle_sweep_timestamp(project.name, 0.0)

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

    prompt = build_triage_prompt(
        project=project,
        issue_id=issue_id,
        issue_title=issue_title,
        trigger=trigger,
        output_label=output_label,
        processed_label=processed_label,
        queued_label=queued_label,
    )

    console.print(f"\n  [bold magenta]âš¡ [{project.name}:architect][/bold magenta] [bold white]Evaluating User Story #{issue_id}:[/bold white] [cyan]'{issue_title}'[/cyan]")
    console.print(f"  [dim]â€¢ Target: {project.repo} | Harness: {harness_name} ({node_cfg.model or 'default'})[/dim]")
    console.print(f"  [dim]â€¢ Scope: Issue Classification, 3-Amigos Triage & INVEST Subtask Decomposition[/dim]")

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
            env = {**os.environ, "GH_PROMPT_DISABLED": "1"}
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
                    "--body", f"ðŸ¤– **Architect Node Execution Failed** (Exit Code {exit_code}). Log trace saved to `{log_file.name}`.",
                    env=env,
                )
                await asyncio.wait_for(p2.communicate(), timeout=10.0)
            except Exception:
                pass
        return False, f"Architect execution failed on issue #{issue_id} (exit code {exit_code})."

    linked_count = await sync_parent_subtask_links(project.repo, issue_id, processed_label, trigger)

    if shutil.which("gh"):
        env = {**os.environ, "GH_PROMPT_DISABLED": "1"}
        is_closed = False
        current_labels = []
        try:
            proc_view = await asyncio.create_subprocess_exec(
                "gh", "issue", "view", str(issue_id),
                "--repo", project.repo,
                "--json", "state,labels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_view, _ = await asyncio.wait_for(proc_view.communicate(), timeout=10.0)
            if proc_view.returncode == 0 and stdout_view:
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

        if linked_count > 0 or processed_label in current_labels:
            await state_manager.sync_project_sdlc_items(
                project.name,
                [{
                    "issue_number": issue_id,
                    "title": issue_title,
                    "state": "OPEN",
                    "labels": [processed_label],
                    "item_type": "STORY",
                }],
            )
            await state_manager.release_lock(issue_id, project.repo, "architect")
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

    await state_manager.release_lock(issue_id, project.repo, "architect")
    return True, f"Architect node completed evaluation on issue #{issue_id}."


async def run_architect_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    force_research: bool = False,
) -> tuple[bool, str]:
    """
    Executes the Architect Node across its 2 core pillars:
    1. Living Architecture Plane synchronization (Bootstrap & Weekly Modernization via Antigravity).
    2. Story Triage & INVEST Decomposition (Decompose stories via Claude Sonnet).
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

    # 2. Story Triage & INVEST Decomposition
    triage_ran, triage_msg = await _triage_story(
        project, config, state_manager, node_cfg
    )
    if triage_ran:
        return True, triage_msg

    if "Lookahead limit reached" in triage_msg or "Idle backoff active" in triage_msg:
        return False, triage_msg

    return False, "No architecture sync due, no issues to triage. Idle (0 tokens)."
