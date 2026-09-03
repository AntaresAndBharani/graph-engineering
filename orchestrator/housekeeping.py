from __future__ import annotations

import asyncio
import shutil
from typing import Dict, List
from orchestrator.config import LabelConfig, ProjectConfig


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


async def purge_obsolete_repository_labels(repo: str) -> List[str]:
    """
    Deletes known legacy/deprecated SDLC labels from the target GitHub repository.
    """
    if not shutil.which("gh"):
        return []

    deleted: List[str] = []
    for label in LEGACY_OBSOLETE_LABELS:
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
) -> Dict[str, bool]:
    """
    Ensures all standard taxonomy labels exist with the configured colors/descriptions
    in the target GitHub repository using `gh label create --force`.
    Optionally purges obsolete legacy labels.
    """
    if not shutil.which("gh"):
        return {label.name: False for label in labels}

    if purge_legacy:
        await purge_obsolete_repository_labels(repo)

    results: Dict[str, bool] = {}

    for label in labels:
        cmd = [
            "gh",
            "label",
            "create",
            label.name,
            "--repo",
            repo,
            "--color",
            label.color,
            "--description",
            label.description,
            "--force",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.wait()
            results[label.name] = process.returncode == 0
        except Exception:
            results[label.name] = False

    return results


async def sync_all_projects_labels(
    projects: List[ProjectConfig],
    labels: List[LabelConfig],
) -> Dict[str, Dict[str, bool]]:
    """Syncs labels across all enabled projects."""
    all_results: Dict[str, Dict[str, bool]] = {}
    for project in projects:
        if project.enabled:
            all_results[project.repo] = await sync_repository_labels(project.repo, labels)
    return all_results
