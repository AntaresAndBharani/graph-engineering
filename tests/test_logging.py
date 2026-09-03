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


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for ProjectLogBufferManager (Issue #91)
# ---------------------------------------------------------------------------


def test_scenario_idempotent_project_scoped_log_hydration():
    """
    Scenario: Idempotent Project-Scoped Log Hydration
      Given the user switches project selection from "graph-engineering" to "crosstrainingapp"
      When the project selection event fires
      Then the dashboard must retrieve "crosstrainingapp's" scoped log buffer from ProjectLogBufferManager
      And clear the RichLog pane and populate it with the retrieved historical lines
      And incoming live logs from other projects must accumulate in the background without polluting the active view
    """
    from orchestrator.logging import ProjectLogBufferManager, TextualLogHandler

    ProjectLogBufferManager.reset()

    handler = TextualLogHandler(maxlen=1000)
    logger = logging.getLogger("orchestrator.test_scoped_hydration")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Seed logs for graph-engineering
    logger.info("[graph-engineering:architect] Starting INVEST story decomposition")
    logger.info("[graph-engineering:devtest] Running pytest suite")

    # Seed logs for crosstrainingapp
    logger.info("[crosstrainingapp:supervisor] Watchdog health check OK")
    logger.info("[crosstrainingapp:reviewer] PR #42 approved by gatekeeper")

    # Verify ProjectLogBufferManager separated the logs
    ge_logs = ProjectLogBufferManager.get_project_logs("graph-engineering")
    assert len(ge_logs) == 2
    assert any("INVEST story decomposition" in line for line in ge_logs)
    assert any("Running pytest suite" in line for line in ge_logs)
    assert not any("crosstrainingapp" in line for line in ge_logs)

    ct_logs = ProjectLogBufferManager.get_project_logs("crosstrainingapp")
    assert len(ct_logs) == 2
    assert any("Watchdog health check OK" in line for line in ct_logs)
    assert any("PR #42 approved" in line for line in ct_logs)
    assert not any("graph-engineering" in line for line in ct_logs)

    # Idempotent retrieval
    ct_logs_2 = ProjectLogBufferManager.get_project_logs("crosstrainingapp")
    assert ct_logs == ct_logs_2


def test_scenario_cold_start_disk_log_fallback(tmp_path: Path):
    """
    Scenario: Cold-Start Disk Log Fallback
      Given project "crosstrainingapp" is selected but the orchestrator daemon was recently restarted (in-memory deque is empty)
      When the UI requests the project logs
      Then the log manager must fallback to tailing the last 100 lines from the latest disk log file in "~/.config/orchestrator/logs/crosstrainingapp/"
      And display them in the RichLog pane with immediate historical context
    """
    from orchestrator.logging import ProjectLogBufferManager, tail_latest_project_logs

    ProjectLogBufferManager.reset()

    # Create mock disk structure
    logs_root = tmp_path / "logs"
    ct_dir = logs_root / "crosstrainingapp" / "devtest"
    ct_dir.mkdir(parents=True, exist_ok=True)

    # Older log file
    older_file = ct_dir / "20260829_120000_devtest_run.log"
    older_file.write_text("Older log line 1\nOlder log line 2\n", encoding="utf-8")
    import os
    os.utime(older_file, (time.time() - 3600, time.time() - 3600))

    # Latest log file with 150 lines
    latest_file = ct_dir / "20260830_120000_devtest_run.log"
    content = "\n".join([f"Execution trace line {i:03d}" for i in range(1, 151)])
    latest_file.write_text(content, encoding="utf-8")
    os.utime(latest_file, (time.time(), time.time()))

    # When UI requests project logs with cold start (empty in-memory deque)
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS.get("crosstrainingapp", [])) == 0
    logs = ProjectLogBufferManager.get_project_logs("crosstrainingapp", log_dir=logs_root, max_lines=100)

    # Then it tails the last 100 lines from the latest log file
    assert len(logs) == 100
    assert logs[0] == "Execution trace line 051"
    assert logs[-1] == "Execution trace line 150"
    assert "Older log line" not in logs

    # Module-level function also works identically
    direct_logs = tail_latest_project_logs("crosstrainingapp", log_dir=logs_root, max_lines=100)
    assert len(direct_logs) == 100
    assert direct_logs[0] == "Execution trace line 051"


