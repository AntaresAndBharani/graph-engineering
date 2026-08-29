from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import random
import shutil
from typing import Dict, List, Optional
import psutil

from rich.console import Console

from orchestrator.config import HarnessConfig, HarnessRetryConfig
from orchestrator.logging import strip_ansi

_logger = logging.getLogger(__name__)
_console = Console()


def is_retryable_error(output: str, retryable_patterns: list[str]) -> bool:
    """Checks if output contains any configured transient/retryable error pattern."""
    if not output or not retryable_patterns:
        return False
    lower_output = output.lower()
    return any(pattern.lower() in lower_output for pattern in retryable_patterns)


def get_matched_retryable_pattern(output: str, retryable_patterns: list[str]) -> Optional[str]:
    """Returns the first matched transient error pattern found in output."""
    if not output or not retryable_patterns:
        return None
    lower_output = output.lower()
    for pattern in retryable_patterns:
        if pattern.lower() in lower_output:
            return pattern
    return None


def calculate_backoff_delay(attempt: int, config: HarnessRetryConfig) -> float:
    """
    Calculates exponential backoff delay with randomized jitter:
    delay = min(max_delay, initial_delay * (backoff_factor ** attempt)) * (0.8 + 0.4 * random())
    """
    base_delay = min(
        config.max_delay_seconds,
        config.initial_delay_seconds * (config.backoff_factor ** attempt),
    )
    jitter = 0.8 + 0.4 * random.random()
    return base_delay * jitter


