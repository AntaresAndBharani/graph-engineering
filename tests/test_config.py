from __future__ import annotations

from pathlib import Path
import pytest
from orchestrator.config import (
    GlobalConfig,
    HarnessConfig,
    LabelConfig,
    ProjectConfig,
    SettingsConfig,
    load_config,
)


def test_harness_config_defaults():
    cfg = HarnessConfig(binary="claude", args=["-p", "{prompt}"])
    assert cfg.binary == "claude"
    assert cfg.timeout_minutes == 30
    assert cfg.retry_on_failure == 1
    assert cfg.env_vars == {}


def test_project_config_path_expansion(tmp_path: Path):
    proj = ProjectConfig(
        name="test-proj",
        repo="org/test-repo",
        local_path=str(tmp_path),
    )
    assert proj.local_path == tmp_path.resolve()
    assert proj.enabled is True

    # Test tilde expansion (OS-agnostic)
    proj_tilde = ProjectConfig(
        name="tilde-proj",
        repo="org/tilde-repo",
        local_path="~/some_workspace",
    )
    assert str(Path.home()) in str(proj_tilde.local_path)

    # Test $HOME expansion on any platform
    proj_dollar_home = ProjectConfig(
        name="dollar-home-proj",
        repo="org/dollar-home",
        local_path="$HOME/workspaces/my-app",
    )
    assert "$HOME" not in str(proj_dollar_home.local_path)
    assert str(Path.home()) in str(proj_dollar_home.local_path)

    # Test ${HOME} expansion
    proj_bracket_home = ProjectConfig(
        name="bracket-home-proj",
        repo="org/bracket-home",
        local_path="${HOME}/workspaces/my-app",
    )
    assert "${HOME}" not in str(proj_bracket_home.local_path)
    assert str(Path.home()) in str(proj_bracket_home.local_path)

    # Test %USERPROFILE% expansion
    proj_win_home = ProjectConfig(
        name="win-home-proj",
        repo="org/win-home",
        local_path="%USERPROFILE%/workspaces/my-app",
    )
    assert "%USERPROFILE%" not in str(proj_win_home.local_path)
    assert str(Path.home()) in str(proj_win_home.local_path)


def test_load_config_from_file(tmp_path: Path):
    yaml_file = tmp_path / "test_config.yaml"
    yaml_file.write_text(
        """
version: 2
settings:
  poll_interval_seconds: 120
  db_path: "./test_state.db"
projects:
  - name: "alpha"
    repo: "my-org/alpha"
    local_path: "."
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert loaded.version == 2
    assert loaded.settings.poll_interval_seconds == 120
    assert len(loaded.projects) == 1
    assert loaded.projects[0].name == "alpha"
    assert len(loaded.managed_labels) > 0  # Default labels populated
    assert "claude" in loaded.harnesses  # Default harnesses populated


def test_project_config_default_context_files(tmp_path: Path):
    proj = ProjectConfig(name="proj", repo="org/repo", local_path=str(tmp_path))
    assert ".graph/architecture.md" in proj.context_files
    assert ".graph/testing-standards.md" in proj.context_files
    assert ".graph/git-workflow.md" in proj.context_files