def test_tail_latest_project_logs_empty_or_nonexistent(tmp_path: Path):
    """Asserts tail_latest_project_logs gracefully returns [] on empty or missing directories."""
    from orchestrator.logging import tail_latest_project_logs

    # Nonexistent log_dir
    assert tail_latest_project_logs("nonexistent", log_dir=tmp_path / "does_not_exist") == []

    # Empty project directory
    empty_proj = tmp_path / "logs" / "empty_proj"
    empty_proj.mkdir(parents=True)
    assert tail_latest_project_logs("empty_proj", log_dir=tmp_path / "logs") == []

    # Empty project name
    assert tail_latest_project_logs("", log_dir=tmp_path / "logs") == []


def test_project_log_buffer_manager_extract_project_name():
    """Asserts robust project name extraction across various log record types and string tags."""
    from orchestrator.logging import ProjectLogBufferManager

    # 1. String with [project:node]
    assert ProjectLogBufferManager.extract_project_name("[crosstrainingapp:supervisor] Error occurred") == "crosstrainingapp"

    # 2. String with [project]
    assert ProjectLogBufferManager.extract_project_name("[graph-engineering] Polling 5 issues") == "graph-engineering"

    # 3. String with Rich markup [dim cyan][project:node][/dim cyan]
    assert ProjectLogBufferManager.extract_project_name("  [dim cyan][ws-gym:devtest][/dim cyan] Tests running") == "ws-gym"

    # 4. Formatted log string with log level [INFO] preceding project tag
    assert ProjectLogBufferManager.extract_project_name("12:00:00 [INFO] [crosstrainingapp:architect] Story ready") == "crosstrainingapp"

    # 5. LogRecord with attribute project_name
    rec1 = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="Msg", args=(), exc_info=None)
    setattr(rec1, "project_name", "alpha-project")
    assert ProjectLogBufferManager.extract_project_name(rec1) == "alpha-project"

    # 6. LogRecord with attribute project (object with .name)
    class DummyProj:
        name = "beta-project"
    rec2 = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="Msg", args=(), exc_info=None)
    setattr(rec2, "project", DummyProj())
    assert ProjectLogBufferManager.extract_project_name(rec2) == "beta-project"

    # 7. Unscoped general log record
    rec3 = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="Daemon starting", args=(), exc_info=None)
    assert ProjectLogBufferManager.extract_project_name(rec3) is None


def test_project_log_buffer_manager_bounded_capacity():
    """Asserts per-project buffer respects 500 maxlen and global buffer respects 1000 maxlen."""
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    for i in range(600):
        ProjectLogBufferManager.add_line(f"Line {i}", project_name="proj-bounded")

    assert len(ProjectLogBufferManager.PROJECT_BUFFERS["proj-bounded"]) == 500
    assert ProjectLogBufferManager.PROJECT_BUFFERS["proj-bounded"][0] == (None, "Line 100")
    assert ProjectLogBufferManager.PROJECT_BUFFERS["proj-bounded"][-1] == (None, "Line 599")

    # Global buffer has all 600
    assert len(ProjectLogBufferManager.GLOBAL_LOG_BUFFER) == 600

    # Add 500 more to test global buffer 1000 maxlen
    for i in range(600, 1100):
        ProjectLogBufferManager.add_line(f"Line {i}", project_name="other-proj")

    assert len(ProjectLogBufferManager.GLOBAL_LOG_BUFFER) == 1000
    assert ProjectLogBufferManager.GLOBAL_LOG_BUFFER[0] == "Line 100"
    assert ProjectLogBufferManager.GLOBAL_LOG_BUFFER[-1] == "Line 1099"


