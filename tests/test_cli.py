from __future__ import annotations

from typer.testing import CliRunner
from orchestrator.cli import app

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Graph Orchestrator" in result.stdout


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Decoupled, Agnostic Multi-Agent CLI Orchestrator" in result.stdout
    assert "run" in result.stdout
    assert "watch" in result.stdout
    assert "list" in result.stdout
    assert "doctor" in result.stdout


def test_cli_list(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
version: 2
projects:
  - name: "alpha"
    repo: "org/alpha"
    local_path: "."
        """,
        encoding="utf-8",
    )
    result = runner.invoke(app, ["list", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "org/alpha" in result.stdout


def test_cli_doctor(tmp_path):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Orchestrator System Diagnostics" in result.stdout
