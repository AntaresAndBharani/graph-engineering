from __future__ import annotations

import asyncio
import shutil
from typing import Dict, List
from orchestrator.config import LabelConfig, ProjectConfig


async def sync_repository_labels(
    repo: str,
    labels: List[LabelConfig],
) -> Dict[str, bool]:
    """
    Ensures all standard taxonomy labels exist with the configured colors/descriptions
    in the target GitHub repository using `gh label create --force`.
    """
    if not shutil.which("gh"):
        return {label.name: False for label in labels}

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
