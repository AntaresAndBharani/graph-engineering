from __future__ import annotations

from pathlib import Path
import logging
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


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for TextualLogHandler (Issue #23)
# ---------------------------------------------------------------------------


def test_scenario_bounded_buffer():
    """
    Scenario: Bounded buffer
      Given more than 1000 log records are emitted
      When TextualLogHandler processes them
      Then only the most recent 1000 records are retained
    """
    from orchestrator.logging import TextualLogHandler

    handler = TextualLogHandler(maxlen=1000)
    logger = logging.getLogger("orchestrator.test_bounded")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Given more than 1000 log records are emitted
    for i in range(1250):
        # When TextualLogHandler processes them
        logger.info(f"Orchestrator heartbeat sequence {i}")

    # Then only the most recent 1000 records are retained
    assert len(handler.buffer) == 1000
    assert len(handler.records) == 1000
    assert "sequence 1249" in handler.records[-1].getMessage()
    assert "sequence 250" in handler.records[0].getMessage()
    assert "sequence 249" not in [r.getMessage() for r in handler.records]


def test_scenario_node_trace_filtering():
    """
    Scenario: Node trace filtering
      Given a log record originates from a per-node agent harness trace already written to its own log file
      When TextualLogHandler.emit is called
      Then that record is not forwarded into the bounded buffer
    """
    import logging
    from orchestrator.logging import TextualLogHandler

    handler = TextualLogHandler(maxlen=1000)

    # Given records originating from per-node agent harness traces
    # 1. node_trace attribute
    rec1 = logging.LogRecord(
        name="orchestrator.nodes.devtest",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=10,
        msg="Harness raw subprocess trace line",
        args=(),
        exc_info=None,
    )
    setattr(rec1, "node_trace", True)

    # 2. is_node_trace attribute
    rec2 = logging.LogRecord(
        name="orchestrator.nodes.architect",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=20,
        msg="Harness reasoning step chunk",
        args=(),
        exc_info=None,
    )
    setattr(rec2, "is_node_trace", True)

    # 3. is_harness_trace attribute
    rec3 = logging.LogRecord(
        name="orchestrator.harness",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=30,
        msg="Subprocess stdout ANSI buffer chunk",
        args=(),
        exc_info=None,
    )
    setattr(rec3, "is_harness_trace", True)

    # 4. category attribute
    rec4 = logging.LogRecord(
        name="orchestrator.nodes.reviewer",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=40,
        msg="Subprocess raw stderr",
        args=(),
        exc_info=None,
    )
    setattr(rec4, "category", "node_trace")

    # 5. Logger name prefix orchestrator.node_trace
    rec5 = logging.LogRecord(
        name="orchestrator.node_trace.devtest",
        level=logging.INFO,
        pathname=__file__,
        lineno=50,
        msg="Verbose devtest harness trace",
        args=(),
        exc_info=None,
    )

    # 6. Logger name prefix orchestrator.harness_trace
    rec6 = logging.LogRecord(
        name="orchestrator.harness_trace.claude",
        level=logging.INFO,
        pathname=__file__,
        lineno=60,
        msg="Verbose claude stream trace",
        args=(),
        exc_info=None,
    )

    # 7. trace=True attribute
    rec7 = logging.LogRecord(
        name="orchestrator.nodes.bau",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=70,
        msg="Harness token stream",
        args=(),
        exc_info=None,
    )
    setattr(rec7, "trace", True)

    # When TextualLogHandler.emit is called for all node trace records
    for r in [rec1, rec2, rec3, rec4, rec5, rec6, rec7]:
        handler.emit(r)

    # Then that record is not forwarded into the bounded buffer
    assert len(handler.buffer) == 0
    assert len(handler.records) == 0


def test_scenario_root_orchestrator_events_captured():
    """
    Scenario: Root orchestrator events are captured
      Given a log record from the root "orchestrator" logger (daemon/poller events)
      When TextualLogHandler.emit is called
      Then the record is appended to the bounded buffer
    """
    import logging
    from orchestrator.logging import TextualLogHandler

    handler = TextualLogHandler(maxlen=1000)

    # Given log records from the root "orchestrator" logger (daemon/poller events)
    rec1 = logging.LogRecord(
        name="orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=100,
        msg="Poller checking repository status",
        args=(),
        exc_info=None,
    )
    rec2 = logging.LogRecord(
        name="orchestrator.poller",
        level=logging.INFO,
        pathname=__file__,
        lineno=110,
        msg="Fetched 3 open issues for project alpha",
        args=(),
        exc_info=None,
    )
    rec3 = logging.LogRecord(
        name="orchestrator.reloader",
        level=logging.WARNING,
        pathname=__file__,
        lineno=120,
        msg="Source modification detected, reloading runtime",
        args=(),
        exc_info=None,
    )

    # When TextualLogHandler.emit is called
    handler.emit(rec1)
    handler.emit(rec2)
    handler.emit(rec3)

    # Then the record is appended to the bounded buffer
    assert len(handler.buffer) == 3
    assert len(handler.records) == 3
    assert handler.records[0].getMessage() == "Poller checking repository status"
    assert handler.records[1].getMessage() == "Fetched 3 open issues for project alpha"
    assert handler.records[2].getMessage() == "Source modification detected, reloading runtime"


def test_textual_log_handler_callback_invocation():
    """Asserts TextualLogHandler invokes registered callback with formatted message."""
    import logging
    from orchestrator.logging import TextualLogHandler

    emitted_items = []

    def log_callback(record: logging.LogRecord, formatted: str) -> None:
        emitted_items.append((record, formatted))

    handler = TextualLogHandler(maxlen=100, callback=log_callback)
    rec = logging.LogRecord(
        name="orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Daemon cycle completed in 1.2s",
        args=(),
        exc_info=None,
    )
    handler.emit(rec)

    assert len(emitted_items) == 1
    record, formatted = emitted_items[0]
    assert record.getMessage() == "Daemon cycle completed in 1.2s"
    assert "[INFO] Daemon cycle completed in 1.2s" in formatted


def test_textual_log_handler_emit_error_handling(monkeypatch):
    """Asserts TextualLogHandler gracefully catches exceptions and calls handleError."""
    import logging
    from orchestrator.logging import TextualLogHandler

    handler = TextualLogHandler(maxlen=100)
    handled_errors = []

    def mock_handle_error(record):
        handled_errors.append(record)

    monkeypatch.setattr(handler, "handleError", mock_handle_error)

    # Force format to raise exception
    def failing_format(record):
        raise ValueError("Formatting failed")

    monkeypatch.setattr(handler, "format", failing_format)
    handler.callback = lambda rec, fmt: None

    rec = logging.LogRecord(
        name="orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    handler.emit(rec)
    assert len(handled_errors) == 1
    assert handled_errors[0] == rec


def test_setup_logger_with_textual_handler(tmp_path: Path):
    """Asserts setup_logger attaches TextualLogHandler to root orchestrator logger."""
    import logging
    from orchestrator.logging import TextualLogHandler, setup_logger

    handler = TextualLogHandler(maxlen=500)
    logger = setup_logger(
        log_dir=tmp_path / "logs",
        log_level="INFO",
        textual_handler=handler,
    )

    assert handler in logger.handlers

    # Emit message through root orchestrator logger
    logger.info("Core orchestrator worker started")

    assert len(handler.buffer) >= 1
    assert any("Core orchestrator worker started" in r.getMessage() for r in handler.records)

