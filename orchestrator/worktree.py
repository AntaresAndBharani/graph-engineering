from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

from orchestrator.config import ProjectConfig, resolve_path

_logger = logging.getLogger(__name__)


def parse_worktree_porcelain(output: str) -> List[Dict[str, Any]]:
    """
    Parses the output of `git worktree list --porcelain` into a structured list of dictionaries.
    Handles standard, detached, bare, locked, and prunable entries.
    """
    worktrees: List[Dict[str, Any]] = []
    current_entry: Dict[str, Any] = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current_entry and "worktree" in current_entry:
                worktrees.append(current_entry)
                current_entry = {}
            continue

        if line.startswith("worktree "):
            if current_entry and "worktree" in current_entry:
                worktrees.append(current_entry)
                current_entry = {}
            current_entry["worktree"] = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            current_entry["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            current_entry["branch"] = line[len("branch "):].strip()
        elif line == "bare":
            current_entry["bare"] = True
        elif line == "detached":
            current_entry["detached"] = True
        elif line.startswith("locked"):
            current_entry["locked"] = line[len("locked"):].strip() or True
        elif line.startswith("prunable"):
            current_entry["prunable"] = line[len("prunable"):].strip() or True

    if current_entry and "worktree" in current_entry:
        worktrees.append(current_entry)

    return worktrees


def get_worktree_path(project: ProjectConfig, node_name: str) -> Path:
    """
    Determines the filesystem path for a node's dedicated worktree.
    Uses `project.worktree_dir` if configured; otherwise defaults to
    `<project.local_path>/.graph/worktrees/<node_name>_<project.name>`.
    """
    base_dir = (
        project.worktree_dir
        if project.worktree_dir is not None
        else (project.local_path / ".graph" / "worktrees")
    )
    target_name = f"{node_name}_{project.name}"
    return (base_dir / target_name).resolve()


async def list_worktrees(repo_path: Path | str) -> List[Dict[str, Any]]:
    """
    Lists all git worktrees registered in the given repository by executing
    `git worktree list --porcelain`. Returns an empty list if git fails or is unavailable.
    """
    if not shutil.which("git"):
        return []

    cwd = str(resolve_path(repo_path))
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "list", "--porcelain",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0 or not stdout:
            return []
        return parse_worktree_porcelain(stdout.decode("utf-8", errors="replace"))
    except Exception as e:
        _logger.debug("Error listing git worktrees in '%s': %s", cwd, e)
        return []


async def is_worktree_registered(repo_path: Path | str, target_path: Path | str) -> bool:
    """
    Checks whether `target_path` is already registered as an active worktree in `repo_path`.
    Performs case-insensitive normalization on Windows.
    """
    resolved_target = resolve_path(target_path)
    registered_list = await list_worktrees(repo_path)

    for wt in registered_list:
        wt_path_str = wt.get("worktree")
        if not wt_path_str:
            continue
        resolved_wt = resolve_path(wt_path_str)
        if resolved_wt == resolved_target:
            return True
        if os.name == "nt" and str(resolved_wt).lower() == str(resolved_target).lower():
            return True

    return False


async def sync_worktree(
    worktree_path: Path | str,
    default_branch: str = "main",
) -> bool:
    """
    Safely synchronizes an existing worktree with the latest upstream default branch.
    Performs best-effort fetch and reset. Swallows errors and returns False if sync fails.
    """
    target = resolve_path(worktree_path)
    if not target.exists():
        return False

    if not shutil.which("git"):
        return False

    try:
        # Best-effort fetch origin
        proc_fetch = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin", default_branch,
            cwd=str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc_fetch.communicate()

        # Best-effort reset or checkout
        proc_reset = await asyncio.create_subprocess_exec(
            "git", "reset", "--hard", f"origin/{default_branch}",
            cwd=str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        res_code = await proc_reset.wait()
        return res_code == 0
    except Exception as e:
        _logger.debug("Non-fatal error syncing worktree at '%s': %s", target, e)
        return False


async def ensure_worktree(
    project: ProjectConfig,
    node_name: str,
    default_branch: str = "main",
) -> Path:
    """
    Ensures that an ephemeral git worktree exists for the given project and node.
    - If worktrees are disabled in config (`worktrees_enabled=False`), returns `project.local_path`.
    - If worktree already exists and is registered in git, reuses and syncs it.
    - If worktree does not exist, creates it via `git worktree add`.
    - If creation fails (e.g. locked repo, unsupported git version), logs a warning
      indicating fallback to serial execution and returns `project.local_path` safely.
    """
    if not getattr(project, "worktrees_enabled", True):
        _logger.debug(
            "[%s:%s] Worktrees disabled in configuration. Using primary workspace '%s'.",
            project.name,
            node_name,
            project.local_path,
        )
        return project.local_path

    if not shutil.which("git"):
        _logger.warning(
            "[%s:%s] 'git' binary not found in PATH. Falling back to primary workspace '%s' for serial execution.",
            project.name,
            node_name,
            project.local_path,
        )
        return project.local_path

    target_path = get_worktree_path(project, node_name)

    # 1. Check if already registered and present on disk
    try:
        registered = await is_worktree_registered(project.local_path, target_path)
    except Exception as e:
        _logger.warning(
            "[%s:%s] Error inspecting git worktrees at '%s': %s. Falling back to primary workspace '%s' for serial execution.",
            project.name,
            node_name,
            project.local_path,
            e,
            project.local_path,
        )
        return project.local_path

    if registered and target_path.exists():
        _logger.debug(
            "[%s:%s] Reusing existing registered worktree at '%s'. Syncing...",
            project.name,
            node_name,
            target_path,
        )
        await sync_worktree(target_path, default_branch=default_branch)
        return target_path

    # 2. Ensure parent directory exists
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _logger.warning(
            "[%s:%s] Failed to create parent directory for worktree at '%s': %s. Falling back to primary workspace '%s' for serial execution.",
            project.name,
            node_name,
            target_path.parent,
            e,
            project.local_path,
        )
        return project.local_path

    # 3. Create worktree via git worktree add
    cmd = ["git", "worktree", "add", str(target_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project.local_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            _logger.warning(
                "[%s:%s] 'git worktree add' failed (code %d: %s). Falling back to primary workspace '%s' for serial execution.",
                project.name,
                node_name,
                proc.returncode,
                err_msg,
                project.local_path,
            )
            return project.local_path

        _logger.info(
            "[%s:%s] Created git worktree at '%s'.",
            project.name,
            node_name,
            target_path,
        )
        return target_path
    except Exception as e:
        _logger.warning(
            "[%s:%s] Exception during 'git worktree add' for '%s': %s. Falling back to primary workspace '%s' for serial execution.",
            project.name,
            node_name,
            target_path,
            e,
            project.local_path,
        )
        return project.local_path


async def prune_worktrees(
    target: Optional[ProjectConfig | Path | str] = None,
) -> bool:
    """
    Invokes `git worktree prune` in the target repository root.
    Never propagates exceptions if there is nothing to prune or if git returns non-zero.
    """
    if not shutil.which("git"):
        return False

    if isinstance(target, ProjectConfig):
        cwd = str(target.local_path)
    elif isinstance(target, (str, Path)):
        cwd = str(resolve_path(target))
    elif target is None:
        cwd = str(Path.cwd())
    else:
        try:
            cwd = str(getattr(target, "local_path", Path.cwd()))
        except Exception:
            cwd = str(Path.cwd())

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "prune",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception as e:
        _logger.debug("Non-fatal exception during git worktree prune in '%s': %s", cwd, e)
        return False


async def remove_worktree(
    project: ProjectConfig,
    node_name: str,
    force: bool = True,
) -> bool:
    """
    Safely removes a git worktree and cleans up registered references.
    """
    if not shutil.which("git"):
        return False

    target_path = get_worktree_path(project, node_name)
    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(target_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project.local_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        await prune_worktrees(project)
        return proc.returncode == 0
    except Exception as e:
        _logger.debug("Non-fatal error removing worktree '%s': %s", target_path, e)
        return False


async def clean_worktree(
    worktree_path: Path | str,
    default_branch: str = "main",
) -> bool:
    """
    Safely sanitizes a worktree after task completion or PR merge.
    Checks `git status --porcelain`: if untracked or uncommitted changes exist,
    executes `git stash push -u -m "Orchestrator pre-clean recovery"` before resetting
    to prevent accidental deletion of uncommitted code artifacts.
    """
    if not shutil.which("git"):
        return False

    wp = str(resolve_path(worktree_path))
    try:
        # 1. Check for untracked/uncommitted changes
        proc_status = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=wp,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_s, _ = await proc_status.communicate()
        status_out = stdout_s.decode("utf-8", errors="replace").strip() if stdout_s else ""

        if status_out:
            _logger.debug("Untracked/uncommitted changes in worktree '%s'. Stashing prior to reset...", wp)
            proc_stash = await asyncio.create_subprocess_exec(
                "git", "stash", "push", "-u", "-m", "Orchestrator pre-clean recovery",
                cwd=wp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc_stash.communicate()

        # 2. Reset worktree to origin/<default_branch> or clean HEAD
        proc_reset = await asyncio.create_subprocess_exec(
            "git", "checkout", f"origin/{default_branch}",
            cwd=wp,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc_reset.communicate()
        return True
    except Exception as e:
        _logger.debug("Non-fatal exception during clean_worktree for '%s': %s", wp, e)
        return False


class WorktreeManager:
    """
    Manager for ephemeral git worktree lifecycles per node and project.
    Provides create, sync, remove, and prune capabilities with non-destructive
    fallback to the primary project workspace for serial execution.
    """

    def __init__(self, project: Optional[ProjectConfig] = None) -> None:
        self.project = project

    @classmethod
    def get_worktree_path(cls, project: ProjectConfig, node_name: str) -> Path:
        return get_worktree_path(project, node_name)

    @classmethod
    async def is_worktree_registered(cls, repo_path: Path | str, target_path: Path | str) -> bool:
        return await is_worktree_registered(repo_path, target_path)

    @classmethod
    async def list_worktrees(cls, repo_path: Path | str) -> List[Dict[str, Any]]:
        return await list_worktrees(repo_path)

    @classmethod
    async def sync_worktree(cls, worktree_path: Path | str, default_branch: str = "main") -> bool:
        return await sync_worktree(worktree_path, default_branch=default_branch)

    @classmethod
    async def ensure_worktree(
        cls,
        project: ProjectConfig,
        node_name: str,
        default_branch: str = "main",
    ) -> Path:
        return await ensure_worktree(project, node_name, default_branch=default_branch)

    @classmethod
    async def prune(
        cls,
        target: Optional[ProjectConfig | Path | str] = None,
    ) -> bool:
        return await prune_worktrees(target)

    @classmethod
    async def clean_worktree(
        cls,
        worktree_path: Path | str,
        default_branch: str = "main",
    ) -> bool:
        return await clean_worktree(worktree_path, default_branch=default_branch)

    @classmethod
    async def remove_worktree(
        cls,
        project: ProjectConfig,
        node_name: str,
        force: bool = True,
    ) -> bool:
        return await remove_worktree(project, node_name, force=force)

    async def ensure(self, node_name: str, default_branch: str = "main") -> Path:
        if not self.project:
            raise ValueError("WorktreeManager instance was not initialized with a ProjectConfig.")
        return await self.ensure_worktree(self.project, node_name, default_branch=default_branch)

    async def clean(self, node_name: str, default_branch: str = "main") -> bool:
        if not self.project:
            raise ValueError("WorktreeManager instance was not initialized with a ProjectConfig.")
        target_path = self.get_worktree_path(self.project, node_name)
        return await self.clean_worktree(target_path, default_branch=default_branch)

    async def prune_self(self) -> bool:
        return await self.prune(self.project)

    async def remove(self, node_name: str, force: bool = True) -> bool:
        if not self.project:
            raise ValueError("WorktreeManager instance was not initialized with a ProjectConfig.")
        return await self.remove_worktree(self.project, node_name, force=force)
