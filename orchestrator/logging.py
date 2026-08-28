from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
import re
import time
from typing import Optional
from rich.logging import RichHandler

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes from text."""
    if not text:
        return ""
    return ANSI_ESCAPE_RE.sub("", text)


def get_project_log_path(
    log_dir: Path,
    project_name: str,
    node_name: str,
    issue_id: Optional[int | str] = None,
) -> Path:
    """
    Returns a structured path for node execution logs and ensures parent directory exists.
    Format: <log_dir>/<project_name>/<node_name>/<timestamp>_<node_name>_issue_<id>.log
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    target_dir = log_dir / project_name / node_name
    target_dir.mkdir(parents=True, exist_ok=True)

    if issue_id is not None:
        filename = f"{timestamp}_{node_name}_issue_{issue_id}.log"
    else:
        filename = f"{timestamp}_{node_name}_run.log"

    return target_dir / filename


def rotate_logs(log_dir: Path, max_age_days: int = 30, max_size_mb: int = 50) -> None:
    """
    Removes log files older than max_age_days or if directory exceeds max_size_mb.
    """
    if not log_dir.exists():
        return

    now = time.time()
    cutoff = now - (max_age_days * 86400)

    for p in log_dir.rglob("*.log"):
        if p.is_file():
            try:
                stat = p.stat()
                if stat.st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except Exception:
                pass


def setup_logger(log_dir: Optional[Path] = None, log_level: str = "INFO") -> logging.Logger:
    """
    Configures root logger with RichHandler and orchestrator.log file handler.
    """
    logger = logging.getLogger("orchestrator")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        # Rich console handler
        console_handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
            markup=True,
        )
        console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        logger.addHandler(console_handler)

        # File handler for main daemon
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            daemon_log_path = log_dir / "orchestrator.log"
            file_handler = logging.FileHandler(daemon_log_path, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

    return logger
