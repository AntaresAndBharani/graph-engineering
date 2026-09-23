from __future__ import annotations

import asyncio
from collections import deque
import logging
import os
from pathlib import Path
import random
import re
import shutil
from typing import Callable, Dict, List, Optional
import psutil

from rich.console import Console

from orchestrator.config import HarnessConfig, HarnessRetryConfig
from orchestrator.db import StateManager
from orchestrator.logging import strip_ansi
from orchestrator.quota import extract_token_usage

_logger = logging.getLogger(__name__)
_console = Console()


def classify_error(output: str, is_timeout: bool = False, error_snippet: Optional[str] = None) -> str:
    """
    Classifies failure / anomaly output into a standardized error type string
    (e.g., 'http_503', 'http_429', 'http_502', 'http_504', 'sla_violation').
    """
    if is_timeout:
        return "sla_violation"

    lower_output = (output or "").lower()
    lower_snippet = (error_snippet or "").lower()

    if "503" in lower_output or "503" in lower_snippet or "unavailable" in lower_output:
        return "http_503"
    if "429" in lower_output or "429" in lower_snippet or "resource_exhausted" in lower_output or "rate limit" in lower_output or "quota" in lower_output:
        return "http_429"
    if "502" in lower_output or "502" in lower_snippet or "bad gateway" in lower_output:
        return "http_502"
    if "504" in lower_output or "504" in lower_snippet or "gateway timeout" in lower_output:
        return "http_504"
    if "sla" in lower_output or "sla_violation" in lower_output or "timed out" in lower_output:
        return "sla_violation"

    if error_snippet:
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", error_snippet.strip().lower()).strip("_")
        if cleaned:
            return cleaned

    return "execution_failure"


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


PREMATURE_EXIT_CODE = 75  # EX_TEMPFAIL: harness exited before finishing, resumes exhausted

PREMATURE_EXIT_RESUME_NOTICE = (
    "\n\nRESUME NOTICE (attempt {attempt}/{max_attempts}):\n"
    "A previous attempt at this exact task was terminated because the session went idle while "
    "background commands were still running, which kills them in non-interactive batch mode.\n"
    "- The working tree may already contain partial work from that attempt: inspect `git status` "
    "and `git diff` first and continue from there instead of starting over.\n"
    "- Run EVERY command (tests, linters, git, gh) in the FOREGROUND and block until it finishes. "
    "Never start a command in the background and then wait for it.\n"
)


