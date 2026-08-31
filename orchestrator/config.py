from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrchestratorBaseModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class HarnessRetryConfig(OrchestratorBaseModel):
    max_retries: int = Field(default=3, ge=0)
    initial_delay_seconds: float = Field(default=5.0, ge=0.5)
    backoff_factor: float = Field(default=2.0, ge=1.0)
    max_delay_seconds: float = Field(default=60.0, ge=5.0)
    retryable_patterns: list[str] = Field(default_factory=lambda: [
        "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "502", "504",
        "rate limit", "quota exceeded", "connection reset", "server disconnected", "fetch failed"
    ])


class HarnessConfig(OrchestratorBaseModel):
    binary: str
    args: List[str] = Field(default_factory=list)
    model_flag: Optional[str] = None
    effort_flag: Optional[str] = None
    timeout_minutes: int = 30
    retry_on_failure: int = 1
    env_vars: Dict[str, str] = Field(default_factory=dict)
    retry: HarnessRetryConfig = Field(default_factory=HarnessRetryConfig)


class LabelConfig(OrchestratorBaseModel):
    name: str
    color: str = "ededed"
    description: str = ""


class NodeConfig(OrchestratorBaseModel):
    enabled: bool = True
    harness: str = "claude"
    model: Optional[str] = None
    effort: Optional[str] = None
    label_trigger: Optional[str] = None
    label_output: Optional[str] = None
    processed_label: Optional[str] = None
    queued_label: Optional[str] = "queued"
    branch_prefix: Optional[str] = "feat/issue-"
    auto_merge_approved: bool = True
    review_trigger: Optional[str] = "needs-architect-review"
    research_harness: Optional[str] = None
    research_model: Optional[str] = None
    research_effort: Optional[str] = None
    research_interval_seconds: int = 604800
    conflict_harness: Optional[str] = "antigravity"
    conflict_model: Optional[str] = "gemini-3.7-flash-low"
    conflict_effort: Optional[str] = None


def resolve_path(v: str | Path) -> Path:
    """
    Robustly expands paths across platforms, supporting:
    - Tilde (~) expansion
    - POSIX $HOME / ${HOME} variables even when running in Windows shells
    - Windows %USERPROFILE% / %APPDATA% variables on Linux/Windows
    - Any custom environment variables ($VAR, %VAR%)
    - Relative paths resolved against current working directory
    """
    if isinstance(v, Path):
        raw = str(v)
    else:
        raw = str(v)

    # 1. Expand ~ to user home
    expanded = os.path.expanduser(raw)

    # 2. Cross-platform home alias resolution ($HOME on Windows or %USERPROFILE% on POSIX)
    home_dir = str(Path.home())
    if "$HOME" in expanded:
        expanded = expanded.replace("$HOME", home_dir)
    if "${HOME}" in expanded:
        expanded = expanded.replace("${HOME}", home_dir)
    if "%USERPROFILE%" in expanded:
        expanded = expanded.replace("%USERPROFILE%", home_dir)

    # 3. Expand remaining environment variables
    expanded = os.path.expandvars(expanded)

    return Path(expanded).resolve()


DEFAULT_CONTEXT_FILES: List[str] = [
    ".graph/architecture.md",
    ".graph/testing-standards.md",
    ".graph/git-workflow.md",
]


class ProjectConfig(OrchestratorBaseModel):
    name: str
    repo: str
    local_path: Path
    enabled: bool = True
    context_files: List[str] = Field(default_factory=lambda: list(DEFAULT_CONTEXT_FILES))
    nodes: Dict[str, NodeConfig] = Field(default_factory=dict)
    max_planned_stories: int = 2
    worktrees_enabled: bool = True
    worktree_dir: Optional[Path] = None

    @field_validator("local_path", mode="before")
    @classmethod
    def expand_local_path(cls, v: str | Path) -> Path:
        return resolve_path(v)

    @field_validator("worktree_dir", mode="before")
    @classmethod
    def expand_worktree_dir(cls, v: Optional[str | Path]) -> Optional[Path]:
        if v is None:
            return None
        return resolve_path(v)


