from __future__ import annotations

from pathlib import Path
import time
from orchestrator.logging import get_project_log_path, rotate_logs, strip_ansi


def test_strip_ansi():
    raw = "\x1b[31mHello\x1b[0m \x1b[1;32mWorld\x1b[0m\n"
    cleaned = strip_ansi(raw)
    assert cleaned == "Hello World\n"


def test_get_project_log_path(tmp_path: Path):
    log_file = get_project_log_path(tmp_path, "project-alpha", "architect", issue_id=42)
    assert log_file.parent.exists()
    assert "project-alpha" in str(log_file)
    assert "architect" in str(log_file)
    assert "issue_42.log" in log_file.name


def test_rotate_logs(tmp_path: Path):
    old_file = tmp_path / "old.log"
    old_file.write_text("old", encoding="utf-8")

    # Set mtime to 40 days ago
    old_time = time.time() - (40 * 86400)
    import os
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "new.log"
    new_file.write_text("new", encoding="utf-8")

    rotate_logs(tmp_path, max_age_days=30)
    assert not old_file.exists()
    assert new_file.exists()