def test_project_log_buffer_manager_clear_and_reset():
    """Asserts clear(project) and reset() selectively or completely clear buffers."""
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()
    ProjectLogBufferManager.add_line("P1 log", project_name="p1")
    ProjectLogBufferManager.add_line("P2 log", project_name="p2")

    assert len(ProjectLogBufferManager.PROJECT_BUFFERS["p1"]) == 1
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS["p2"]) == 1

    # Clear specific project
    ProjectLogBufferManager.clear(project_name="p1")
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS["p1"]) == 0
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS["p2"]) == 1

    # Global reset
    ProjectLogBufferManager.reset()
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS) == 0
    assert len(ProjectLogBufferManager.GLOBAL_LOG_BUFFER) == 0


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for Node-Scoped Log Buffers (Issue #103)
# ---------------------------------------------------------------------------


def test_extract_node_name():
    """Asserts extract_node_name extracts node from tags, strings, and LogRecords."""
    from orchestrator.logging import ProjectLogBufferManager

    # 1. String with [project:node]
    assert ProjectLogBufferManager.extract_node_name("[crosstrainingapp:supervisor] Error occurred") == "supervisor"

    # 2. String with Rich markup [dim cyan][project:node][/dim cyan]
    assert ProjectLogBufferManager.extract_node_name("  [dim cyan][ws-gym:devtest][/dim cyan] Tests running") == "devtest"

    # 3. Formatted log string with log level [INFO] preceding project:node tag
    assert ProjectLogBufferManager.extract_node_name("12:00:00 [INFO] [crosstrainingapp:architect] Story ready") == "architect"

    # 4. String without node (only project)
    assert ProjectLogBufferManager.extract_node_name("[graph-engineering] Polling 5 issues") is None

    # 5. LogRecord with node_name attribute
    rec1 = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="Msg", args=(), exc_info=None)
    setattr(rec1, "node_name", "devtest")
    assert ProjectLogBufferManager.extract_node_name(rec1) == "devtest"

    # 6. LogRecord with node attribute object
    class DummyNode:
        name = "architect"
    rec2 = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="Msg", args=(), exc_info=None)
    setattr(rec2, "node", DummyNode())
    assert ProjectLogBufferManager.extract_node_name(rec2) == "architect"

    # 7. Unscoped LogRecord
    rec3 = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="Daemon starting", args=(), exc_info=None)
    assert ProjectLogBufferManager.extract_node_name(rec3) is None


