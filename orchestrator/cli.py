from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
import sys
import time
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live

_logger = logging.getLogger(__name__)

from orchestrator import __version__
from orchestrator.config import GlobalConfig, load_config
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.housekeeping import sync_all_projects_labels, sync_repository_labels
from orchestrator.logging import setup_logger
from orchestrator.nodes.architect import run_architect_node
from orchestrator.nodes.bau import run_bau_node
from orchestrator.nodes.devtest import run_devtest_node
from orchestrator.nodes.reviewer import run_reviewer_node
from orchestrator.nodes.supervisor import (
    POEvaluationResult,
    evaluate_supervisor_issue,
    run_supervisor_node,
)
from orchestrator import poller
from orchestrator.reloader import SourceWatcher, hot_reload_runtime

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

app = typer.Typer(
    name="orchestrator",
    help="Decoupled, Agnostic Multi-Agent CLI Orchestrator for Engineering Pipelines.",
    add_completion=False,
    no_args_is_help=True,
)
supervisor_app = typer.Typer(
    name="supervisor",
    help="PO-proxy Supervisor inspection, status, and evaluation commands.",
    no_args_is_help=True,
)
app.add_typer(supervisor_app, name="supervisor")
console = Console(legacy_windows=False)


def version_callback(value: bool):
    if value:
        console.print(f"[bold cyan]Graph Orchestrator[/bold cyan] version [green]{__version__}[/green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show orchestrator version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
):
    """Decoupled, Agnostic Multi-Agent CLI Orchestrator."""
    pass