def find_premature_exit_marker(output: str, patterns: list[str]) -> Optional[str]:
    """Returns the matched text if output shows the CLI exited while background tasks were still running."""
    if not output or not patterns:
        return None
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(0)
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
    _tui_mode: bool = False
    _stream_listeners: set[
        Callable[[Optional[str], Optional[str], str], None]
        | Callable[[Optional[str], str], None]
        | Callable[[str], None]
    ] = set()

    @classmethod
    def is_tui_mode(cls) -> bool:
        """Returns True if TUI application mode is active, suppressing direct stdout console prints."""
        return cls._tui_mode

    @classmethod
    def set_tui_mode(cls, enabled: bool) -> None:
        """Enables or disables TUI mode, muting module-level console prints to avoid stdout corruption."""
        cls._tui_mode = enabled
        _console.quiet = enabled

    @classmethod
    def register_stream_listener(
        cls,
        listener: Callable[[Optional[str], Optional[str], str], None]
        | Callable[[Optional[str], str], None]
        | Callable[[str], None],
    ) -> None:
        """Registers a callback to receive live real-time output stream lines."""
        cls._stream_listeners.add(listener)

    @classmethod
    def unregister_stream_listener(
        cls,
        listener: Callable[[Optional[str], Optional[str], str], None]
        | Callable[[Optional[str], str], None]
        | Callable[[str], None],
    ) -> None:
        """Unregisters a stream listener callback."""
        cls._stream_listeners.discard(listener)

    def __init__(
        self,
        name: str,
        config: HarnessConfig,
        state_manager: Optional[StateManager] = None,
        project_name: Optional[str] = None,
        node_name: Optional[str] = None,
        issue_number: Optional[int] = None,
    ):
        self.name = name
        self.config = config
        self.state_manager = state_manager
        self.project_name = project_name
        self.node_name = node_name
        self.issue_number = issue_number
        self.retry_config = config.retry
        self.binary = config.binary
        self.args_template = config.args
        self.model_flag = config.model_flag
        self.effort_flag = config.effort_flag
        self.timeout_seconds = config.timeout_minutes * 60
        self.retry_limit = config.retry_on_failure
        self.env_vars = config.env_vars

    @classmethod
    def has_active_processes(cls) -> bool:
        """Returns True if any harness subprocess is currently executing."""
        return len(cls._active_processes) > 0

    @classmethod
    def get_active_process_count(cls) -> int:
        """Returns the count of currently executing harness subprocesses."""
        return len(cls._active_processes)

    @classmethod
    async def wait_all_active(cls, timeout: float = 30.0) -> bool:
        """
        Asynchronously waits for all active harness subprocesses to finish.
        Returns True if all finished within timeout, False if timeout elapsed.
        """
        loop = asyncio.get_event_loop()
        start = loop.time()
        while len(cls._active_processes) > 0:
            elapsed = loop.time() - start
            if elapsed >= timeout:
                return False
            await asyncio.sleep(0.5)
        return True

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
        project_name: Optional[str] = None,
        node_name: Optional[str] = None,
    ) -> tuple[int, str]:
        """
        Runs a single subprocess attempt, streaming stdout/stderr to disk and console,
        and returns (exit_code, captured_output).
        """
        process: Optional[asyncio.subprocess.Process] = None
        captured_chunks: deque[str] = deque(maxlen=1000)

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

                        # Live real-time console & TUI streaming
                        if console_prefix and cleaned.strip():
                            for subline in cleaned.splitlines():
                                if subline.strip():
                                    formatted_line = f"  [dim cyan]{console_prefix}[/dim cyan] [dim]{subline}[/dim]"
                                    if not AsyncHarnessAdapter.is_tui_mode():
                                        _console.print(formatted_line)
                                    for listener in list(AsyncHarnessAdapter._stream_listeners):
                                        try:
                                            try:
                                                listener(project_name, node_name, formatted_line)
                                            except TypeError:
                                                try:
                                                    listener(project_name, formatted_line)
                                                except TypeError:
                                                    listener(formatted_line)
                                        except Exception:
                                            pass
                        elif cleaned.strip():
                            for subline in cleaned.splitlines():
                                if subline.strip():
                                    for listener in list(AsyncHarnessAdapter._stream_listeners):
                                        try:
                                            try:
                                                listener(project_name, node_name, subline)
                                            except TypeError:
                                                try:
                                                    listener(project_name, subline)
                                                except TypeError:
                                                    listener(subline)
                                        except Exception:
                                            pass

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
        project_name: Optional[str] = None,
        node_name: Optional[str] = None,
        issue_number: Optional[int] = None,
        state_manager: Optional[StateManager] = None,
    ) -> int:
        """
        Executes the CLI harness asynchronously in the project directory with transient error retry engine.
        Streams stdout/stderr with ANSI stripping to log_file and live to console if console_prefix is provided.
        Gracefully kills process tree on timeout and records anomaly events in StateManager.
        """
        eff_project_name = project_name or self.project_name or "unknown"
        eff_node_name = node_name or self.node_name or self.name
        eff_issue_number = issue_number if issue_number is not None else self.issue_number
        eff_state_manager = state_manager or self.state_manager

        if not self.is_available():
            err_msg = f"[ERROR] Binary '{self.binary}' for harness '{self.name}' not found in PATH."
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{err_msg}\n")
            if console_prefix and not AsyncHarnessAdapter.is_tui_mode():
                _console.print(f"  [bold red]{console_prefix}[/bold red] {err_msg}")
            return 127

        env = self.build_env(extra_env)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        retry_cfg = self.retry_config
        max_resumes = retry_cfg.max_premature_exit_resumes
        attempt_prompt = prompt
        for resume_num in range(max_resumes + 1):
            cmd = self.build_command(attempt_prompt, model=model, effort=effort)
            returncode, captured_output = await self._execute_with_transient_retry(
                cmd=cmd,
                cwd=cwd,
                env=env,
                log_file=log_file,
                prompt=attempt_prompt,
                model=model,
                console_prefix=console_prefix,
                eff_project_name=eff_project_name,
                eff_node_name=eff_node_name,
                eff_issue_number=eff_issue_number,
                eff_state_manager=eff_state_manager,
            )
            if returncode != 0:
                return returncode

            marker = find_premature_exit_marker(captured_output, retry_cfg.premature_exit_patterns)
            if marker is None:
                return 0

            if resume_num < max_resumes:
                msg = (
                    f"[WARN] [harness:{self.name}] Premature exit detected ('{marker}'): the CLI exited while "
                    f"background tasks were still running. Resuming in the same worktree "
                    f"({resume_num + 1}/{max_resumes})..."
                )
                _logger.warning(msg)
            else:
                msg = (
                    f"[ERROR] [harness:{self.name}] Premature exit detected ('{marker}') and resumes exhausted "
                    f"({max_resumes}/{max_resumes}). Failing run with exit code {PREMATURE_EXIT_CODE}."
                )
                _logger.error(msg)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{msg}\n")
            if console_prefix and not AsyncHarnessAdapter.is_tui_mode():
                _console.print(f"  [bold yellow]{console_prefix}[/bold yellow] {msg}")
            if eff_state_manager is not None:
                try:
                    await eff_state_manager.record_anomaly_event(
                        project_name=eff_project_name,
                        node_name=eff_node_name,
                        error_type="premature_exit",
                        error_message=msg,
                        issue_number=eff_issue_number,
                    )
                except Exception as e:
                    _logger.warning("Failed to record anomaly event in state manager: %s", e)

            attempt_prompt = prompt + PREMATURE_EXIT_RESUME_NOTICE.format(
                attempt=resume_num + 2, max_attempts=max_resumes + 1
            )

        return PREMATURE_EXIT_CODE

    async def _execute_with_transient_retry(
        self,
        cmd: List[str],
        cwd: Path,
        env: Dict[str, str],
        log_file: Path,
        prompt: str,
        model: Optional[str],
        console_prefix: Optional[str],
        eff_project_name: str,
        eff_node_name: str,
        eff_issue_number: Optional[int],
        eff_state_manager: Optional[StateManager],
    ) -> tuple[int, str]:
        """Runs one harness invocation with the transient upstream-error retry engine; returns (exit_code, output)."""
        try:
            returncode, captured_output = await self._execute_once(
                cmd,
                cwd,
                env,
                log_file,
                console_prefix,
                project_name=eff_project_name,
                node_name=eff_node_name,
            )
        except TypeError:
            returncode, captured_output = await self._execute_once(
                cmd, cwd, env, log_file, console_prefix
            )
        await self._record_tokens(
            captured_output=captured_output,
            prompt=prompt,
            model=model,
            eff_state_manager=eff_state_manager,
            eff_project_name=eff_project_name,
            eff_node_name=eff_node_name,
            eff_issue_number=eff_issue_number,
        )
        if returncode == 0:
            return 0, captured_output

        if returncode == 124 and eff_state_manager is not None:
            try:
                await eff_state_manager.record_anomaly_event(
                    project_name=eff_project_name,
                    node_name=eff_node_name,
                    error_type="sla_violation",
                    error_message=f"Process timed out after {self.config.timeout_minutes} minutes (SLA violation).",
                    issue_number=eff_issue_number,
                )
            except Exception as e:
                _logger.warning("Failed to record anomaly event in state manager: %s", e)

        retry_cfg = self.retry_config
        is_transient = is_retryable_error(captured_output, retry_cfg.retryable_patterns)

        if not is_transient or retry_cfg.max_retries <= 0:
            if is_transient and eff_state_manager is not None:
                err_snip = get_matched_retryable_pattern(captured_output, retry_cfg.retryable_patterns)
                err_type = classify_error(captured_output, is_timeout=False, error_snippet=err_snip)
                try:
                    await eff_state_manager.record_anomaly_event(
                        project_name=eff_project_name,
                        node_name=eff_node_name,
                        error_type=err_type,
                        error_message=f"Transient failure ({err_snip or err_type}): {captured_output[:200].strip()}",
                        issue_number=eff_issue_number,
                    )
                except Exception as e:
                    _logger.warning("Failed to record anomaly event in state manager: %s", e)
            return returncode, captured_output

        for attempt_num in range(1, retry_cfg.max_retries + 1):
            error_snippet = get_matched_retryable_pattern(captured_output, retry_cfg.retryable_patterns) or "transient error"
            err_type = classify_error(captured_output, is_timeout=False, error_snippet=error_snippet)
            delay = calculate_backoff_delay(attempt_num - 1, retry_cfg)
            warn_msg = (
                f"[WARN] [harness:{self.name}] Transient upstream error detected ({error_snippet}). "
                f"Retrying attempt {attempt_num}/{retry_cfg.max_retries} in {delay:.1f}s (jitter applied)..."
            )
            _logger.warning(warn_msg)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{warn_msg}\n")
            if console_prefix and not AsyncHarnessAdapter.is_tui_mode():
                _console.print(f"  [bold yellow]{console_prefix}[/bold yellow] {warn_msg}")

            if eff_state_manager is not None:
                try:
                    await eff_state_manager.record_anomaly_event(
                        project_name=eff_project_name,
                        node_name=eff_node_name,
                        error_type=err_type,
                        error_message=warn_msg,
                        issue_number=eff_issue_number,
                    )
                except Exception as e:
                    _logger.warning("Failed to record anomaly event in state manager: %s", e)

            await asyncio.sleep(delay)

            try:
                returncode, captured_output = await self._execute_once(
                    cmd,
                    cwd,
                    env,
                    log_file,
                    console_prefix,
                    project_name=eff_project_name,
                    node_name=eff_node_name,
                )
            except TypeError:
                returncode, captured_output = await self._execute_once(
                    cmd, cwd, env, log_file, console_prefix
                )
            await self._record_tokens(
                captured_output=captured_output,
                prompt=prompt,
                model=model,
                eff_state_manager=eff_state_manager,
                eff_project_name=eff_project_name,
                eff_node_name=eff_node_name,
                eff_issue_number=eff_issue_number,
            )
            if returncode == 0:
                return 0, captured_output

            if returncode == 124 and eff_state_manager is not None:
                try:
                    await eff_state_manager.record_anomaly_event(
                        project_name=eff_project_name,
                        node_name=eff_node_name,
                        error_type="sla_violation",
                        error_message=f"Retry attempt {attempt_num} timed out (SLA violation).",
                        issue_number=eff_issue_number,
                    )
                except Exception as e:
                    _logger.warning("Failed to record anomaly event in state manager: %s", e)

            if not is_retryable_error(captured_output, retry_cfg.retryable_patterns):
                return returncode, captured_output

        # Retries exhausted
        err_snippet = get_matched_retryable_pattern(captured_output, retry_cfg.retryable_patterns) or "transient error"
        err_type = classify_error(captured_output, is_timeout=(returncode == 124), error_snippet=err_snippet)
        err_msg = (
            f"[ERROR] [harness:{self.name}] Retries exhausted ({retry_cfg.max_retries}/{retry_cfg.max_retries}). "
            "Upstream service unavailable."
        )
        _logger.error(err_msg)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{err_msg}\n")
        if console_prefix and not AsyncHarnessAdapter.is_tui_mode():
            _console.print(f"  [bold red]{console_prefix}[/bold red] {err_msg}")

        if eff_state_manager is not None:
            try:
                await eff_state_manager.record_anomaly_event(
                    project_name=eff_project_name,
                    node_name=eff_node_name,
                    error_type=err_type,
                    error_message=err_msg,
                    issue_number=eff_issue_number,
                )
            except Exception as e:
                _logger.warning("Failed to record anomaly event in state manager: %s", e)

        return returncode, captured_output

    async def _record_tokens(
        self,
        captured_output: str,
        prompt: str,
        model: Optional[str],
        eff_state_manager: Optional[StateManager],
        eff_project_name: str,
        eff_node_name: str,
        eff_issue_number: Optional[int],
    ) -> None:
        """Extracts and records token usage events into StateManager ledger."""
        if eff_state_manager is None:
            return
        p_tok, c_tok, tot_tok = extract_token_usage(captured_output, prompt=prompt)
        if tot_tok > 0:
            try:
                await eff_state_manager.record_token_usage_event(
                    harness_name=self.name,
                    model_name=model or getattr(self.config, "model", None) or "default",
                    project_name=eff_project_name,
                    node_name=eff_node_name,
                    issue_number=eff_issue_number,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    total_tokens=tot_tok,
                )
            except Exception as e:
                _logger.warning("Failed to record token usage event in state manager: %s", e)

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