class SettingsConfig(OrchestratorBaseModel):
    poll_interval_seconds: int = 300
    supervisor_interval_seconds: int = 3600
    bau_interval_seconds: int = 86400  # 1 day / 24 hours interval for BAU maintenance
    max_concurrent_jobs: int = 4
    db_path: str = "~/.config/orchestrator/state.db"
    log_dir: str = "~/.config/orchestrator/logs"
    log_level: str = "INFO"
    max_planned_stories: int = 2
    worktrees_enabled: bool = True
    worktree_dir: Optional[str] = None

    @property
    def resolved_db_path(self) -> Path:
        p = resolve_path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def resolved_log_dir(self) -> Path:
        p = resolve_path(self.log_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def resolved_worktree_dir(self) -> Optional[Path]:
        if not self.worktree_dir:
            return None
        return resolve_path(self.worktree_dir)


DEFAULT_MANAGED_LABELS: List[LabelConfig] = [
    LabelConfig(name="needs-triage", color="E2B7E1", description="Awaiting Architect Node triage and decomposition"),
    LabelConfig(name="ready-for-dev", color="0E8A16", description="Awaiting 3Amigos DevTest implementation"),
    LabelConfig(name="queued", color="CFD3D7", description="Subtask queued for sequential execution"),
    LabelConfig(name="needs-architect-review", color="FBCA04", description="PR submitted, ready for architectural review"),
    LabelConfig(name="architect-approved", color="2EA44F", description="Architectural review passed, ready for final CI merge"),
    LabelConfig(name="needs-refactor", color="D93F0B", description="Architect identified structural violations, returned to DevTest"),
    LabelConfig(name="dev-implemented", color="C2E0C6", description="Implementation completed by DevTest node"),
    LabelConfig(name="orchestration-failed", color="B60205", description="AI Harness execution failed"),
    LabelConfig(name="needs-po-review", color="D93F0B", description="Supervisor flagged methodological conflict"),
    LabelConfig(name="architect-processed", color="D4C5F9", description="Architect decomposition complete"),
    LabelConfig(name="tech-debt", color="FBCA04", description="Technical debt or non-blocking improvement"),
    LabelConfig(name="enhancement", color="A2EEEF", description="New feature request or enhancement"),
    LabelConfig(name="planned", color="D4C5F9", description="Story queued in lookahead buffer behind active story"),
]

DEFAULT_HARNESSES: Dict[str, HarnessConfig] = {
    "claude": HarnessConfig(
        binary="claude",
        args=["-p", "{prompt}", "--dangerously-skip-permissions"],
        model_flag="--model",
        effort_flag="--effort",
        timeout_minutes=30,
        retry_on_failure=1,
    ),
    "antigravity": HarnessConfig(
        binary="agy",
        args=["-p", "{prompt}", "--dangerously-skip-permissions", "--print-timeout", "45m"],
        model_flag="--model",
        effort_flag="--effort",
        timeout_minutes=45,
        retry_on_failure=1,
    ),
    "devin": HarnessConfig(
        binary="devin",
        args=["-p", "{prompt}", "--permission-mode", "bypass"],
        model_flag="--model",
        timeout_minutes=60,
        retry_on_failure=1,
    ),
}


class HarnessQuotaConfig(OrchestratorBaseModel):
    window_hours: float = Field(default=1.0, gt=0)
    window_token_limit: int = Field(default=2_000_000, gt=0)
    avg_tokens_per_hour: int = Field(default=400_000, gt=0)


DEFAULT_HARNESS_QUOTAS: Dict[str, HarnessQuotaConfig] = {
    "antigravity": HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
    "claude": HarnessQuotaConfig(window_hours=5.0, window_token_limit=5_000_000, avg_tokens_per_hour=300_000),
    "devin": HarnessQuotaConfig(window_hours=5.0, window_token_limit=2_500_000, avg_tokens_per_hour=150_000),
    "openai": HarnessQuotaConfig(window_hours=1.0, window_token_limit=1_500_000, avg_tokens_per_hour=300_000),
}


class QuotaSettings(OrchestratorBaseModel):
    buffer_minutes: int = Field(default=30, ge=0)
    harnesses: Dict[str, HarnessQuotaConfig] = Field(default_factory=lambda: dict(DEFAULT_HARNESS_QUOTAS))


class GlobalConfig(OrchestratorBaseModel):
    version: int = 2
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    managed_labels: List[LabelConfig] = Field(default_factory=lambda: list(DEFAULT_MANAGED_LABELS))
    harnesses: Dict[str, HarnessConfig] = Field(default_factory=lambda: dict(DEFAULT_HARNESSES))
    quota: QuotaSettings = Field(default_factory=QuotaSettings)
    projects: List[ProjectConfig] = Field(default_factory=list)


def get_default_config_search_paths() -> List[Path]:
    paths: List[Path] = []
    # 1. Custom / Current directory
    paths.append(Path("config.yaml").resolve())
    paths.append(Path(".orchestrator.yaml").resolve())

    # 2. User config path ~/.config/orchestrator/config.yaml
    home = Path.home()
    paths.append(home / ".config" / "orchestrator" / "config.yaml")

    # 3. Windows userprofile fallback ~/.orchestrator/config.yaml
    paths.append(home / ".orchestrator" / "config.yaml")
    return paths


def find_config_file(custom_path: Optional[Path | str] = None) -> Optional[Path]:
    if custom_path:
        p = resolve_path(custom_path)
        if p.exists() and p.is_file():
            return p
        raise FileNotFoundError(f"Specified configuration file does not exist: {p}")

    for candidate in get_default_config_search_paths():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_config(custom_path: Optional[Path | str] = None) -> GlobalConfig:
    """
    Loads configuration from custom path or default paths, loading .env if present.
    """
    load_dotenv()

    config_path = find_config_file(custom_path)
    if not config_path:
        # Return default config if no file found yet
        return GlobalConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    # Merge default labels and harnesses if omitted in user config
    if "managed_labels" not in raw_data or not raw_data["managed_labels"]:
        raw_data["managed_labels"] = [lbl.model_dump() for lbl in DEFAULT_MANAGED_LABELS]

    if "harnesses" not in raw_data or not raw_data["harnesses"]:
        raw_data["harnesses"] = {k: v.model_dump() for k, v in DEFAULT_HARNESSES.items()}
    else:
        # Merge defaults for standard names
        for k, v in DEFAULT_HARNESSES.items():
            if k not in raw_data["harnesses"]:
                raw_data["harnesses"][k] = v.model_dump()

    # Merge quota defaults
    if "quota" in raw_data and isinstance(raw_data["quota"], dict):
        q_data = raw_data["quota"]
        if "harnesses" in q_data and isinstance(q_data["harnesses"], dict):
            merged_harnesses = {}
            for k, default_cfg in DEFAULT_HARNESS_QUOTAS.items():
                if k in q_data["harnesses"]:
                    user_h = q_data["harnesses"][k]
                    if isinstance(user_h, dict):
                        d = default_cfg.model_dump()
                        d.update(user_h)
                        merged_harnesses[k] = d
                    else:
                        merged_harnesses[k] = user_h
                else:
                    merged_harnesses[k] = default_cfg.model_dump()
            for k, user_h in q_data["harnesses"].items():
                if k not in merged_harnesses:
                    merged_harnesses[k] = user_h
            q_data["harnesses"] = merged_harnesses

    return GlobalConfig(**raw_data)
