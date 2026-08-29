from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
import re
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Union
from rich.logging import RichHandler

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class TextualLogHandler(logging.Handler):
    """
    Bounded log handler designed for the Textual TUI dashboard.
    Captures core root orchestrator events into a bounded deque buffer (default maxlen=1000)
    while dropping/filtering verbose per-node agent harness traces (which are isolated in per-node log files).
    """

    def __init__(
        self,
        maxlen: int = 1000,
        callback: Optional[Callable[[logging.LogRecord, str], None]] = None,
    ):
        super().__init__()
        self.buffer: Deque[logging.LogRecord] = deque(maxlen=maxlen)
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    @property
    def records(self) -> List[logging.LogRecord]:
        return list(self.buffer)

    def is_node_trace(self, record: logging.LogRecord) -> bool:
        """
        Determines if a log record originates from a per-node agent harness trace
        that should be filtered out from the dashboard log stream.
        """
        if getattr(record, "node_trace", False) or getattr(record, "is_node_trace", False) or getattr(record, "is_harness_trace", False):
            return True
        if getattr(record, "category", "") in ("node_trace", "harness_trace"):
            return True
        if record.name.startswith("orchestrator.node_trace") or record.name.startswith("orchestrator.harness_trace"):
            return True
        if getattr(record, "trace", False):
            return True
        return False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.is_node_trace(record):
                return
            self.buffer.append(record)
            if self.callback:
                formatted = self.format(record)
                self.callback(record, formatted)
        except Exception:
            self.handleError(record)


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


def setup_logger(
    log_dir: Optional[Path] = None,
    log_level: str = "INFO",
    textual_handler: Optional[TextualLogHandler] = None,
) -> logging.Logger:
    """
    Configures root logger with RichHandler and orchestrator.log file handler.
    Optionally attaches a TextualLogHandler for the TUI dashboard.
    """
    logger = logging.getLogger("orchestrator")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = True

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

    if textual_handler:
        # Avoid duplicate textual handler
        if textual_handler not in logger.handlers:
            logger.addHandler(textual_handler)

    return logger
