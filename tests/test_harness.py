from __future__ import annotations

from pathlib import Path
import pytest
from orchestrator.config import HarnessConfig
from orchestrator.harness import AsyncHarnessAdapter


def test_harness_build_command():
    cfg = HarnessConfig(
        binary="claude",
        args=["-p", "{prompt}", "--dangerously-skip-permissions"],
        model_flag="--model",
        effort_flag="--effort",
    )
    adapter = AsyncHarnessAdapter("claude", cfg)

    cmd = adapter.build_command("Analyze issue #10", model="claude-sonnet-5", effort="high")
    assert cmd == ["claude", "--model", "claude-sonnet-5", "--effort", "high", "-p", "Analyze issue #10", "--dangerously-skip-permissions"]


def test_harness_build_command_no_effort_flag():
    cfg = HarnessConfig(
        binary="custom_cli",
        args=["exec", "{prompt}"],
        model_flag="--model",
    )
    adapter = AsyncHarnessAdapter("custom", cfg)

    cmd = adapter.build_command("Run tests", model="custom-model", effort="high")
    # effort flag is None, so effort argument is ignored
    assert cmd == ["custom_cli", "--model", "custom-model", "exec", "Run tests"]


def test_harness_build_env():
    cfg = HarnessConfig(
        binary="claude",
        args=["-p", "{prompt}"],
        env_vars={"CUSTOM_ENDPOINT": "https://api.example.com"},
    )
    adapter = AsyncHarnessAdapter("claude", cfg)
    env = adapter.build_env()

    assert env["NO_COLOR"] == "1"
    assert env["TERM"] == "dumb"
    assert env["CUSTOM_ENDPOINT"] == "https://api.example.com"
    # System environment variables must be preserved for OAuth session stores
    assert "PATH" in env


@pytest.mark.asyncio
async def test_harness_execution_missing_binary(tmp_path: Path):
    cfg = HarnessConfig(
        binary="non_existent_binary_xyz_123",
        args=["{prompt}"],
    )
    adapter = AsyncHarnessAdapter("fake", cfg)
    log_file = tmp_path / "test.log"

    exit_code = await adapter.execute("test prompt", tmp_path, log_file)
    assert exit_code == 127
    assert "not found in PATH" in log_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_harness_execution_with_console_prefix(tmp_path: Path):
    cfg = HarnessConfig(
        binary="non_existent_binary_xyz_123",
        args=["{prompt}"],
    )
    adapter = AsyncHarnessAdapter("fake", cfg)
    log_file = tmp_path / "test.log"

    exit_code = await adapter.execute("test prompt", tmp_path, log_file, console_prefix="[test:prefix]")
    assert exit_code == 127


def test_is_retryable_error():
    from orchestrator.harness import is_retryable_error, get_matched_retryable_pattern

    patterns = [
        "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "502", "504",
        "rate limit", "quota exceeded", "connection reset", "server disconnected", "fetch failed"
    ]

    # Transient error matches
    assert is_retryable_error("API Error: 503 UNAVAILABLE - upstream gateway timed out", patterns) is True
    assert is_retryable_error("HTTP Status 429: Too Many Requests (Rate limit hit)", patterns) is True
    assert is_retryable_error("RESOURCE_EXHAUSTED: Quota exceeded for project", patterns) is True
    assert is_retryable_error("Fatal: connection reset by peer", patterns) is True
    assert is_retryable_error("Error: server disconnected abruptly", patterns) is True
    assert is_retryable_error("Fetch failed: network unreachable (502 Bad Gateway)", patterns) is True

    # Case insensitivity checks
    assert is_retryable_error("error 503 unavailable", patterns) is True
    assert is_retryable_error("resource_exhausted", patterns) is True

    # Non-retryable error mismatches
    assert is_retryable_error("401 Unauthorized: Invalid API key", patterns) is False
    assert is_retryable_error("400 Bad Request: Malformed JSON payload", patterns) is False
    assert is_retryable_error("404 Not Found: Resource does not exist", patterns) is False
    assert is_retryable_error("SyntaxError: invalid syntax in file.py", patterns) is False
    assert is_retryable_error("", patterns) is False
    assert is_retryable_error("Everything is fine", []) is False

    # get_matched_retryable_pattern checks
    assert get_matched_retryable_pattern("503 Service Unavailable", patterns) == "503"
    assert get_matched_retryable_pattern("RESOURCE_EXHAUSTED error", patterns) == "RESOURCE_EXHAUSTED"
    assert get_matched_retryable_pattern("401 Unauthorized", patterns) is None


