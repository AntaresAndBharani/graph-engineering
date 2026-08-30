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


class ProjectLogBufferManager:
    """
    In-memory and disk-backed project-scoped log buffer manager.
    Maintains a global bounded deque buffer (maxlen=1000) and per-project bounded deque buffers (maxlen=500).
    Provides recursive pure-Python disk-tailing fallback when the in-memory deque is empty on cold start.
    """

    GLOBAL_LOG_BUFFER: Deque[str] = deque(maxlen=1000)
    PROJECT_BUFFERS: Dict[str, Deque[str]] = {}

    LOG_LEVEL_NAMES = {
        "INFO",
        "DEBUG",
        "WARNING",
        "WARN",
        "ERROR",
        "CRITICAL",
        "FATAL",
        "ORCHESTRATOR",
        "ROOT",
    }
    RICH_STYLE_TAGS = {
        "DIM",
        "BOLD",
        "ITALIC",
        "UNDERLINE",
        "RED",
        "GREEN",
        "YELLOW",
        "BLUE",
        "CYAN",
        "MAGENTA",
        "WHITE",
        "BLACK",
        "DEFAULT",
    }
    IGNORE_TAGS = LOG_LEVEL_NAMES | RICH_STYLE_TAGS

    @classmethod
    def reset(cls) -> None:
        """Clears all in-memory class buffers (useful for testing and daemon restarts)."""
        cls.GLOBAL_LOG_BUFFER.clear()
        cls.PROJECT_BUFFERS.clear()

    @classmethod
    def clear(cls, project_name: Optional[str] = None) -> None:
        """Clears specific project buffer or all class buffers if project_name is None."""
        if project_name:
            if project_name in cls.PROJECT_BUFFERS:
                cls.PROJECT_BUFFERS[project_name].clear()
        else:
            cls.reset()

    @classmethod
    def extract_project_name(cls, target: Union[logging.LogRecord, str, Any]) -> Optional[str]:
        """
        Extracts project name from LogRecord attributes or bracketed prefix in strings/messages.
        Examples:
          - LogRecord(project='crosstrainingapp') -> 'crosstrainingapp'
          - '[crosstrainingapp:supervisor] Error...' -> 'crosstrainingapp'
          - '[crosstrainingapp] Polling...' -> 'crosstrainingapp'
          - '  [dim cyan][crosstrainingapp:devtest][/dim cyan] ...' -> 'crosstrainingapp'
        """
        if isinstance(target, logging.LogRecord):
            proj = getattr(target, "project_name", None) or getattr(target, "project", None)
            if proj:
                return getattr(proj, "name", str(proj))
            msg = target.getMessage()
        elif isinstance(target, str):
            msg = target
        else:
            return None

        cleaned = strip_ansi(msg)
        for match in re.finditer(r"\[([a-zA-Z0-9_\-\.]+)(?::[a-zA-Z0-9_\-\.]+)?\]", cleaned):
            tag = match.group(1)
            if tag.upper() in cls.IGNORE_TAGS:
                continue
            return tag
        return None

    @classmethod
    def add_record(
        cls,
        record: logging.LogRecord,
        formatted: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> None:
        """
        Routes a LogRecord to GLOBAL_LOG_BUFFER and the corresponding project's buffer.
        """
        line = formatted or record.getMessage()
        cls.GLOBAL_LOG_BUFFER.append(line)

        proj = project_name or cls.extract_project_name(record)
        if proj:
            if proj not in cls.PROJECT_BUFFERS:
                cls.PROJECT_BUFFERS[proj] = deque(maxlen=500)
            cls.PROJECT_BUFFERS[proj].append(line)

    @classmethod
    def add_line(
        cls,
        line: str,
        project_name: Optional[str] = None,
    ) -> None:
        """
        Routes a raw/stream line to GLOBAL_LOG_BUFFER and the corresponding project's buffer.
        """
        cls.GLOBAL_LOG_BUFFER.append(line)
        proj = project_name or cls.extract_project_name(line)
        if proj:
            if proj not in cls.PROJECT_BUFFERS:
                cls.PROJECT_BUFFERS[proj] = deque(maxlen=500)
            cls.PROJECT_BUFFERS[proj].append(line)

    @classmethod
    def tail_latest_project_logs(
        cls,
        project_name: str,
        log_dir: Optional[Union[Path, str]] = None,
        max_lines: int = 100,
    ) -> List[str]:
        """
        Pure Python recursive disk-tailing fallback (pathlib.Path.rglob) reading the last max_lines (default 100)
        from the latest execution log file under <log_dir>/<project_name>/**/*.log.
        """
        if not project_name:
            return []

        resolved_log_dir: Path
        if log_dir is not None:
            resolved_log_dir = Path(log_dir).expanduser()
        else:
            resolved_log_dir = Path("~/.config/orchestrator/logs").expanduser()

        project_dir = resolved_log_dir / project_name
        if not project_dir.exists() or not project_dir.is_dir():
            return []

        try:
            log_files = [p for p in project_dir.rglob("*.log") if p.is_file()]
            if not log_files:
                return []
            latest_file = max(log_files, key=lambda p: (p.stat().st_mtime, p.name))
            with open(latest_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [strip_ansi(line).rstrip("\r\n") for line in lines[-max_lines:]]
        except Exception:
            return []

    @classmethod
    def get_project_logs(
        cls,
        project_name: Optional[str] = None,
        log_dir: Optional[Union[Path, str]] = None,
        max_lines: int = 100,
    ) -> List[str]:
        """
        Retrieves scoped historical log lines for the given project.
        If in-memory deque has entries, returns them.
        If in-memory deque is empty and project_name is provided, falls back to tailing disk logs.
        If no disk logs exist or project_name is None, returns global buffer.
        """
        if not project_name:
            return list(cls.GLOBAL_LOG_BUFFER)

        buf = cls.PROJECT_BUFFERS.get(project_name)
        if buf and len(buf) > 0:
            return list(buf)

        disk_lines = cls.tail_latest_project_logs(
            project_name=project_name,
            log_dir=log_dir,
            max_lines=max_lines,
        )
        if disk_lines:
            if project_name not in cls.PROJECT_BUFFERS:
                cls.PROJECT_BUFFERS[project_name] = deque(maxlen=500)
            cls.PROJECT_BUFFERS[project_name].extend(disk_lines)
            return disk_lines

        return list(cls.GLOBAL_LOG_BUFFER)


GLOBAL_LOG_BUFFER = ProjectLogBufferManager.GLOBAL_LOG_BUFFER
PROJECT_BUFFERS = ProjectLogBufferManager.PROJECT_BUFFERS


def tail_latest_project_logs(
    project_name: str,
    log_dir: Optional[Union[Path, str]] = None,
    max_lines: int = 100,
) -> List[str]:
    """Module-level pure Python recursive disk-tailing fallback."""
    return ProjectLogBufferManager.tail_latest_project_logs(
        project_name=project_name,
        log_dir=log_dir,
        max_lines=max_lines,
    )


class TextualLogHandler(logging.Handler):
    """
    Bounded log handler designed for the Textual TUI dashboard.
    Captures core root orchestrator events into a bounded deque buffer (default maxlen=1000)
    and routes records into ProjectLogBufferManager (both global and per-project buffers),
    while dropping/filtering verbose per-node agent harness traces (which are isolated in per-node log files).
    """

    def __init__(
        self,
        maxlen: int = 1000,
        callback: Optional[Callable[[logging.LogRecord, str], None]] = None,
        buffer_manager: Optional[Any] = None,
    ):
        super().__init__()
        self.buffer: Deque[logging.LogRecord] = deque(maxlen=maxlen)
        self.callback = callback
        self.buffer_manager = buffer_manager if buffer_manager is not None else ProjectLogBufferManager
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
            formatted = self.format(record)
            if self.buffer_manager is not None:
                try:
                    self.buffer_manager.add_record(record, formatted=formatted)
                except Exception:
                    pass
            if self.callback:
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
