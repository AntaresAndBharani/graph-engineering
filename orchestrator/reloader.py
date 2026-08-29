from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from orchestrator.config import GlobalConfig, find_config_file, load_config


class SourceWatcher:
    """
    Monitors configuration files and orchestrator Python source files for modifications.
    Enables automatic hot-reloading on the fly without stopping the daemon.
    """

    def __init__(self, config_path: Optional[Path] = None, watch_source: bool = True):
        found = find_config_file(config_path)
        self.config_path = (found if found else Path(config_path or "config.yaml")).resolve()
        self.watch_source = watch_source
        self.source_dir = Path(__file__).resolve().parent

        # Map of file path string -> last known mtime
        self._file_mtimes: Dict[str, float] = {}
        self._record_initial_snapshots()

    def _record_initial_snapshots(self) -> None:
        """Records the baseline modification times for watched files."""
        if self.config_path.exists():
            try:
                self._file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime
            except OSError:
                pass

        if self.watch_source and self.source_dir.exists():
            for py_file in self.source_dir.rglob("*.py"):
                try:
                    self._file_mtimes[str(py_file)] = py_file.stat().st_mtime
                except OSError:
                    pass

    def check_for_changes(self) -> Tuple[bool, List[str]]:
        """
        Scans watched files and detects if any modification occurred.
        Returns tuple: (has_changed: bool, modified_files: List[str]).
        """
        modified: List[str] = []

        # 1. Check Configuration File
        if self.config_path.exists():
            try:
                curr_mtime = self.config_path.stat().st_mtime
                prev_mtime = self._file_mtimes.get(str(self.config_path))
                if prev_mtime is not None and curr_mtime > prev_mtime:
                    modified.append(self.config_path.name)
                self._file_mtimes[str(self.config_path)] = curr_mtime
            except OSError:
                pass

        # 2. Check Python Source Files
        if self.watch_source and self.source_dir.exists():
            for py_file in self.source_dir.rglob("*.py"):
                try:
                    curr_mtime = py_file.stat().st_mtime
                    prev_mtime = self._file_mtimes.get(str(py_file))
                    if prev_mtime is not None and curr_mtime > prev_mtime:
                        rel_path = py_file.relative_to(self.source_dir.parent).as_posix()
                        modified.append(rel_path)
                    self._file_mtimes[str(py_file)] = curr_mtime
                except OSError:
                    pass

        return len(modified) > 0, modified


def hot_reload_runtime(config_path: Optional[Path] = None) -> GlobalConfig:
    """
    Dynamically reloads in-memory orchestrator modules across sys.modules
    and loads the latest configuration from disk.
    """
    # Reload core modules in topological order to prevent circular binding issues
    module_names = [
        "orchestrator.logging",
        "orchestrator.config",
        "orchestrator.db",
        "orchestrator.harness",
        "orchestrator.poller",
        "orchestrator.housekeeping",
        "orchestrator.nodes.supervisor",
        "orchestrator.nodes.architect",
        "orchestrator.nodes.devtest",
        "orchestrator.nodes.reviewer",
        "orchestrator.nodes.bau",
        "orchestrator.reloader",
    ]

    for mod_name in module_names:
        if mod_name in sys.modules:
            try:
                importlib.reload(sys.modules[mod_name])
            except Exception:
                pass

    # Re-read and validate fresh configuration
    from orchestrator.config import load_config as fresh_load_config
    new_config = fresh_load_config(config_path)
    return new_config
