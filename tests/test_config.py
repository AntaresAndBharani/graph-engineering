from __future__ import annotations

from pathlib import Path
from pydantic import ValidationError
import pytest
from orchestrator.config import (
    HarnessConfig,
    HarnessQuotaConfig,
    NodeConfig,
    ProjectConfig,
    QuotaSettings,
    SettingsConfig,
    WindowLimitConfig,
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


def test_load_config_with_harness_retry(tmp_path: Path):
    yaml_file = tmp_path / "retry_config.yaml"
    yaml_file.write_text(
        """
version: 2
harnesses:
  custom_harness:
    binary: "custom"
    retry:
      max_retries: 5
      initial_delay_seconds: 2.0
      backoff_factor: 3.0
      max_delay_seconds: 30.0
      retryable_patterns:
        - "503"
        - "custom_rate_limit"
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert "custom_harness" in loaded.harnesses
    custom_retry = loaded.harnesses["custom_harness"].retry
    assert custom_retry.max_retries == 5
    assert custom_retry.initial_delay_seconds == 2.0
    assert custom_retry.backoff_factor == 3.0
    assert custom_retry.max_delay_seconds == 30.0
    assert "custom_rate_limit" in custom_retry.retryable_patterns


def test_default_quota_config_available_with_no_user_overrides(tmp_path: Path):
    """
    Scenario: Default harness quota definitions load
      Given a config.yaml with no explicit `quota` section
      When configuration is loaded via `load_config()`
      Then `QuotaSettings` defaults to buffer_minutes=30
      And harnesses "antigravity", "claude", "devin", "openai" have their documented
          window_hours, window_token_limit, and avg_tokens_per_hour defaults
      And harnesses have sensible default weekly limits configured
    """
    yaml_file = tmp_path / "default_quota_config.yaml"
    yaml_file.write_text(
        """
version: 2
settings:
  poll_interval_seconds: 60
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert loaded.quota.buffer_minutes == 30
    assert set(loaded.quota.harnesses.keys()) == {"antigravity", "claude", "devin", "openai"}

    antigravity = loaded.quota.harnesses["antigravity"]
    assert antigravity.window_hours == 1.0
    assert antigravity.window_token_limit == 2_000_000
    assert antigravity.avg_tokens_per_hour == 400_000
    assert antigravity.weekly is not None
    assert antigravity.weekly.hours == 168.0
    assert antigravity.weekly.token_limit == 20_000_000

    claude = loaded.quota.harnesses["claude"]
    assert claude.window_hours == 5.0
    assert claude.window_token_limit == 5_000_000
    assert claude.avg_tokens_per_hour == 300_000
    assert claude.weekly is not None
    assert claude.weekly.hours == 168.0
    assert claude.weekly.token_limit == 20_000_000

    devin = loaded.quota.harnesses["devin"]
    assert devin.window_hours == 5.0
    assert devin.window_token_limit == 2_500_000
    assert devin.avg_tokens_per_hour == 150_000
    assert devin.weekly is not None
    assert devin.weekly.hours == 168.0
    assert devin.weekly.token_limit == 10_000_000

    openai = loaded.quota.harnesses["openai"]
    assert openai.window_hours == 1.0
    assert openai.window_token_limit == 1_500_000
    assert openai.avg_tokens_per_hour == 300_000
    assert openai.weekly is not None
    assert openai.weekly.hours == 168.0
    assert openai.weekly.token_limit == 10_000_000


def test_partial_user_override_preserves_other_harness_defaults(tmp_path: Path):
    """
    Scenario: Override a harness quota window
      Given config.yaml sets `quota.harnesses.claude.window_token_limit: 3000000`
      When configuration is loaded
      Then `GlobalConfig.quota.harnesses["claude"].window_token_limit == 3000000`
      And unspecified fields (including weekly limit) retain their documented defaults
    """
    yaml_file = tmp_path / "partial_override_config.yaml"
    yaml_file.write_text(
        """
version: 2
quota:
  buffer_minutes: 45
  harnesses:
    claude:
      window_token_limit: 3000000
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert loaded.quota.buffer_minutes == 45

    # Claude was partially overridden: window_token_limit updated, window_hours & avg_tokens_per_hour & weekly retained
    claude = loaded.quota.harnesses["claude"]
    assert claude.window_token_limit == 3_000_000
    assert claude.window_hours == 5.0
    assert claude.avg_tokens_per_hour == 300_000
    assert claude.weekly is not None
    assert claude.weekly.hours == 168.0
    assert claude.weekly.token_limit == 20_000_000

    # Antigravity, devin, openai retained defaults
    antigravity = loaded.quota.harnesses["antigravity"]
    assert antigravity.window_hours == 1.0
    assert antigravity.window_token_limit == 2_000_000
    assert antigravity.avg_tokens_per_hour == 400_000
    assert antigravity.weekly is not None
    assert antigravity.weekly.token_limit == 20_000_000

    devin = loaded.quota.harnesses["devin"]
    assert devin.window_hours == 5.0
    assert devin.window_token_limit == 2_500_000
    assert devin.avg_tokens_per_hour == 150_000
    assert devin.weekly is not None
    assert devin.weekly.token_limit == 10_000_000

    openai = loaded.quota.harnesses["openai"]
    assert openai.window_hours == 1.0
    assert openai.window_token_limit == 1_500_000
    assert openai.avg_tokens_per_hour == 300_000
    assert openai.weekly is not None
    assert openai.weekly.token_limit == 10_000_000


def test_dual_window_schema_backward_compatibility():
    """
    Scenario: Backward-compatible dual-window schema
      Given the existing single-window `HarnessQuotaConfig` (window_hours, window_token_limit, avg_tokens_per_hour)
      When a new `WindowLimitConfig` (hours, token_limit) is introduced
      And `HarnessQuotaConfig` is extended with an optional `weekly: WindowLimitConfig` field defaulting to None
      Then existing config instantiations without a `weekly` block continue to load and validate without error
    """
    # Single-window without weekly block defaults weekly to None
    single = HarnessQuotaConfig(window_hours=2.0, window_token_limit=1_000_000, avg_tokens_per_hour=200_000)
    assert single.window_hours == 2.0
    assert single.window_token_limit == 1_000_000
    assert single.avg_tokens_per_hour == 200_000
    assert single.weekly is None

    # WindowLimitConfig defaults hours to 168.0
    w_limit = WindowLimitConfig(token_limit=15_000_000)
    assert w_limit.hours == 168.0
    assert w_limit.token_limit == 15_000_000

    # Dual-window with explicit weekly block
    dual = HarnessQuotaConfig(
        window_hours=5.0,
        window_token_limit=5_000_000,
        avg_tokens_per_hour=300_000,
        weekly=WindowLimitConfig(hours=168.0, token_limit=20_000_000),
    )
    assert dual.weekly is not None
    assert dual.weekly.hours == 168.0
    assert dual.weekly.token_limit == 20_000_000


def test_weekly_quota_override_from_yaml(tmp_path: Path):
    """
    Asserts weekly limits can be fully or partially overridden in config.yaml.
    """
    yaml_file = tmp_path / "weekly_override_config.yaml"
    yaml_file.write_text(
        """
version: 2
quota:
  harnesses:
    claude:
      weekly:
        hours: 120.0
        token_limit: 30000000
    antigravity:
      weekly:
        token_limit: 25000000
    custom_ai:
      window_hours: 2.0
      window_token_limit: 1000000
      avg_tokens_per_hour: 200000
      weekly:
        hours: 168.0
        token_limit: 8000000
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)

    # Claude: weekly fully overridden
    claude = loaded.quota.harnesses["claude"]
    assert claude.weekly is not None
    assert claude.weekly.hours == 120.0
    assert claude.weekly.token_limit == 30_000_000
    assert claude.window_hours == 5.0
    assert claude.window_token_limit == 5_000_000

    # Antigravity: weekly partially overridden (token_limit changed, hours inherited default)
    antigravity = loaded.quota.harnesses["antigravity"]
    assert antigravity.weekly is not None
    assert antigravity.weekly.hours == 168.0
    assert antigravity.weekly.token_limit == 25_000_000

    # Custom AI harness with weekly block
    custom_ai = loaded.quota.harnesses["custom_ai"]
    assert custom_ai.weekly is not None
    assert custom_ai.weekly.hours == 168.0
    assert custom_ai.weekly.token_limit == 8_000_000


def test_invalid_quota_values_raise_validation_error(tmp_path: Path):
    """
    Scenario: Invalid quota values are rejected
      Given config.yaml sets a negative `window_hours` for a harness
      When configuration is loaded
      Then Pydantic validation raises a descriptive error at load time (fail fast, not at dispatch time)
    """
    yaml_file = tmp_path / "invalid_quota_config.yaml"
    yaml_file.write_text(
        """
version: 2
quota:
  harnesses:
    claude:
      window_hours: -1.0
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(yaml_file)


def test_invalid_weekly_quota_values_raise_validation_error(tmp_path: Path):
    """
    Scenario: Validation rejects malformed weekly config
      Given a `weekly.hours` or `weekly.token_limit` <= 0
      When the config is loaded via Pydantic
      Then a validation error is raised at load time (fail-fast), not at query time
    """
    # Negative weekly.hours via YAML
    yaml_hours_neg = tmp_path / "invalid_weekly_hours_neg.yaml"
    yaml_hours_neg.write_text(
        """
version: 2
quota:
  harnesses:
    claude:
      weekly:
        hours: -5.0
        token_limit: 20000000
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(yaml_hours_neg)

    # Zero weekly.hours via YAML
    yaml_hours_zero = tmp_path / "invalid_weekly_hours_zero.yaml"
    yaml_hours_zero.write_text(
        """
version: 2
quota:
  harnesses:
    claude:
      weekly:
        hours: 0
        token_limit: 20000000
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(yaml_hours_zero)

    # Negative weekly.token_limit via YAML
    yaml_token_neg = tmp_path / "invalid_weekly_token_neg.yaml"
    yaml_token_neg.write_text(
        """
version: 2
quota:
  harnesses:
    claude:
      weekly:
        token_limit: -1000
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(yaml_token_neg)

    # Zero weekly.token_limit via YAML
    yaml_token_zero = tmp_path / "invalid_weekly_token_zero.yaml"
    yaml_token_zero.write_text(
        """
version: 2
quota:
  harnesses:
    claude:
      weekly:
        token_limit: 0
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(yaml_token_zero)


def test_window_limit_config_validation_errors():
    # Negative and zero hours on WindowLimitConfig
    with pytest.raises(ValidationError):
        WindowLimitConfig(hours=0, token_limit=1_000_000)
    with pytest.raises(ValidationError):
        WindowLimitConfig(hours=-10.0, token_limit=1_000_000)

    # Negative and zero token_limit on WindowLimitConfig
    with pytest.raises(ValidationError):
        WindowLimitConfig(hours=168.0, token_limit=0)
    with pytest.raises(ValidationError):
        WindowLimitConfig(hours=168.0, token_limit=-500)


def test_custom_harness_quota_merged(tmp_path: Path):
    yaml_file = tmp_path / "custom_quota_config.yaml"
    yaml_file.write_text(
        """
version: 2
quota:
  harnesses:
    custom_runner:
      window_hours: 2.5
      window_token_limit: 1000000
      avg_tokens_per_hour: 200000
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert "custom_runner" in loaded.quota.harnesses
    assert loaded.quota.harnesses["custom_runner"].window_hours == 2.5
    assert loaded.quota.harnesses["custom_runner"].window_token_limit == 1_000_000
    assert loaded.quota.harnesses["custom_runner"].avg_tokens_per_hour == 200_000
    # Standard harnesses still present
    assert "antigravity" in loaded.quota.harnesses
    assert "claude" in loaded.quota.harnesses
    assert "devin" in loaded.quota.harnesses
    assert "openai" in loaded.quota.harnesses


def test_harness_quota_config_validation_errors():
    # Negative and zero window_hours
    with pytest.raises(ValidationError):
        HarnessQuotaConfig(window_hours=0)
    with pytest.raises(ValidationError):
        HarnessQuotaConfig(window_hours=-1.0)

    # Negative and zero window_token_limit
    with pytest.raises(ValidationError):
        HarnessQuotaConfig(window_token_limit=0)
    with pytest.raises(ValidationError):
        HarnessQuotaConfig(window_token_limit=-500)

    # Negative and zero avg_tokens_per_hour
    with pytest.raises(ValidationError):
        HarnessQuotaConfig(avg_tokens_per_hour=0)
    with pytest.raises(ValidationError):
        HarnessQuotaConfig(avg_tokens_per_hour=-100)

    # Negative buffer_minutes on QuotaSettings
    with pytest.raises(ValidationError):
        QuotaSettings(buffer_minutes=-5)

    # Buffer minutes of 0 is allowed
    qs = QuotaSettings(buffer_minutes=0)
    assert qs.buffer_minutes == 0


def test_project_config_worktree_defaults(tmp_path: Path):
    """
    Scenario: New config fields have safe defaults
      Given a ProjectConfig loaded without any worktree-related keys
      When the config is parsed
      Then "max_planned_stories" defaults to 2
      And "worktrees_enabled" defaults to True
      And "worktree_dir" defaults to None
    """
    proj = ProjectConfig(
        name="alpha",
        repo="my-org/alpha",
        local_path=str(tmp_path),
    )
    assert proj.max_planned_stories == 2
    assert proj.worktrees_enabled is True
    assert proj.worktree_dir is None


def test_project_config_worktree_overrides_from_yaml(tmp_path: Path):
    """
    Scenario: Config fields are overridable per project
      Given a project YAML entry with "max_planned_stories: 5" and "worktrees_enabled: false"
      When the config is loaded
      Then ProjectConfig.max_planned_stories equals 5
      And ProjectConfig.worktrees_enabled equals False
    """
    yaml_file = tmp_path / "worktree_override.yaml"
    yaml_file.write_text(
        f"""
version: 2
projects:
  - name: "alpha"
    repo: "my-org/alpha"
    local_path: "{tmp_path.as_posix()}"
    max_planned_stories: 5
    worktrees_enabled: false
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert len(loaded.projects) == 1
    proj = loaded.projects[0]
    assert proj.max_planned_stories == 5
    assert proj.worktrees_enabled is False
    assert proj.worktree_dir is None


def test_project_config_worktree_dir_path_expansion(tmp_path: Path):
    """
    Scenario: worktree_dir resolves like other paths
      Given a project YAML entry with "worktree_dir: ~/custom/worktrees"
      When the config is loaded
      Then the resolved path uses the existing resolve_path() home/env expansion behavior consistent with local_path
    """
    yaml_file = tmp_path / "worktree_dir.yaml"
    yaml_file.write_text(
        f"""
version: 2
projects:
  - name: "alpha"
    repo: "my-org/alpha"
    local_path: "{tmp_path.as_posix()}"
    worktree_dir: "~/custom/worktrees"
  - name: "beta"
    repo: "my-org/beta"
    local_path: "{tmp_path.as_posix()}"
    worktree_dir: "$HOME/beta_worktrees"
  - name: "gamma"
    repo: "my-org/gamma"
    local_path: "{tmp_path.as_posix()}"
    worktree_dir: "%USERPROFILE%/gamma_worktrees"
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert len(loaded.projects) == 3

    alpha = loaded.projects[0]
    assert isinstance(alpha.worktree_dir, Path)
    assert alpha.worktree_dir == (Path.home() / "custom" / "worktrees").resolve()

    beta = loaded.projects[1]
    assert isinstance(beta.worktree_dir, Path)
    assert beta.worktree_dir == (Path.home() / "beta_worktrees").resolve()

    gamma = loaded.projects[2]
    assert isinstance(gamma.worktree_dir, Path)
    assert gamma.worktree_dir == (Path.home() / "gamma_worktrees").resolve()


def test_settings_config_worktree_defaults_and_overrides(tmp_path: Path):
    """
    Scenario: SettingsConfig supports worktree & lookahead settings with safe defaults
    """
    # Safe defaults
    settings = SettingsConfig()
    assert settings.max_planned_stories == 2
    assert settings.worktrees_enabled is True
    assert settings.worktree_dir is None
    assert settings.resolved_worktree_dir is None

    # Overrides via YAML
    yaml_file = tmp_path / "settings_worktree.yaml"
    yaml_file.write_text(
        """
version: 2
settings:
  max_planned_stories: 4
  worktrees_enabled: false
  worktree_dir: "~/global/worktrees"
        """,
        encoding="utf-8",
    )

    loaded = load_config(yaml_file)
    assert loaded.settings.max_planned_stories == 4
    assert loaded.settings.worktrees_enabled is False
    assert loaded.settings.worktree_dir == "~/global/worktrees"
    assert loaded.settings.resolved_worktree_dir == (Path.home() / "global" / "worktrees").resolve()


def test_project_config_is_node_enabled_helper(tmp_path: Path):
    """
    Asserts ProjectConfig.is_node_enabled accurately respects project and node level flags.
    """
    proj = ProjectConfig(
        name="test-project",
        repo="org/repo",
        local_path=str(tmp_path),
        enabled=True,
        nodes={
            "architect": NodeConfig(enabled=True),
            "devtest": NodeConfig(enabled=True),
            "reviewer": NodeConfig(enabled=False),
            "supervisor": NodeConfig(enabled=False),
        },
    )

    # Configured nodes
    assert proj.is_node_enabled("architect") is True
    assert proj.is_node_enabled("devtest") is True
    assert proj.is_node_enabled("reviewer") is False
    assert proj.is_node_enabled("supervisor") is False

    # Unconfigured node defaults to enabled if project is enabled
    assert proj.is_node_enabled("bau") is True
    assert proj.is_node_enabled("custom_node") is True

    # Disabled project overrides all nodes to False
    proj_disabled = ProjectConfig(
        name="disabled-proj",
        repo="org/repo",
        local_path=str(tmp_path),
        enabled=False,
        nodes={
            "architect": NodeConfig(enabled=True),
            "devtest": NodeConfig(enabled=True),
        },
    )
    assert proj_disabled.is_node_enabled("architect") is False
    assert proj_disabled.is_node_enabled("devtest") is False
    assert proj_disabled.is_node_enabled("unconfigured") is False


def test_example_config_template_disables_dormant_nodes():
    """
    Asserts templates/config.example.yaml disables dormant nodes by default (reviewer, supervisor, bau)
    while keeping active nodes enabled (architect, devtest).
    """
    template_path = Path(__file__).resolve().parent.parent / "templates" / "config.example.yaml"
    assert template_path.exists(), f"Template not found at {template_path}"

    config = load_config(template_path)
    assert len(config.projects) >= 1
    proj = config.projects[0]

    assert proj.is_node_enabled("architect") is True
    assert proj.is_node_enabled("devtest") is True
    assert proj.is_node_enabled("reviewer") is False
    assert proj.is_node_enabled("supervisor") is False
    assert proj.is_node_enabled("bau") is False

    assert proj.nodes["architect"].enabled is True
    assert proj.nodes["devtest"].enabled is True
    assert proj.nodes["reviewer"].enabled is False
    assert proj.nodes["supervisor"].enabled is False
    assert proj.nodes["bau"].enabled is False