def test_calculate_backoff_delay():
    from orchestrator.config import HarnessRetryConfig
    from orchestrator.harness import calculate_backoff_delay

    cfg = HarnessRetryConfig(
        max_retries=3,
        initial_delay_seconds=5.0,
        backoff_factor=2.0,
        max_delay_seconds=60.0,
    )

    # Attempt 0: base is 5.0 -> with jitter [0.8 * 5.0, 1.2 * 5.0] = [4.0, 6.0]
    for _ in range(20):
        d0 = calculate_backoff_delay(0, cfg)
        assert 4.0 <= d0 <= 6.0

    # Attempt 1: base is 5.0 * 2.0 = 10.0 -> [8.0, 12.0]
    for _ in range(20):
        d1 = calculate_backoff_delay(1, cfg)
        assert 8.0 <= d1 <= 12.0

    # Attempt 2: base is 5.0 * 4.0 = 20.0 -> [16.0, 24.0]
    for _ in range(20):
        d2 = calculate_backoff_delay(2, cfg)
        assert 16.0 <= d2 <= 24.0

    # High attempt: base capped at 60.0 -> [48.0, 72.0]
    for _ in range(20):
        d_high = calculate_backoff_delay(10, cfg)
        assert 48.0 <= d_high <= 72.0


def test_harness_retry_config_validation():
    from orchestrator.config import HarnessRetryConfig
    from pydantic import ValidationError

    cfg = HarnessRetryConfig()
    assert cfg.max_retries == 3
    assert cfg.initial_delay_seconds == 5.0
    assert cfg.backoff_factor == 2.0
    assert cfg.max_delay_seconds == 60.0
    assert "503" in cfg.retryable_patterns

    with pytest.raises(ValidationError):
        HarnessRetryConfig(max_retries=-1)

    with pytest.raises(ValidationError):
        HarnessRetryConfig(initial_delay_seconds=0.1)

    with pytest.raises(ValidationError):
        HarnessRetryConfig(backoff_factor=0.5)

    with pytest.raises(ValidationError):
        HarnessRetryConfig(max_delay_seconds=2.0)


@pytest.mark.asyncio
async def test_harness_transient_retry_success(tmp_path: Path, monkeypatch):
    """
    AC 1: When harness returns transient error (503 UNAVAILABLE), adapter retries with backoff & jitter.
    If retry succeeds on attempt 2, returns 0.
    """
    import asyncio
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.harness import AsyncHarnessAdapter

    retry_cfg = HarnessRetryConfig(
        max_retries=3,
        initial_delay_seconds=1.0,
        backoff_factor=2.0,
        max_delay_seconds=10.0,
    )
    cfg = HarnessConfig(binary="claude", args=["-p", "{prompt}"], retry=retry_cfg)
    adapter = AsyncHarnessAdapter("claude", cfg)

    # Mock availability
    monkeypatch.setattr(adapter, "is_available", lambda: True)

    call_count = 0
    slept_delays = []

    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return 1, "Error: 503 UNAVAILABLE upstream server error"
        return 0, "Task completed successfully"

    async def mock_sleep(delay):
        slept_delays.append(delay)

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    log_file = tmp_path / "harness_retry.log"
    exit_code = await adapter.execute(
        prompt="Build feature",
        cwd=tmp_path,
        log_file=log_file,
        console_prefix="[test:claude]",
    )

    assert exit_code == 0
    assert call_count == 2
    assert len(slept_delays) == 1
    assert 0.8 <= slept_delays[0] <= 1.2

    log_content = log_file.read_text(encoding="utf-8")
    assert "[WARN] [harness:claude] Transient upstream error detected (503)." in log_content
    assert "Retrying attempt 1/3 in" in log_content


@pytest.mark.asyncio
async def test_harness_non_retryable_fail_fast(tmp_path: Path, monkeypatch):
    """
    AC 2: Non-retryable error (401 Unauthorized) fails fast on attempt 1 without retrying.
    """
    import asyncio
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.harness import AsyncHarnessAdapter

    retry_cfg = HarnessRetryConfig(max_retries=3)
    cfg = HarnessConfig(binary="claude", args=["-p", "{prompt}"], retry=retry_cfg)
    adapter = AsyncHarnessAdapter("claude", cfg)

    monkeypatch.setattr(adapter, "is_available", lambda: True)

    call_count = 0
    slept_delays = []

    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        nonlocal call_count
        call_count += 1
        return 1, "401 Unauthorized: Invalid credentials"

    async def mock_sleep(delay):
        slept_delays.append(delay)

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    log_file = tmp_path / "harness_fail_fast.log"
    exit_code = await adapter.execute(
        prompt="Build feature",
        cwd=tmp_path,
        log_file=log_file,
    )

    assert exit_code == 1
    assert call_count == 1
    assert len(slept_delays) == 0