class AsyncHarnessAdapter:
    _active_processes: set[asyncio.subprocess.Process] = set()

    def __init__(self, name: str, config: HarnessConfig):
        self.name = name
        self.config = config
        self.retry_config = config.retry
        self.binary = config.binary
        self.args_template = config.args
        self.model_flag = config.model_flag
        self.effort_flag = config.effort_flag
        self.timeout_seconds = config.timeout_minutes * 60
        self.retry_limit = config.retry_on_failure
        self.env_vars = config.env_vars

    @classmethod
    def terminate_all_active(cls) -> int:
        """Terminates all currently running harness subprocesses and their process trees."""
        count = len(cls._active_processes)
        for proc in list(cls._active_processes):
            cls._kill_process_tree(proc)
        cls._active_processes.clear()
        return count

    def is_available(self) -> bool:
        """Checks if the CLI executable is available in PATH."""
        return shutil.which(self.binary) is not None

    def build_command(
        self,
        prompt: str,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> List[str]:
        """Constructs the command argument list."""
        cmd = [self.binary]
        if model and self.model_flag:
            cmd.extend([self.model_flag, model])
        if effort and self.effort_flag:
            cmd.extend([self.effort_flag, str(effort)])

        for arg in self.args_template:
            cmd.append(arg.replace("{prompt}", prompt))
        return cmd

    def build_env(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Builds child execution environment.
        Preserves user environment so OAuth session stores in ~/.claude, ~/.gemini, etc. work seamlessly.
        Injects NO_COLOR=1 and TERM=dumb to prevent ANSI code bloat.
        """
        env = dict(os.environ)
        # Suppress ANSI styling from CLIs
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"
        env["FORCE_COLOR"] = "0"

        # Inject harness-specific environment variables if configured
        if self.env_vars:
            env.update(self.env_vars)

        if extra_env:
            env.update(extra_env)

        return env

    async def _execute_once(
        self,
        cmd: List[str],
        cwd: Path,
        env: Dict[str, str],
        log_file: Path,
        console_prefix: Optional[str] = None,
    ) -> tuple[int, str]:
        """
        Runs a single subprocess attempt, streaming stdout/stderr to disk and console,
        and returns (exit_code, captured_output).
        """
        process: Optional[asyncio.subprocess.Process] = None
        captured_chunks: list[str] = []

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            AsyncHarnessAdapter._active_processes.add(process)

            async def stream_output():
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"=== EXECUTION STARTED: {' '.join(cmd)} ===\n")
                    f.write(f"=== CWD: {cwd} ===\n\n")
                    f.flush()

                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        decoded = line.decode("utf-8", errors="replace")
                        cleaned = strip_ansi(decoded)
                        captured_chunks.append(cleaned)
                        f.write(cleaned)
                        f.flush()

                        # Live real-time console streaming
                        if console_prefix and cleaned.strip():
                            for subline in cleaned.splitlines():
                                if subline.strip():
                                    _console.print(f"  [dim cyan]{console_prefix}[/dim cyan] [dim]{subline}[/dim]")

            await asyncio.wait_for(stream_output(), timeout=self.timeout_seconds)
            await process.wait()
            returncode = process.returncode if process.returncode is not None else 0
            return returncode, "".join(captured_chunks)

        except asyncio.TimeoutError:
            self._kill_process_tree(process)
            timeout_msg = f"\n\n[ORCHESTRATOR ERROR] Process timed out after {self.config.timeout_minutes} minutes and was killed.\n"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(timeout_msg)
            captured_chunks.append(timeout_msg)
            return 124, "".join(captured_chunks)

        except Exception as e:
            if process:
                self._kill_process_tree(process)
            error_msg = f"\n\n[ORCHESTRATOR ERROR] Subprocess execution error: {e}\n"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(error_msg)
            captured_chunks.append(error_msg)
            return 1, "".join(captured_chunks)
        finally:
            if process:
                AsyncHarnessAdapter._active_processes.discard(process)

    async def execute(
        self,
        prompt: str,
        cwd: Path,
        log_file: Path,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
        console_prefix: Optional[str] = None,
    ) -> int:
        """
        Executes the CLI harness asynchronously in the project directory with transient error retry engine.
        Streams stdout/stderr with ANSI stripping to log_file and live to console if console_prefix is provided.
        Gracefully kills process tree on timeout.
        """
        if not self.is_available():
            err_msg = f"[ERROR] Binary '{self.binary}' for harness '{self.name}' not found in PATH."
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{err_msg}\n")
            if console_prefix:
                _console.print(f"  [bold red]{console_prefix}[/bold red] {err_msg}")
            return 127

        cmd = self.build_command(prompt, model=model, effort=effort)
        env = self.build_env(extra_env)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        returncode, captured_output = await self._execute_once(cmd, cwd, env, log_file, console_prefix)
        if returncode == 0:
            return 0

        retry_cfg = self.retry_config
        if not is_retryable_error(captured_output, retry_cfg.retryable_patterns) or retry_cfg.max_retries <= 0:
            return returncode

        for attempt_num in range(1, retry_cfg.max_retries + 1):
            error_snippet = get_matched_retryable_pattern(captured_output, retry_cfg.retryable_patterns) or "transient error"
            delay = calculate_backoff_delay(attempt_num - 1, retry_cfg)
            warn_msg = (
                f"[WARN] [harness:{self.name}] Transient upstream error detected ({error_snippet}). "
                f"Retrying attempt {attempt_num}/{retry_cfg.max_retries} in {delay:.1f}s (jitter applied)..."
            )
            _logger.warning(warn_msg)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{warn_msg}\n")
            if console_prefix:
                _console.print(f"  [bold yellow]{console_prefix}[/bold yellow] {warn_msg}")

            await asyncio.sleep(delay)

            returncode, captured_output = await self._execute_once(cmd, cwd, env, log_file, console_prefix)
            if returncode == 0:
                return 0

            if not is_retryable_error(captured_output, retry_cfg.retryable_patterns):
                return returncode

        # Retries exhausted
        err_msg = (
            f"[ERROR] [harness:{self.name}] Retries exhausted ({retry_cfg.max_retries}/{retry_cfg.max_retries}). "
            "Upstream service unavailable."
        )
        _logger.error(err_msg)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{err_msg}\n")
        if console_prefix:
            _console.print(f"  [bold red]{console_prefix}[/bold red] {err_msg}")

        return returncode

    @staticmethod
    def _kill_process_tree(process: Optional[asyncio.subprocess.Process]) -> None:
        """Recursively terminates a process and all its child processes to avoid zombies."""
        if not process or process.returncode is not None:
            return

        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