def test_scenario_node_scoped_in_memory_buffering():
    """
    Scenario: Node-scoped in-memory buffering
      Given two nodes "architect" and "devtest" are emitting log lines for project "crosstrainingapp"
      When add_line/add_record is called with an explicit node_name for each line
      Then PROJECT_BUFFERS["crosstrainingapp"] must store (node_name, line) tuples, still bounded to maxlen=500
      And existing callers that omit node_name must continue to work without raising (default node_name="unknown" or None)
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    # Given two nodes "architect" and "devtest" emitting log lines for project "crosstrainingapp"
    # When add_line is called with explicit node_name
    ProjectLogBufferManager.add_line("Architect starting INVEST decomposition", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("DevTest running pytest suite", project_name="crosstrainingapp", node_name="devtest")

    # When add_record is called with explicit node_name
    rec = logging.LogRecord(name="orchestrator", level=logging.INFO, pathname=__file__, lineno=1, msg="DevTest PR created", args=(), exc_info=None)
    ProjectLogBufferManager.add_record(rec, project_name="crosstrainingapp", node_name="devtest")

    # When caller omits node_name
    ProjectLogBufferManager.add_line("General project heartbeat", project_name="crosstrainingapp")

    # Then PROJECT_BUFFERS["crosstrainingapp"] must store (node_name, line) tuples
    buf = ProjectLogBufferManager.PROJECT_BUFFERS["crosstrainingapp"]
    assert len(buf) == 4
    assert buf[0] == ("architect", "Architect starting INVEST decomposition")
    assert buf[1] == ("devtest", "DevTest running pytest suite")
    assert buf[2] == ("devtest", "DevTest PR created")
    assert buf[3] == (None, "General project heartbeat")


def test_scenario_node_filtered_log_retrieval(tmp_path: Path):
    """
    Scenario: Node-filtered log retrieval
      Given a project buffer containing interleaved (node_name, line) tuples for "architect" and "devtest"
      When get_project_logs(project_name="crosstrainingapp", node_name="devtest") is called
      Then only lines tagged with node_name == "devtest" are returned, in original order
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    # Interleaved entries
    ProjectLogBufferManager.add_line("Arch line 1", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("Dev line 1", project_name="crosstrainingapp", node_name="devtest")
    ProjectLogBufferManager.add_line("Arch line 2", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("Dev line 2", project_name="crosstrainingapp", node_name="devtest")
    ProjectLogBufferManager.add_line("Dev line 3", project_name="crosstrainingapp", node_name="devtest")

    # Retrieve devtest logs
    dev_logs = ProjectLogBufferManager.get_project_logs(project_name="crosstrainingapp", node_name="devtest", log_dir=tmp_path)
    assert dev_logs == ["Dev line 1", "Dev line 2", "Dev line 3"]

    # Retrieve architect logs
    arch_logs = ProjectLogBufferManager.get_project_logs(project_name="crosstrainingapp", node_name="architect", log_dir=tmp_path)
    assert arch_logs == ["Arch line 1", "Arch line 2"]

    # Retrieve non-existent node returns [] when in-memory buffer has other nodes
    reviewer_logs = ProjectLogBufferManager.get_project_logs(project_name="crosstrainingapp", node_name="reviewer", log_dir=tmp_path)
    assert reviewer_logs == []


def test_scenario_node_scoped_cold_start_disk_tail_fallback(tmp_path: Path):
    """
    Scenario: Node-scoped cold-start disk tail fallback
      Given the in-memory buffer for the requested (project_name, node_name) is empty
      When get_project_logs(project_name, node_name) is called
      Then tail_latest_project_logs must look under "~/.config/orchestrator/logs/<project_name>/<node_name>/*.log" (not just <project_name>/**/*.log)
      And return the last max_lines of the latest file in that node-specific directory
    """
    from orchestrator.logging import ProjectLogBufferManager, tail_latest_project_logs

    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    arch_dir = logs_root / "crosstrainingapp" / "architect"
    dev_dir = logs_root / "crosstrainingapp" / "devtest"
    arch_dir.mkdir(parents=True, exist_ok=True)
    dev_dir.mkdir(parents=True, exist_ok=True)

    # Architect log file
    arch_file = arch_dir / "20260830_100000_architect_run.log"
    arch_file.write_text("Arch disk line 1\nArch disk line 2\n", encoding="utf-8")

    # Devtest log file (newer timestamp)
    dev_file = dev_dir / "20260830_110000_devtest_run.log"
    dev_content = "\n".join([f"Dev disk line {i:02d}" for i in range(1, 21)])
    dev_file.write_text(dev_content, encoding="utf-8")

    # Cold start for devtest: in-memory deque empty
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS.get("crosstrainingapp", [])) == 0

    dev_tail = tail_latest_project_logs("crosstrainingapp", log_dir=logs_root, max_lines=10, node_name="devtest")
    assert len(dev_tail) == 10
    assert dev_tail[0] == "Dev disk line 11"
    assert dev_tail[-1] == "Dev disk line 20"
    assert not any("Arch" in line for line in dev_tail)

    # Via get_project_logs
    retrieved_dev = ProjectLogBufferManager.get_project_logs("crosstrainingapp", log_dir=logs_root, max_lines=5, node_name="devtest")
    assert len(retrieved_dev) == 5
    assert retrieved_dev == ["Dev disk line 16", "Dev disk line 17", "Dev disk line 18", "Dev disk line 19", "Dev disk line 20"]

    # When requesting architect logs
    retrieved_arch = ProjectLogBufferManager.get_project_logs("crosstrainingapp", log_dir=logs_root, max_lines=10, node_name="architect")
    assert retrieved_arch == ["Arch disk line 1", "Arch disk line 2"]


def test_scenario_backward_compatible_no_node_retrieval(tmp_path: Path):
    """
    Scenario: Backward-compatible no-node retrieval
      Given node_name is not supplied to get_project_logs
      Then behavior must remain unchanged (return all lines for the project, global fallback if empty)
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    # 1. In-memory buffer retrieval with no node_name supplied
    ProjectLogBufferManager.add_line("Arch line", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("Dev line", project_name="crosstrainingapp", node_name="devtest")

    all_lines = ProjectLogBufferManager.get_project_logs("crosstrainingapp")
    assert all_lines == ["Arch line", "Dev line"]

    # 2. Disk fallback with no node_name supplied
    ProjectLogBufferManager.reset()
    logs_root = tmp_path / "logs"
    dev_dir = logs_root / "crosstrainingapp" / "devtest"
    dev_dir.mkdir(parents=True, exist_ok=True)
    dev_file = dev_dir / "20260830_120000_devtest_run.log"
    dev_file.write_text("Disk line 1\nDisk line 2\n", encoding="utf-8")

    disk_fallback_lines = ProjectLogBufferManager.get_project_logs("crosstrainingapp", log_dir=logs_root)
    assert disk_fallback_lines == ["Disk line 1", "Disk line 2"]

    # 3. Global fallback when no project_name and no disk logs
    ProjectLogBufferManager.reset()
    ProjectLogBufferManager.add_line("Global daemon log")
    assert ProjectLogBufferManager.get_project_logs(None) == ["Global daemon log"]


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for Issue #106
# ---------------------------------------------------------------------------


def test_scenario_issue_106_node_stream_filtering():
    """
    Scenario: Node stream filtering integration test (Issue #106)
      Given ProjectLogBufferManager buffers containing interleaved (node_name, line) tuples for two nodes
      When get_project_logs(project_name, node_name="devtest") is called
      Then only "devtest" lines are returned and "architect" lines are excluded
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    # Interleaved lines from architect and devtest
    ProjectLogBufferManager.add_line("Architect: analyzing architecture.md", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("DevTest: running unit tests", project_name="crosstrainingapp", node_name="devtest")
    ProjectLogBufferManager.add_line("Architect: story decomposition complete", project_name="crosstrainingapp", node_name="architect")
    ProjectLogBufferManager.add_line("DevTest: all assertions green", project_name="crosstrainingapp", node_name="devtest")

    dev_logs = ProjectLogBufferManager.get_project_logs("crosstrainingapp", node_name="devtest")
    assert dev_logs == [
        "DevTest: running unit tests",
        "DevTest: all assertions green",
    ]
    assert not any("Architect" in line for line in dev_logs)


def test_scenario_issue_106_node_scoped_disk_tail_fallback(tmp_path: Path):
    """
    Scenario: Node-scoped disk tail fallback test (Issue #106)
      Given an empty in-memory buffer and a log file under "<log_dir>/<project>/<node>/*.log"
      When get_project_logs(project, node) is called
      Then the returned lines match the tail of that node-specific file, not a sibling node directory
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    logs_root = tmp_path / "logs"
    arch_dir = logs_root / "crosstrainingapp" / "architect"
    dev_dir = logs_root / "crosstrainingapp" / "devtest"
    arch_dir.mkdir(parents=True, exist_ok=True)
    dev_dir.mkdir(parents=True, exist_ok=True)

    arch_log = arch_dir / "20260830_100000_architect_run.log"
    arch_log.write_text("ARCH_LINE_1\nARCH_LINE_2\n", encoding="utf-8")

    dev_log = dev_dir / "20260830_100000_devtest_run.log"
    dev_log.write_text("DEV_LINE_1\nDEV_LINE_2\nDEV_LINE_3\n", encoding="utf-8")

    # In-memory is empty
    assert len(ProjectLogBufferManager.PROJECT_BUFFERS.get("crosstrainingapp", [])) == 0

    # Fetch devtest logs
    dev_results = ProjectLogBufferManager.get_project_logs("crosstrainingapp", log_dir=logs_root, node_name="devtest")
    assert dev_results == ["DEV_LINE_1", "DEV_LINE_2", "DEV_LINE_3"]
    assert not any("ARCH" in line for line in dev_results)

    # Fetch architect logs
    arch_results = ProjectLogBufferManager.get_project_logs("crosstrainingapp", log_dir=logs_root, node_name="architect")
    assert arch_results == ["ARCH_LINE_1", "ARCH_LINE_2"]
    assert not any("DEV" in line for line in arch_results)


def test_format_story_lock_dispatch_log():
    """Verifies format_story_lock_dispatch_log formats expected structured message."""
    from orchestrator.logging import format_story_lock_dispatch_log

    msg = format_story_lock_dispatch_log(parent_id=90, subtask_id=93)
    assert msg == "Story Lock Active: Parent #90. Dispatched Subtask #93"

    msg_with_proj = format_story_lock_dispatch_log(parent_id=90, subtask_id=93, project_name="graph-engineering")
    assert msg_with_proj == "[graph-engineering:devtest] Story Lock Active: Parent #90. Dispatched Subtask #93"


def test_matches_node_scope():
    """Verifies prefix and family scope matching across compound node identifiers."""
    from orchestrator.logging import matches_node_scope

    # None or empty matches all
    assert matches_node_scope(None, "architect") is True
    assert matches_node_scope("architect", None) is True
    assert matches_node_scope(None, None) is True

    # Exact match
    assert matches_node_scope("architect", "architect") is True
    assert matches_node_scope("devtest", "devtest") is True

    # Prefix match
    assert matches_node_scope("architect", "architect_research") is True
    assert matches_node_scope("architect_research", "architect") is True
    assert matches_node_scope("devtest", "devtest_retry") is True

    # Base family prefix match
    assert matches_node_scope("architect_review", "architect_research") is True
    assert matches_node_scope("devtest-phase1", "devtest-phase2") is True

    # Mismatch
    assert matches_node_scope("architect", "devtest") is False
    assert matches_node_scope("reviewer", "supervisor") is False


def test_scenario_compound_node_log_retrieval(tmp_path: Path):
    """
    Scenario: Compound node log retrieval
      Given buffer contains logs tagged with 'architect_research' and 'architect'
      When get_project_logs is called with node_name='architect'
      Then all lines matching the 'architect' family prefix are returned
    """
    from orchestrator.logging import ProjectLogBufferManager

    ProjectLogBufferManager.reset()

    ProjectLogBufferManager.add_line("Architect core triage", project_name="biq-playbook", node_name="architect")
    ProjectLogBufferManager.add_line("Architect research stream", project_name="biq-playbook", node_name="architect_research")
    ProjectLogBufferManager.add_line("DevTest implementation", project_name="biq-playbook", node_name="devtest")

    arch_logs = ProjectLogBufferManager.get_project_logs(project_name="biq-playbook", node_name="architect", log_dir=tmp_path)
    assert len(arch_logs) == 2
    assert "Architect core triage" in arch_logs
    assert "Architect research stream" in arch_logs
    assert "DevTest implementation" not in arch_logs


# ---------------------------------------------------------------------------
# Gherkin Acceptance Criteria Tests for Issue #155 (LogQueryResult Contract)
# ---------------------------------------------------------------------------


def test_scenario_issue_155_typed_query_result_contract(tmp_path: Path):
    """
    Scenario: Typed query result contract
      Given log querying is requested for project "biq-playbook"
      When "tail_latest_project_logs" or "get_project_logs" executes
      Then it must return a "LogQueryResult" instance containing "lines: List[str]", "target_file: Optional[Path]", and "file_size: int"
      And it must support backwards-compatible iterable unpacking of lines.
    """
    from orchestrator.logging import (
        LogQueryResult,
        ProjectLogBufferManager,
        get_project_logs,
        tail_latest_project_logs,
    )

    ProjectLogBufferManager.reset()

    # 1. Setup mock disk logs for biq-playbook
    log_dir = tmp_path / "logs"
    arch_dir = log_dir / "biq-playbook" / "architect"
    arch_dir.mkdir(parents=True, exist_ok=True)
    log_file = arch_dir / "20260903_120000_architect_issue_155.log"
    log_content = "Line 1: Planning architecture\nLine 2: Generating INVEST decomposition\n"
    log_file.write_text(log_content, encoding="utf-8")

    # When tail_latest_project_logs executes
    tail_res = tail_latest_project_logs("biq-playbook", log_dir=log_dir, node_name="architect")

    # Then it must return a LogQueryResult instance containing lines, target_file, file_size
    assert isinstance(tail_res, LogQueryResult)
    assert tail_res.lines == [
        "Line 1: Planning architecture",
        "Line 2: Generating INVEST decomposition",
    ]
    assert tail_res.target_file == log_file
    assert tail_res.file_size == log_file.stat().st_size

    # And it must support backwards-compatible iterable unpacking of lines
    line_a, line_b = tail_res
    assert line_a == "Line 1: Planning architecture"
    assert line_b == "Line 2: Generating INVEST decomposition"
    assert list(tail_res) == tail_res.lines
    assert len(tail_res) == 2
    assert tail_res[0] == "Line 1: Planning architecture"
    assert tail_res[-1] == "Line 2: Generating INVEST decomposition"
    assert "Line 1: Planning architecture" in tail_res
    assert list(reversed(tail_res)) == [
        "Line 2: Generating INVEST decomposition",
        "Line 1: Planning architecture",
    ]
    assert bool(tail_res) is True
    assert tail_res == tail_res.lines
    assert tail_res.lines == tail_res

    # When get_project_logs executes (disk fallback)
    ProjectLogBufferManager.reset()
    get_res_disk = get_project_logs("biq-playbook", log_dir=log_dir, node_name="architect")
    assert isinstance(get_res_disk, LogQueryResult)
    assert get_res_disk.lines == tail_res.lines
    assert get_res_disk.target_file == log_file
    assert get_res_disk.file_size == tail_res.file_size

    # When get_project_logs executes (in-memory buffer hit)
    ProjectLogBufferManager.add_line("Line 3: Triage approved", project_name="biq-playbook", node_name="architect")
    get_res_mem = ProjectLogBufferManager.get_project_logs("biq-playbook", node_name="architect")
    assert isinstance(get_res_mem, LogQueryResult)
    assert "Line 3: Triage approved" in get_res_mem.lines
    assert get_res_mem.target_file is None
    assert get_res_mem.file_size == 0
    # Iterable unpacking on in-memory result
    for line in get_res_mem:
        assert isinstance(line, str)

    # Empty result contract
    empty_res = tail_latest_project_logs("nonexistent-project", log_dir=log_dir)
    assert isinstance(empty_res, LogQueryResult)
    assert empty_res.lines == []
    assert empty_res.target_file is None
    assert empty_res.file_size == 0
    assert bool(empty_res) is False
    assert len(empty_res) == 0
    assert empty_res == []
    assert [] == empty_res


def test_scenario_issue_155_strict_node_scope_isolation(tmp_path: Path):
    """
    Scenario: Strict node scope isolation across disk and in-memory buffers
      Given project "biq-playbook" has devtest logs on disk and untagged lines in memory
      When "get_project_logs" is invoked with "node_name='architect'"
      Then it must NOT return devtest logs or untagged in-memory lines
      And it must never fall back to unfiltered project files when the requested node has no logs.
    """
    from orchestrator.logging import (
        LogQueryResult,
        ProjectLogBufferManager,
        get_project_logs,
        tail_latest_project_logs,
    )

    ProjectLogBufferManager.reset()

    # Given project "biq-playbook" has devtest logs on disk
    log_dir = tmp_path / "logs"
    devtest_dir = log_dir / "biq-playbook" / "devtest"
    devtest_dir.mkdir(parents=True, exist_ok=True)
    devtest_file = devtest_dir / "20260903_110000_devtest_run.log"
    devtest_file.write_text("DevTest disk line 1\nDevTest disk line 2\n", encoding="utf-8")

    # And untagged lines in memory (as well as devtest lines in memory)
    ProjectLogBufferManager.add_line("Untagged system heartbeat 1", project_name="biq-playbook")
    ProjectLogBufferManager.add_line("Untagged system heartbeat 2", project_name="biq-playbook", node_name=None)
    ProjectLogBufferManager.add_line("DevTest in-memory line", project_name="biq-playbook", node_name="devtest")

    # When "get_project_logs" is invoked with "node_name='architect'"
    result = get_project_logs(
        project_name="biq-playbook",
        log_dir=log_dir,
        node_name="architect",
    )

    # Then it must return a LogQueryResult
    assert isinstance(result, LogQueryResult)
    # Then it must NOT return devtest logs or untagged in-memory lines
    assert result.lines == []
    assert not any("Untagged" in line for line in result.lines)
    assert not any("DevTest" in line for line in result.lines)
    # And it must never fall back to unfiltered project files when requested node has no logs
    assert result.target_file is None
    assert result.file_size == 0

    # Also verify tail_latest_project_logs directly enforces strict isolation
    tail_res = tail_latest_project_logs("biq-playbook", log_dir=log_dir, node_name="architect")
    assert isinstance(tail_res, LogQueryResult)
    assert tail_res.lines == []
    assert tail_res.target_file is None
    assert tail_res.file_size == 0


def test_scenario_issue_155_zero_byte_active_file_metadata_preservation(tmp_path: Path):
    """
    Scenario: 0-byte active file metadata preservation
      Given a freshly spawned node creates an initial 0-byte log file
      When "tail_latest_project_logs" locates the node's log files
      Then "LogQueryResult" must identify "target_file" with "file_size=0"
      And empty lines must not clear existing active context unexpectedly.
    """
    from orchestrator.logging import (
        LogQueryResult,
        ProjectLogBufferManager,
        get_project_logs,
        tail_latest_project_logs,
    )

    ProjectLogBufferManager.reset()

    # Given a freshly spawned node creates an initial 0-byte log file
    log_dir = tmp_path / "logs"
    arch_dir = log_dir / "biq-playbook" / "architect"
    arch_dir.mkdir(parents=True, exist_ok=True)
    initial_0byte_file = arch_dir / "20260903_130000_architect_issue_155.log"
    initial_0byte_file.touch()

    assert initial_0byte_file.exists()
    assert initial_0byte_file.stat().st_size == 0

    # When "tail_latest_project_logs" locates the node's log files
    tail_result = tail_latest_project_logs("biq-playbook", log_dir=log_dir, node_name="architect")

    # Then "LogQueryResult" must identify "target_file" with "file_size=0"
    assert isinstance(tail_result, LogQueryResult)
    assert tail_result.lines == []
    assert tail_result.target_file == initial_0byte_file
    assert tail_result.file_size == 0

    # When "get_project_logs" is invoked on the cold start 0-byte file
    get_result = get_project_logs("biq-playbook", log_dir=log_dir, node_name="architect")
    assert isinstance(get_result, LogQueryResult)
    assert get_result.lines == []
    assert get_result.target_file == initial_0byte_file
    assert get_result.file_size == 0

    # And empty lines must not clear existing active context unexpectedly:
    # Seed active in-memory context for another node
    ProjectLogBufferManager.add_line("DevTest active execution line", project_name="biq-playbook", node_name="devtest")
    # Calling get_project_logs on architect with 0-byte file on disk
    res_arch = get_project_logs("biq-playbook", log_dir=log_dir, node_name="architect")
    assert res_arch.target_file == initial_0byte_file
    assert res_arch.file_size == 0
    # Existing devtest active context in project buffer was not cleared
    devtest_res = get_project_logs("biq-playbook", log_dir=log_dir, node_name="devtest")
    assert devtest_res.lines == ["DevTest active execution line"]






