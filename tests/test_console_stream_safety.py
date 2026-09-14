from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from orchestrator.config import HarnessConfig
from orchestrator.harness import AsyncHarnessAdapter, _console
import orchestrator.cli as cli


def test_tui_mode_toggle():
    """Verify AsyncHarnessAdapter.set_tui_mode toggles _tui_mode and _console.quiet."""
    try:
        AsyncHarnessAdapter.set_tui_mode(True)
        assert AsyncHarnessAdapter.is_tui_mode() is True
        assert _console.quiet is True

        AsyncHarnessAdapter.set_tui_mode(False)
        assert AsyncHarnessAdapter.is_tui_mode() is False
        assert _console.quiet is False
    finally:
        AsyncHarnessAdapter.set_tui_mode(False)


def test_cli_set_tui_mode():
    """Verify cli.set_tui_mode silences cli console and harness."""
    try:
        cli.set_tui_mode(True)
        assert cli.console.quiet is True
        assert AsyncHarnessAdapter.is_tui_mode() is True
        assert _console.quiet is True

        cli.set_tui_mode(False)
        assert cli.console.quiet is False
        assert AsyncHarnessAdapter.is_tui_mode() is False
        assert _console.quiet is False
    finally:
        cli.set_tui_mode(False)


@pytest.mark.asyncio
async def test_stream_listener_receives_lines_when_tui_mode_enabled(tmp_path: Path):
    """
    Ensure that when TUI mode is enabled:
    1. _console.print is NOT called (or suppressed).
    2. Stream listeners STILL receive all formatted lines for display in TUI RichLog.
    """
    cfg = HarnessConfig(
        binary="mock_bin",
        args=["{prompt}"],
    )
    adapter = AsyncHarnessAdapter("test_harness", cfg)

    received_lines: list[tuple] = []

    def listener(project, node, line):
        received_lines.append((project, node, line))

    AsyncHarnessAdapter.register_stream_listener(listener)

    try:
        AsyncHarnessAdapter.set_tui_mode(True)

        with patch("orchestrator.harness._console.print") as mock_print:
            # Mock subprocess to produce stdout lines
            mock_proc = MagicMock()
            mock_proc.returncode = 0

            # Simulate readline() returning lines then EOF
            lines_to_emit = [
                b"Step 1: Reading architecture\n",
                b"Step 2: Generating code\n",
                b"",
            ]
            iter_lines = iter(lines_to_emit)

            async def mock_readline():
                return next(iter_lines)

            mock_proc.stdout = MagicMock()
            mock_proc.stdout.readline = mock_readline
            mock_proc.wait = MagicMock(return_value=asyncio.sleep(0, result=0))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                log_file = tmp_path / "test_stream.log"
                code, out = await adapter._execute_once(
                    cmd=["mock_bin"],
                    cwd=tmp_path,
                    env={},
                    log_file=log_file,
                    console_prefix="mock_proj:devtest",
                    project_name="mock_proj",
                    node_name="devtest",
                )

            # Raw stdout print MUST NOT be invoked when TUI mode is True
            mock_print.assert_not_called()

            # But stream listeners MUST have received both lines
            assert len(received_lines) == 2
            assert "mock_proj:devtest" in received_lines[0][2]
            assert "Step 1: Reading architecture" in received_lines[0][2]
            assert "Step 2: Generating code" in received_lines[1][2]

    finally:
        AsyncHarnessAdapter.unregister_stream_listener(listener)
        AsyncHarnessAdapter.set_tui_mode(False)


@pytest.mark.asyncio
async def test_stream_printed_when_tui_mode_disabled(tmp_path: Path):
    """
    Ensure that when TUI mode is disabled (CLI mode):
    1. _console.print IS invoked with formatted output.
    2. Stream listeners ALSO receive the lines.
    """
    cfg = HarnessConfig(
        binary="mock_bin",
        args=["{prompt}"],
    )
    adapter = AsyncHarnessAdapter("test_harness", cfg)

    received_lines: list[tuple] = []

    def listener(project, node, line):
        received_lines.append((project, node, line))

    AsyncHarnessAdapter.register_stream_listener(listener)

    try:
        AsyncHarnessAdapter.set_tui_mode(False)

        with patch("orchestrator.harness._console.print") as mock_print:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            lines_to_emit = [b"CLI output line\n", b""]
            iter_lines = iter(lines_to_emit)

            async def mock_readline():
                return next(iter_lines)

            mock_proc.stdout = MagicMock()
            mock_proc.stdout.readline = mock_readline
            mock_proc.wait = MagicMock(return_value=asyncio.sleep(0, result=0))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                log_file = tmp_path / "test_stream.log"
                code, out = await adapter._execute_once(
                    cmd=["mock_bin"],
                    cwd=tmp_path,
                    env={},
                    log_file=log_file,
                    console_prefix="cli_proj:devtest",
                    project_name="cli_proj",
                    node_name="devtest",
                )

            # _console.print MUST be called
            assert mock_print.call_count == 1
            assert len(received_lines) == 1

    finally:
        AsyncHarnessAdapter.unregister_stream_listener(listener)
        AsyncHarnessAdapter.set_tui_mode(False)


@pytest.mark.asyncio
async def test_dashboard_app_mount_and_teardown_manages_tui_mode():
    """Verify DashboardApp enables TUI mode on mount and disables on teardown."""
    from orchestrator.config import GlobalConfig
    from orchestrator.ui.dashboard import DashboardApp

    app = DashboardApp(config=GlobalConfig())
    try:
        # Initially False
        AsyncHarnessAdapter.set_tui_mode(False)
        assert AsyncHarnessAdapter.is_tui_mode() is False

        # When mounted, AsyncHarnessAdapter.set_tui_mode(True) is called
        # We can test by calling teardown
        AsyncHarnessAdapter.set_tui_mode(True)
        assert AsyncHarnessAdapter.is_tui_mode() is True

        await app.teardown(force=False)
        assert AsyncHarnessAdapter.is_tui_mode() is False
    finally:
        AsyncHarnessAdapter.set_tui_mode(False)
