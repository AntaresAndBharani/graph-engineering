from __future__ import annotations
import asyncio
from datetime import datetime
import json
import logging
import re
import shutil
import time
from orchestrator.config import GlobalConfig, ProjectConfig
from orchestrator.db import StateManager
from orchestrator.quota import QuotaManager, QuotaCheckResult

_logger = logging.getLogger(__name__)


def parse_iso_timestamp(ts: Any) -> float:
    """Parses an ISO 8601 string or numeric timestamp to epoch seconds."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            clean_ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            return dt.timestamp()
        except Exception:
            pass
    return time.time()


def extract_linked_pr(issue_number: int, prs: List[Dict[str, Any]]) -> Optional[int]:
    """
    Finds if any open PR is linked to the given issue number.
    Inspects PR branch name (headRefName), title, and body for issue references.
    """
    if not prs or not issue_number:
        return None

    pattern_hash = re.compile(rf"#\b{issue_number}\b", re.IGNORECASE)
    pattern_branch = re.compile(rf"issue[-/_]?{issue_number}\b", re.IGNORECASE)

    for pr in prs:
        pr_number = pr.get("number")
        if not pr_number:
            continue

        head_ref = pr.get("headRefName") or ""
        if pattern_branch.search(head_ref) or pattern_hash.search(head_ref):
            return int(pr_number)

        title = pr.get("title") or ""
        if pattern_hash.search(title) or pattern_branch.search(title):
            return int(pr_number)

        body = pr.get("body") or ""
        if pattern_hash.search(body) or pattern_branch.search(body):
            return int(pr_number)

    return None


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


async def fetch_issue_by_number(
    repo: str,
    issue_number: int,
) -> Optional[Dict[str, Any]]:
    """
    Fetches a specific issue by its number from GitHub using gh CLI.
    Consumes 0 LLM tokens.
    """
    if not shutil.which("gh"):
        return None

    cmd = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        "--repo",
        repo,
        "--json",
        "number,title,body,labels,createdAt,updatedAt,url",
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0 or not stdout:
            return None
        return json.loads(stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None


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


async def poll_project_sdlc_items(
    project: ProjectConfig,
    state_manager: Optional[StateManager] = None,
    limit_issues: int = 100,
    limit_prs: int = 20,
) -> List[Dict[str, Any]]:
    """
    Zero-token polling sweep that fetches open issues and open PRs for a project,
    correlates linked PRs to issues, and syncs them to StateManager (sdlc_items table).
    Non-blocking / best-effort on SQLite errors.
    """
    open_issues = await fetch_all_open_issues(project.repo, limit=limit_issues)
    open_prs = await fetch_open_prs(project.repo, limit=limit_prs)

    items: List[Dict[str, Any]] = []
    now = time.time()

    for issue in open_issues:
        issue_num = issue.get("number")
        if not issue_num:
            continue
        title = str(issue.get("title", ""))
        state = str(issue.get("state") or "OPEN")
        labels = issue.get("labels", [])
        linked_pr = extract_linked_pr(issue_num, open_prs)
        updated_ts = parse_iso_timestamp(issue.get("updatedAt") or issue.get("updated_at", now))

        items.append({
            "project_name": project.name,
            "issue_number": int(issue_num),
            "title": title,
            "state": state,
            "labels": labels,
            "linked_pr": linked_pr,
            "updated_at": updated_ts,
        })

    if state_manager is not None:
        try:
            await state_manager.sync_project_sdlc_items(project.name, items)
        except Exception as e:
            _logger.warning(
                "[%s] Non-blocking SDLC memory sync failed during polling sweep: %s",
                project.name,
                e,
            )

    return items


async def fetch_project_workload(
    project: ProjectConfig,
    state_manager: Optional[StateManager] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Determines all pending actionable tasks across a single project's nodes
    with zero token consumption, and optionally syncs SDLC items to state manager.
    """
    if state_manager is not None:
        try:
            await poll_project_sdlc_items(project, state_manager)
        except Exception as e:
            _logger.warning("[%s] Best-effort workload SDLC sync failed: %s", project.name, e)

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


async def check_dispatch_quota(
    project: ProjectConfig,
    node_name: str,
    config: GlobalConfig,
    state_manager: StateManager,
    quota_manager: Optional[QuotaManager] = None,
) -> tuple[bool, QuotaCheckResult]:
    """
    Evaluates whether the resolved harness for the target project and node
    has sufficient runway capacity for dispatch.
    Consumes 0 LLM tokens (pure local SQLite computation).
    If throttled, logs the renewal ETA countdown.
    """
    qm = quota_manager or QuotaManager(config, state_manager)
    harness_name = qm.resolve_harness_for_node(project, node_name)
    res = await qm.check_harness_capacity(harness_name)
    if not res.allowed:
        _logger.warning(
            "[%s:%s] Quota throttled for harness '%s' (Deficit: %d tokens). Dispatch deferred. Renewal ETA: %s",
            project.name,
            node_name,
            res.harness_name,
            res.deficit,
            res.formatted_eta,
        )
    return res.allowed, res

