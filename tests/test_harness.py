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
        binary="agy",
        args=["run", "{prompt}"],
        model_flag="--model",
    )
    adapter = AsyncHarnessAdapter("antigravity", cfg)

    cmd = adapter.build_command("Run tests", model="gemini-3.7-flash-thinking", effort="high")
    # effort flag is None, so effort argument is ignored
    assert cmd == ["agy", "--model", "gemini-3.7-flash-thinking", "run", "Run tests"]


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
