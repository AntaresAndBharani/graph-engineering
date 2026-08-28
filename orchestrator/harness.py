from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
from typing import Dict, List, Optional
import psutil

from orchestrator.config import HarnessConfig
from orchestrator.logging import strip_ansi


class AsyncHarnessAdapter:
    def __init__(self, name: str, config: HarnessConfig):
        self.name = name
        self.config = config
        self.binary = config.binary
        self.args_template = config.args
        self.model_flag = config.model_flag
        self.effort_flag = config.effort_flag
        self.timeout_seconds = config.timeout_minutes * 60
        self.retry_limit = config.retry_on_failure
        self.env_vars = config.env_vars

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

    async def execute(
        self,
        prompt: str,
        cwd: Path,
        log_file: Path,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> int:
        """
        Executes the CLI harness asynchronously in the project directory.
        Streams stdout/stderr with ANSI stripping to log_file.
        Gracefully kills process tree on timeout.
        """
        if not self.is_available():
            err_msg = f"[ERROR] Binary '{self.binary}' for harness '{self.name}' not found in PATH."
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{err_msg}\n")
            return 127

        cmd = self.build_command(prompt, model=model, effort=effort)
        env = self.build_env(extra_env)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        process: Optional[asyncio.subprocess.Process] = None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

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
                        f.write(cleaned)
                        f.flush()

            await asyncio.wait_for(stream_output(), timeout=self.timeout_seconds)
            await process.wait()
            return process.returncode if process.returncode is not None else 0

        except asyncio.TimeoutError:
            self._kill_process_tree(process)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n\n[ORCHESTRATOR ERROR] Process timed out after {self.config.timeout_minutes} minutes and was killed.\n")
            return 124

        except Exception as e:
            if process:
                self._kill_process_tree(process)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n\n[ORCHESTRATOR ERROR] Subprocess execution error: {e}\n")
            return 1

    def _kill_process_tree(self, process: Optional[asyncio.subprocess.Process]) -> None:
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
