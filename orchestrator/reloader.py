from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Optional

from orchestrator.config import GlobalConfig


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