@pytest.mark.asyncio
async def test_harness_retry_exhaustion(tmp_path: Path, monkeypatch):
    """
    AC 4: When transient error persists beyond max_retries, logs terminal error and returns failure exit code.
    """
    import asyncio
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.harness import AsyncHarnessAdapter

    retry_cfg = HarnessRetryConfig(
        max_retries=3,
        initial_delay_seconds=1.0,
        backoff_factor=2.0,
        max_delay_seconds=10.0,
    )
    cfg = HarnessConfig(binary="antigravity", args=["-p", "{prompt}"], retry=retry_cfg)
    adapter = AsyncHarnessAdapter("antigravity", cfg)

    monkeypatch.setattr(adapter, "is_available", lambda: True)

    call_count = 0
    slept_delays = []

    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        nonlocal call_count
        call_count += 1
        return 2, "429 RESOURCE_EXHAUSTED: Rate limit exceeded"

    async def mock_sleep(delay):
        slept_delays.append(delay)

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    log_file = tmp_path / "harness_exhausted.log"
    exit_code = await adapter.execute(
        prompt="Build feature",
        cwd=tmp_path,
        log_file=log_file,
        console_prefix="[test:antigravity]",
    )

    assert exit_code == 2
    # 1 initial attempt + 3 retries = 4 total executions
    assert call_count == 4
    assert len(slept_delays) == 3

    log_content = log_file.read_text(encoding="utf-8")
    assert "[ERROR] [harness:antigravity] Retries exhausted (3/3). Upstream service unavailable." in log_content
    assert "Retrying attempt 1/3 in" in log_content
    assert "Retrying attempt 2/3 in" in log_content
    assert "Retrying attempt 3/3 in" in log_content


@pytest.mark.asyncio
async def test_harness_retry_transition_to_non_retryable(tmp_path: Path, monkeypatch):
    """
    If transient error is followed by a non-retryable error on attempt 2, aborts immediately.
    """
    import asyncio
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.harness import AsyncHarnessAdapter

    retry_cfg = HarnessRetryConfig(max_retries=3)
    cfg = HarnessConfig(binary="devin", args=["-p", "{prompt}"], retry=retry_cfg)
    adapter = AsyncHarnessAdapter("devin", cfg)

    monkeypatch.setattr(adapter, "is_available", lambda: True)

    call_count = 0

    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return 1, "502 Bad Gateway"
        return 1, "400 Bad Request: Syntax error in parameters"

    async def mock_sleep(d):
        pass

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    log_file = tmp_path / "harness_transition.log"
    exit_code = await adapter.execute("prompt", tmp_path, log_file)

    assert exit_code == 1
    assert call_count == 2


def test_classify_error():
    from orchestrator.harness import classify_error

    assert classify_error("", is_timeout=True) == "sla_violation"
    assert classify_error("503 UNAVAILABLE service dropout") == "http_503"
    assert classify_error("HTTP 429 Too Many Requests (Rate limit hit)") == "http_429"
    assert classify_error("502 Bad Gateway") == "http_502"
    assert classify_error("504 Gateway Timeout") == "http_504"
    assert classify_error("Task timed out after 30 minutes") == "sla_violation"
    assert classify_error("Connection reset by peer", error_snippet="connection reset") == "connection_reset"
    assert classify_error("Unknown failure") == "execution_failure"