@app.command("run")
def run_command(
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target a specific registered project by name.",
    ),
    node_name: Optional[str] = typer.Option(
        None,
        "--node",
        "-n",
        help="Run only a specific node (supervisor, architect, devtest).",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Executes a single evaluation pass across registered projects."""
    asyncio.run(_run_single_pass(project_name, node_name, config_path))


async def run_project_cycle(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    node_name: Optional[str] = None,
    silent_idle: bool = False,
) -> bool:
    """
    Executes a single sequential pass across all enabled nodes for a project.
    Returns True if a development pipeline node (Architect, DevTest, Reviewer, BAU) executed active work,
    requiring an immediate follow-up pass.
    Returns False if all development nodes were idle (even if the supervisor completed a watchdog audit).
    """
    if await state_manager.is_stop_requested():
        return False

    if await state_manager.is_project_paused(project.name):
        if not silent_idle:
            console.print(f"  [{project.name}] [bold yellow]⏸️ Project is paused by user. Skipping.[/bold yellow]")
        return False

    pipeline_work_done = False
    prefix = f"[{project.name}]"

    # 0. Zero-Token Polling Sweep: Background sync of SDLC items memory layer
    try:
        await poller.poll_project_sdlc_items(project, state_manager)
    except Exception as e:
        _logger.warning("[%s] Background SDLC items polling failed: %s", project.name, e)

    # 1. Supervisor Node (Periodic Watchdog Audit - does not trigger 1s tight loop)
    if node_name is None or node_name == "supervisor":
        if await state_manager.is_stop_requested():
            return pipeline_work_done
        force_sup = node_name == "supervisor"
        ran, msg = await run_supervisor_node(project, config, state_manager, force=force_sup)
        if ran:
            console.print(f"  {prefix} [bold green]Supervisor:[/bold green] {msg}")
        elif not silent_idle:
            console.print(f"  {prefix} [dim]Supervisor: {msg}[/dim]")

    # 2. Architect Node (Active development work)
    if node_name is None or node_name == "architect":
        if await state_manager.is_stop_requested():
            return pipeline_work_done
        ran, msg = await run_architect_node(project, config, state_manager)
        if ran:
            pipeline_work_done = True
            console.print(f"  {prefix} [bold green]Architect:[/bold green] {msg}")
        elif not silent_idle:
            console.print(f"  {prefix} [dim]Architect: {msg}[/dim]")

    # 3. DevTest Node (Active development work)
    if node_name is None or node_name == "devtest":
        if await state_manager.is_stop_requested():
            return pipeline_work_done
        ran, msg = await run_devtest_node(project, config, state_manager)
        if ran:
            pipeline_work_done = True
            console.print(f"  {prefix} [bold green]DevTest:[/bold green] {msg}")
        elif not silent_idle:
            console.print(f"  {prefix} [dim]DevTest: {msg}[/dim]")

    # 4. Reviewer / Gatekeeper Node (Active development work)
    if node_name is None or node_name in ("reviewer", "review"):
        if await state_manager.is_stop_requested():
            return pipeline_work_done
        ran, msg = await run_reviewer_node(project, config, state_manager)
        if ran:
            pipeline_work_done = True
            console.print(f"  {prefix} [bold green]Reviewer:[/bold green] {msg}")
        elif not silent_idle:
            console.print(f"  {prefix} [dim]Reviewer: {msg}[/dim]")

    # 5. BAU Maintenance Node (Daily tech-debt & enhancement consolidation)
    if node_name is None or node_name in ("bau", "maintenance"):
        if await state_manager.is_stop_requested():
            return pipeline_work_done
        force_bau = node_name in ("bau", "maintenance")
        ran, msg = await run_bau_node(project, config, state_manager, force=force_bau)
        if ran:
            pipeline_work_done = True
            console.print(f"  {prefix} [bold green]BAU:[/bold green] {msg}")
        elif not silent_idle:
            console.print(f"  {prefix} [dim]BAU: {msg}[/dim]")

    return pipeline_work_done


async def _run_single_pass(
    project_name: Optional[str],
    node_name: Optional[str],
    config_path: Optional[Path],
) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    logger = setup_logger(config.settings.resolved_log_dir, config.settings.log_level)
    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    # Clean any orphaned or expired locks and clear stale stop requests for manual one-shot runs
    await state_manager.clear_stop_request()
    await state_manager.cleanup_orphaned_running_jobs()
    cleaned = await state_manager.cleanup_expired_locks()
    if cleaned > 0:
        logger.info(f"Cleaned {cleaned} expired lock(s) from previous runs.")

    targets = [p for p in config.projects if p.enabled]
    if project_name:
        targets = [p for p in targets if p.name == project_name]
        if not targets:
            console.print(f"[bold yellow]Warning:[/bold yellow] No enabled project found matching '{project_name}'.")
            raise typer.Exit(code=1)

    if not targets:
        console.print("[dim]No enabled projects configured. Run 'orchestrator list' to view registered projects.[/dim]")
        return

    # Execute target projects in parallel
    tasks = [
        run_project_cycle(p, config, state_manager, node_name=node_name)
        for p in targets
    ]
    await asyncio.gather(*tasks)


async def _project_worker_loop(
    project: ProjectConfig,
    config: GlobalConfig,
    state_manager: StateManager,
    interval: int,
    config_path: Optional[Path] = None,
    watcher: Optional[SourceWatcher] = None,
) -> None:
    """
    Independent worker loop for a single project.
    Runs sequentially within this project.
    If work is performed in a pass, immediately starts the next pass (with a 1s debounce).
    Automatically checks for file changes or manual reload signals before each pass.
    Only sleeps for `interval` when all nodes in this project are idle.
    """
    while True:
        try:
            if await state_manager.is_stop_requested():
                console.print(f"  [yellow]🛑 [{project.name}]: Safe stop active. Halting worker loop...[/yellow]")
                break

            # In-Memory Hot-Reload Check (Manual Signal or Auto-File Watcher)
            reload_requested = await state_manager.is_reload_requested()
            has_changed = False
            modified_files: List[str] = []
            if watcher:
                has_changed, modified_files = watcher.check_for_changes()

            if reload_requested or has_changed:
                if reload_requested:
                    console.print(f"\n  [bold cyan]🔄 [Manual Reload Requested][/bold cyan] 'orchestrator reload' signal detected.")
                if has_changed:
                    console.print(f"\n  [bold yellow]📝 [File Modification Detected][/bold yellow] {', '.join(modified_files)}")

                console.print("  [dim]⚙️ Reloading in-memory configuration and runtime Python modules...[/dim]")
                try:
                    config = hot_reload_runtime(config_path)
                    await state_manager.clear_reload_request()
                    matching = [p for p in config.projects if p.name == project.name]
                    if matching:
                        project = matching[0]
                    console.print(
                        f"  [bold green]✓ In-Memory Hot-Reload Complete![/bold green] "
                        f"[dim](Project: {project.name} | Poll: {config.settings.poll_interval_seconds}s)[/dim]"
                    )
                except Exception as re_err:
                    console.print(f"  [bold red]Hot-Reload Error:[/bold red] {re_err}")

            await state_manager.cleanup_expired_locks()
            work_done = await run_project_cycle(project, config, state_manager, silent_idle=False)

            if await state_manager.is_stop_requested():
                console.print(f"  [yellow]🛑 [{project.name}]: Safe stop active. Halting worker loop...[/yellow]")
                break

            if work_done:
                console.print(f"[bold cyan]⚡ [{project.name}]: Active work completed. Starting immediate follow-up pass...[/bold cyan]")
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            console.print(f"[bold red]Worker Error on [{project.name}]:[/bold red] {e}")
            await asyncio.sleep(interval)


@app.command("watch")
def watch_command(
    interval: Optional[int] = typer.Option(
        None,
        "--interval",
        "-i",
        help="Polling interval in seconds (overrides config setting).",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
    dashboard: bool = typer.Option(
        True,
        "--dashboard/--no-dashboard",
        help="Enable or disable the interactive Textual TUI dashboard.",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Run in headless mode (disables TUI dashboard).",
    ),
):
    """Starts the continuous background polling daemon with a live terminal dashboard."""
    is_interactive = sys.stdout.isatty() and dashboard and not headless
    if not is_interactive:
        asyncio.run(_watch_daemon_headless(interval, config_path))
    else:
        asyncio.run(_watch_daemon_tui(interval, config_path))


async def _watch_daemon(
    interval_override: Optional[int],
    config_path: Optional[Path],
) -> None:
    """Standard headless daemon entry point (backwards compatibility)."""
    await _watch_daemon_headless(interval_override, config_path)


async def _watch_daemon_headless(
    interval_override: Optional[int],
    config_path: Optional[Path],
) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    logger = setup_logger(config.settings.resolved_log_dir, config.settings.log_level)
    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    # Register daemon process ID and clear past stop/reload flags
    import os
    daemon_pid = os.getpid()
    await state_manager.register_daemon(daemon_pid)
    await state_manager.clear_reload_request()

    watcher = SourceWatcher(config_path=config_path, watch_source=True)

    interval = interval_override or config.settings.poll_interval_seconds
    enabled_projects = [p for p in config.projects if p.enabled]

    console.print(Panel(
        f"[bold green]Starting Orchestrator Daemon[/bold green]\n"
        f"• Poll Interval: [cyan]{interval}s[/cyan]\n"
        f"• Managed Projects: [cyan]{len(enabled_projects)}[/cyan] (Parallel Workers)\n"
        f"• Daemon PID: [cyan]{daemon_pid}[/cyan]\n"
        f"• State DB: [cyan]{config.settings.resolved_db_path}[/cyan]\n"
        f"• Logs: [cyan]{config.settings.resolved_log_dir}[/cyan]\n"
        f"• Auto Hot-Reload: [green]Active[/green] (Watching config.yaml & source files)",
        title="Daemon Active",
        border_style="green",
    ))

    # Startup label synchronization
    console.print("[dim]Synchronizing repository workflow labels...[/dim]")
    await sync_all_projects_labels(config.projects, config.managed_labels)

    if not enabled_projects:
        console.print("[yellow]No enabled projects found in configuration.[/yellow]")
        await state_manager.unregister_daemon()
        return

    # Spawn concurrent worker tasks for each project
    workers = [
        asyncio.create_task(_project_worker_loop(p, config, state_manager, interval, config_path=config_path, watcher=watcher))
        for p in enabled_projects
    ]

    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        console.print("[yellow]Daemon stopped by user.[/yellow]")
    finally:
        AsyncHarnessAdapter.terminate_all_active()
        await state_manager.unregister_daemon()


async def _watch_daemon_tui(
    interval_override: Optional[int],
    config_path: Optional[Path],
) -> None:
    from orchestrator.logging import TextualLogHandler
    from orchestrator.ui.dashboard import DashboardApp

    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    textual_handler = TextualLogHandler(maxlen=1000)
    logger = setup_logger(
        config.settings.resolved_log_dir,
        config.settings.log_level,
        textual_handler=textual_handler,
    )
    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    import os
    daemon_pid = os.getpid()
    await state_manager.register_daemon(daemon_pid)
    await state_manager.clear_reload_request()

    watcher = SourceWatcher(config_path=config_path, watch_source=True)
    interval = interval_override or config.settings.poll_interval_seconds
    enabled_projects = [p for p in config.projects if p.enabled]

    # Startup label synchronization
    await sync_all_projects_labels(config.projects, config.managed_labels)

    app_instance = DashboardApp(
        config=config,
        state_manager=state_manager,
        log_handler=textual_handler,
    )

    if not enabled_projects:
        try:
            await app_instance.run_async()
        finally:
            await app_instance.teardown()
            await state_manager.unregister_daemon()
        return

    workers = [
        asyncio.create_task(_project_worker_loop(p, config, state_manager, interval, config_path=config_path, watcher=watcher))
        for p in enabled_projects
    ]
    tui_task = asyncio.create_task(app_instance.run_async())

    try:
        done, pending = await asyncio.wait([tui_task, *workers], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        tui_task.cancel()
    finally:
        await app_instance.teardown()
        AsyncHarnessAdapter.terminate_all_active()
        await state_manager.unregister_daemon()


@app.command("list")
def list_command(
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Displays a formatted table of all registered repositories and harness assignments."""
    asyncio.run(_list_projects(config_path))


async def _list_projects(config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()
    paused_projects = await state_manager.get_paused_projects()

    table = Table(title="Managed Project Repositories", header_style="bold cyan")
    table.add_column("Name", style="bold white")
    table.add_column("Repository", style="magenta")
    table.add_column("Local Path", style="green")
    table.add_column("Architect Harness", style="blue")
    table.add_column("DevTest Harness", style="yellow")
    table.add_column("Status", style="bold")

    for p in config.projects:
        arch_cfg = p.nodes.get("architect")
        dev_cfg = p.nodes.get("devtest")
        arch_str = f"{arch_cfg.harness}" if arch_cfg else "claude"
        dev_str = f"{dev_cfg.harness}" if dev_cfg else "antigravity"
        if not p.enabled:
            status = "[dim red]Disabled (config)[/dim red]"
        elif p.name in paused_projects:
            status = "[bold yellow]Paused (CLI)[/bold yellow]"
        else:
            status = "[bold green]Active[/bold green]"

        table.add_row(p.name, p.repo, str(p.local_path), arch_str, dev_str, status)

    try:
        console.print(table)
    except Exception:
        # Fallback for legacy console encodings
        console.print(str(table))


@app.command("init")
def init_command(
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target specific project to initialize and provision labels for.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Initializes SQLite database and provisions managed taxonomy labels across repositories."""
    asyncio.run(_run_init(project_name, config_path))


async def _run_init(project_name: Optional[str], config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    console.rule("[bold cyan]Initializing Graph Orchestrator[/bold cyan]")

    # 1. State Database Initialization
    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()
    console.print(f"[green]✔[/green] SQLite WAL State Database initialized at: [cyan]{config.settings.resolved_db_path}[/cyan]")

    # 2. Log Directory Verification
    config.settings.resolved_log_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]✔[/green] Logs directory verified at: [cyan]{config.settings.resolved_log_dir}[/cyan]")

    # 3. Provision Labels
    targets = [p for p in config.projects if p.enabled]
    if project_name:
        targets = [p for p in targets if p.name == project_name]

    if targets:
        console.print("[dim]Provisioning managed taxonomy labels on GitHub...[/dim]")
        for project in targets:
            results = await sync_repository_labels(project.repo, config.managed_labels)
            success_count = sum(1 for s in results.values() if s)
            total_count = len(config.managed_labels)
            if success_count == total_count:
                console.print(f"[green]✔[/green] Repository [bold magenta]{project.repo}[/bold magenta]: all {total_count} labels synchronized.")
            else:
                console.print(f"[yellow]⚠[/yellow] Repository [bold magenta]{project.repo}[/bold magenta]: {success_count}/{total_count} labels synchronized (check gh auth / permissions).")
    else:
        console.print("[dim]No enabled projects configured to synchronize labels.[/dim]")


@app.command("labels")
def labels_command(
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target a specific project repository.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Provisions and synchronizes workflow taxonomy labels to GitHub repositories."""
    asyncio.run(_run_labels(project_name, config_path))


async def _run_labels(project_name: Optional[str], config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    targets = [p for p in config.projects if p.enabled]
    if project_name:
        targets = [p for p in targets if p.name == project_name]

    if not targets:
        console.print("[yellow]No matching enabled projects found.[/yellow]")
        return

    table = Table(title="Repository Taxonomy Labels Synchronization", header_style="bold cyan")
    table.add_column("Repository", style="magenta")
    table.add_column("Label Name", style="bold white")
    table.add_column("Color", style="dim")
    table.add_column("Status", style="bold")

    for project in targets:
        with console.status(f"[cyan]Syncing labels for {project.repo}...[/cyan]"):
            results = await sync_repository_labels(project.repo, config.managed_labels)
        for label in config.managed_labels:
            synced = results.get(label.name, False)
            status = "[green]SYNCED[/green]" if synced else "[red]FAILED[/red]"
            table.add_row(project.repo, label.name, f"#{label.color}", status)

    console.print(table)


@app.command("doctor")
def doctor_command(
    sync_labels: bool = typer.Option(
        False,
        "--sync-labels",
        help="Automatically provision/sync managed taxonomy labels to target repositories during diagnostic check.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Verifies system prerequisites, tool availability, and local permissions."""
    asyncio.run(_run_doctor(sync_labels, config_path))


async def _run_doctor(sync_labels: bool, config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    table = Table(title="Orchestrator System Diagnostics", header_style="bold cyan")
    table.add_column("Check", style="bold white")
    table.add_column("Type", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    # 1. Check CLI binaries in PATH
    binaries = [
        ("git", "Version Control", True),
        ("gh", "GitHub CLI", True),
        ("claude", "Claude Code CLI", False),
        ("agy", "Antigravity CLI", False),
        ("devin", "Devin CLI", False),
    ]

    for binary, desc, mandatory in binaries:
        path = shutil.which(binary)
        if path:
            table.add_row(binary, desc, "[green]FOUND[/green]", path)
        else:
            status = "[red]MISSING[/red]" if mandatory else "[yellow]OPTIONAL[/yellow]"
            table.add_row(binary, desc, status, "Not found in PATH")

    # 2. Check GitHub Authentication Status
    if shutil.which("gh"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode == 0:
                table.add_row("gh auth", "GitHub Authentication", "[green]AUTHENTICATED[/green]", "Access verified")
            else:
                table.add_row("gh auth", "GitHub Authentication", "[yellow]WARNING[/yellow]", "Run 'gh auth login'")
        except Exception:
            table.add_row("gh auth", "GitHub Authentication", "[yellow]UNKNOWN[/yellow]", "Could not verify")

    # 3. Check State Database & WAL Mode
    try:
        state_manager = StateManager(config.settings.resolved_db_path)
        await state_manager.init_db()
        table.add_row("state.db", "SQLite WAL Database", "[green]OPERATIONAL[/green]", str(config.settings.resolved_db_path))
    except Exception as e:
        table.add_row("state.db", "SQLite WAL Database", "[red]ERROR[/red]", str(e))

    # 4. Check Project Directories
    for p in config.projects:
        if p.local_path.exists():
            git_dir = p.local_path / ".git"
            if git_dir.exists():
                table.add_row(f"Repo: {p.name}", "Project Directory", "[green]VALID[/green]", str(p.local_path))
            else:
                table.add_row(f"Repo: {p.name}", "Project Directory", "[yellow]NO .GIT[/yellow]", "Missing .git folder")
        else:
            table.add_row(f"Repo: {p.name}", "Project Directory", "[red]NOT FOUND[/red]", str(p.local_path))

    # 5. Label Synchronization (if requested)
    if sync_labels and config.projects:
        for p in [proj for proj in config.projects if proj.enabled]:
            results = await sync_repository_labels(p.repo, config.managed_labels)
            synced = sum(1 for s in results.values() if s)
            total = len(config.managed_labels)
            if synced == total:
                table.add_row(f"Labels: {p.name}", "GitHub Taxonomy", "[green]PROVISIONED[/green]", f"{synced}/{total} labels created/verified")
            else:
                table.add_row(f"Labels: {p.name}", "GitHub Taxonomy", "[yellow]PARTIAL[/yellow]", f"{synced}/{total} labels created/verified")

    console.print(table)


@app.command("ingest")
def ingest_command(
    project_name: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Target project name.",
    ),
    file_path: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to hardened User Story Markdown file.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Publishes a refined User Story to GitHub with the 'needs-triage' trigger label."""
    asyncio.run(_ingest_story(project_name, file_path, config_path))


async def _ingest_story(project_name: str, file_path: Path, config_path: Optional[Path]) -> None:
    config = load_config(config_path)
    matching = [p for p in config.projects if p.name == project_name]
    if not matching:
        console.print(f"[bold red]Error:[/bold red] Project '{project_name}' not found in configuration.")
        raise typer.Exit(code=1)

    project = matching[0]
    if not file_path.exists():
        console.print(f"[bold red]Error:[/bold red] File '{file_path}' does not exist.")
        raise typer.Exit(code=1)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract title from first H1 or use filename
    title = file_path.stem.replace("-", " ").title()
    for line in content.splitlines():
        if line.startswith("# "):
            title = line.replace("# ", "").strip()
            break

    if not shutil.which("gh"):
        console.print("[bold red]Error:[/bold red] 'gh' CLI not found in PATH.")
        raise typer.Exit(code=1)

    cmd = [
        "gh", "issue", "create",
        "--repo", project.repo,
        "--title", title,
        "--body-file", str(file_path),
        "--label", "needs-triage",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        issue_url = stdout.decode("utf-8").strip()
        console.print(f"[bold green]Success![/bold green] User story published: {issue_url}")
    else:
        console.print(f"[bold red]Failed to create issue:[/bold red] {stderr.decode('utf-8')}")


@app.command("clean")
def clean_command(
    stale_only: bool = typer.Option(
        False,
        "--stale-only",
        help="Only clean expired/failed locks rather than all entries.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Cleans lock entries from SQLite state database."""
    asyncio.run(_clean_db(stale_only, config_path))


async def _clean_db(stale_only: bool, config_path: Optional[Path]) -> None:
    config = load_config(config_path)
    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()
    count = await state_manager.clear_all_locks(stale_only=stale_only)
    console.print(f"[green]Cleared {count} lock record(s) from state database.[/green]")


@app.command("logs")
def logs_command(
    project_name: str = typer.Argument(
        ...,
        help="Name of the project to inspect.",
    ),
    node: Optional[str] = typer.Option(
        None,
        "--node",
        "-n",
        help="Filter logs by node (architect, devtest, supervisor).",
    ),
    lines: int = typer.Option(
        30,
        "--lines",
        "-l",
        help="Number of lines to tail.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Inspects recent execution logs for a project."""
    config = load_config(config_path)
    target_dir = config.settings.resolved_log_dir / project_name
    if node:
        target_dir = target_dir / node

    if not target_dir.exists():
        console.print(f"[yellow]No logs found in {target_dir}[/yellow]")
        return

    log_files = sorted(target_dir.rglob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        console.print(f"[yellow]No log files found in {target_dir}[/yellow]")
        return

    latest_file = log_files[0]
    console.rule(f"[bold cyan]Log: {latest_file.relative_to(config.settings.resolved_log_dir)}[/bold cyan]")

    with open(latest_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        content = "".join(tail)
        try:
            console.print(content)
        except Exception:
            sys.stdout.buffer.write(content.encode("utf-8", errors="replace"))
            sys.stdout.flush()


@app.command("pause")
def pause_command(
    project_name: str = typer.Argument(
        ...,
        help="Name of the project to pause.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Pauses scanning and development cycle for a specific project."""
    asyncio.run(_pause_project(project_name, config_path))


async def _pause_project(project_name: str, config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    matching = [p for p in config.projects if p.name == project_name]
    if not matching:
        console.print(f"[bold red]Error:[/bold red] Project '{project_name}' is not registered in configuration.")
        raise typer.Exit(code=1)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()
    await state_manager.pause_project(project_name)
    console.print(f"[bold yellow]⏸️ Project '{project_name}' is now PAUSED.[/bold yellow]")
    console.print("[dim]The orchestrator daemon will skip scanning and development for this project until resumed.[/dim]")


@app.command("resume")
def resume_command(
    project_name: str = typer.Argument(
        ...,
        help="Name of the project to resume.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Resumes scanning and development cycle for a paused project."""
    asyncio.run(_resume_project(project_name, config_path))


async def _resume_project(project_name: str, config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    matching = [p for p in config.projects if p.name == project_name]
    if not matching:
        console.print(f"[bold red]Error:[/bold red] Project '{project_name}' is not registered in configuration.")
        raise typer.Exit(code=1)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()
    await state_manager.resume_project(project_name)
    console.print(f"[bold green]▶️ Project '{project_name}' is now RESUMED (Active).[/bold green]")
    console.print("[dim]The orchestrator daemon will resume scanning and development for this project.[/dim]")


@app.command("stop")
def stop_command(
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target a specific registered project to pause instead of halting the global daemon.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Immediately terminate daemon process and all running child agents.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Gracefully halts the running background daemon or pauses a specific project."""
    if project_name:
        asyncio.run(_pause_project(project_name, config_path))
    else:
        asyncio.run(_stop_daemon(force, config_path))


async def _stop_daemon(force: bool, config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    daemon_pid = await state_manager.request_stop()
    active_killed = AsyncHarnessAdapter.terminate_all_active()

    if force and daemon_pid:
        try:
            import psutil
            proc = psutil.Process(daemon_pid)
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
            console.print(f"[bold green]✓ Force killed daemon process (PID: {daemon_pid}) and active agent processes.[/bold green]")
        except Exception as e:
            console.print(f"[bold yellow]Daemon PID {daemon_pid} was not active or already terminated: {e}[/bold yellow]")
    else:
        console.print("[bold green]✓ Safe stop signal registered in state database.[/bold green]")
        console.print("[dim]Daemon workers will finish current step without scheduling any new nodes.[/dim]")
        if active_killed > 0:
            console.print(f"[bold yellow]Terminated {active_killed} active AI harness process(es).[/bold yellow]")


@app.command("reload")
def reload_command(
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Hot-reloads configuration and Python modules in the running daemon without restarting."""
    asyncio.run(_reload_daemon(config_path))


async def _reload_daemon(config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    daemon_pid = await state_manager.request_reload()
    console.print("[bold green]✓ In-memory hot-reload signal registered in state database.[/bold green]")
    if daemon_pid:
        console.print(f"[dim]Active daemon (PID: {daemon_pid}) notified. It will reload configuration and Python modules on its next cycle.[/dim]")
    else:
        console.print("[dim]The daemon will reload configuration and Python modules on its next cycle check.[/dim]")



@app.command("artifact")
def artifact_command(
    pr_number: int = typer.Argument(
        ...,
        help="Pull Request number to inspect in the Blackboard.",
    ),
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target a specific registered project name.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Inspects a Pull Request review artifact stored in the local SQLite Blackboard."""
    asyncio.run(_show_artifact(pr_number, project_name, config_path))


async def _show_artifact(pr_number: int, project_name: Optional[str], config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    target_repo: Optional[str] = None
    if project_name:
        matching = [p for p in config.projects if p.name == project_name]
        if matching:
            target_repo = matching[0].repo

    if not target_repo and len(config.projects) == 1:
        target_repo = config.projects[0].repo

    all_artifacts = await state_manager.list_pr_artifacts(repo=target_repo)
    found = [a for a in all_artifacts if a.get("pr_number") == pr_number]

    if not found:
        console.print(f"[yellow]No Blackboard artifact found for PR #{pr_number}.[/yellow]")
        return

    art = found[0]
    console.rule(f"[bold cyan]Blackboard PR Artifact #{pr_number}[/bold cyan]")
    console.print(f"[bold white]Repository:[/bold white] [magenta]{art.get('repo')}[/magenta]")
    console.print(f"[bold white]Created By Node:[/bold white] [cyan]{art.get('node_name')}[/cyan]")
    console.print(f"[bold white]Status:[/bold white] [bold green]{art.get('status')}[/bold green]")
    console.print(f"[bold white]Updated At:[/bold white] [dim]{time.ctime(art.get('updated_at', 0))}[/dim]")
    console.print(f"\n[bold white]Context / Comment:[/bold white]\n{art.get('comment')}")


@app.command("artifacts")
def artifacts_command(
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Filter artifacts by project name.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Lists all active PR review artifacts stored in the local SQLite Blackboard."""
    asyncio.run(_list_artifacts(project_name, config_path))


async def _list_artifacts(project_name: Optional[str], config_path: Optional[Path]) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    target_repo: Optional[str] = None
    if project_name:
        matching = [p for p in config.projects if p.name == project_name]
        if matching:
            target_repo = matching[0].repo

    artifacts = await state_manager.list_pr_artifacts(repo=target_repo)
    if not artifacts:
        console.print("[dim]No PR review artifacts recorded in the Blackboard.[/dim]")
        return

    table = Table(title="Decoupled Blackboard: PR Review Artifacts", header_style="bold cyan")
    table.add_column("PR #", style="bold white")
    table.add_column("Repository", style="magenta")
    table.add_column("Node", style="cyan")
    table.add_column("Status", style="bold yellow")
    table.add_column("Comment / Decision", style="dim")
    table.add_column("Updated", style="dim")

    for art in artifacts:
        updated_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(art.get("updated_at", 0)))
        table.add_row(
            str(art.get("pr_number")),
            art.get("repo", ""),
            art.get("node_name", ""),
            art.get("status", ""),
            art.get("comment", "")[:60] + ("..." if len(art.get("comment", "")) > 60 else ""),
            updated_str,
        )

    console.print(table)


# =========================================================================
# Supervisor PO-Proxy CLI Subcommands
# =========================================================================

@supervisor_app.command("evaluate")
def supervisor_evaluate_command(
    issue_id: int = typer.Argument(
        ...,
        help="GitHub Issue number to evaluate.",
    ),
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target a specific registered project by name.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Render evaluation verdict, gaps, and Gherkin AC without mutating GitHub.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Evaluates an issue's readiness via PO-proxy Supervisor, generating Gherkin AC."""
    asyncio.run(_run_supervisor_evaluate(issue_id, project_name, dry_run, config_path))


async def _run_supervisor_evaluate(
    issue_id: int,
    project_name: Optional[str],
    dry_run: bool,
    config_path: Optional[Path],
) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    target_project: Optional[ProjectConfig] = None
    if project_name:
        matching = [p for p in config.projects if p.name == project_name]
        if not matching:
            console.print(f"[bold red]Error:[/bold red] Project '{project_name}' not found in configuration.")
            raise typer.Exit(code=1)
        target_project = matching[0]
    elif len(config.projects) == 1:
        target_project = config.projects[0]
    else:
        enabled = [p for p in config.projects if p.enabled]
        if len(enabled) == 1:
            target_project = enabled[0]
        else:
            console.print("[bold red]Error:[/bold red] Multiple projects configured. Please specify `-p/--project`.")
            raise typer.Exit(code=1)

    # Fetch the issue
    issue = await poller.fetch_issue_by_number(target_project.repo, issue_id)
    if not issue:
        # Fallback dictionary for testing / offline
        issue = {
            "number": issue_id,
            "title": f"Issue #{issue_id}",
            "body": "",
            "labels": [],
        }

    # Execute PO Evaluation
    result = await evaluate_supervisor_issue(
        project=target_project,
        issue=issue,
        config=config,
        state_manager=state_manager,
        dry_run=dry_run,
        force=True,
    )

    verdict_style = "bold green" if result.verdict == "PO_APPROVED" else "bold yellow"
    mode_text = "[bold cyan]DRY-RUN (No GitHub mutations emitted)[/bold cyan]" if dry_run else "[bold green]LIVE (GitHub Updated)[/bold green]"

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold white", width=22)
    table.add_column("Value", style="cyan")

    table.add_row("Issue #:", f"#{result.issue_number}")
    table.add_row("Title:", result.title)
    table.add_row("Repository:", target_project.repo)
    table.add_row("Execution Mode:", mode_text)
    table.add_row("Body Hash (SHA-256):", f"[dim]{result.body_hash}[/dim]")
    table.add_row("Readiness Verdict:", f"[{verdict_style}]{result.verdict}[/{verdict_style}]")

    if result.gaps:
        table.add_row("Detected Gaps / Blockers:", f"[yellow]{result.gaps}[/yellow]")
    else:
        table.add_row("Detected Gaps:", "[green]None (Complete functional requirements)[/green]")

    console.print(Panel(
        table,
        title=f"PO-Proxy Supervisor Evaluation: Issue #{result.issue_number}",
        border_style="green" if result.verdict == "PO_APPROVED" else "yellow",
    ))

    if result.gherkin_ac:
        console.print(Panel(
            result.gherkin_ac,
            title="[bold green]Generated Gherkin Acceptance Criteria[/bold green]",
            border_style="green",
        ))


@supervisor_app.command("status")
def supervisor_status_command(
    project_name: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Target a specific registered project by name.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to custom config.yaml file.",
    ),
):
    """Displays tracked issues from the po_tracking Blackboard table."""
    asyncio.run(_run_supervisor_status(project_name, config_path))


async def _run_supervisor_status(
    project_name: Optional[str],
    config_path: Optional[Path],
) -> None:
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

    state_manager = StateManager(config.settings.resolved_db_path)
    await state_manager.init_db()

    target_repo: Optional[str] = None
    if project_name:
        matching = [p for p in config.projects if p.name == project_name]
        if matching:
            target_repo = matching[0].repo
        else:
            console.print(f"[bold red]Error:[/bold red] Project '{project_name}' not found in configuration.")
            raise typer.Exit(code=1)
    elif len(config.projects) == 1:
        target_repo = config.projects[0].repo

    records = await state_manager.list_po_trackings(repo=target_repo)
    if not records:
        console.print("[dim]No issues currently tracked in po_tracking Blackboard.[/dim]")
        return

    table = Table(title="PO-Proxy Blackboard Tracking (po_tracking)", header_style="bold cyan")
    table.add_column("Issue #", style="bold white")
    table.add_column("Repository", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Hash", style="dim")
    table.add_column("Blockers / Gaps", style="dim")
    table.add_column("Updated", style="dim")

    for rec in records:
        updated_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec.get("updated_at", 0)))
        status_style = "bold green" if rec.get("status") == "PO_APPROVED" else "bold yellow"
        blockers_preview = (rec.get("blockers") or "None")[:40]
        hash_preview = (rec.get("body_hash") or "")[:8] + "..."

        table.add_row(
            str(rec.get("issue_number")),
            rec.get("repo", ""),
            f"[{status_style}]{rec.get('status', '')}[/{status_style}]",
            hash_preview,
            blockers_preview,
            updated_str,
        )

    console.print(table)



if __name__ == "__main__":
    app()
