from __future__ import annotations

import asyncio
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

from orchestrator import __version__
from orchestrator.config import GlobalConfig, load_config
from orchestrator.db import StateManager
from orchestrator.housekeeping import sync_all_projects_labels, sync_repository_labels
from orchestrator.logging import setup_logger
from orchestrator.nodes.architect import run_architect_node
from orchestrator.nodes.devtest import run_devtest_node
from orchestrator.nodes.reviewer import run_reviewer_node
from orchestrator.nodes.supervisor import run_supervisor_node

app = typer.Typer(
    name="orchestrator",
    help="Decoupled, Agnostic Multi-Agent CLI Orchestrator for Engineering Pipelines.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


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

    # Clean any expired locks
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

    for project in targets:
        console.rule(f"[bold cyan]Project: {project.name} ({project.repo})[/bold cyan]")

        # 1. Supervisor Node
        if node_name is None or node_name == "supervisor":
            with console.status("[bold blue]Checking Consistency Supervisor...[/bold blue]"):
                ran, msg = await run_supervisor_node(project, config, state_manager)
            if ran:
                console.print(f"  [bold green]Supervisor:[/bold green] {msg}")
            else:
                console.print(f"  [dim]Supervisor: {msg}[/dim]")

        # 2. Architect Node
        if node_name is None or node_name == "architect":
            with console.status("[bold magenta]Evaluating Architect Node...[/bold magenta]"):
                ran, msg = await run_architect_node(project, config, state_manager)
            if ran:
                console.print(f"  [bold green]Architect:[/bold green] {msg}")
            else:
                console.print(f"  [dim]Architect: {msg}[/dim]")

        # 3. DevTest Node
        if node_name is None or node_name == "devtest":
            with console.status("[bold yellow]Evaluating DevTest Node...[/bold yellow]"):
                ran, msg = await run_devtest_node(project, config, state_manager)
            if ran:
                console.print(f"  [bold green]DevTest:[/bold green] {msg}")
            else:
                console.print(f"  [dim]DevTest: {msg}[/dim]")

        # 4. Reviewer / Gatekeeper Node
        if node_name is None or node_name in ("reviewer", "review"):
            with console.status("[bold green]Evaluating Reviewer Gatekeeper Node...[/bold green]"):
                ran, msg = await run_reviewer_node(project, config, state_manager)
            if ran:
                console.print(f"  [bold green]Reviewer:[/bold green] {msg}")
            else:
                console.print(f"  [dim]Reviewer: {msg}[/dim]")


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
):
    """Starts the continuous background polling daemon with a live terminal dashboard."""
    asyncio.run(_watch_daemon(interval, config_path))


async def _watch_daemon(
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

    interval = interval_override or config.settings.poll_interval_seconds
    console.print(Panel(
        f"[bold green]Starting Orchestrator Daemon[/bold green]\n"
        f"• Poll Interval: [cyan]{interval}s[/cyan]\n"
        f"• Managed Projects: [cyan]{len([p for p in config.projects if p.enabled])}[/cyan]\n"
        f"• State DB: [cyan]{config.settings.resolved_db_path}[/cyan]\n"
        f"• Logs: [cyan]{config.settings.resolved_log_dir}[/cyan]",
        title="Daemon Active",
        border_style="green",
    ))

    # Startup label synchronization
    console.print("[dim]Synchronizing repository workflow labels...[/dim]")
    await sync_all_projects_labels(config.projects, config.managed_labels)

    iteration = 0
    try:
        while True:
            iteration += 1
            await state_manager.cleanup_expired_locks()

            # Execute run pass
            for project in [p for p in config.projects if p.enabled]:
                await run_supervisor_node(project, config, state_manager)
                await run_architect_node(project, config, state_manager)
                await run_devtest_node(project, config, state_manager)
                await run_reviewer_node(project, config, state_manager)

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        console.print("[yellow]Daemon stopped by user.[/yellow]")


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
    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=2)

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
        status = "[green]Enabled[/green]" if p.enabled else "[dim red]Disabled[/dim red]"

        table.add_row(p.name, p.repo, str(p.local_path), arch_str, dev_str, status)

    console.print(table)


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


if __name__ == "__main__":
    app()
