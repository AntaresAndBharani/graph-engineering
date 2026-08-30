"""
Decoupled, Agnostic Multi-Agent CLI Orchestrator.
"""

import sys

# Ensure UTF-8 stdout/stderr across all platforms (specifically Windows cp1252 fix)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__version__ = "2.0.0"
