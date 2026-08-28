from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, Dict, List, Optional
from orchestrator.config import ProjectConfig


async def fetch_issues_with_label(
    repo: str,
    label: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Fetches open issues labeled with `label` from GitHub using gh CLI.
    Consumes 0 LLM tokens.
    """
    if not shutil.which("gh"):
        return []

    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--label",
        label,
        "--state",
        "open",
        "--json",
        "number,title,body,labels,createdAt,url",
        "--limit",
        str(limit),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0 or not stdout:
            return []
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        if isinstance(data, list):
            data.sort(key=lambda x: x.get("number", 0))
            return data[:limit]
        return []
    except Exception:
        return []


async def fetch_all_open_issues(
    repo: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Fetches all open issues from GitHub using gh CLI for status auditing.
    Sorted chronologically by issue number (FIFO).
    Consumes 0 LLM tokens.
    """
    if not shutil.which("gh"):
        return []

    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        "number,title,body,labels,createdAt,updatedAt,url",
        "--limit",
        str(limit),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0 or not stdout:
            return []
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        if isinstance(data, list):
            data.sort(key=lambda x: x.get("number", 0))
            return data[:limit]
        return []
    except Exception:
        return []


async def fetch_open_prs(
    repo: str,
    label: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Fetches open PRs (optionally filtered by label) from GitHub using gh CLI.
    Sorted chronologically by PR number (FIFO).
    Consumes 0 LLM tokens.
    """
    if not shutil.which("gh"):
        return []

    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        "number,title,body,labels,mergeable,url,headRefName,isDraft",
        "--limit",
        str(limit),
    ]

    if label:
        cmd.extend(["--label", label])

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0 or not stdout:
            return []
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        if isinstance(data, list):
            data.sort(key=lambda x: x.get("number", 0))
            return data[:limit]
        return []
    except Exception:
        return []


async def fetch_project_workload(
    project: ProjectConfig,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Determines all pending actionable tasks across a single project's nodes
    with zero token consumption.
    """
    workload: Dict[str, List[Dict[str, Any]]] = {
        "architect": [],
        "devtest": [],
        "prs": [],
    }

    architect_cfg = project.nodes.get("architect")
    architect_trigger = (
        architect_cfg.label_trigger if (architect_cfg and architect_cfg.label_trigger) else "needs-triage"
    )
    if architect_cfg is None or architect_cfg.enabled:
        workload["architect"] = await fetch_issues_with_label(project.repo, architect_trigger)

    devtest_cfg = project.nodes.get("devtest")
    devtest_trigger = (
        devtest_cfg.label_trigger if (devtest_cfg and devtest_cfg.label_trigger) else "ready-for-dev"
    )
    if devtest_cfg is None or devtest_cfg.enabled:
        workload["devtest"] = await fetch_issues_with_label(project.repo, devtest_trigger)
        workload["prs"] = await fetch_open_prs(project.repo, label="needs-architect-review")

    return workload
