from __future__ import annotations

import importlib
import sys
from pathlib import Path
import threading
from typing import Any, Optional

from orchestrator.config import GlobalConfig, ProjectConfig


class ConfigHolder:
    """
    Thread-safe and async-safe container for the active GlobalConfig instance.
    Provides atomic read and update access across background tasks and worker loops.
    """

    def __init__(self, initial_config: GlobalConfig) -> None:
        self._lock = threading.RLock()
        self._config: GlobalConfig = initial_config

    @property
    def config(self) -> GlobalConfig:
        """Returns the current active GlobalConfig snapshot."""
        with self._lock:
            return self._config

    def get(self) -> GlobalConfig:
        """Alias for .config property."""
        with self._lock:
            return self._config

    def update(self, new_config: GlobalConfig) -> None:
        """Atomically updates the held GlobalConfig."""
        with self._lock:
            self._config = new_config

    def set(self, new_config: GlobalConfig) -> None:
        """Alias for update()."""
        with self._lock:
            self._config = new_config

    def get_project(self, project_name: str) -> Optional[ProjectConfig]:
        """Convenience accessor to look up a ProjectConfig by name atomically."""
        with self._lock:
            for p in self._config.projects:
                if p.name == project_name:
                    return p
            return None

    def __getattr__(self, item: str) -> Any:
        with self._lock:
            return getattr(self._config, item)


def hot_reload_runtime(config_path: Optional[Path] = None) -> GlobalConfig:
    """
    Dynamically reloads in-memory orchestrator modules across sys.modules
    and loads the latest configuration from disk upon explicit operator command.
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