@pytest.mark.asyncio
async def test_scenario_harness_records_anomaly_on_transient_failure(tmp_path: Path, monkeypatch):
    """
    Scenario: Harness records anomaly on transient failure
    Given `orchestrator/harness.py` AsyncHarnessAdapter executes a node harness call
    When the call fails or is retried due to a transient upstream error (503, 429) or an SLA violation
    Then `StateManager.record_anomaly_event(project_name, node_name, error_type, error_message, issue_number)` is called
     with error_type reflecting the failure classification (e.g. "http_503", "http_429", "sla_violation")
    """
    import asyncio
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.db import StateManager
    from orchestrator.harness import AsyncHarnessAdapter

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    retry_cfg = HarnessRetryConfig(max_retries=2, initial_delay_seconds=1.0)
    cfg = HarnessConfig(binary="antigravity", args=["-p", "{prompt}"], retry=retry_cfg)
    adapter = AsyncHarnessAdapter(
        name="antigravity",
        config=cfg,
        state_manager=state_manager,
        project_name="my-project",
        node_name="devtest",
        issue_number=37,
    )

    monkeypatch.setattr(adapter, "is_available", lambda: True)

    call_count = 0
    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return 1, "503 UNAVAILABLE - Gateway Dropout"
        return 0, "Success on retry"

    async def mock_sleep(d):
        pass

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    log_file = tmp_path / "harness.log"
    exit_code = await adapter.execute(
        prompt="Execute task",
        cwd=tmp_path,
        log_file=log_file,
    )

    assert exit_code == 0
    assert call_count == 2

    # Verify anomaly recorded in StateManager
    anomalies = await state_manager.get_recent_anomalies("my-project")
    assert len(anomalies) == 1
    assert anomalies[0]["node_name"] == "devtest"
    assert anomalies[0]["error_type"] == "http_503"
    assert anomalies[0]["issue_number"] == 37
    assert "503" in anomalies[0]["error_message"]


@pytest.mark.asyncio
async def test_scenario_harness_records_anomaly_on_timeout_sla_violation(tmp_path: Path, monkeypatch):
    """
    Asserts timeout (returncode 124) records 'sla_violation' anomaly event.
    """
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.db import StateManager
    from orchestrator.harness import AsyncHarnessAdapter

    db_path = tmp_path / "state.db"
    state_manager = StateManager(db_path)
    await state_manager.init_db()

    retry_cfg = HarnessRetryConfig(max_retries=0)
    cfg = HarnessConfig(binary="claude", args=["-p", "{prompt}"], retry=retry_cfg)
    adapter = AsyncHarnessAdapter(
        name="claude",
        config=cfg,
        state_manager=state_manager,
        project_name="proj-timeout",
        node_name="architect",
        issue_number=100,
    )

    monkeypatch.setattr(adapter, "is_available", lambda: True)

    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        return 124, "Process timed out after 30 minutes"

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)

    log_file = tmp_path / "timeout.log"
    exit_code = await adapter.execute(
        prompt="Execute task",
        cwd=tmp_path,
        log_file=log_file,
    )

    assert exit_code == 124

    anomalies = await state_manager.get_recent_anomalies("proj-timeout")
    assert len(anomalies) == 1
    assert anomalies[0]["error_type"] == "sla_violation"
    assert anomalies[0]["issue_number"] == 100
    assert anomalies[0]["node_name"] == "architect"


@pytest.mark.asyncio
async def test_scenario_non_blocking_harness_anomaly_recording_failure(tmp_path: Path, monkeypatch):
    """
    Scenario: Non-blocking, best-effort recording
    Given the SQLite write for anomaly_events fails unexpectedly
    When the harness continues its normal flow
    Then the failure must not crash the node execution (log and continue)
    """
    import asyncio
    from unittest.mock import AsyncMock
    from orchestrator.config import HarnessConfig, HarnessRetryConfig
    from orchestrator.harness import AsyncHarnessAdapter

    retry_cfg = HarnessRetryConfig(max_retries=1, initial_delay_seconds=1.0)
    cfg = HarnessConfig(binary="claude", args=["-p", "{prompt}"], retry=retry_cfg)

    # StateManager mock that fails on record_anomaly_event
    failing_sm = AsyncMock()
    failing_sm.record_anomaly_event.side_effect = RuntimeError("Database Locked / Disk Full")

    adapter = AsyncHarnessAdapter(
        name="claude",
        config=cfg,
        state_manager=failing_sm,
        project_name="fail-db",
        node_name="devtest",
        issue_number=1,
    )
    monkeypatch.setattr(adapter, "is_available", lambda: True)

    call_count = 0
    async def mock_execute_once(cmd, cwd, env, log_file, console_prefix=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return 1, "429 RESOURCE_EXHAUSTED"
        return 0, "Success"

    async def mock_sleep(d):
        pass

    monkeypatch.setattr(adapter, "_execute_once", mock_execute_once)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    log_file = tmp_path / "resilient.log"
    exit_code = await adapter.execute("prompt", tmp_path, log_file)

    # Must complete successfully despite SQLite exception
    assert exit_code == 0
    assert call_count == 2



