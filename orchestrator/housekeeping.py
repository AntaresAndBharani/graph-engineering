from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Dict, List, Optional, Set, TYPE_CHECKING
from orchestrator.config import LabelConfig, ProjectConfig

if TYPE_CHECKING:
    from orchestrator.db import StateManager

_logger = logging.getLogger(__name__)


LEGACY_OBSOLETE_LABELS = [
    "status:definition",
    "status:ready",
    "status:in-progress",
    "status:ready-for-architect",
    "status:needs-po-input",
    "status:review",
    "status:needs-clarification",
    "status:needs-revision",
    "status:awaiting-approval",
    "status:pending-review",
    "status:in-development",
    "status:done",
    "type:subtask",
    "type:user-story",
    "pipeline:locked",
    "review:approved",
    "review:changes-requested",
    "origin:backlog-triage",
    "needs-architect-review",
    "needs-po-review",
    "architect-approved",
    "planned",
    "tech-debt",
]


async def purge_obsolete_repository_labels(
    repo: str,
    existing_names: Optional[Set[str]] = None,
    exclude_names: Optional[Set[str]] = None,
) -> List[str]:
    """
    Deletes known legacy/deprecated SDLC labels from the target GitHub repository.
    If existing_names is provided, only attempts to delete labels that actually exist.
    Excludes any label names specified in exclude_names.
    """
    if not shutil.which("gh"):
        return []

    deleted: List[str] = []
    candidates = [
        label for label in LEGACY_OBSOLETE_LABELS
        if (exclude_names is None or label not in exclude_names)
        and (existing_names is None or label in existing_names)
    ]

    for label in candidates:
        cmd = ["gh", "label", "delete", label, "--repo", repo, "--yes"]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.wait()
            if process.returncode == 0:
                deleted.append(label)
        except Exception:
            pass
    return deleted


async def sync_repository_labels(
    repo: str,
    labels: List[LabelConfig],
    purge_legacy: bool = True,
    *,
    state_manager: Optional[StateManager] = None,
) -> Dict[str, bool]:
    """
    Ensures all standard taxonomy labels exist with configured colors and descriptions
    in the target GitHub repository.
    
    Inspects existing labels in a single 'gh label list --json name,color,description --limit 200' query.
    Normalizes colors via color.lstrip('#').casefold() to prevent false-positive drift.
    Skips the obsolete label deletion pass if 'legacy_purge_done:{repo}' is recorded in daemon_control.
    Issues 'gh label create --force' only if a managed label is missing or differs in color/description.
    """
    if not shutil.which("gh"):
        return {label.name: False for label in labels}

    list_cmd = [
        "gh",
        "label",
        "list",
        "--repo",
        repo,
        "--json",
        "name,color,description",
        "--limit",
        "200",
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *list_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            _logger.warning(
                "Failed to list labels for %s (exit code %s): %s",
                repo,
                process.returncode,
                stderr.decode("utf-8", errors="replace").strip(),
            )
            return {label.name: False for label in labels}
        raw_items = json.loads(stdout.decode("utf-8", errors="replace"))
        existing_by_name: Dict[str, dict] = {
            item["name"]: item
            for item in raw_items
            if isinstance(item, dict) and "name" in item
        }
    except Exception as e:
        _logger.warning("Error inspecting labels for %s: %s", repo, e)
        return {label.name: False for label in labels}

    # One-shot purge guard check via daemon_control
    purge_key = f"legacy_purge_done:{repo}"
    skip_purge = False
    if state_manager is not None:
        if hasattr(state_manager, "get_daemon_control_value"):
            skip_purge = (await state_manager.get_daemon_control_value(purge_key)) is not None
        elif hasattr(state_manager, "get_daemon_info"):
            info = await state_manager.get_daemon_info()
            skip_purge = purge_key in info

    managed_names = {lbl.name for lbl in labels}

    if purge_legacy and not skip_purge:
        deleted = await purge_obsolete_repository_labels(
            repo,
            existing_names=set(existing_by_name.keys()),
            exclude_names=managed_names,
        )
        for d in deleted:
            existing_by_name.pop(d, None)

        if state_manager is not None:
            if hasattr(state_manager, "set_daemon_control_value"):
                await state_manager.set_daemon_control_value(purge_key, "1")

    results: Dict[str, bool] = {}

    for label in labels:
        if label.name in existing_by_name:
            existing_item = existing_by_name[label.name]
            existing_color = (existing_item.get("color") or "").lstrip("#").casefold()
            target_color = (label.color or "").lstrip("#").casefold()
            existing_desc = (existing_item.get("description") or "").strip()
            target_desc = (label.description or "").strip()

            if existing_color == target_color and existing_desc == target_desc:
                results[label.name] = True
                continue

        cmd = [
            "gh",
            "label",
            "create",
            label.name,
            "--repo",
            repo,
            "--color",
            label.color.lstrip("#") if label.color else "",
            "--description",
            label.description or "",
            "--force",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.wait()
            results[label.name] = (process.returncode == 0)
        except Exception:
            results[label.name] = False

    return results


async def sync_all_projects_labels(
    projects: List[ProjectConfig],
    labels: List[LabelConfig],
    *,
    state_manager: Optional[StateManager] = None,
    concurrency: int = 4,
    sync_events: Optional[Dict[str, asyncio.Event]] = None,
) -> Dict[str, Dict[str, bool]]:
    """
    Syncs labels across all enabled projects concurrently (up to `concurrency` parallel repos).
    If `sync_events` is provided, sets each project's event upon completion.
    """
    enabled_projects = [p for p in projects if p.enabled]
    if not enabled_projects:
        return {}

    sem = asyncio.Semaphore(concurrency)
    all_results: Dict[str, Dict[str, bool]] = {}

    async def _sync_one(p: ProjectConfig) -> tuple[str, Dict[str, bool]]:
        async with sem:
            try:
                res = await sync_repository_labels(p.repo, labels, state_manager=state_manager)
                return p.repo, res
            except Exception as e:
                _logger.error("Failed to sync labels for %s: %s", p.repo, e)
                return p.repo, {lbl.name: False for lbl in labels}
            finally:
                if sync_events and p.name in sync_events:
                    sync_events[p.name].set()

    results = await asyncio.gather(*[_sync_one(p) for p in enabled_projects], return_exceptions=True)
    for r in results:
        if isinstance(r, tuple):
            repo, res = r
            all_results[repo] = res
        elif isinstance(r, Exception):
            _logger.error("Label sync task error: %s", r)
    return all_results
