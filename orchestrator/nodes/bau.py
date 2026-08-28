from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.config import GlobalConfig, NodeConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import get_project_log_path
from orchestrator.poller import fetch_issues_with_label


async def run_bau_node(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Executes BAU (Business As Usual / Maintenance) Node.
    Runs once a day to collect open 'tech-debt' and 'enhancement' issues,
    synthesizes them into structured User Story issues labeled 'needs-triage',
    and closes the original constituent issues.
    """
    node_cfg = project.nodes.get(
        "bau",
        NodeConfig(
            harness="antigravity",
            model="gemini-3.7-flash-low",
        ),
    )
    if not node_cfg.enabled:
        return False, "BAU node disabled for project."

    # 1. Schedule Gating (Once a Day / 86400s)
    if not force:
        last_run = await state_manager.get_last_run("bau", project.repo)
        if last_run is not None:
            elapsed = time.time() - last_run
            interval = getattr(config.settings, "bau_interval_seconds", 86400)
            if elapsed < interval:
                return False, f"BAU node not due ({int((interval - elapsed) / 3600)}h remaining). Idle (0 tokens)."

    # 2. Deterministic Gating (0 Tokens)
    tech_debt_issues = await fetch_issues_with_label(project.repo, "tech-debt", limit=30)
    enhancement_issues = await fetch_issues_with_label(project.repo, "enhancement", limit=30)

    # Deduplicate issues by issue number
    seen_ids = set()
    raw_issues: List[Dict[str, Any]] = []
    for issue in tech_debt_issues + enhancement_issues:
        num = issue.get("number")
        if num and num not in seen_ids:
            seen_ids.add(num)
            raw_issues.append(issue)

    if not raw_issues:
        await state_manager.record_node_run("bau", project.repo)
        return False, "No issues labeled 'tech-debt' or 'enhancement'. Idle (0 tokens)."

    # 3. Acquire State Lock
    harness_name = node_cfg.harness or "antigravity"
    harness_cfg = config.harnesses.get(harness_name)
    if not harness_cfg:
        return False, f"Harness '{harness_name}' not found in configuration."

    lock_acquired = await state_manager.acquire_lock(
        issue_id="global",
        repo=project.repo,
        node_type="bau",
        ttl_minutes=15,
    )
    if not lock_acquired:
        return False, "BAU node is currently locked by another active run. Skipping."

    log_file = get_project_log_path(
        config.settings.resolved_log_dir,
        project.name,
        "bau",
    )

    # 4. Formulate Prompt
    issues_summary = []
    for iss in raw_issues:
        labels_str = ", ".join([l.get("name", "") for l in iss.get("labels", []) if isinstance(l, dict)])
        issues_summary.append(
            f"### Issue #{iss['number']}: {iss.get('title', '')}\n"
            f"Labels: {labels_str}\n"
            f"Body:\n{iss.get('body', '').strip()}\n"
        )
    formatted_issues = "\n---\n".join(issues_summary)

    prompt = (
        f"You are the BAU (Business-As-Usual) Technical Product Owner & Architect.\n"
        f"Review the following non-blocking 'tech-debt' and 'enhancement' issues from repository '{project.repo}':\n\n"
        f"{formatted_issues}\n\n"
        f"Task:\n"
        f"1. Group related tech-debt and enhancements into one or more cohesive, actionable User Stories.\n"
        f"2. For each story, write a clear Title and standard Markdown Body with Gherkin acceptance criteria.\n"
        f"3. Make sure every constituent issue number is listed in 'closes_issues'.\n"
        f"4. Output strictly valid JSON matching this schema with NO markdown code fencing or conversational text:\n"
        f"{{\n"
        f'  "consolidated_stories": [\n'
        f"    {{\n"
        f'      "title": "[Story]: <Consolidated Story Title>",\n'
        f'      "body": "## User Story Description\\n<Description>\\n\\n## Acceptance Criteria (Gherkin)\\nScenario: ...\\n\\n## Consolidated Items\\n- Consolidates #<num>",\n'
        f'      "closes_issues": [<issue_number_1>, <issue_number_2>]\n'
        f"    }}\n"
        f"  ]\n"
        f"}}\n"
    )

    adapter = AsyncHarnessAdapter(harness_name, harness_cfg)
    model = node_cfg.model or "gemini-3.7-flash-low"
    effort = node_cfg.effort

    exit_code = await adapter.execute(
        prompt=prompt,
        cwd=project.local_path,
        log_file=log_file,
        model=model,
        effort=effort,
    )

    if exit_code != 0:
        await state_manager.fail_job(
            issue_id="global",
            repo=project.repo,
            node_type="bau",
            error_message=f"BAU Harness exited with code {exit_code}. See logs: {log_file.name}",
        )
        await state_manager.release_lock("global", project.repo, "bau")
        return False, f"BAU execution failed (exit code {exit_code})."

    # 5. Parse Output
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        # Extract JSON from output
        json_match = re.search(r"(\{[\s\S]*\})", content)
        if not json_match:
            await state_manager.release_lock("global", project.repo, "bau")
            return False, f"BAU node did not produce valid JSON. See log: {log_file.name}"

        data = json.loads(json_match.group(1))
        stories = data.get("consolidated_stories", [])
        if not stories:
            await state_manager.record_node_run("bau", project.repo)
            await state_manager.release_lock("global", project.repo, "bau")
            return False, "BAU model produced 0 consolidated stories."
    except Exception as e:
        await state_manager.release_lock("global", project.repo, "bau")
        return False, f"Error parsing BAU output: {e}"

    # 6. Create New Story Issues & Close Old Constituent Issues
    created_stories: List[int] = []
    if shutil.which("gh"):
        for story in stories:
            title = story.get("title", "[Story]: Technical Debt & Maintenance")
            body = story.get("body", "")
            closes = story.get("closes_issues", [])

            # Create new story issue with label 'needs-triage'
            p_create = await asyncio.create_subprocess_exec(
                "gh", "issue", "create",
                "--repo", project.repo,
                "--title", title,
                "--body", body,
                "--label", "needs-triage",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_create, _ = await p_create.communicate()
            new_issue_id = None
            if stdout_create:
                out_url = stdout_create.decode("utf-8").strip()
                url_match = re.search(r"/issues/(\d+)", out_url)
                if url_match:
                    new_issue_id = int(url_match.group(1))
                    created_stories.append(new_issue_id)

            # Close constituent issues
            for old_id in closes:
                ref_text = f"#{new_issue_id}" if new_issue_id else "a new consolidated story"
                p_close = await asyncio.create_subprocess_exec(
                    "gh", "issue", "close", str(old_id),
                    "--repo", project.repo,
                    "--comment", f"🤖 **BAU Maintenance Node**: Consolidated into {ref_text} and labeled `needs-triage` for Architect review.",
                )
                await p_close.wait()

    await state_manager.record_node_run("bau", project.repo)
    await state_manager.release_lock("global", project.repo, "bau")
    return True, f"BAU node consolidated {len(raw_issues)} issue(s) into {len(stories)} new story issue(s) labeled 'needs-triage'."
